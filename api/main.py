"""FastAPI app: POST /run, GET /run/{id}/stream (SSE), GET /run/{id},
POST /run/{id}/cancel, POST /compare, POST /check, GET /personas,
GET /examples; serves the built frontend (web/dist) in production.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from panel import config
from panel.classify import check_artifact
from panel.compare import delta
from panel.fetch import UnsafeURLError, fetch_url_text
from panel.personas import load_personas, sample_stable
from panel.react import fan_out
from panel.schemas import (
    MIN_ARTIFACT_CHARS,
    Artifact,
    ArtifactCheck,
    CheckRequest,
    CompareRequest,
    CompareResult,
    Persona,
    Reaction,
    Run,
    RunRequest,
    RunStats,
    Synthesis,
)
from panel.stats import mean_sentiment, would_act_pct
from panel.synthesize import _apply_recounted_pct, synthesize
from panel.tf_client import track_calls

app = FastAPI(title="Panel")

# Same-origin by default: production serves the frontend from this app and
# the Vite dev server proxies to it, so neither needs CORS. An open policy
# would let any third-party page spend this service's credits from its
# visitors' browsers, each with its own IP and its own rate-limit window.
_CORS_ORIGINS = [o.strip() for o in (config.env("CORS_ORIGINS") or "").split(",") if o.strip()]
if _CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_CORS_ORIGINS,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

# GET /personas is ~700KB at 1,000 personas and is fetched before first
# paint. starlette's GZipMiddleware excludes text/event-stream by default,
# so run streams still flush per event; minimum_size keeps it off small
# JSON responses.
app.add_middleware(GZipMiddleware, minimum_size=16384)


@dataclass
class RunContext:
    """Server-side state alongside the public Run record."""

    run: Run
    personas: list[Persona]
    subscribers: list[asyncio.Queue] = field(default_factory=list)
    reached_synthesizing: bool = False
    error_message: str | None = None
    # Strong reference to the task driving this run: asyncio only holds a
    # weak one, so a bare create_task can be garbage collected mid-run.
    task: asyncio.Task | None = None
    # POST /compare results with this run as run B, keyed by run A's id.
    # Each compare costs an Ultra call, so a repeat request reuses the
    # first (or joins it while it is still in flight).
    compares: dict[str, asyncio.Task] = field(default_factory=dict)


RUNS: dict[str, RunContext] = {}

# A run still doing (and still paying for) work.
ACTIVE_STATUSES = ("pending", "streaming", "synthesizing")

# RUNS is the only record an SSE reconnect or a /compare can read, which
# is why the deployment is pinned to one instance (infra/main.tf). At
# 1,000 personas a run holds ~400KB, so the oldest finished runs are
# evicted; 50 keeps any plausible compare pair alive.
MAX_RETAINED_RUNS = 50


def _prune_run_files() -> None:
    """Keeps at most MAX_RETAINED_RUNS persisted run records on disk. On
    Cloud Run RUNS_DIR is tmpfs, charged against the instance's memory."""
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
    Dicts preserve insertion order, so the oldest are the first keys. A
    run still in progress, or with a live SSE subscriber, is never
    evicted."""
    removable = [
        rid
        for rid, c in RUNS.items()
        if c.run.status in ("done", "error", "cancelled") and not c.subscribers
    ]
    for rid in removable[: max(0, len(RUNS) - MAX_RETAINED_RUNS)]:
        del RUNS[rid]


PERSONA_POOL = load_personas()

# --- Public-deployment spend limits -----------------------------------------
#
# The deployed service is public and unauthenticated, and every model call
# spends real credits, so these bound what one client can do. Generous for
# a person, useless for a script; all env-overridable.
MAX_PERSONA_COUNT = int(config.env("MAX_PERSONA_COUNT") or "1000")
MAX_CONCURRENT_RUNS = int(config.env("MAX_CONCURRENT_RUNS") or "3")
RATE_LIMIT_RUNS = int(config.env("RATE_LIMIT_RUNS") or "20")
# A check is one Nano call and a user re-checks while editing; a compare
# is one Ultra call per pair of runs (and cached after that).
RATE_LIMIT_CHECKS = int(config.env("RATE_LIMIT_CHECKS") or "120")
RATE_LIMIT_COMPARES = int(config.env("RATE_LIMIT_COMPARES") or "60")
RATE_LIMIT_WINDOW_S = int(config.env("RATE_LIMIT_WINDOW_S") or "3600")

# How many reverse proxies in front of this app append to X-Forwarded-For.
# 0 trusts nothing and keys rate limits on the socket peer, which is right
# for local runs. Behind Cloud Run the socket peer is Google's front end --
# the same address for every client -- so the deployment sets this to 1
# and the client is the address Google appended last. Entries further left
# are client-supplied and never trusted.
TRUSTED_PROXY_HOPS = int(config.env("TRUSTED_PROXY_HOPS") or "0")

_RATE_WINDOWS: dict[str, list[float]] = defaultdict(list)
# Above this many tracked clients, windows with no recent requests are
# swept so the table can't grow without bound over weeks of uptime.
_RATE_SWEEP_THRESHOLD = 10_000


def _client_ip(request: Request) -> str:
    if TRUSTED_PROXY_HOPS:
        hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",")]
        hops = [h for h in hops if h]
        if len(hops) >= TRUSTED_PROXY_HOPS:
            return hops[-TRUSTED_PROXY_HOPS]
    return request.client.host if request.client else "unknown"


def _check_rate_limit(client_ip: str, bucket: str = "run", limit: int | None = None) -> None:
    """Charges one request against a client's rolling window for `bucket`,
    or raises 429. In-process state is sound because the deployment runs a
    single instance. Separate buckets keep cheap endpoints from eating an
    expensive endpoint's allowance."""
    cap = RATE_LIMIT_RUNS if limit is None else limit
    now = time.monotonic()
    if len(_RATE_WINDOWS) > _RATE_SWEEP_THRESHOLD:
        stale = [k for k, w in _RATE_WINDOWS.items() if not w or now - w[-1] >= RATE_LIMIT_WINDOW_S]
        for key in stale:
            del _RATE_WINDOWS[key]
    window = _RATE_WINDOWS[f"{bucket}:{client_ip}"]
    window[:] = [t for t in window if now - t < RATE_LIMIT_WINDOW_S]
    if len(window) >= cap:
        raise HTTPException(
            429,
            f"rate limit: at most {cap} {bucket} requests per "
            f"{RATE_LIMIT_WINDOW_S // 60} minutes from one client",
        )
    window.append(now)


