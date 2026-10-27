"""CLI: python scripts/gen_personas.py --n 100 [--append]

Generates data/personas.json via Super, stratified per section 4.1, then
runs the anti-sycophancy check from section 4.2's note against the 3
example artifacts via Nano. Owned by Agent C, stage 4.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from panel.personas import (  # noqa: E402
    AGE_BANDS,
    DEFAULT_PATH,
    build_slots,
    find_region_duplicates,
    generate_batch,
    load_personas,
    make_more_skeptical,
    regenerate_one,
    save_personas,
)
from panel.prompts import REACTION_SYSTEM, reaction_user  # noqa: E402
from panel.schemas import Persona, Reaction  # noqa: E402
from panel.tf_client import ChatJsonError, chat_json, env  # noqa: E402

MODEL_NANO = env("MODEL_NANO", "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B")
EXAMPLES_DIR = ROOT / "data" / "examples"
EXAMPLE_FILES = ["pricing_v1.txt", "ad_v1.txt", "policy_v1.txt"]

# Deliberately modest: the real concurrency-ceiling measurement (10/25/50,
# watching for 429s) is section 5.2's job in stage 5, not this stage.
REACTION_CONCURRENCY = 8

MEAN_SENTIMENT_BOUNDS = (-0.8, 0.8)
MIN_WOULD_ACT_FALSE = 25
MAX_DEDUP_PASSES = 5
MAX_SYCOPHANCY_ROUNDS = 3
SKEPTICAL_BATCH_SIZE = 10


def age_band_for(age: int) -> str:
    if age <= 24:
        return "18-24"
    if age <= 34:
        return "25-34"
    if age <= 49:
        return "35-49"
    if age <= 64:
        return "50-64"
    return "65+"


async def generate_all(n: int, start_id: int = 1) -> list[Persona]:
    if n % 10 != 0:
        raise ValueError("n must be a multiple of 10 (10 personas per Super call)")
    slots_all = build_slots(n)
    batches = [slots_all[i : i + 10] for i in range(0, n, 10)]
    personas: list[Persona] = []
    next_id = start_id
    for batch_num, batch_slots in enumerate(batches, start=1):
        print(f"  generating batch {batch_num}/{len(batches)}...", flush=True)
        batch = await generate_batch(batch_slots)
        for p, slot in zip(batch, batch_slots):
            p = p.model_copy(update={"id": f"p{next_id:03d}"})
            personas.append(p)
            next_id += 1
    return personas


async def fix_region_duplicates(personas: list[Persona]) -> list[Persona]:
    by_id = {p.id: p for p in personas}
    for pass_num in range(1, MAX_DEDUP_PASSES + 1):
        issues = find_region_duplicates(list(by_id.values()))
        if not issues:
            if pass_num > 1:
                print(f"  region duplicates resolved after {pass_num - 1} pass(es)")
            break
        print(f"  dedup pass {pass_num}: {len(issues)} collision(s) found")
        # Keep the first occurrence of each duplicated value, regenerate the rest.
        already_fixed: set[str] = set()
        for kind, region, value, ids in issues:
            for dup_id in ids[1:]:
                if dup_id in already_fixed:
                    continue
                already_fixed.add(dup_id)
                old = by_id[dup_id]
                region_group = [p for p in by_id.values() if p.region == region and p.id != dup_id]
                avoid_names = [p.name.split(" ")[0] for p in region_group]
                avoid_occs = [p.occupation for p in region_group]
                slot = {
                    "region": old.region,
                    "age_band": age_band_for(old.age),
                    "income_band": old.income_band,
                }
                replacement = await regenerate_one(slot, avoid_names, avoid_occs)
                replacement = replacement.model_copy(update={"id": dup_id})
                by_id[dup_id] = replacement
                print(f"    replaced {dup_id} ({kind} collision on '{value}' in {region})")
    else:
        remaining = find_region_duplicates(list(by_id.values()))
        if remaining:
            print(f"  WARNING: {len(remaining)} duplicate(s) still unresolved after {MAX_DEDUP_PASSES} passes")
    return list(by_id.values())


def check_c1(personas: list[Persona]) -> dict:
    regions = Counter(p.region for p in personas)
    age_bands = Counter(age_band_for(p.age) for p in personas)
    income_bands = Counter(p.income_band for p in personas)
    duplicates = find_region_duplicates(personas)
    all_age_bands_present = set(AGE_BANDS) <= set(age_bands)
    return {
        "region_counts": dict(regions),
        "age_band_counts": dict(age_bands),
        "income_band_counts": dict(income_bands),
        "all_regions_present": len(regions) == 8,
        "all_age_bands_present": all_age_bands_present,
        "all_income_bands_present": len(income_bands) == 5,
        "duplicates_remaining": duplicates,
        "no_duplicate_names_or_occupations": len(duplicates) == 0,
    }


async def react_one(persona: Persona, artifact_text: str, sem: asyncio.Semaphore) -> Reaction:
    async with sem:
        try:
            result = await chat_json(
                model=MODEL_NANO,
                system=REACTION_SYSTEM,
                user=reaction_user(persona.model_dump_json(), artifact_text),
                schema=Reaction,
                temperature=0.7,
                seed=hash(persona.id) % (2**31),
                max_tokens=400,
            )
            return result.data.model_copy(update={"persona_id": persona.id})
        except ChatJsonError as e:
            return Reaction(
                persona_id=persona.id,
                sentiment=0,
                would_act=False,
                gut_reaction="(no response)",
                top_confusion="(no response)",
                top_objection="(no response)",
                what_would_change_my_mind="(no response)",
                raw_error=str(e),
            )


async def react_all(personas: list[Persona], artifact_text: str) -> dict[str, Reaction]:
    sem = asyncio.Semaphore(REACTION_CONCURRENCY)
    reactions = await asyncio.gather(*(react_one(p, artifact_text, sem) for p in personas))
    return {r.persona_id: r for r in reactions}


def summarize(reactions: dict[str, Reaction]) -> dict:
    values = list(reactions.values())
    n = len(values)
    mean_sentiment = sum(r.sentiment for r in values) / n if n else 0.0
    would_act_false = sum(1 for r in values if not r.would_act)
    return {"n": n, "mean_sentiment": mean_sentiment, "would_act_false": would_act_false}


async def run_anti_sycophancy_check(personas: list[Persona]) -> tuple[bool, dict]:
    reactions_by_example: dict[str, dict[str, Reaction]] = {}
    summaries: dict[str, dict] = {}

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

    for round_num in range(1, MAX_SYCOPHANCY_ROUNDS + 1):
        failing = {
            fname: s
            for fname, s in summaries.items()
            if not (MEAN_SENTIMENT_BOUNDS[0] <= s["mean_sentiment"] <= MEAN_SENTIMENT_BOUNDS[1])
            or s["would_act_false"] < MIN_WOULD_ACT_FALSE
        }
        if not failing:
            return True, summaries

        print(f"  anti-sycophancy round {round_num}: failing on {list(failing.keys())}")

        # Most-agreeable-first: average sentiment + would_act count across
        # every example reacted to so far, restricted to personas that
        # still have room to get more skeptical.
        by_id = {p.id: p for p in personas}
        agreeableness: dict[str, float] = {}
        for p in personas:
            if p.skepticism >= 5:
                continue
            scores = [reactions_by_example[f][p.id].sentiment for f in EXAMPLE_FILES]
            acts = [reactions_by_example[f][p.id].would_act for f in EXAMPLE_FILES]
            agreeableness[p.id] = sum(scores) / len(scores) + sum(acts)

        if not agreeableness:
            print("  WARNING: no personas left below max skepticism to regenerate; giving up")
            return False, summaries

        most_agreeable_ids = sorted(agreeableness, key=lambda pid: agreeableness[pid], reverse=True)[
            :SKEPTICAL_BATCH_SIZE
        ]

        for pid in most_agreeable_ids:
            old = by_id[pid]
            new_level = 5 if old.skepticism <= 2 else 4
            updated = await make_more_skeptical(old, new_level)
            updated = updated.model_copy(update={"id": pid})
            by_id[pid] = updated
            print(f"    {pid}: skepticism {old.skepticism} -> {new_level}")

        personas = list(by_id.values())

        # Only the regenerated personas need fresh reactions; everyone
        # else's reaction is still valid, so re-score cheaply instead of
        # re-running all n x 3 calls.
        for fname in EXAMPLE_FILES:
            text = (EXAMPLES_DIR / fname).read_text()
            sem = asyncio.Semaphore(REACTION_CONCURRENCY)
            refreshed = await asyncio.gather(
                *(react_one(by_id[pid], text, sem) for pid in most_agreeable_ids)
            )
            for r in refreshed:
                reactions_by_example[fname][r.persona_id] = r
            s = summarize(reactions_by_example[fname])
            summaries[fname] = s
            print(f"    re-check {fname}: mean_sentiment={s['mean_sentiment']:.3f} would_act_false={s['would_act_false']}/{s['n']}")

    return False, summaries


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=100)
    parser.add_argument("--append", action="store_true")
    args = parser.parse_args()

    if args.append:
        existing = load_personas()
        start_id = len(existing) + 1
        print(f"Appending {args.n} personas to {len(existing)} existing...")
        new_personas = await generate_all(args.n, start_id=start_id)
        personas = existing + new_personas
    else:
        print(f"Generating {args.n} personas...")
        personas = await generate_all(args.n)

    print("Checking for region-scoped duplicate names/occupations (C1)...")
    personas = await fix_region_duplicates(personas)

    c1 = check_c1(personas)
    print("\n=== C1: stratification + uniqueness ===")
    print("region_counts:", c1["region_counts"])
    print("age_band_counts:", c1["age_band_counts"])
    print("income_band_counts:", c1["income_band_counts"])
    print("all_regions_present:", c1["all_regions_present"])
    print("all_age_bands_present:", c1["all_age_bands_present"])
    print("all_income_bands_present:", c1["all_income_bands_present"])
    print("no_duplicate_names_or_occupations:", c1["no_duplicate_names_or_occupations"])

    save_personas(personas, DEFAULT_PATH)
    print(f"\nSaved {len(personas)} personas to {DEFAULT_PATH}")

    print("\n=== C2: anti-sycophancy check ===")
    passed, summaries = await run_anti_sycophancy_check(personas)
    for fname, s in summaries.items():
        print(f"{fname}: mean_sentiment={s['mean_sentiment']:.3f} would_act_false={s['would_act_false']}/{s['n']}")
    print("C2 PASSED" if passed else "C2 FAILED")

    save_personas(personas, DEFAULT_PATH)
    print(f"Final save: {len(personas)} personas to {DEFAULT_PATH}")

    if not c1["all_regions_present"] or not c1["all_age_bands_present"] or not c1["all_income_bands_present"]:
        sys.exit("C1 FAILED: coverage incomplete")
    if not c1["no_duplicate_names_or_occupations"]:
        sys.exit("C1 FAILED: duplicates remain")
    if not passed:
        sys.exit("C2 FAILED")


if __name__ == "__main__":
    asyncio.run(main())
