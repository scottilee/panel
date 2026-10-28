"""CLI: python scripts/gen_personas.py --n 100 [--append]

Generates data/personas.json via Super, stratified per section 4.1, then
runs the anti-sycophancy check from section 4.2's note against the 3
example artifacts via Nano.

This is the only thing that writes data/personas.json, and it is never
run by the server -- the pool is generated offline and committed.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from panel.personas import (  # noqa: E402
    AGE_BANDS,
    AVOID_NAMES_CAP,
    AVOID_OCCUPATIONS_CAP,
    DEFAULT_PATH,
    age_band_for,
    avoid_sample,
    build_slots,
    find_duplicate_names,
    generate_batch,
    load_personas,
    make_more_skeptical,
    occupation_stats,
    rename_one,
    save_personas,
)
from panel.react import react_one  # noqa: E402
from panel.schemas import Persona, Reaction  # noqa: E402
from panel.tf_client import ChatJsonError  # noqa: E402

EXAMPLES_DIR = ROOT / "data" / "examples"
EXAMPLE_FILES = ["pricing_v1.txt", "ad_v1.txt", "policy_v1.txt"]

# The measured ceiling is 50 concurrent (zero 429s); this stays
# below it because C2 is an offline batch job with no user waiting on it,
# and leaving headroom means a generation run never competes with a live
# demo for rate limit.
REACTION_CONCURRENCY = 20

MEAN_SENTIMENT_BOUNDS = (-0.8, 0.8)
# Section 4.2's note states this as "at least 25 of 100". Held as a
# percentage so it means the same thing when C2 runs against a sample of
# a 1000-persona pool rather than the whole of a 100-persona one.
MIN_WOULD_ACT_FALSE_PCT = 25
# C2 costs len(personas) x 3 Nano calls. At n=1000 that's 3000 calls for
# a statistic that a few hundred personas already pins down to within a
# point or two, so the check runs on a deterministic sample above this
# size. Raise with --c2-sample if you want the exhaustive version.
DEFAULT_C2_SAMPLE = 150
C2_SAMPLE_SEED = 11
MAX_DEDUP_PASSES = 5
MAX_SYCOPHANCY_ROUNDS = 3
SKEPTICAL_BATCH_SIZE = 10
# Fixed so the saved file's order is reproducible across regenerations.
PERSONA_ORDER_SEED = 20261028

# Super calls run in waves rather than one at a time: 90 sequential calls
# for a 900-persona append is ~an hour of mostly waiting. Batches inside a
# wave are blind to each other (they share one avoid-list snapshot), which
# is the price of the parallelism -- the dedup pass cleans up the few
# collisions that result. Also the repair concurrency.
GEN_WAVE_SIZE = 5
DEDUP_CONCURRENCY = 5
# How many times a batch's slots go back into a later wave after its own
# in-wave retries are exhausted.
GEN_REQUEUE_ROUNDS = 2


async def _generate_batch_with_retry(
    batch_num: int,
    total_batches: int,
    batch_slots: list[dict],
    avoid_names: list[str],
    avoid_occupations: list[str],
) -> list[Persona] | None:
    # Measured (2026-10-28): Super occasionally returns a short list
    # (e.g. 2 personas instead of 10) with finish_reason == "stop" -- a
    # real response, just not following the count instruction, so
    # chat_json's own truncation/validation retries don't apply.
    #
    # Returns None rather than raising once its own attempts are spent:
    # these run five-to-a-wave under asyncio.gather, and raising took the
    # whole wave down with it -- discarding four healthy batches, and the
    # wave's checkpoint write along with them -- over one batch Super
    # miscounted. The caller requeues the slots instead.
    for attempt in range(3):
        try:
            return await generate_batch(
                batch_slots,
                avoid_names=avoid_names,
                avoid_occupations=avoid_occupations,
            )
        except ChatJsonError as e:
            print(
                f"    batch {batch_num}/{total_batches} attempt {attempt + 1} failed: {e}",
                flush=True,
            )
    return None


async def generate_all(
    n: int,
    start_id: int = 1,
    existing: list[Persona] | None = None,
    checkpoint_path: Path | None = None,
    checkpoint_prefix: list[Persona] | None = None,
) -> list[Persona]:
    """Generates n new personas. `existing` is the pool they must not
    collide with (names) or echo (occupations) -- without it, an append
    of 900 would happily re-invent the first 100's names.

    checkpoint_path, if given, is rewritten after every wave: a 900-wide
    run is tens of minutes and ~90 paid Super calls, so a failure in the
    last wave should not throw away the first 17. checkpoint_prefix is
    written ahead of them, so a --resume run's checkpoint stays a complete
    record of the new block rather than only the part this call made.
    """
    if n % 10 != 0:
        raise ValueError("n must be a multiple of 10 (10 personas per Super call)")
    slots_all = build_slots(n, offset=start_id - 1)
    batches = [slots_all[i : i + 10] for i in range(0, n, 10)]

    used_names: set[str] = {p.name for p in existing or []}
    used_occs: set[str] = {p.occupation for p in existing or []}

    personas: list[Persona] = []
    next_id = start_id
    queue = list(batches)
    total = len(batches)

    for round_num in range(GEN_REQUEUE_ROUNDS + 1):
        if not queue:
            break
        if round_num:
            print(
                f"  requeue round {round_num}: retrying {len(queue)} batch(es) that "
                "exhausted their in-wave attempts",
                flush=True,
            )
        failed: list[list[dict]] = []
        done_so_far = total - len(queue)
        for wave_start in range(0, len(queue), GEN_WAVE_SIZE):
            wave = queue[wave_start : wave_start + GEN_WAVE_SIZE]
            first = done_so_far + wave_start + 1
            print(
                f"  generating batches {first}-{first + len(wave) - 1}/{total} "
                f"(avoiding {len(used_names)} names, {len(used_occs)} occupations)...",
                flush=True,
            )
            # One snapshot per wave, with a wave-dependent seed so
            # successive waves show the model different slices of a long
            # used-list.
            seed = wave_start + 1000 * round_num
            avoid_names = avoid_sample(used_names, AVOID_NAMES_CAP, seed=seed)
            avoid_occs = avoid_sample(used_occs, AVOID_OCCUPATIONS_CAP, seed=seed + 1)
            results = await asyncio.gather(
                *(
                    _generate_batch_with_retry(
                        first + i, total, batch_slots, avoid_names, avoid_occs
                    )
                    for i, batch_slots in enumerate(wave)
                )
            )
            for batch_slots, batch in zip(wave, results):
                if batch is None:
                    failed.append(batch_slots)
                    continue
                for p in batch:
                    p = p.model_copy(update={"id": f"p{next_id:03d}"})
                    personas.append(p)
                    used_names.add(p.name)
                    used_occs.add(p.occupation)
                    next_id += 1
            if checkpoint_path is not None:
                save_personas((checkpoint_prefix or []) + personas, checkpoint_path)
        queue = failed

    if queue:
        # Short of the requested n, but with everything that did generate
        # intact. The caller's C1 coverage check is what decides whether
        # that is acceptable, so report it loudly and let it run.
        missing = sum(len(b) for b in queue)
        print(f"  WARNING: {missing} persona slot(s) never generated after "
              f"{GEN_REQUEUE_ROUNDS} requeue round(s)")
    return personas


async def _rename_one(
    old: Persona, avoid_names: list[str], sem: asyncio.Semaphore
) -> tuple[str, Persona | None]:
    async with sem:
        try:
            return old.id, await rename_one(old, avoid_names)
        except ChatJsonError as e:
            print(f"    {old.id}: rename failed ({e})", flush=True)
            return old.id, None


async def fix_duplicate_names(personas: list[Persona]) -> list[Persona]:
    by_id = {p.id: p for p in personas}
    for pass_num in range(1, MAX_DEDUP_PASSES + 1):
        issues = find_duplicate_names(list(by_id.values()))
        if not issues:
            if pass_num > 1:
                print(f"  duplicate names resolved after {pass_num - 1} pass(es)")
            break
        dup_ids = [dup_id for _, ids in issues for dup_id in ids[1:]]  # keep the first of each
        print(f"  dedup pass {pass_num}: {len(issues)} repeated name(s), renaming {len(dup_ids)}")
        sem = asyncio.Semaphore(DEDUP_CONCURRENCY)
        all_names = {p.name for p in by_id.values()}
        tasks = [
            _rename_one(
                by_id[dup_id],
                avoid_sample(all_names, AVOID_NAMES_CAP, seed=pass_num),
                sem,
            )
            for dup_id in dup_ids
        ]
        for dup_id, renamed in await asyncio.gather(*tasks):
            if renamed is not None:
                print(f"    {dup_id}: {by_id[dup_id].name} -> {renamed.name}", flush=True)
                by_id[dup_id] = renamed
    else:
        remaining = find_duplicate_names(list(by_id.values()))
        if remaining:
            print(
                f"  WARNING: {len(remaining)} repeated name(s) still unresolved after "
                f"{MAX_DEDUP_PASSES} passes"
            )
    return list(by_id.values())


def check_c1(personas: list[Persona]) -> dict:
    regions = Counter(p.region for p in personas)
    age_bands = Counter(age_band_for(p.age) for p in personas)
    income_bands = Counter(p.income_band for p in personas)
    duplicates = find_duplicate_names(personas)
    all_age_bands_present = set(AGE_BANDS) <= set(age_bands)
    return {
        "region_counts": dict(regions),
        "age_band_counts": dict(age_bands),
        "income_band_counts": dict(income_bands),
        "all_regions_present": len(regions) == 8,
        "all_age_bands_present": all_age_bands_present,
        "all_income_bands_present": len(income_bands) == 5,
        "duplicates_remaining": duplicates,
        "no_duplicate_names": len(duplicates) == 0,
        "occupations": occupation_stats(personas),
    }


C2_RUN_ID = "c2-check"


async def react_all(personas: list[Persona], artifact_text: str) -> dict[str, Reaction]:
    """Uses panel.react.react_one rather than a local copy. This script
    used to carry its own duplicate of it, which drifted: the real one
    gained a broadened except (so a dropped socket costs one reaction
    instead of the whole batch) and this one didn't.
    """
    sem = asyncio.Semaphore(REACTION_CONCURRENCY)
    reactions = await asyncio.gather(
        *(react_one(p, artifact_text, C2_RUN_ID, sem) for p in personas)
    )
    return {r.persona_id: r for r in reactions}


def summarize(reactions: dict[str, Reaction]) -> dict:
    values = list(reactions.values())
    n = len(values)
    mean_sentiment = sum(r.sentiment for r in values) / n if n else 0.0
    would_act_false = sum(1 for r in values if not r.would_act)
    return {"n": n, "mean_sentiment": mean_sentiment, "would_act_false": would_act_false}


async def run_anti_sycophancy_check(
    personas: list[Persona], full_pool: list[Persona] | None = None
) -> tuple[bool, dict, list[Persona]]:
    """Reacts `personas` to all 3 examples and raises skepticism on the
    most agreeable until the C2 bounds hold.

    Returns the pool with any rewrites applied -- `full_pool` is the pool
    to return (with rewrites merged in) when `personas` is only a sample
    of it; earlier this returned nothing but (passed, summaries), so
    make_more_skeptical's rewrites were computed, printed, and then
    silently discarded by the caller.
    """
    reactions_by_example: dict[str, dict[str, Reaction]] = {}
    summaries: dict[str, dict] = {}
    rewritten: dict[str, Persona] = {}

    for fname in EXAMPLE_FILES:
        text = (EXAMPLES_DIR / fname).read_text()
        print(f"  reacting to {fname} ({len(personas)} personas, concurrency={REACTION_CONCURRENCY})...", flush=True)
        start = time.monotonic()
        reactions = await react_all(personas, text)
        elapsed = time.monotonic() - start
        reactions_by_example[fname] = reactions
        s = summarize(reactions)
        s["elapsed_s"] = elapsed
        summaries[fname] = s
        print(
            f"    mean_sentiment={s['mean_sentiment']:.3f} "
            f"would_act_false={s['would_act_false']}/{s['n']} ({elapsed:.1f}s)"
        )

    def _merged(pool: list[Persona]) -> list[Persona]:
        return [rewritten.get(p.id, p) for p in pool]

    pool_to_return = full_pool if full_pool is not None else personas

    for round_num in range(1, MAX_SYCOPHANCY_ROUNDS + 1):
        failing = {
            fname: s
            for fname, s in summaries.items()
            if not (MEAN_SENTIMENT_BOUNDS[0] <= s["mean_sentiment"] <= MEAN_SENTIMENT_BOUNDS[1])
            # s["n"] guarded: an empty reaction set is a failed check, not
            # a ZeroDivisionError three-quarters of the way through a
            # paid generation run.
            or (100 * s["would_act_false"] / s["n"] if s["n"] else 0) < MIN_WOULD_ACT_FALSE_PCT
        }
        if not failing:
            return True, summaries, _merged(pool_to_return)

        print(f"  anti-sycophancy round {round_num}: failing on {list(failing.keys())}")

        # Most-agreeable-first: average sentiment + would_act count across
        # every example reacted to so far, restricted to personas that
        # still have room to get more skeptical.
        by_id = {p.id: p for p in personas}
        agreeableness: dict[str, float] = {}
        for p in personas:
            if p.skepticism >= 95:  # 1-100 scale; already near the ceiling
                continue
            scores = [reactions_by_example[f][p.id].sentiment for f in EXAMPLE_FILES]
            acts = [reactions_by_example[f][p.id].would_act for f in EXAMPLE_FILES]
            agreeableness[p.id] = sum(scores) / len(scores) + sum(acts)

        if not agreeableness:
            print("  WARNING: no personas left below max skepticism to regenerate; giving up")
            return False, summaries, _merged(pool_to_return)

        most_agreeable_ids = sorted(agreeableness, key=lambda pid: agreeableness[pid], reverse=True)[
            :SKEPTICAL_BATCH_SIZE
        ]

        for pid in most_agreeable_ids:
            old = by_id[pid]
            # 1-100 scale. This used to read `5 if old.skepticism <= 2
            # else 4`, left over from the spec's 1-5 scale -- on 1-100 it
            # *lowered* skepticism to 4/100, the exact opposite of the
            # intent. A real bump (+25, floor 75) instead.
            new_level = min(100, max(old.skepticism + 25, 75))
            updated = await make_more_skeptical(old, new_level)
            updated = updated.model_copy(update={"id": pid})
            by_id[pid] = updated
            rewritten[pid] = updated
            print(f"    {pid}: skepticism {old.skepticism} -> {new_level}")

        personas = list(by_id.values())

        # Only the regenerated personas need fresh reactions; everyone
        # else's reaction is still valid, so re-score cheaply instead of
        # re-running all n x 3 calls.
        for fname in EXAMPLE_FILES:
            text = (EXAMPLES_DIR / fname).read_text()
            sem = asyncio.Semaphore(REACTION_CONCURRENCY)
            refreshed = await asyncio.gather(
                *(react_one(by_id[pid], text, C2_RUN_ID, sem) for pid in most_agreeable_ids)
            )
            for r in refreshed:
                reactions_by_example[fname][r.persona_id] = r
            s = summarize(reactions_by_example[fname])
            summaries[fname] = s
            print(f"    re-check {fname}: mean_sentiment={s['mean_sentiment']:.3f} would_act_false={s['would_act_false']}/{s['n']}")

    return False, summaries, _merged(pool_to_return)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=100)
    parser.add_argument("--append", action="store_true")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="with --append: reuse data/personas.partial.json and generate only the remainder",
    )
    parser.add_argument(
        "--c2-sample",
        type=int,
        default=DEFAULT_C2_SAMPLE,
        help="cap the anti-sycophancy check at this many personas (0 = check all)",
    )
    args = parser.parse_args()

    if args.append:
        existing = load_personas()
        start_id = len(existing) + 1
        print(f"Appending {args.n} personas to {len(existing)} existing...")
        checkpoint = DEFAULT_PATH.with_name("personas.partial.json")
        done: list[Persona] = []
        if args.resume and checkpoint.is_file():
            done = load_personas(checkpoint)
            print(f"  resuming: {len(done)} already generated, in {checkpoint.name}")
        remaining = args.n - len(done)
        if remaining < 0:
            sys.exit(f"checkpoint already holds {len(done)} personas, more than --n {args.n}")
        new_personas = done + (
            await generate_all(
                remaining,
                start_id=start_id + len(done),
                existing=existing + done,
                checkpoint_path=checkpoint,
                checkpoint_prefix=done,
            )
            if remaining
            else []
        )
        checkpoint.unlink(missing_ok=True)
        # Shuffle only the new block and append it after the existing
        # pool, rather than reshuffling everything. api/main.py selects a
        # run's personas as the first N in file order (sample_stable), so
        # a global reshuffle would change which personas N=100 uses --
        # which would invalidate every cached example run in data/cached
        # (those are matched to persona_ids) and the anti-sycophancy
        # numbers measured against them. Keeping the prefix fixed means
        # the demo fallback and all existing tests stay valid, and N>100
        # simply reaches further into the new block.
        random.Random(PERSONA_ORDER_SEED).shuffle(new_personas)
        personas = existing + new_personas
        print("Checking for duplicate names (C1)...")
        personas = await fix_duplicate_names(personas)
    else:
        print(f"Generating {args.n} personas...")
        personas = await generate_all(args.n)
        print("Checking for duplicate names (C1)...")
        personas = await fix_duplicate_names(personas)
        # The stratification grid assigns age and income bands in
        # repeating 5-cycles, so generation order puts one age+income
        # cohort in every 5th slot. The grid is 10 columns wide -- 5
        # divides 10, so unshuffled order made every grid column a single
        # cohort, showing up as vertical colour stripes instead of a
        # mixed crowd. Shuffling here (deterministically, so the file is
        # reproducible) keeps file order itself mixed, which means
        # neither the server nor the browser needs its own shuffle to
        # agree on which personas a given N uses.
        random.Random(PERSONA_ORDER_SEED).shuffle(personas)

    c1 = check_c1(personas)
    print("\n=== C1: stratification + uniqueness ===")
    print("region_counts:", c1["region_counts"])
    print("age_band_counts:", c1["age_band_counts"])
    print("income_band_counts:", c1["income_band_counts"])
    print("all_regions_present:", c1["all_regions_present"])
    print("all_age_bands_present:", c1["all_age_bands_present"])
    print("all_income_bands_present:", c1["all_income_bands_present"])
    print("no_duplicate_names:", c1["no_duplicate_names"])
    occ = c1["occupations"]
    print(f"occupations: {occ['unique']}/{occ['total']} unique ({occ['ratio']:.0%})")
    print("most repeated occupations:", occ["top_repeats"])

    save_personas(personas, DEFAULT_PATH)
    print(f"\nSaved {len(personas)} personas to {DEFAULT_PATH}")

    print("\n=== C2: anti-sycophancy check ===")
    if args.c2_sample and len(personas) > args.c2_sample:
        sample = random.Random(C2_SAMPLE_SEED).sample(personas, args.c2_sample)
        print(f"  checking a {args.c2_sample}-persona sample of {len(personas)}")
    else:
        sample = personas
    passed, summaries, personas = await run_anti_sycophancy_check(sample, full_pool=personas)
    for fname, s in summaries.items():
        print(f"{fname}: mean_sentiment={s['mean_sentiment']:.3f} would_act_false={s['would_act_false']}/{s['n']}")
    print("C2 PASSED" if passed else "C2 FAILED")

    save_personas(personas, DEFAULT_PATH)
    print(f"Final save: {len(personas)} personas to {DEFAULT_PATH}")

    if not c1["all_regions_present"] or not c1["all_age_bands_present"] or not c1["all_income_bands_present"]:
        sys.exit("C1 FAILED: coverage incomplete")
    if not c1["no_duplicate_names"]:
        sys.exit("C1 FAILED: duplicate names remain")
    if not passed:
        sys.exit("C2 FAILED")


if __name__ == "__main__":
    asyncio.run(main())