def _new_run_id() -> str:
    # Unguessable: a run id is the only credential for reading a run's
    # pasted text (GET /run/{id}) or cancelling it.
    return f"r_{secrets.token_urlsafe(16)}"


def _sse_event(event_type: str, data: object) -> dict:
    if hasattr(data, "model_dump_json"):
        payload = data.model_dump_json()
    else:
        payload = json.dumps(data)
    return {"event": event_type, "data": payload}


async def _broadcast(ctx: RunContext, event_type: str, data: object) -> None:
    event = _sse_event(event_type, data)
    # Snapshot: a subscriber can unregister while this awaits a put.
    for queue in list(ctx.subscribers):
        await queue.put(event)


def _persist(ctx: RunContext) -> None:
    """Best-effort durability. RUNS is what serves reads, so a failed write
    must never fail a run whose model calls are already paid for."""
    try:
        config.RUNS_DIR.mkdir(parents=True, exist_ok=True)
        path = config.RUNS_DIR / f"{ctx.run.run_id}.json"
        path.write_text(ctx.run.model_dump_json(indent=2))
    except OSError as e:
        print(f"warning: could not persist run {ctx.run.run_id}: {e}")


def _find_cached_example(text: str) -> dict | None:
    """Matches by exact text against the real example files rather than a
    client-supplied id, so ?cached=true can't serve a canned result for
    different text. Both sides are stripped: RunRequest strips the
    submitted text, and the files end with a newline."""
    for example_path in config.EXAMPLES_DIR.glob("*.txt"):
        if example_path.read_text().strip() != text.strip():
            continue
        cache_path = config.CACHED_DIR / f"{example_path.stem}.json"
        if cache_path.is_file():
            return json.loads(cache_path.read_text())
    return None


