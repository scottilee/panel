"""FastAPI app: POST /run, GET /run/{id}/stream (SSE), POST /compare,
GET /personas, GET /examples; serves web/dist in production. Section 5.4.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from panel import config
from panel.compare import delta
from panel.fetch import UnsafeURLError, fetch_url_text
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

# GET /personas serves the whole pool in one response, which went from
# ~72KB at 100 personas to ~700KB at 1000 -- worth compressing, since the
# frontend fetches it before the first paint. Safe for the SSE endpoint:
# starlette 1.10's GZipMiddleware excludes text/event-stream by default
# (DEFAULT_EXCLUDED_CONTENT_TYPES), so run streams are passed through
# uncompressed and keep flushing per event. minimum_size then keeps it off
# the small JSON responses too, where it would only add CPU.
app.add_middleware(GZipMiddleware, minimum_size=16384)


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
    # Strong reference to the task driving this run -- see create_run.
    task: asyncio.Task | None = None


RUNS: dict[str, RunContext] = {}

# RUNS is the only record of a run that an SSE reconnect or a /compare can
# read (section 5.4 keeps it in process memory deliberately -- hence
# max_instance_count = 1 in infra/). It used to grow without bound, which
# was survivable when a run meant 100 reactions and the service was a demo
# you restarted often; at 1,000 personas a run holds roughly ten times the
# data, and this deployment stays up for six weeks of judging on a 1Gi
# instance. Evicting the oldest *finished* runs bounds that. 50 is far more
# than a demo session needs and keeps any plausible compare pair alive.
MAX_RETAINED_RUNS = 50


def _prune_run_files() -> None:
    """Keeps at most MAX_RETAINED_RUNS persisted run records on disk.

    On Cloud Run RUNS_DIR is /tmp, which is tmpfs -- RAM, charged against
    the same 1Gi as the process. A 1,000-persona run persists ~400KB, and
    these files were never deleted, so an unattended six-week judging
    window was a slow memory leak dressed as durability. Pruned to the
    same count the in-memory dict keeps, since a file for an evicted run
    is only reachable by someone reading the disk anyway.
    """
    try:
        files = sorted(
            config.RUNS_DIR.glob("r_*.json"), key=lambda p: p.stat().st_mtime, reverse=True
        )
        for stale in files[MAX_RETAINED_RUNS:]:
            stale.unlink(missing_ok=True)
    except OSError as e:
        print(f"warning: could not prune run files: {e}")


def _evict_old_runs() -> None:
    """Drops the oldest finished runs once RUNS exceeds MAX_RETAINED_RUNS.
    Insertion order is creation order (dicts preserve it), so the oldest
    are simply the first keys. A run that is still pending/streaming/
    synthesizing is never evicted, nor is one with a live SSE subscriber --
    evicting either would strand a connection that can still be served,
    and both sets are tiny compared to the retention limit.
    """
    removable = [
        rid
        for rid, c in RUNS.items()
        if c.run.status in ("done", "error") and not c.subscribers
    ]
    for rid in removable[: max(0, len(RUNS) - MAX_RETAINED_RUNS)]:
        del RUNS[rid]


PERSONA_POOL = load_personas()

# --- Public-deployment spend limits -----------------------------------------
#
# Nothing below is in the build spec. They exist because this service is
# deployed unauthenticated for six weeks of judging and every run spends
# real credits, so the defaults are "generous for a judge, useless for a
# script". All three are env-overridable so a local run isn't constrained.
MAX_PERSONA_COUNT = int(config.env("MAX_PERSONA_COUNT") or "1000")
MAX_CONCURRENT_RUNS = int(config.env("MAX_CONCURRENT_RUNS") or "3")
RATE_LIMIT_RUNS = int(config.env("RATE_LIMIT_RUNS") or "20")
RATE_LIMIT_WINDOW_S = int(config.env("RATE_LIMIT_WINDOW_S") or "3600")

_RUN_TIMES_BY_IP: dict[str, list[float]] = defaultdict(list)


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
    """Best-effort durability. The in-memory RUNS dict is what actually
    serves reads and SSE replay, so a failed write must never take a run
    down with it -- on a read-only or full container filesystem the whole
    run would otherwise fail at the final persist step, after all the
    model calls were already paid for.
    """
    try:
        config.RUNS_DIR.mkdir(parents=True, exist_ok=True)
        path = config.RUNS_DIR / f"{ctx.run.run_id}.json"
        path.write_text(ctx.run.model_dump_json(indent=2))
    except OSError as e:
        print(f"warning: could not persist run {ctx.run.run_id}: {e}")


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


def _compute_stats(reactions: list[Reaction], nano_calls: int, ultra_ms: int) -> RunStats:
    """One definition, used by both the live and the cached-replay path --
    they had separate copies of this arithmetic that had already drifted
    (the replay reported the cache's reaction count as nano_calls, which
    is zero real Nano calls)."""
    sentiments = [r.sentiment for r in reactions]
    return RunStats(
        mean_sentiment=sum(sentiments) / len(sentiments) if sentiments else 0.0,
        would_act_pct=(
            100 * sum(1 for r in reactions if r.would_act) / len(reactions) if reactions else 0.0
        ),
        nano_calls=nano_calls,
        nano_ms_total=sum(r.latency_ms or 0 for r in reactions),
        ultra_ms=ultra_ms,
    )


async def _fail_run(ctx: RunContext, exc: Exception) -> None:
    """Marks a run failed, logging the real exception server-side and
    broadcasting a generic message.

    The message used to be str(exc) verbatim, which put arbitrary internal
    exception text -- provider auth failures, connection strings, file
    paths -- onto a public SSE stream that anyone with the run id can
    read. ctx.error_message keeps the detail for GET /run, which is the
    same trust boundary, so that one is generic too; the log is where the
    real text lives.
    """
    print(f"run {ctx.run.run_id} failed: {type(exc).__name__}: {exc}", flush=True)
    ctx.run.status = "error"
    message = "the run failed; see server logs for details"
    ctx.error_message = message
    await _broadcast(ctx, "error", {"message": message})


async def _execute_cached_run(ctx: RunContext, cached_data: dict) -> None:
    """Section 5.8's demo fallback: replay a known-good cached result
    instead of making real model calls. Only ever used for a request that
    _find_cached_example has already matched byte-for-byte against a real
    example file, so the cached reactions' persona_ids are guaranteed to
    line up with ctx.personas (same example -> same cache -> same pool).
    """
    run = ctx.run
    try:
        # Select by persona_id, not by list position: the cached file holds
        # its reactions in fan-out completion order (effectively random),
        # so slicing the first N would replay reactions for personas this
        # run didn't select -- every one of those would arrive with a
        # persona_id that has no tile in the grid. create_run guarantees
        # the cache covers every selected persona (_cache_covers), so the
        # count below always equals len(ctx.personas).
        wanted = {p.id for p in ctx.personas}
        reactions = [
            Reaction.model_validate(r)
            for r in cached_data["reactions"]
            if r["persona_id"] in wanted
        ]
        total = len(ctx.personas)
        for reaction in reactions:
            await asyncio.sleep(CACHED_REACTION_INTERVAL_S)
            run.reactions.append(reaction)
            if run.status == "pending":
                run.status = "streaming"
            await _broadcast(ctx, "reaction", reaction)
            if len(run.reactions) % 10 == 0 or len(run.reactions) == total:
                await _broadcast(ctx, "progress", {"done": len(run.reactions), "total": total})

        run.status = "synthesizing"
        ctx.reached_synthesizing = True
        await _broadcast(ctx, "synthesizing", {})
        await asyncio.sleep(CACHED_REACTION_INTERVAL_S)

        synthesis = Synthesis.model_validate(cached_data["synthesis"])
        run.synthesis = synthesis
        # A replay makes no model calls at all, so every cost figure is
        # zero. _compute_stats would otherwise sum the cached reactions'
        # recorded latencies into nano_ms_total -- half an hour of Nano
        # time reported next to nano_calls=0, describing calls made days
        # ago on someone else's run. The sentiment figures below are real:
        # they describe the reactions being shown.
        run.stats = _compute_stats(reactions, nano_calls=0, ultra_ms=0).model_copy(
            update={"nano_ms_total": 0}
        )
        await _broadcast(ctx, "synthesis", synthesis)
        run.status = "done"
        await _broadcast(ctx, "done", {"stats": run.stats.model_dump(mode="json")})
    except Exception as e:  # noqa: BLE001
        await _fail_run(ctx, e)
    finally:
        _persist(ctx)


async def _execute_run(ctx: RunContext) -> None:
    run = ctx.run
    try:
        async for reaction in fan_out(run.artifact.text, ctx.personas, run.run_id):
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
        run.stats = _compute_stats(
            run.reactions, nano_calls=len(ctx.personas), ultra_ms=ultra_ms
        )
        await _broadcast(ctx, "synthesis", synthesis)

        run.status = "done"
        await _broadcast(ctx, "done", {"stats": run.stats.model_dump(mode="json")})
    except Exception as e:  # noqa: BLE001 -- unrecoverable failure, section 5.4's "error" status
        await _fail_run(ctx, e)
    finally:
        _persist(ctx)


def _cache_covers(cached_data: dict, personas: list[Persona]) -> bool:
    """Whether a cached result has a reaction for every selected persona.

    The cached files were recorded at 100 personas. With a 1,000-persona
    pool a judge can ask for more than that, and the replay would then
    stream 100 reactions into an N-tile grid and report progress against
    the wrong denominator. Easier to decline the cache than to half-serve
    it -- the frontend only reaches for it as a last resort anyway.
    """
    have = {r["persona_id"] for r in cached_data.get("reactions", [])}
    return all(p.id in have for p in personas)


def _active_run_count() -> int:
    return sum(1 for c in RUNS.values() if c.run.status in ("pending", "streaming", "synthesizing"))


def _check_rate_limit(client_ip: str) -> None:
    """Per-client run budget over a rolling window.

    This service is public, unauthenticated (allUsers invoker), CORS-open,
    and every POST /run spends real Token Factory credits -- up to 1,000
    Nano calls plus Super digests plus one Ultra call. A for-loop against
    the demo URL would drain the hackathon budget, and it has to survive
    six weeks of judging unattended. An in-process window is sound here
    specifically because the deployment is pinned to one instance
    (max_instance_count = 1, for the SSE state reasons documented in
    infra/main.tf).
    """
    now = time.monotonic()
    window = _RUN_TIMES_BY_IP[client_ip]
    window[:] = [t for t in window if now - t < RATE_LIMIT_WINDOW_S]
    if len(window) >= RATE_LIMIT_RUNS:
        raise HTTPException(
            429,
            f"rate limit: at most {RATE_LIMIT_RUNS} runs per "
            f"{RATE_LIMIT_WINDOW_S // 60} minutes from one client",
        )
    window.append(now)


@app.post("/run")
async def create_run(request: Request, req: RunRequest, cached: bool = False) -> dict:
    _check_rate_limit(request.client.host if request.client else "unknown")
    if _active_run_count() >= MAX_CONCURRENT_RUNS:
        raise HTTPException(
            429, f"too many runs in flight (limit {MAX_CONCURRENT_RUNS}); try again shortly"
        )

    persona_count = req.persona_count or config.DEFAULT_PERSONA_COUNT
    ceiling = min(MAX_PERSONA_COUNT, len(PERSONA_POOL))
    if persona_count > ceiling:
        raise HTTPException(400, f"persona_count {persona_count} exceeds the limit ({ceiling})")

    # Validated before the URL fetch, which is the expensive, externally
    # reachable side effect in this handler.
    if req.url:
        try:
            text = await asyncio.to_thread(fetch_url_text, req.url)
        except UnsafeURLError as e:
            raise HTTPException(400, str(e)) from e
        except Exception as e:  # noqa: BLE001 -- any fetch failure is a 400, not a crash
            # Deliberately not echoing str(e): the upstream exception text
            # turns this endpoint into a host/port oracle for whatever the
            # container can reach.
            print(f"URL fetch failed for {req.url!r}: {type(e).__name__}: {e}", flush=True)
            raise HTTPException(400, "could not fetch that URL") from e
        source, url = "url", req.url
    else:
        text, source, url = req.text, "text", None

    personas = sample_stable(persona_count, personas=PERSONA_POOL)

    # Section 5.8: the frontend's own last resort (retry with ?cached=true
    # if a live run errors within 10s) -- find a cached example matching
    # this exact text rather than trusting a client-supplied id, so a
    # mismatched/forged ?cached=true can't serve the wrong canned result.
    #
    # Resolved before the run is registered. It used to run after, so
    # every rejected ?cached=true left a permanent RunContext in RUNS and
    # a JSON file in RUNS_DIR (tmpfs on Cloud Run, charged against the
    # same 1Gi as the process) -- an unauthenticated way to exhaust the
    # single instance at zero model cost.
    cached_data = _find_cached_example(text) if cached else None
    covered = 0
    if cached_data is not None and not _cache_covers(cached_data, personas):
        covered = len(cached_data.get("reactions", []))
        cached_data = None
    if cached and cached_data is None:
        # Says which panel size the fallback does cover, so the message is
        # actionable rather than just a refusal -- this is the path a judge
        # reaches only when a live run has already failed on them.
        detail = "no cached example matches this artifact"
        if covered:
            detail = (
                f"the cached fallback for this example covers {covered} personas, "
                f"not {persona_count}"
            )
        raise HTTPException(404, detail)

    run_id = _new_run_id()
    run = Run(
        run_id=run_id,
        artifact=Artifact(source=source, text=text, url=url, label=req.label),
        persona_set=f"v1-{persona_count}",
        status="pending",
    )
    ctx = RunContext(run=run, personas=personas)
    RUNS[run_id] = ctx
    _evict_old_runs()
    _prune_run_files()
    _persist(ctx)

    # Held on the context, not dropped on the floor: asyncio only keeps a
    # weak reference to a task, so a bare create_task can be garbage
    # collected mid-run (a documented asyncio hazard). This is the task
    # that drives the entire run.
    coro = _execute_cached_run(ctx, cached_data) if cached_data is not None else _execute_run(ctx)
    ctx.task = asyncio.create_task(coro)
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


# Human labels for the example buttons, keyed by filename stem. Mock mode
# has always shown these (web/src/mock.js's EXAMPLES); this route used to
# return the bare stem as the label, so real mode showed "pricing_v1" on
# the button and as the run's name while mock mode showed "Pricing page
# v1" for the same file. Falls back to the stem for any example added to
# data/examples/ without a label here.
EXAMPLE_LABELS = {
    "pricing_v1": "Pricing page v1",
    "ad_v1": "Fintech ad v1",
    "policy_v1": "Parking policy notice v1",
}


@app.get("/examples")
async def get_examples() -> list[dict]:
    examples = []
    for path in sorted(config.EXAMPLES_DIR.glob("*.txt")):
        examples.append(
            {
                "id": path.stem,
                "label": EXAMPLE_LABELS.get(path.stem, path.stem),
                "text": path.read_text(),
            }
        )
    return examples


# Static file serving (section 5.4/5.8): mounted LAST so every API route
# above takes priority -- Starlette matches routes in registration order,
# and a catch-all static mount registered first would shadow /run, etc.
if config.WEB_DIST_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(config.WEB_DIST_DIR), html=True), name="web")
