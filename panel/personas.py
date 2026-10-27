"""Load data/personas.json; sample(n, seed); generate via Super.

Owned by Agent C, stage 4.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import get_args

from pydantic import BaseModel

from panel.prompts import PERSONA_GENERATION_SYSTEM, PERSONA_SCHEMA_JSON, PersonaDraftBatch, persona_generation_user
from panel.schemas import INCOME_BANDS, Persona, REGIONS
from panel.tf_client import ChatJsonError, chat_json, env

REGIONS_LIST = list(get_args(REGIONS))
INCOME_BANDS_LIST = list(get_args(INCOME_BANDS))

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "personas.json"

MODEL_SUPER = env("MODEL_SUPER", "nvidia/nemotron-3-super-120b-a12b")

AGE_BANDS = ["18-24", "25-34", "35-49", "50-64", "65+"]
AGE_BAND_SAMPLE_AGE = {"18-24": 21, "25-34": 29, "35-49": 41, "50-64": 58, "65+": 70}


def load_personas(path: Path = DEFAULT_PATH) -> list[Persona]:
    with open(path) as f:
        raw = json.load(f)
    return [Persona.model_validate(p) for p in raw]


def save_personas(personas: list[Persona], path: Path = DEFAULT_PATH) -> None:
    with open(path, "w") as f:
        json.dump([p.model_dump(mode="json") for p in personas], f, indent=2)
        f.write("\n")


def sample(n: int, seed: int, personas: list[Persona] | None = None) -> list[Persona]:
    """Deterministic: the same (n, seed) always returns the same subset in
    the same order, since random.Random(seed).sample is deterministic
    given a fixed input sequence (the JSON's on-disk order never changes
    once committed). Compare mode's apples-to-apples guarantee depends on
    this (section 3).
    """
    pool = personas if personas is not None else load_personas()
    if n > len(pool):
        raise ValueError(f"requested {n} personas but pool only has {len(pool)}")
    return random.Random(seed).sample(pool, n)


def build_slots(total: int = 100) -> list[dict]:
    """total (region, age_band, income_band) target slots for generation,
    per section 4.1's stratification grid. Assigned by independent
    modular cycles so region/age_band/income_band coverage is exact and
    deterministic regardless of model behavior — the model only invents
    the human details (name, occupation, voice, ...) within each slot.

    region: i % 8 -> first 4 regions get one extra occurrence when total
    isn't a multiple of 8 (100 -> 13,13,13,13,12,12,12,12).
    age_band: i % 5 -> exactly even at total=100.
    income_band: (i*3 + 2) % 5 -> a coprime stride so it doesn't move in
    lockstep with age_band, exactly even at total=100.
    """
    slots = []
    for i in range(total):
        slots.append(
            {
                "region": REGIONS_LIST[i % len(REGIONS_LIST)],
                "age_band": AGE_BANDS[i % len(AGE_BANDS)],
                "income_band": INCOME_BANDS_LIST[(i * 3 + 2) % len(INCOME_BANDS_LIST)],
            }
        )
    return slots


def slice_description(slots: list[dict]) -> str:
    lines = [
        f"  {i + 1}. region={s['region']}, age band={s['age_band']} "
        f"(pick a specific age within that band), income_band={s['income_band']}"
        for i, s in enumerate(slots)
    ]
    return "Exactly these 10 target profiles, one persona per line:\n" + "\n".join(lines)


async def generate_batch(slots: list[dict], temperature: float = 0.9) -> list[Persona]:
    """One Super call -> 10 PersonaDraft -> 10 Persona (ids assigned by
    the caller, not here, since a batch doesn't know its position in the
    full 100).
    """
    if len(slots) != 10:
        raise ValueError("section 4.1 calls for exactly 10 personas per Super call")

    result = await chat_json(
        model=MODEL_SUPER,
        system=PERSONA_GENERATION_SYSTEM,
        user=persona_generation_user(slice_description(slots)),
        schema=PersonaDraftBatch,
        temperature=temperature,
        # Reasoning-token usage for this prompt is stochastic (see
        # tf_client's module docstring); chat_json doubles this on
        # truncation up to 2 more times, so this only needs to be a
        # reasonable starting point, not a hard ceiling.
        max_tokens=8000,
    )
    drafts = result.data.root
    if len(drafts) != 10:
        raise ChatJsonError(
            f"expected 10 personas, got {len(drafts)}", result.latency_ms
        )

    personas = []
    for slot, draft in zip(slots, drafts):
        data = draft.model_dump()
        data["region"] = slot["region"]
        data["income_band"] = slot["income_band"]
        personas.append(Persona(id="pending", **data))
    return personas


# --- Duplicate-fixing and anti-sycophancy regeneration ----------------------
#
# Both are implementation details for scripts/gen_personas.py's C1/C2 checks,
# not among section 4's four canonical prompts, so they're small local
# templates here rather than additions to prompts.py.


async def regenerate_one(
    slot: dict, avoid_names: list[str], avoid_occupations: list[str], temperature: float = 0.9
) -> Persona:
    """One replacement persona for `slot`, told to avoid names/occupations
    already used elsewhere in its region (fixing a C1 duplicate).
    """
    avoid_clause = ""
    if avoid_names:
        avoid_clause += (
            "\nDo not use any of these first names (already used in this region): "
            + ", ".join(sorted(set(avoid_names)))
            + "."
        )
    if avoid_occupations:
        avoid_clause += (
            "\nDo not use any of these occupations (already used in this region): "
            + ", ".join(sorted(set(avoid_occupations)))
            + "."
        )

    user = f"""Generate exactly 1 persona matching this slice:
  region={slot['region']}, age band={slot['age_band']} (pick a specific age within that band), income_band={slot['income_band']}

