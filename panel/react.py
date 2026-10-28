"""fan_out(artifact_text, personas, run_id) -> async generator of Reaction.
Section 5.2.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from panel.prompts import REACTION_SYSTEM, reaction_user
from panel.schemas import Persona, Reaction
from panel.tf_client import ChatJsonError, chat_json, env

MODEL_NANO = env("MODEL_NANO", "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B")
DEFAULT_MAX_CONCURRENCY = int(env("MAX_CONCURRENCY", "25"))

# Section 5.2 states max_tokens=400 for Nano. Measured empirically
# (2026-10-27, 15-call samples at 800/1500/3000 against pricing_v1.txt):
# 400 truncates essentially every call (14/15 truncated even at 800) because
# Nano, like Super and Ultra, burns completion-token budget on hidden
# reasoning before emitting the Reaction JSON. 3000 truncated 0/15 with
# observed completion_tokens topping out at 2092 -- real margin, not a
# coincidence. Flagging the spec's stated value as wrong rather than
# working around it with chat_json's truncation-retry path alone, which
# would otherwise silently 2-3x every call's latency.
REACTION_MAX_TOKENS = 3000

# Section 4 states temperature=0.7 for reactions. Measured empirically
# (2026-10-28): rerunning the identical edited artifact against the
# identical 100 personas gave 7 flipped-to-act personas on one run and 4
# on an immediate rerun -- the same edit, same people, same text, nearly
# double the variance. That noise was swamping the signal we actually
# care about (does this edit help). Lower temperature trades away some
# per-reaction variety for materially more run-to-run stability, which
# matters more here than it does for the one-off persona-generation calls.
REACTION_TEMPERATURE = 0.4


async def _react_one(
    persona: Persona, artifact_text: str, run_id: str, sem: asyncio.Semaphore
) -> Reaction:
    async with sem:
        try:
            result = await chat_json(
                model=MODEL_NANO,
                system=REACTION_SYSTEM,
                user=reaction_user(persona.model_dump_json(), artifact_text),
                schema=Reaction,
                temperature=REACTION_TEMPERATURE,
                seed=hash(persona.id) % (2**31),
                max_tokens=REACTION_MAX_TOKENS,
            )
            return result.data.model_copy(
                update={
                    "persona_id": persona.id,
                    "run_id": run_id,
                    "latency_ms": result.latency_ms,
                }
            )
        except ChatJsonError as e:
            return Reaction(
                persona_id=persona.id,
                sentiment=0,
                would_act=False,
                gut_reaction="(no response)",
                top_confusion="(no response)",
                top_objection="(no response)",
                what_would_change_my_mind="(no response)",
                run_id=run_id,
                latency_ms=getattr(e, "latency_ms", None),
                raw_error=str(e),
            )


async def fan_out(
    artifact_text: str,
    personas: list[Persona],
    run_id: str,
    concurrency: int | None = None,
) -> AsyncIterator[Reaction]:
    """Yields one Reaction per persona as it completes (asyncio.as_completed
    order, not persona order) under a concurrency cap. A persona whose call
    fails after tf_client's retries still yields a Reaction — raw_error set,
    sentiment 0 — so one bad call never stalls the run.
    """
    sem = asyncio.Semaphore(concurrency or DEFAULT_MAX_CONCURRENCY)
    tasks = [
        asyncio.create_task(_react_one(p, artifact_text, run_id, sem)) for p in personas
    ]
    for task in asyncio.as_completed(tasks):
        yield await task
