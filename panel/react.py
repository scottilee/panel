"""fan_out(artifact_text, personas, run_id) -> async generator of Reaction.
Section 5.2.
"""

from __future__ import annotations

import asyncio
import zlib
from collections.abc import AsyncIterator

from panel.config import MAX_CONCURRENCY, MODEL_NANO
from panel.prompts import REACTION_SYSTEM, reaction_user
from panel.schemas import Persona, Reaction
from panel.tf_client import chat_json

# Imported from config rather than re-read from env here. This module used
# to declare its own DEFAULT_MAX_CONCURRENCY = env("MAX_CONCURRENCY", "25")
# while config.py declared the same name defaulting to 50 -- and it was
# this one that actually capped the fan-out, so an unconfigured deployment
# ran at half the throughput .env.example, infra/variables.tf and the
# README's performance table all advertise (and the frontend's "roughly N
# x 0.4 seconds" caption, calibrated at 50, under-predicted by ~1.7x).
DEFAULT_MAX_CONCURRENCY = MAX_CONCURRENCY


def seed_for(persona_id: str) -> int:
    """Stable per-persona seed. Python's built-in hash() randomizes string
    hashing per process (PYTHONHASHSEED), so using it here meant the
    "fixed seed per persona" the spec asks for (section 4) silently
    changed on every server restart -- the one thing a seed is supposed to
    prevent. crc32 is stable across processes and machines.
    """
    return zlib.crc32(persona_id.encode()) % (2**31)

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
# identical 100-persona panel gave 7 flipped-to-act personas on one run and 4
# on an immediate rerun -- the same edit, same people, same text, nearly
# double the variance. That noise was swamping the signal we actually
# care about (does this edit help). Lower temperature trades away some
# per-reaction variety for materially more run-to-run stability, which
# matters more here than it does for the one-off persona-generation calls.
REACTION_TEMPERATURE = 0.4


async def react_one(
    persona: Persona, artifact_text: str, run_id: str, sem: asyncio.Semaphore
) -> Reaction:
    """One persona's reaction, never raising. Public (it was _react_one)
    because scripts/gen_personas.py's anti-sycophancy check had its own
    line-for-line copy of this -- same model, prompt, temperature, seed
    and token budget, and the same "(no response)" fallback -- which meant
    fixes here (the broadened except below) silently didn't reach it.
    """
    async with sem:
        try:
            result = await chat_json(
                model=MODEL_NANO,
                system=REACTION_SYSTEM,
                user=reaction_user(persona.model_dump_json(), artifact_text),
                schema=Reaction,
                temperature=REACTION_TEMPERATURE,
                seed=seed_for(persona.id),
                max_tokens=REACTION_MAX_TOKENS,
            )
            return result.data.model_copy(
                update={
                    "persona_id": persona.id,
                    "run_id": run_id,
                    "latency_ms": result.latency_ms,
                }
            )
        except asyncio.CancelledError:
            # Cancellation is fan_out shutting the run down on purpose;
            # swallowing it into a "(no response)" Reaction would make the
            # task look like it completed and defeat the cancel.
            raise
        except Exception as e:  # noqa: BLE001
            # Deliberately not just ChatJsonError. tf_client's backoff
            # retries only RateLimitError/InternalServerError, so
            # APIConnectionError, APITimeoutError, BadRequestError and
            # AuthenticationError all propagated out of here uncaught --
            # straight past fan_out into _execute_run's blanket handler,
            # which marked the whole run "error". That contradicted this
            # module's own promise below, and at N=1000 meant one network
            # blip on persona 900 discarded 899 already-paid reactions.
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
                raw_error=f"{type(e).__name__}: {e}",
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

    Every task is cancelled if the consumer stops early (an exception
    downstream, or the run being abandoned). All N tasks are created up
    front, so without this they would keep running -- and keep spending
    credits one Nano call at a time -- for a run nobody is reading.
    """
    sem = asyncio.Semaphore(concurrency or DEFAULT_MAX_CONCURRENCY)
    tasks = [
        asyncio.create_task(react_one(p, artifact_text, run_id, sem)) for p in personas
    ]
    try:
        for task in asyncio.as_completed(tasks):
            yield await task
    finally:
        pending = [t for t in tasks if not t.done()]
        for t in pending:
            t.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
