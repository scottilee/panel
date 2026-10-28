"""FastAPI app: POST /run, GET /run/{id}/stream (SSE), POST /compare,
GET /personas, GET /examples; serves web/dist in production. Section 5.4.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from panel import config
from panel.compare import delta
from panel.fetch import fetch_url_text
from panel.personas import load_personas, sample_stable
from panel.react import fan_out
from panel.schemas import (
    Artifact,
    CompareRequest,
    CompareResult,
    Persona,
    Reaction,
    Run,
    RunRequest,
    RunStats,
    Synthesis,
)
from panel.synthesize import synthesize

app = FastAPI(title="Panel")

# CORS open for the demo (section 3).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@dataclass
class RunContext:
    """Server-side bookkeeping alongside the public Run record. `personas`
    and `subscribers` aren't part of the Run contract (section 3) -- they're
    this process's own state, not something a client ever sees directly.
    """

    run: Run
    personas: list[Persona]
    subscribers: list[asyncio.Queue] = field(default_factory=list)
    reached_synthesizing: bool = False
    error_message: str | None = None


RUNS: dict[str, RunContext] = {}

PERSONA_POOL = load_personas()


def _new_run_id() -> str:
    return f"r_{datetime.now(timezone.utc):%Y%m%d}_{uuid.uuid4().hex[:4]}"


def _sse_event(event_type: str, data: object) -> dict:
    if hasattr(data, "model_dump_json"):
        payload = data.model_dump_json()
    else:
        payload = json.dumps(data)
    return {"event": event_type, "data": payload}


async def _broadcast(ctx: RunContext, event_type: str, data: object) -> None:
    event = _sse_event(event_type, data)
    # list(...): a subscriber can unregister (on disconnect) while this
    # loop awaits individual queue.put calls -- iterate a snapshot so that
    # doesn't mutate the list out from under us.
    for queue in list(ctx.subscribers):
        await queue.put(event)


def _persist(ctx: RunContext) -> None:
    config.RUNS_DIR.mkdir(parents=True, exist_ok=True)
    path = config.RUNS_DIR / f"{ctx.run.run_id}.json"
    path.write_text(ctx.run.model_dump_json(indent=2))


def _find_cached_example(text: str) -> dict | None:
    """Matches by exact text content against the real example files,
    not a client-supplied id -- so a forged/mismatched ?cached=true can't
    be used to serve an arbitrary cached result for different text.
    """
    for example_path in config.EXAMPLES_DIR.glob("*.txt"):
        # .strip() on both sides: RunRequest's validator strips the
        # submitted text, while the file keeps its trailing newline --
        # comparing raw never matched, which silently disabled the whole
        # fallback (found 2026-10-28 testing this path end to end).
        if example_path.read_text().strip() != text.strip():
            continue
        cache_path = config.CACHED_DIR / f"{example_path.stem}.json"
        if cache_path.is_file():
            return json.loads(cache_path.read_text())
    return None


CACHED_REACTION_INTERVAL_S = 0.05  # section 5.8: "replays... at 50ms intervals"


async def _execute_cached_run(ctx: RunContext, cached_data: dict) -> None:
    """Section 5.8's demo fallback: replay a known-good cached result
    instead of making real model calls. Only ever used for a request that
    _find_cached_example has already matched byte-for-byte against a real
    example file, so the cached reactions' persona_ids are guaranteed to
    line up with ctx.personas (same example -> same cache -> same pool).
    """
    run = ctx.run
    try:
        reactions = [Reaction.model_validate(r) for r in cached_data["reactions"]][: len(ctx.personas)]
        for reaction in reactions:
            await asyncio.sleep(CACHED_REACTION_INTERVAL_S)
            run.reactions.append(reaction)
            if run.status == "pending":
                run.status = "streaming"
            await _broadcast(ctx, "reaction", reaction)
            if len(run.reactions) % 10 == 0 or len(run.reactions) == len(reactions):
                await _broadcast(
                    ctx, "progress", {"done": len(run.reactions), "total": len(reactions)}
                )

        run.status = "synthesizing"
        ctx.reached_synthesizing = True
        await _broadcast(ctx, "synthesizing", {})
        await asyncio.sleep(CACHED_REACTION_INTERVAL_S)

        synthesis = Synthesis.model_validate(cached_data["synthesis"])
        run.synthesis = synthesis
        sentiments = [r.sentiment for r in reactions]
        run.stats = RunStats(
            mean_sentiment=sum(sentiments) / len(sentiments) if sentiments else 0.0,
            would_act_pct=(
                100 * sum(1 for r in reactions if r.would_act) / len(reactions) if reactions else 0.0
            ),
            nano_calls=len(reactions),
            nano_ms_total=sum(r.latency_ms or 0 for r in reactions),
            ultra_ms=0,
        )
        await _broadcast(ctx, "synthesis", synthesis)
        run.status = "done"
        await _broadcast(ctx, "done", {"stats": run.stats.model_dump(mode="json")})
    except Exception as e:  # noqa: BLE001
        run.status = "error"
        ctx.error_message = str(e)
        await _broadcast(ctx, "error", {"message": str(e)})
    finally:
        _persist(ctx)


JOB_POLL_INTERVAL_S = 0.5


async def _reactions_from_job(
    run_id: str, artifact_text: str, persona_count: int
) -> AsyncIterator[Reaction]:
    """Section 5.7's Serverless Job path. NOT a real Nebius Job submission
    -- this sandbox only has a Token Factory API key, not Nebius Job
    credentials, so the actual "submit to Nebius, poll Nebius for status"
    calls are a placeholder, not something verified against the real
    service (see jobs/react_job.py's module docstring). What IS real and
    tested: spawning the job script as a subprocess and polling its
    output file for new reactions as they're written -- architecturally
    identical to what a real submit-and-poll-a-remote-job integration
    would do, just against a local process instead of Nebius's API. Swap
    the subprocess launch below for the real Nebius submission call once
    real credentials are available; the polling/streaming logic doesn't
    need to change.
    """
    output_path = config.RUNS_DIR / f"{run_id}_job_output.json"
    config.RUNS_DIR.mkdir(parents=True, exist_ok=True)

    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        str(config.ROOT / "jobs" / "react_job.py"),
        "--artifact-text",
        artifact_text,
        "--persona-count",
        str(persona_count),
        "--run-id",
        run_id,
        "--output-path",
        str(output_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    seen = 0
    while True:
        if output_path.is_file():
            try:
                data = json.loads(output_path.read_text())
            except json.JSONDecodeError:
                data = None  # file mid-write (react_job.py rewrites it whole); retry next poll
            if data:
                for raw in data["reactions"][seen:]:
                    yield Reaction.model_validate(raw)
                seen = len(data["reactions"])
                if data["done"] >= data["total"]:
                    break
        if proc.returncode is not None and seen == 0:
            stderr = (await proc.stderr.read()).decode()
            raise RuntimeError(f"react_job.py exited before producing output: {stderr[-2000:]}")
        await asyncio.sleep(JOB_POLL_INTERVAL_S)

    await proc.wait()


async def _execute_run(ctx: RunContext) -> None:
    run = ctx.run
    try:
        reaction_source = (
            _reactions_from_job(run.run_id, run.artifact.text, len(ctx.personas))
            if config.USE_SERVERLESS_JOB
            else fan_out(run.artifact.text, ctx.personas, run.run_id)
        )
        async for reaction in reaction_source:
            run.reactions.append(reaction)
            if run.status == "pending":
                run.status = "streaming"
            await _broadcast(ctx, "reaction", reaction)
            if len(run.reactions) % 10 == 0 or len(run.reactions) == len(ctx.personas):
                progress = {"done": len(run.reactions), "total": len(ctx.personas)}
                await _broadcast(ctx, "progress", progress)

        run.status = "synthesizing"
        ctx.reached_synthesizing = True
        await _broadcast(ctx, "synthesizing", {})

        synthesis_start = time.monotonic()
        synthesis = await synthesize(run.artifact.text, ctx.personas, run.reactions)
        ultra_ms = int((time.monotonic() - synthesis_start) * 1000)

        run.synthesis = synthesis
        sentiments = [r.sentiment for r in run.reactions]
        run.stats = RunStats(
            mean_sentiment=sum(sentiments) / len(sentiments) if sentiments else 0.0,
            would_act_pct=(
                100 * sum(1 for r in run.reactions if r.would_act) / len(run.reactions)
                if run.reactions
                else 0.0
            ),
            nano_calls=len(ctx.personas),
            nano_ms_total=sum(r.latency_ms or 0 for r in run.reactions),
            ultra_ms=ultra_ms,
        )
        await _broadcast(ctx, "synthesis", synthesis)

        run.status = "done"
        await _broadcast(ctx, "done", {"stats": run.stats.model_dump(mode="json")})
    except Exception as e:  # noqa: BLE001 -- unrecoverable failure, section 5.4's "error" status
        run.status = "error"
        ctx.error_message = str(e)
        await _broadcast(ctx, "error", {"message": str(e)})
    finally:
        _persist(ctx)


@app.post("/run")
async def create_run(req: RunRequest, cached: bool = False) -> dict:
    if req.url:
        try:
            text = await asyncio.to_thread(fetch_url_text, req.url)
        except Exception as e:  # noqa: BLE001 -- any fetch failure is a 400, not a crash
            raise HTTPException(400, f"could not fetch URL: {e}") from e
        source, url = "url", req.url
    else:
        text, source, url = req.text, "text", None

    persona_count = req.persona_count or config.DEFAULT_PERSONA_COUNT
    if persona_count > len(PERSONA_POOL):
        raise HTTPException(
            400,
            f"persona_count {persona_count} exceeds the pool size ({len(PERSONA_POOL)})",
        )

    run_id = _new_run_id()
    personas = sample_stable(persona_count, personas=PERSONA_POOL)
    run = Run(
        run_id=run_id,
        artifact=Artifact(source=source, text=text, url=url, label=req.label),
        persona_set=f"v1-{persona_count}",
        status="pending",
    )
    ctx = RunContext(run=run, personas=personas)
    RUNS[run_id] = ctx
    _persist(ctx)

    # Section 5.8: the frontend's own last resort (retry with ?cached=true
    # if a live run errors within 10s) -- find a cached example matching
    # this exact text rather than trusting a client-supplied id, so a
    # mismatched/forged ?cached=true can't serve the wrong canned result.
    cached_data = _find_cached_example(text) if cached else None
    if cached and cached_data is None:
        raise HTTPException(404, "no cached example matches this artifact")

    if cached_data is not None:
        asyncio.create_task(_execute_cached_run(ctx, cached_data))
    else:
        asyncio.create_task(_execute_run(ctx))
    return {"run_id": run_id}


@app.get("/run/{run_id}/stream")
async def stream_run(run_id: str) -> EventSourceResponse:
    ctx = RUNS.get(run_id)
    if ctx is None:
        raise HTTPException(404, "run not found")

    async def generator():
        queue: asyncio.Queue = asyncio.Queue()
        # Subscribe BEFORE replaying from the current snapshot: an event
        # broadcast between registering and finishing the replay loop will
        # land in both the replay and the queue -- a harmless duplicate.
        # Subscribing after replay instead risks a terminal event (done/
        # error) firing in the gap and never reaching this connection at
        # all, which hangs it forever.
        ctx.subscribers.append(queue)
        try:
            run = ctx.run
            for reaction in list(run.reactions):
                yield _sse_event("reaction", reaction)
            if run.reactions:
                yield _sse_event(
                    "progress", {"done": len(run.reactions), "total": len(ctx.personas)}
                )
            if ctx.reached_synthesizing:
                yield _sse_event("synthesizing", {})
            if run.synthesis is not None:
                yield _sse_event("synthesis", run.synthesis)
            if run.status == "done":
                yield _sse_event("done", {"stats": run.stats.model_dump(mode="json")})
                return
            if run.status == "error":
                yield _sse_event("error", {"message": ctx.error_message or "unknown error"})
                return

            while True:
                event = await queue.get()
                yield event
                if event["event"] in ("done", "error"):
                    break
        finally:
            if queue in ctx.subscribers:
                ctx.subscribers.remove(queue)

    return EventSourceResponse(generator())


@app.get("/run/{run_id}")
async def get_run(run_id: str) -> Run:
    ctx = RUNS.get(run_id)
    if ctx is None:
        raise HTTPException(404, "run not found")
    return ctx.run


@app.post("/compare")
async def compare_runs(req: CompareRequest) -> CompareResult:
    ctx_a = RUNS.get(req.run_a)
    ctx_b = RUNS.get(req.run_b)
    if ctx_a is None or ctx_b is None:
        raise HTTPException(404, "one or both runs not found")
    if ctx_a.run.status != "done" or ctx_b.run.status != "done":
        raise HTTPException(409, "both runs must be done before comparing")
    return await delta(ctx_a.run, ctx_b.run)


@app.get("/personas")
async def get_personas() -> list[Persona]:
    return PERSONA_POOL


@app.get("/examples")
async def get_examples() -> list[dict]:
    examples = []
    for path in sorted(config.EXAMPLES_DIR.glob("*.txt")):
        examples.append({"id": path.stem, "label": path.stem, "text": path.read_text()})
    return examples


# Static file serving (section 5.4/5.8): mounted LAST so every API route
# above takes priority -- Starlette matches routes in registration order,
# and a catch-all static mount registered first would shadow /run, etc.
if config.WEB_DIST_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(config.WEB_DIST_DIR), html=True), name="web")
