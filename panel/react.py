"""fan_out(artifact_text, personas, run_id) -> async generator of Reaction.
One Nano call per persona, under a concurrency cap.
"""

from __future__ import annotations

import asyncio
import zlib
from collections.abc import AsyncIterator

from panel.config import MAX_CONCURRENCY, MODEL_NANO
from panel.prompts import REACTION_SYSTEM, reaction_user
from panel.schemas import Persona, Reaction
from panel.tf_client import chat_json


def seed_for(persona_id: str) -> int:
    """Stable per-persona seed. crc32 rather than hash(), which Python
    randomizes per process, so a persona's seed survives restarts."""
    return zlib.crc32(persona_id.encode()) % (2**31)


# Nano reasons before it emits the Reaction JSON. Measured 2026-10-27,
# 15-call samples against pricing_v1.txt: 400 truncates essentially every
# call (14/15 even at 800); 3000 truncated 0/15, with observed usage
# topping out at 2092.
REACTION_MAX_TOKENS = 3000

# Lower than the 0.7 used for one-off generation: at 0.7 the identical
# edited artifact against the identical panel flipped 7 people on one run
# and 4 on an immediate rerun, noise that swamped the edit's own effect.
REACTION_TEMPERATURE = 0.4


# The public value of Reaction.raw_error. Only its presence means anything.
REACTION_FAILED = "the model call failed"


async def react_one(
    persona: Persona, artifact_text: str, run_id: str, sem: asyncio.Semaphore
) -> Reaction:
    """One persona's reaction, never raising (scripts/gen_personas.py's
    anti-sycophancy check reuses it)."""
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
            # fan_out shutting the run down on purpose; swallowing this
            # would make the task look completed and defeat the cancel.
            raise
        except Exception as e:  # noqa: BLE001
            # Any failure -- not just ChatJsonError -- becomes a placeholder
            # reaction with raw_error set, so one bad call never discards the
            # reactions already paid for. Aggregates skip these (panel/stats.py).
            # The exception text goes to the log only: a reaction is public
            # (SSE stream, GET /run), and provider errors name the account,
            # the auth failure and internal hosts.
            print(f"reaction {run_id}/{persona.id} failed: {type(e).__name__}: {e}", flush=True)
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
                raw_error=REACTION_FAILED,
            )


async def fan_out(
    artifact_text: str,
    personas: list[Persona],
    run_id: str,
    concurrency: int | None = None,
) -> AsyncIterator[Reaction]:
    """Yields one Reaction per persona as it completes (completion order,
    not persona order). A persona whose call fails still yields a Reaction
    with raw_error set, so one bad call never stalls the run.

    Every task is cancelled if the consumer stops early: all N tasks are
    created up front, and otherwise they would keep spending credits on a
    run nobody is reading.
    """
    sem = asyncio.Semaphore(concurrency or MAX_CONCURRENCY)
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
