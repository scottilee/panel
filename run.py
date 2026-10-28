"""CLI: python run.py --artifact file.txt [--compare file2.txt] [--n 100]

Runs only the Nano fan-out (no synthesis) and prints aggregate sentiment,
for quick checks against the live API without the web app.
"""

from __future__ import annotations

import argparse
import asyncio
import time
import uuid
from pathlib import Path

from panel.personas import sample
from panel.react import fan_out
from panel.schemas import Reaction
from panel.stats import mean_sentiment, would_act_pct


def summarize(reactions: list[Reaction]) -> dict:
    return {
        "n": len(reactions),
        "mean_sentiment": mean_sentiment(reactions),
        "would_act_pct": would_act_pct(reactions),
        "errors": sum(1 for r in reactions if r.raw_error),
    }


async def run_once(artifact_text: str, n: int, seed: int, run_id: str) -> list[Reaction]:
    personas = sample(n, seed=seed)
    reactions: list[Reaction] = []
    start = time.monotonic()
    async for r in fan_out(artifact_text, personas, run_id):
        reactions.append(r)
    elapsed = time.monotonic() - start
    print(f"{len(reactions)} reactions in {elapsed:.1f}s")
    return reactions


def print_summary(label: str, reactions: list[Reaction]) -> None:
    s = summarize(reactions)
    print(
        f"{label}: mean_sentiment={s['mean_sentiment']:.3f} "
        f"would_act_pct={s['would_act_pct']:.1f}% errors={s['errors']}/{s['n']}"
    )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--compare")
    parser.add_argument("--n", type=int, default=100)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    artifact_text = Path(args.artifact).read_text()
    reactions = await run_once(artifact_text, args.n, args.seed, run_id=f"cli-{uuid.uuid4().hex[:8]}")
    print_summary(args.artifact, reactions)

    if args.compare:
        compare_text = Path(args.compare).read_text()
        reactions_b = await run_once(
            compare_text, args.n, args.seed, run_id=f"cli-{uuid.uuid4().hex[:8]}"
        )
        print_summary(args.compare, reactions_b)


if __name__ == "__main__":
    asyncio.run(main())