CACHED_REACTION_INTERVAL_S = 0.05


def _compute_stats(
    reactions: list[Reaction], calls: Mapping[str, int], *, nano_ms_total: int, ultra_ms: int
) -> RunStats:
    return RunStats(
        mean_sentiment=mean_sentiment(reactions),
        would_act_pct=would_act_pct(reactions),
        failed_reactions=sum(1 for r in reactions if r.raw_error),
        nano_calls=calls.get(config.MODEL_NANO, 0),
        super_calls=calls.get(config.MODEL_SUPER, 0),
        ultra_calls=calls.get(config.MODEL_ULTRA, 0),
        nano_ms_total=nano_ms_total,
        ultra_ms=ultra_ms,
    )


async def _record_reaction(ctx: RunContext, reaction: Reaction) -> None:
    run = ctx.run
    run.reactions.append(reaction)
    if run.status == "pending":
        run.status = "streaming"
    await _broadcast(ctx, "reaction", reaction)
    done, total = len(run.reactions), len(ctx.personas)
    if done % 10 == 0 or done == total:
        await _broadcast(ctx, "progress", {"done": done, "total": total})


async def _start_synthesizing(ctx: RunContext) -> None:
    ctx.run.status = "synthesizing"
    ctx.reached_synthesizing = True
    await _broadcast(ctx, "synthesizing", {})


async def _finish(ctx: RunContext, synthesis: Synthesis, stats: RunStats) -> None:
    ctx.run.synthesis = synthesis
    ctx.run.stats = stats
    await _broadcast(ctx, "synthesis", synthesis)
    ctx.run.status = "done"
    await _broadcast(ctx, "done", {"stats": stats.model_dump(mode="json")})


async def _mark_cancelled(ctx: RunContext) -> None:
    if ctx.run.status == "cancelled":
        return
    print(f"run {ctx.run.run_id} cancelled by the user", flush=True)
    ctx.run.status = "cancelled"
    await _broadcast(ctx, "cancelled", {"done": len(ctx.run.reactions)})


async def _fail_run(ctx: RunContext, exc: Exception) -> None:
    """Marks a run failed. The real exception goes to the server log only:
    its text (provider auth failures, paths) must not reach a public SSE
    stream or GET /run."""
    print(f"run {ctx.run.run_id} failed: {type(exc).__name__}: {exc}", flush=True)
    ctx.run.status = "error"
    message = "the run failed; see server logs for details"
    ctx.error_message = message
    await _broadcast(ctx, "error", {"message": message})


async def _execute_cached_run(ctx: RunContext, cached_data: dict) -> None:
    """Demo fallback: replays a known-good cached result with no model
    calls. create_run only gets here after _find_cached_example matched
    the text exactly and _cache_covers confirmed every selected persona
    has a cached reaction."""
    try:
        # By persona_id, not list position: the file holds reactions in
        # completion order, so slicing would replay personas with no tile.
        wanted = {p.id for p in ctx.personas}
        reactions = [
            Reaction.model_validate(r) for r in cached_data["reactions"] if r["persona_id"] in wanted
        ]
        for reaction in reactions:
            await asyncio.sleep(CACHED_REACTION_INTERVAL_S)
            await _record_reaction(ctx, reaction)

        await _start_synthesizing(ctx)
        await asyncio.sleep(CACHED_REACTION_INTERVAL_S)

        # Recounted against the reactions actually being replayed, so a
        # cached report's numbers follow the same rules as a live one
        # however old the file is.
        synthesis = _apply_recounted_pct(
            Synthesis.model_validate(cached_data["synthesis"]),
            {p.id: p for p in ctx.personas},
            reactions,
        )
        # No model calls, so every cost figure is zero; the sentiment
        # figures are real, since they describe the reactions shown.
        await _finish(ctx, synthesis, _compute_stats(reactions, {}, nano_ms_total=0, ultra_ms=0))
    except asyncio.CancelledError:
        await _mark_cancelled(ctx)
        raise
    except Exception as e:  # noqa: BLE001
        await _fail_run(ctx, e)
    finally:
        _persist(ctx)


