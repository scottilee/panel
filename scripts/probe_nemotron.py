"""Measurement harness for the Nemotron behaviours the README's feedback
section reports.

    python scripts/probe_nemotron.py --trials 10

Exists so the numbers in the README's feedback section can be re-derived
rather than taken on trust. Every probe calls the live API and costs real
credits (the budget probe provokes failures on purpose, and a failed call
bills for every token it burned), so none of this runs in the test suite.

Ten trials is the floor worth reporting. At three, this harness told us a
24000-token budget failed 0/3 and we changed production code on it; at ten
the same budget is the second best of four. The variance is wide enough to
invert a conclusion.

Probe 1 -- budget. One fixed prompt at several max_tokens values. Tests
whether a larger ceiling reduces truncation, and what a failure costs.

Probe 2 -- input length. One fixed budget over several batch sizes. Tests
whether runaway reasoning tracks how much there is to read.

Probe 3 -- model. The shipped digest configuration run on Nano and on
Super. Tests which tier is actually better at this job, as opposed to
which one sounds like it should be.

All of them bypass chat_json's retry ladder: each attempt is measured on
its own terms, not rescued by a doubling, which is the whole point.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from panel.config import MODEL_NANO, MODEL_SUPER  # noqa: E402
from panel.schemas import Reaction  # noqa: E402
from panel.synthesize import (  # noqa: E402
    DIGEST_EXAMPLES_PER_THEME,
    DIGEST_MAX_TOKENS,
    DIGEST_THEMES_PER_CATEGORY,
    SHARD_SIZE,
    _DIGEST_SYSTEM,
    _DIGEST_USER_TEMPLATE,
    _reactions_for_digest,
    _ShardDigest,
)
from panel.tf_client import _json_schema_for, get_client  # noqa: E402

BUDGETS = [8_000, 16_000, 24_000, 48_000]
SIZES = [50, 25, 10, 5]
PROBE_SEED = 20261028


def load_reactions() -> list[Reaction]:
    """Real reactions from the largest persisted run available."""
    runs = sorted(ROOT.glob("data/runs/*.json"))
    best: list[Reaction] = []
    for path in runs:
        data = json.loads(path.read_text())
        reactions = [Reaction.model_validate(r) for r in data.get("reactions", [])]
        reactions = [r for r in reactions if not r.raw_error]
        if len(reactions) > len(best):
            best = reactions
    if len(best) < max(SIZES):
        sys.exit(
            f"need a persisted run with at least {max(SIZES)} good reactions in "
            "data/runs/; do a real run first"
        )
    return best


def build_prompt(shard: list[Reaction]) -> str:
    return _DIGEST_USER_TEMPLATE.format(
        n=len(shard),
        reactions_text=_reactions_for_digest(shard),
        schema=json.dumps(_ShardDigest.model_json_schema()),
        max_themes=DIGEST_THEMES_PER_CATEGORY,
        max_examples=DIGEST_EXAMPLES_PER_THEME,
    )


async def attempt(user: str, budget: int, model: str = MODEL_NANO) -> dict:
    """One raw call. No retries, so a failure is recorded as a failure."""
    start = time.monotonic()
    resp = await get_client().chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _DIGEST_SYSTEM},
            {"role": "user", "content": user},
        ],
        temperature=0.2,
        max_tokens=budget,
        response_format={"type": "json_schema", "json_schema": _json_schema_for(_ShardDigest)},
    )
    elapsed = time.monotonic() - start
    choice = resp.choices[0]
    content = choice.message.content or ""
    ok = choice.finish_reason != "length" and bool(content.strip())
    themes = quotes = 0
    if ok:
        try:
            digest = _ShardDigest.model_validate_json(content)
            themes = len(digest.confusions) + len(digest.objections) + len(digest.requested_changes)
            quotes = len(digest.quotes)
        except Exception:
            ok = False  # well-formed enough to return, not enough to use
    return {
        "ok": ok,
        "secs": elapsed,
        "completion_tokens": resp.usage.completion_tokens,
        "themes": themes,
        "quotes": quotes,
    }


def summarize(label: str, budget: int, results: list[dict]) -> None:
    oks = [r for r in results if r["ok"]]
    fails = [r for r in results if not r["ok"]]
    n = len(results)
    rate = f"{len(oks)}/{n}"
    med = statistics.median(r["secs"] for r in results)
    ok_tokens = (
        f"{min(r['completion_tokens'] for r in oks) / 1000:.1f}-"
        f"{max(r['completion_tokens'] for r in oks) / 1000:.1f}k"
        if oks
        else "-"
    )
    # The claim being tested: a failure consumes the entire budget.
    ate_budget = sum(1 for r in fails if r["completion_tokens"] >= budget * 0.98)
    fail_note = f"{ate_budget}/{len(fails)} ate the full budget" if fails else "-"
    themes = f"{statistics.mean(r['themes'] for r in oks):.1f}" if oks else "-"
    print(f"{label:>12} {rate:>8} {med:>9.0f} {ok_tokens:>14} {themes:>8}  {fail_note}")


async def probe_budget(reactions: list[Reaction], trials: int) -> None:
    shard = reactions[:50]
    user = build_prompt(shard)
    print(f"\n=== Probe 1: budget (fixed {len(shard)}-reaction prompt, ~{len(user) // 4} tokens) ===")
    print(f"{'max_tokens':>12} {'ok':>8} {'median s':>9} {'tokens if ok':>14} {'themes':>8}  failures")
    for budget in BUDGETS:
        results = await asyncio.gather(*(attempt(user, budget) for _ in range(trials)))
        summarize(f"{budget:,}", budget, list(results))


async def probe_size(reactions: list[Reaction], trials: int) -> None:
    print(f"\n=== Probe 2: input length (fixed budget {DIGEST_MAX_TOKENS:,}) ===")
    print(f"{'reactions':>12} {'ok':>8} {'median s':>9} {'tokens if ok':>14} {'themes':>8}  failures")
    for size in SIZES:
        # A different random batch per trial, seeded per (size, trial): with
        # 200 reactions there are only 4 non-overlapping 50-slices, so fixed
        # slices would let one unlucky batch carry a whole row.
        prompts = []
        for trial in range(trials):
            rng = random.Random(PROBE_SEED + size * 100 + trial)
            prompts.append(build_prompt(rng.sample(reactions, size)))
        results = await asyncio.gather(
            *(attempt(p, DIGEST_MAX_TOKENS) for p in prompts)
        )
        label = f"{size}" + (" (shipped)" if size == SHARD_SIZE else "")
        summarize(label, DIGEST_MAX_TOKENS, list(results))


async def probe_model(reactions: list[Reaction], trials: int) -> None:
    """Nano vs Super on the shipped configuration.

    Same batches for both models, so the comparison is paired rather than
    each getting its own luck of the draw.
    """
    print(
        f"\n=== Probe 3: model (shipped config: {SHARD_SIZE} reactions, "
        f"budget {DIGEST_MAX_TOKENS:,}) ==="
    )
    prompts = []
    for trial in range(trials):
        rng = random.Random(PROBE_SEED + 7000 + trial)
        prompts.append(build_prompt(rng.sample(reactions, SHARD_SIZE)))
    print(f"{'model':>12} {'ok':>8} {'median s':>9} {'tokens if ok':>14} {'themes':>8} {'quotes':>7}")
    for model, label in ((MODEL_NANO, "Nano 30B"), (MODEL_SUPER, "Super 120B")):
        results = list(
            await asyncio.gather(*(attempt(p, DIGEST_MAX_TOKENS, model) for p in prompts))
        )
        oks = [r for r in results if r["ok"]]
        med = statistics.median(r["secs"] for r in results)
        tok = (
            f"{min(r['completion_tokens'] for r in oks) / 1000:.1f}-"
            f"{max(r['completion_tokens'] for r in oks) / 1000:.1f}k"
            if oks
            else "-"
        )
        themes = f"{statistics.mean(r['themes'] for r in oks):.1f}" if oks else "-"
        quotes = f"{statistics.mean(r['quotes'] for r in oks):.1f}" if oks else "-"
        print(
            f"{label:>12} {f'{len(oks)}/{trials}':>8} {med:>9.0f} {tok:>14} "
            f"{themes:>8} {quotes:>7}"
        )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=10)
    parser.add_argument(
        "--probe", choices=["budget", "size", "model", "all"], default="all"
    )
    args = parser.parse_args()

    reactions = load_reactions()
    print(f"{len(reactions)} real reactions available, {args.trials} trials per row")
    print(
        "Trials within a row run concurrently, so latencies are comparable "
        "across rows but higher than a single call in isolation."
    )
    if args.probe in ("budget", "all"):
        await probe_budget(reactions, args.trials)
    if args.probe in ("size", "all"):
        await probe_size(reactions, args.trials)
    if args.probe in ("model", "all"):
        await probe_model(reactions, args.trials)


if __name__ == "__main__":
    asyncio.run(main())