Rules:
- Follows this schema exactly: {PERSONA_SCHEMA_JSON}
- values, buying_triggers, pet_peeves: 2 to 4 items each, concrete, not abstract.
- voice: 1 to 3 first-person sentences revealing how this person reads marketing, with a specific habit, limit, or trigger.
- Avoid stereotypes tied to gender, race, or nationality.{avoid_clause}
"""
    result = await chat_json(
        model=MODEL_SUPER,
        system=PERSONA_GENERATION_SYSTEM,
        user=user,
        schema=PersonaDraftBatch,
        temperature=temperature,
        max_tokens=1500,
    )
    drafts = result.data.root
    if len(drafts) != 1:
        raise ChatJsonError(f"expected 1 persona, got {len(drafts)}", result.latency_ms)
    data = drafts[0].model_dump()
    data["region"] = slot["region"]
    data["income_band"] = slot["income_band"]
    return Persona(id="pending", **data)


def find_region_duplicates(personas: list[Persona]) -> list[tuple[str, str, str, list[str]]]:
    """Returns (kind, region, value, persona_ids) for every name or
    occupation that repeats within a region (check C1).
    """
    from collections import Counter, defaultdict

    by_region: dict[str, list[Persona]] = defaultdict(list)
    for p in personas:
        by_region[p.region].append(p)

    issues = []
    for region, group in by_region.items():
        names = Counter(p.name.strip().lower() for p in group)
        occs = Counter(p.occupation.strip().lower() for p in group)
        for name, count in names.items():
            if count > 1:
                ids = [p.id for p in group if p.name.strip().lower() == name]
                issues.append(("name", region, name, ids))
        for occ, count in occs.items():
            if count > 1:
                ids = [p.id for p in group if p.occupation.strip().lower() == occ]
                issues.append(("occupation", region, occ, ids))
    return issues


class _VoicePeeves(BaseModel):
    voice: str
    pet_peeves: list[str]


async def make_more_skeptical(persona: Persona, new_skepticism: int) -> Persona:
    """Raises skepticism and rewrites voice + pet_peeves to actually match
    it, rather than just flipping the number — a forced-up skepticism with
    an unchanged trusting voice would be internally inconsistent (section
    4.2's anti-sycophancy note only mandates the number; the voice/peeves
    rewrite is this stage's call to keep the persona coherent).
    """
    system = (
        "You rewrite one aspect of an existing consumer persona to match a "
        "new skepticism level, keeping everything else about who they are "
        "intact. Output only JSON, no prose, no code fences."
    )
    user = (
        f"This persona: {persona.model_dump_json()}\n\n"
        f"Their skepticism is being raised from {persona.skepticism} to "
        f"{new_skepticism} because they reacted too agreeably in testing. "
        "Rewrite their voice (1 to 3 first-person sentences with a specific "
        "distrustful habit, limit, or trigger — not a vague statement of "
        "distrust) and pet_peeves (2 to 4 concrete items) so both actually "
        f"read as skepticism level {new_skepticism}/5. Keep their name, "
        "age, occupation, region, income_band, tech_comfort, values, and "
        "buying_triggers exactly as given.\n\n"
        f"Schema: {json.dumps(_VoicePeeves.model_json_schema())}"
    )
    result = await chat_json(
        model=MODEL_SUPER,
        system=system,
        user=user,
        schema=_VoicePeeves,
        temperature=0.9,
        max_tokens=1200,
    )
    return persona.model_copy(
        update={
            "skepticism": new_skepticism,
            "voice": result.data.voice,
            "pet_peeves": result.data.pet_peeves,
        }
    )