async def _execute_run(ctx: RunContext) -> None:
    run = ctx.run
    calls = track_calls()
    try:
        async for reaction in fan_out(run.artifact.text, ctx.personas, run.run_id):
            await _record_reaction(ctx, reaction)

        await _start_synthesizing(ctx)
        synthesis_start = time.monotonic()
        synthesis = await synthesize(run.artifact.text, ctx.personas, run.reactions)
        stats = _compute_stats(
            run.reactions,
            calls,
            nano_ms_total=sum(r.latency_ms or 0 for r in run.reactions),
            ultra_ms=int((time.monotonic() - synthesis_start) * 1000),
        )
        await _finish(ctx, synthesis, stats)
    except asyncio.CancelledError:
        # A deliberate stop: broadcast a terminal event before re-raising
        # so open streams don't wait forever.
        await _mark_cancelled(ctx)
        raise
    except Exception as e:  # noqa: BLE001
        await _fail_run(ctx, e)
    finally:
        _persist(ctx)


def _cache_covers(cached_data: dict, personas: list[Persona]) -> bool:
    """Whether a cached result has a reaction for every selected persona.
    The caches were recorded at 100 personas; a bigger panel is declined
    rather than half-served."""
    have = {r["persona_id"] for r in cached_data.get("reactions", [])}
    return all(p.id in have for p in personas)


def _active_run_count() -> int:
    return sum(1 for c in RUNS.values() if c.run.status in ACTIVE_STATUSES)


@app.post("/run")
async def create_run(request: Request, req: RunRequest, cached: bool = False) -> dict:
    # Free checks first, so a request rejected for its own shape or for
    # server load doesn't use up the client's allowance.
    if _active_run_count() >= MAX_CONCURRENT_RUNS:
        raise HTTPException(
            429, f"too many runs in flight (limit {MAX_CONCURRENT_RUNS}); try again shortly"
        )
    persona_count = req.persona_count or config.DEFAULT_PERSONA_COUNT
    ceiling = min(MAX_PERSONA_COUNT, len(PERSONA_POOL))
    if persona_count > ceiling:
        raise HTTPException(400, f"persona_count {persona_count} exceeds the limit ({ceiling})")

    # Charged before the URL fetch and the cached lookup, so neither can
    # be probed for free.
    client_ip = _client_ip(request)
    _check_rate_limit(client_ip)

    if req.url:
        try:
            text = await asyncio.to_thread(fetch_url_text, req.url)
        except UnsafeURLError as e:
            raise HTTPException(400, str(e)) from e
        except Exception as e:  # noqa: BLE001 -- any fetch failure is a 400, not a crash
            # Not echoing str(e): upstream error text would make this a
            # host/port oracle for whatever the container can reach.
            print(f"URL fetch failed for {req.url!r}: {type(e).__name__}: {e}", flush=True)
            raise HTTPException(400, "could not fetch that URL") from e
        text = text.strip()
        if len(text) < MIN_ARTIFACT_CHARS:
            raise HTTPException(
                400,
                f"could not find enough readable text on that page "
                f"(got {len(text)} characters, need at least {MIN_ARTIFACT_CHARS})",
            )
        source, url = "url", req.url
    else:
        text, source, url = req.text, "text", None

    personas = sample_stable(persona_count, personas=PERSONA_POOL)

    # Resolved before the run is registered, so a rejected ?cached=true
    # leaves nothing behind in RUNS or on disk.
    cached_data = _find_cached_example(text) if cached else None
    covered = 0
    if cached_data is not None and not _cache_covers(cached_data, personas):
        covered = len(cached_data.get("reactions", []))
        cached_data = None
    if cached and cached_data is None:
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
    # The resolved client address is logged so the rate-limit keying can
    # be checked against real traffic after a deploy.
    print(
        f"run {run_id} started: n={persona_count} source={source} "
        f"cached={cached_data is not None} client={client_ip}",
        flush=True,
    )

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
        # Subscribe BEFORE replaying the snapshot: an event broadcast in
        # between lands in both (a harmless duplicate), whereas subscribing
        # after could miss a terminal event and hang the connection.
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
            if run.status == "cancelled":
                yield _sse_event("cancelled", {"done": len(run.reactions)})
                return

            while True:
                event = await queue.get()
                yield event
                if event["event"] in ("done", "error", "cancelled"):
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


