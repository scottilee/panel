"""The persona pool: load and sample data/personas.json, and generate it
via Super.

Generation (generate_batch, rename_one, the avoid-list helpers) is only
reached by scripts/gen_personas.py, offline. The server only ever calls
load_personas and sample_stable.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import get_args

from pydantic import BaseModel

from panel.config import MODEL_SUPER
from panel.prompts import PERSONA_GENERATION_SYSTEM, PersonaDraftBatch, persona_generation_user
from panel.schemas import INCOME_BANDS, Persona, REGIONS
from panel.tf_client import ChatJsonError, chat_json

REGIONS_LIST = list(get_args(REGIONS))
INCOME_BANDS_LIST = list(get_args(INCOME_BANDS))

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "personas.json"

AGE_BANDS = ["18-24", "25-34", "35-49", "50-64", "65+"]


def age_band_for(age: int) -> str:
    """age_band isn't a stored Persona field (only age is) -- it's derived
    wherever a segment needs to bucket by it, so there's exactly one
    definition (used by scripts/gen_personas.py's C1 check and
    synthesize.py's segment-filter recount)."""
    if age <= 24:
        return "18-24"
    if age <= 34:
        return "25-34"
    if age <= 49:
        return "35-49"
    if age <= 64:
        return "50-64"
    return "65+"


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


def sample_stable(n: int, personas: list[Persona] | None = None) -> list[Persona]:
    """The first n personas in on-disk order. Used by the real HTTP API
    (api/main.py) instead of sample(): the frontend needs to know, without
    a network round trip, exactly which personas a given N will use (so
    the pre-run grid preview shows the same identities incoming reactions
    will actually arrive for) -- that's trivial to replicate in JS as
    `pool.slice(0, n)` once it has the pool from GET /personas, whereas
    replicating Python's random.Random(seed).sample bit-for-bit in JS is
    a real porting job for no real benefit here (CLI/offline uses of
    sample() that don't have this constraint keep using the real one).
    """
    pool = personas if personas is not None else load_personas()
    if n > len(pool):
        raise ValueError(f"requested {n} personas but pool only has {len(pool)}")
    return pool[:n]


def build_slots(total: int = 100, offset: int = 0) -> list[dict]:
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

    offset continues the cycles from where a previous run stopped, so
    appending 900 to an existing 100 lands on an exactly even 1000-wide
    grid (125 per region) instead of restarting each cycle at 0 and
    re-overweighting the same first four regions.
    """
    slots = []
    for i in range(offset, offset + total):
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


# How much of the already-used set to actually put in a generation prompt.
# The full set at n=1000 is ~3k tokens of names and ~4k of occupations;
# cost is negligible but a list that long measurably drowns out the rules
# that follow it. Sampling instead (a different sample each wave, see
# avoid_sample) rotates coverage across batches and leaves the residue to
# the dedup pass.
AVOID_NAMES_CAP = 400
AVOID_OCCUPATIONS_CAP = 200


def avoid_sample(used: set[str], cap: int, seed: int) -> list[str]:
    """A deterministic, bounded sample of `used`. sorted() first so the
    sample depends only on the set contents and the seed, not on the
    insertion order a set happens to iterate in."""
    pool = sorted(used)
    if len(pool) <= cap:
        return pool
    return random.Random(seed).sample(pool, cap)


async def generate_batch(
    slots: list[dict],
    temperature: float = 0.9,
    avoid_names: list[str] | None = None,
    avoid_occupations: list[str] | None = None,
) -> list[Persona]:
    """One Super call -> 10 PersonaDraft -> 10 Persona (ids assigned by
    the caller, not here, since a batch doesn't know its position in the
    full 100).

    avoid_names/avoid_occupations carry what earlier batches already used,
    so collisions are prevented rather than repaired afterwards -- at
    n=1000 the repair path alone would be hundreds of individual Super
    calls and still wouldn't converge in the 5 passes it allows.
    """
    if len(slots) != 10:
        raise ValueError("section 4.1 calls for exactly 10 personas per Super call")

    result = await chat_json(
        model=MODEL_SUPER,
        system=PERSONA_GENERATION_SYSTEM,
        user=persona_generation_user(slice_description(slots), avoid_names, avoid_occupations),
        schema=PersonaDraftBatch,
        temperature=temperature,
        # Reasoning-token usage for this prompt is stochastic (see
        # tf_client's module docstring); chat_json doubles this on
        # truncation up to 2 more times, so this only needs to be a
        # reasonable starting point, not a hard ceiling.
        #
        # 16000, not the 8000 this started at. 10 personas serialize to
        # ~4.5k tokens of content and tf_client measured reasoning for
        # this exact prompt at ~3.4k, so 8000 sat right on the line --
        # and adding the avoid-lists pushed most calls over it. Every
        # such call truncated, got silently retried at 16000, and so cost
        # roughly 2.3x the tokens and wall-clock of asking for 16000 in
        # the first place: a 900-persona run was pacing at ~110 minutes
        # before this change.
        max_tokens=16000,
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


class _NewName(BaseModel):
    name: str


async def rename_one(
    persona: Persona, avoid_names: list[str], temperature: float = 1.0
) -> Persona:
    """A new full name for a persona whose name collides with another's,
    with every other field untouched.

    Repairing a name collision by regenerating the whole persona (what
    this used to do) is both wasteful and destructive: data/cached holds
    replayable example runs keyed by persona_id, so swapping the person
    behind an id leaves a cached reaction attributed to someone whose
    traits no longer explain it. A name is the only thing actually wrong,
    so a name is the only thing that changes.
    """
    user = (
        f"This persona's name collides with another persona's and needs a new one:\n"
        f"  name: {persona.name}\n"
        f"  age {persona.age}, {persona.gender}, {persona.location} "
        f"({persona.region}), works as a {persona.occupation}\n\n"
        "Give one new full name (first and last) that plausibly belongs to this "
        "specific person -- same kind of background, era, and place, not a "
        "generic substitution. Do not reuse their current name or any of these, "
        "and avoid reusing these first names:\n"
        + ", ".join(sorted(set(avoid_names)))
        + f"\n\nSchema: {json.dumps(_NewName.model_json_schema())}"
    )
    result = await chat_json(
        model=MODEL_SUPER,
        system=(
            "You name fictional consumer personas. Output only JSON, no prose, "
            "no code fences."
        ),
        user=user,
        schema=_NewName,
        temperature=temperature,
        max_tokens=1500,
    )
    new_name = result.data.name.strip()
    if not new_name:
        raise ChatJsonError("empty replacement name", result.latency_ms)
    return persona.model_copy(update={"name": new_name})


def find_duplicate_names(personas: list[Persona]) -> list[tuple[str, list[str]]]:
    """Returns (name, persona_ids) for every full name used by more than
    one persona anywhere in the pool (check C1's hard gate).

    Scoped globally, not per region: two identical names are a visible
    bug wherever they sit, and at n=1000 the grid shows personas from
    every region side by side anyway. Earlier this was region-scoped and
    covered occupations too -- occupations are now a soft diversity
    target instead (see occupation_stats), because 1000 personas across
    8 regions cannot have 125 distinct jobs per region without the
    generator inventing absurd ones, while 1000 distinct names is both
    achievable and what a reader actually notices.
    """
    from collections import Counter

    names = Counter(p.name.strip().lower() for p in personas)
    issues = []
    for name, count in names.items():
        if count > 1:
            ids = [p.id for p in personas if p.name.strip().lower() == name]
            issues.append((name, ids))
    return issues


def occupation_stats(personas: list[Persona]) -> dict:
    """Occupation diversity, reported but never gated on: repeats are
    realistic (there really are many nurses), so this exists to catch
    the generator collapsing onto a handful of jobs rather than to force
    uniqueness.
    """
    from collections import Counter

    occs = Counter(p.occupation.strip().lower() for p in personas)
    total = len(personas)
    return {
        "total": total,
        "unique": len(occs),
        "ratio": len(occs) / total if total else 0.0,
        "top_repeats": occs.most_common(8),
    }


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
        f"read as skepticism level {new_skepticism}/100. Keep their name, "
        "age, occupation, region, income_band, tech_comfort, openness, "
        "conscientiousness, extraversion, agreeableness, neuroticism, "
        "values, and buying_triggers exactly as given.\n\n"
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