@app.post("/run/{run_id}/cancel")
async def cancel_run(run_id: str) -> dict:
    """Stops the server-side work, not just the client's stream: cancelling
    ctx.task propagates into fan_out, which cancels the in-flight reaction
    tasks, so spending stops within one call's latency."""
    ctx = RUNS.get(run_id)
    if ctx is None:
        raise HTTPException(404, "run not found")
    if ctx.run.status not in ACTIVE_STATUSES:
        # Idempotent: a double-click, or a cancel racing completion.
        return {"run_id": run_id, "status": ctx.run.status, "cancelled": False}
    if ctx.task is not None:
        ctx.task.cancel()
    # Marked here as well as in the task's handler, so this response
    # already shows the new status while the task unwinds.
    await _mark_cancelled(ctx)
    _persist(ctx)
    return {"run_id": run_id, "status": ctx.run.status, "cancelled": True}


@app.post("/compare")
async def compare_runs(request: Request, req: CompareRequest) -> CompareResult:
    ctx_a = RUNS.get(req.run_a)
    ctx_b = RUNS.get(req.run_b)
    if ctx_a is None or ctx_b is None:
        raise HTTPException(404, "one or both runs not found")
    if ctx_a.run.status != "done" or ctx_b.run.status != "done":
        raise HTTPException(409, "both runs must be done before comparing")
    if ctx_a.run.persona_set != ctx_b.run.persona_set:
        raise HTTPException(400, "both runs must use the same panel size")

    task = ctx_b.compares.get(req.run_a)
    if task is None or task.cancelled() or (task.done() and task.exception() is not None):
        _check_rate_limit(_client_ip(request), bucket="compare", limit=RATE_LIMIT_COMPARES)
        task = asyncio.create_task(delta(ctx_a.run, ctx_b.run))
        ctx_b.compares[req.run_a] = task
    # Shielded: one client disconnecting must not cancel a result another
    # request is waiting on.
    return await asyncio.shield(task)


@app.post("/check")
async def check_text(request: Request, req: CheckRequest) -> ArtifactCheck:
    """Advisory pre-submission check (panel/classify.py). Deliberately not
    called from inside POST /run: it advises, and the user decides."""
    _check_rate_limit(_client_ip(request), bucket="check", limit=RATE_LIMIT_CHECKS)
    return await check_artifact(req.text)


@app.get("/personas")
async def get_personas() -> list[Persona]:
    return PERSONA_POOL


# Human labels for the example buttons, keyed by filename stem; anything
# added to data/examples/ without a label here falls back to its stem.
EXAMPLE_LABELS = {
    "pricing_v1": "Pricing page v1",
    "ad_v1": "Fintech ad v1",
    "policy_v1": "Parking policy notice v1",
}


@app.get("/examples")
async def get_examples() -> list[dict]:
    return [
        {
            "id": path.stem,
            "label": EXAMPLE_LABELS.get(path.stem, path.stem),
            "text": path.read_text(),
        }
        for path in sorted(config.EXAMPLES_DIR.glob("*.txt"))
    ]


# Mounted last: Starlette matches in registration order, so a catch-all
# static mount registered first would shadow the API routes.
if config.WEB_DIST_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(config.WEB_DIST_DIR), html=True), name="web")
