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
from panel.market import (
    COMPETITOR_PAGES,
    TAVILY_API_KEY,
    build_comparison,
    derive_query,
    pick_pages,
    search_candidates,
)
from panel.fetch import UnsafeURLError, fetch_url_text
from panel.personas import load_personas, sample_stable
from panel.react import fan_out
from panel.schemas import (
    MIN_ARTIFACT_CHARS,
    Artifact,
    ArtifactCheck,
    BenchmarkRequest,
    CheckRequest,
    CompareRequest,
    CompareResult,
    MarketComparison,
    MarketPage,
    Persona,
    Reaction,
    Run,
    RunRequest,
    RunStats,
    Synthesis,
)
from panel.stats import mean_sentiment, usable, would_act_pct
from panel.synthesize import _apply_recounted_pct, synthesize, theme_breakdown
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
    # Who started it, for the one-active-run-per-client rule.
    client_ip: str = ""
    # This run's charge against the daily budgets; None for a cached replay.
    spend: _Spend | None = None


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

# --- Market benchmark -------------------------------------------------------

# Each competitor page is a real panel run, so this is the cost lever.
# 50 is enough for the theme stage to be stable (it shards at 10) and
# keeps a three-competitor benchmark at ~150 Nano calls, about 4 cents.
MARKET_PANEL_SIZE = int(config.env("MARKET_PANEL_SIZE") or "50")


@dataclass
class BenchmarkContext:
    benchmark_id: str
    run_id: str
    status: str = "pending"
    query: str = ""
    pages: list[MarketPage] = field(default_factory=list)
    comparison: MarketComparison | None = None
    error_message: str | None = None
    subscribers: list[asyncio.Queue] = field(default_factory=list)
    task: asyncio.Task | None = None
    spend: _Spend | None = None
    # Competitor-page reactions actually requested, for settling `spend`.
    personas_sent: int = 0
    # Who started it, for the one-active-job-per-client rule.
    client_ip: str = ""
    # Pages the panel has finished reading, so a reconnecting stream can
    # replay per-page progress rather than only the page list.
    pages_done: list[str] = field(default_factory=list)


# A benchmark still doing (and still paying for) work.
BENCHMARK_ACTIVE_STATUSES = ("pending", "searching", "reading", "comparing")


BENCHMARKS: dict[str, BenchmarkContext] = {}

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
# Ceilings on persona reactions in any rolling 24 hours, counting live
# runs and benchmarks only (a cached replay costs nothing). The service
# figure bounds the total, so the credits last until judging ends however
# many callers there are; the per-client figure stops one caller spending
# all of it. 10,000 is a hundred default-size runs a day; 2,000 is two
# full 1,000-persona runs per client.
DAILY_PERSONA_BUDGET = int(config.env("DAILY_PERSONA_BUDGET") or "10000")
DAILY_CLIENT_PERSONA_BUDGET = int(config.env("DAILY_CLIENT_PERSONA_BUDGET") or "2000")
# Service-wide daily ceilings on the two endpoints that cost a model call
# per request but aren't panel runs. A compare is one Ultra call per pair
# of runs, and pairs of free cached replays are unlimited without this.
DAILY_COMPARE_BUDGET = int(config.env("DAILY_COMPARE_BUDGET") or "500")
DAILY_CHECK_BUDGET = int(config.env("DAILY_CHECK_BUDGET") or "5000")
_DAY_S = 24 * 3600


@dataclass
class _Spend:
    """One live run's (or benchmark's) charge against the daily budgets.
    `personas` starts as the full reservation and is settled down to what
    was actually sent once the work stops, so a cancelled or failed run is
    charged for the calls it made, not the ones it never got to."""

    at: float
    client_ip: str
    personas: int


_LIVE_RUN_SPEND: list[_Spend] = []
_SERVICE_CALLS: dict[str, list[float]] = defaultdict(list)

# How many reverse proxies in front of this app append to X-Forwarded-For.
# 0 trusts nothing and keys rate limits on the socket peer, which is right
# for local runs. Behind Cloud Run the socket peer is Google's front end --
# the same address for every client -- so the deployment sets this to 1
# and the client is the address Google appended last. Entries further left
# are client-supplied and never trusted.
TRUSTED_PROXY_HOPS = int(config.env("TRUSTED_PROXY_HOPS") or "0")

_RATE_WINDOWS: dict[str, list[float]] = defaultdict(list)
# Clients with a POST /run URL fetch in progress (see create_run).
_FETCHING_CLIENTS: set[str] = set()
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


def _daily_spend(now: float, client_ip: str | None = None) -> int:
    _LIVE_RUN_SPEND[:] = [s for s in _LIVE_RUN_SPEND if now - s.at < _DAY_S]
    return sum(s.personas for s in _LIVE_RUN_SPEND if client_ip in (None, s.client_ip))


def _reserve_spend(client_ip: str, personas: int) -> _Spend:
    """Checks both daily persona budgets and reserves `personas` against
    them, or raises 429. Reserving the whole amount up front is what stops
    concurrent requests from each fitting under the ceiling separately."""
    now = time.monotonic()
    if _daily_spend(now) + personas > DAILY_PERSONA_BUDGET:
        raise HTTPException(
            429,
            "daily panel budget used up for this demo; try a smaller panel, or come back tomorrow",
        )
    if _daily_spend(now, client_ip) + personas > DAILY_CLIENT_PERSONA_BUDGET:
        raise HTTPException(
            429,
            f"you have used your daily allowance of {DAILY_CLIENT_PERSONA_BUDGET:,} persona "
            "reactions; try a smaller panel, or come back tomorrow",
        )
    spend = _Spend(at=now, client_ip=client_ip, personas=personas)
    _LIVE_RUN_SPEND.append(spend)
    return spend


def _settled_personas(reserved: int, answered: int, finished: bool) -> int:
    """What a run actually cost, in persona reactions. A run stopped early
    also paid for the calls in flight when it stopped (up to the
    concurrency cap), which never produced a reaction."""
    if finished:
        return reserved
    return min(reserved, answered + config.MAX_CONCURRENCY)


def _check_service_cap(bucket: str, cap: int) -> None:
    """Service-wide rolling-24h ceiling on one endpoint, across every
    client. Kept apart from _RATE_WINDOWS, whose hourly sweep would drop
    entries that still count here."""
    now = time.monotonic()
    window = _SERVICE_CALLS[bucket]
    window[:] = [t for t in window if now - t < _DAY_S]
    if len(window) >= cap:
        raise HTTPException(429, f"the demo's daily {bucket} budget is used up; try again tomorrow")
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


async def _finish(ctx: RunContext, synthesis: Synthesis | None, stats: RunStats) -> None:
    ctx.run.synthesis = synthesis
    ctx.run.stats = stats
    if synthesis is not None:
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
    await _end_with_error(ctx, "the run failed; see server logs for details")


async def _end_with_error(ctx: RunContext, message: str) -> None:
    """`message` goes out on the public stream, so it is always ours."""
    ctx.run.status = "error"
    ctx.error_message = message
    await _broadcast(ctx, "error", {"message": message})


# A report needs most of the panel to have answered. Below this, Ultra
# would be summarising a handful of people (or, if every call failed,
# nobody) while the headline speaks for the whole panel.
MIN_USABLE_SHARE = 0.5


def _too_few_answered(ctx: RunContext) -> str | None:
    answered = len(usable(ctx.run.reactions))
    total = len(ctx.personas)
    if answered >= total * MIN_USABLE_SHARE and answered > 0:
        return None
    return (
        f"only {answered} of {total} personas answered (the other model calls failed), "
        "too few for a report; try again in a minute"
    )


async def _execute_cached_run(ctx: RunContext, cached_data: dict, skip_synthesis: bool) -> None:
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

        stats = _compute_stats(reactions, {}, nano_ms_total=0, ultra_ms=0)
        if skip_synthesis:
            await _finish(ctx, None, stats)
            return
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
        await _finish(ctx, synthesis, stats)
    except asyncio.CancelledError:
        await _mark_cancelled(ctx)
        raise
    except Exception as e:  # noqa: BLE001
        await _fail_run(ctx, e)
    finally:
        _persist(ctx)


async def _execute_run(ctx: RunContext, skip_synthesis: bool) -> None:
    run = ctx.run
    calls = track_calls()
    try:
        async for reaction in fan_out(run.artifact.text, ctx.personas, run.run_id):
            await _record_reaction(ctx, reaction)

        def stats(ultra_ms: int = 0) -> RunStats:
            return _compute_stats(
                run.reactions,
                calls,
                nano_ms_total=sum(r.latency_ms or 0 for r in run.reactions),
                ultra_ms=ultra_ms,
            )

        # Checked for Run B of a compare too: a compare over a handful of
        # survivors would report flips the panel never made.
        shortfall = _too_few_answered(ctx)
        if shortfall:
            print(f"run {run.run_id}: {shortfall}", flush=True)
            run.stats = stats()
            await _end_with_error(ctx, shortfall)
            return

        synthesis = None
        synthesis_start = time.monotonic()
        if not skip_synthesis:
            await _start_synthesizing(ctx)
            synthesis = await synthesize(run.artifact.text, ctx.personas, run.reactions)
        await _finish(ctx, synthesis, stats(int((time.monotonic() - synthesis_start) * 1000)))
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
    """Whether a cached result was recorded for exactly this panel. A
    bigger panel would be half-served; a smaller one would replay a report
    whose headline, claims and evidence ids describe people who aren't on
    screen. Recounting fixes the percentages but not the prose."""
    have = {r["persona_id"] for r in cached_data.get("reactions", [])}
    return have == {p.id for p in personas}


def _cached_persona_count(example_stem: str) -> int | None:
    path = config.CACHED_DIR / f"{example_stem}.json"
    if not path.is_file():
        return None
    try:
        return len(json.loads(path.read_text()).get("reactions", []))
    except (OSError, ValueError):
        return None


def _active_run_count() -> int:
    """Runs and market benchmarks in flight. A benchmark is three panel
    reads at 50 concurrent calls each, so it takes a slot like a run."""
    runs = sum(1 for c in RUNS.values() if c.run.status in ACTIVE_STATUSES)
    benchmarks = sum(1 for b in BENCHMARKS.values() if b.status in BENCHMARK_ACTIVE_STATUSES)
    return runs + benchmarks


def _client_is_busy(client_ip: str) -> bool:
    return any(
        c.client_ip == client_ip and c.run.status in ACTIVE_STATUSES for c in RUNS.values()
    ) or any(
        b.client_ip == client_ip and b.status in BENCHMARK_ACTIVE_STATUSES
        for b in BENCHMARKS.values()
    )


@app.post("/run")
async def create_run(request: Request, req: RunRequest, cached: bool = False) -> dict:
    # Free checks first, so a request rejected for its own shape or for
    # server load doesn't use up the client's allowance.
    persona_count = req.persona_count or config.DEFAULT_PERSONA_COUNT
    ceiling = min(MAX_PERSONA_COUNT, len(PERSONA_POOL))
    if persona_count > ceiling:
        raise HTTPException(400, f"persona_count {persona_count} exceeds the limit ({ceiling})")

    client_ip = _client_ip(request)
    if req.replaces:
        previous = RUNS.get(req.replaces)
        if previous is not None and previous.run.status in ACTIVE_STATUSES:
            await _cancel(previous)
    # One run at a time per client, so a single caller can't fill every
    # service-wide slot and lock everyone else out for minutes.
    if _client_is_busy(client_ip):
        raise HTTPException(
            429,
            "you already have a run or market comparison in progress; "
            "wait for it to finish or cancel it",
        )
    # The run isn't registered until its page has been fetched, so the
    # rule above can't see a fetch in progress; without this one client
    # could hold a thread per request for the whole fetch timeout.
    if req.url and client_ip in _FETCHING_CLIENTS:
        raise HTTPException(429, "already fetching a page for you; wait for it to finish")
    # A cached replay makes no model calls, so the spend ceilings don't
    # apply to it -- it stays available as the fallback even when the
    # service is busy.
    if not cached and _active_run_count() >= MAX_CONCURRENT_RUNS:
        raise HTTPException(
            429, f"too many runs in flight (limit {MAX_CONCURRENT_RUNS}); try again shortly"
        )

    # Charged before the URL fetch and the cached lookup, so neither can
    # be probed for free.
    _check_rate_limit(client_ip)

    if req.url:
        _FETCHING_CLIENTS.add(client_ip)
        try:
            text = await asyncio.to_thread(fetch_url_text, req.url)
        except UnsafeURLError as e:
            raise HTTPException(400, str(e)) from e
        except Exception as e:  # noqa: BLE001 -- any fetch failure is a 400, not a crash
            # Not echoing str(e): upstream error text would make this a
            # host/port oracle for whatever the container can reach.
            print(f"URL fetch failed for {req.url!r}: {type(e).__name__}: {e}", flush=True)
            raise HTTPException(400, "could not fetch that URL") from e
        finally:
            _FETCHING_CLIENTS.discard(client_ip)
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

    # Last check before anything is registered: a refused run leaves
    # nothing behind, and the reservation can't be orphaned.
    spend = _reserve_spend(client_ip, persona_count) if cached_data is None else None

    run_id = _new_run_id()
    run = Run(
        run_id=run_id,
        artifact=Artifact(source=source, text=text, url=url, label=req.label),
        persona_set=f"v1-{persona_count}",
        status="pending",
    )
    ctx = RunContext(run=run, personas=personas, client_ip=client_ip, spend=spend)
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

    coro = (
        _execute_cached_run(ctx, cached_data, req.skip_synthesis)
        if cached_data is not None
        else _execute_run(ctx, req.skip_synthesis)
    )
    ctx.task = asyncio.create_task(coro)
    if spend is not None:
        # A done callback rather than the task's own finally: a task
        # cancelled before its first step never runs that finally.
        ctx.task.add_done_callback(lambda _t: _settle_run_spend(ctx))
    return {"run_id": run_id}


def _settle_run_spend(ctx: RunContext) -> None:
    if ctx.spend is not None:
        ctx.spend.personas = _settled_personas(
            ctx.spend.personas, len(ctx.run.reactions), ctx.run.status == "done"
        )


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


async def _cancel(ctx: RunContext) -> None:
    if ctx.task is not None:
        ctx.task.cancel()
    # Marked here as well as in the task's handler, so the caller already
    # sees the new status while the task unwinds.
    await _mark_cancelled(ctx)
    _persist(ctx)


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
    await _cancel(ctx)
    return {"run_id": run_id, "status": ctx.run.status, "cancelled": True}


async def _broadcast_benchmark(ctx: BenchmarkContext, event_type: str, data: object) -> None:
    event = _sse_event(event_type, data)
    for queue in list(ctx.subscribers):
        await queue.put(event)


async def _panel_on_text(text: str, personas: list[Persona], run_id: str) -> list[Reaction]:
    """A panel read with no Run record and no synthesis -- the benchmark
    only needs the reactions, and a competitor's page does not deserve a
    verdict of its own."""
    reactions: list[Reaction] = []
    async for reaction in fan_out(text, personas, run_id):
        reactions.append(reaction)
    return reactions


async def _execute_benchmark(ctx: BenchmarkContext) -> None:
    parent = RUNS.get(ctx.run_id)
    try:
        if parent is None:
            raise ValueError("the run this benchmark was started from is gone")

        # Same personas as the parent run, capped: apples to apples on who
        # read what, without a 1,000-persona parent making this cost four
        # times a full run.
        personas = parent.personas[:MARKET_PANEL_SIZE]
        own_text = parent.run.artifact.text

        ctx.status = "searching"
        await _broadcast_benchmark(ctx, "searching", {})
        market = await derive_query(own_text)
        ctx.query = market.query
        await _broadcast_benchmark(
            ctx, "query", {"category": market.category, "query": market.query}
        )

        candidates = await asyncio.to_thread(
            search_candidates, market.query, parent.run.artifact.url
        )
        picked = await pick_pages(candidates, market.category, COMPETITOR_PAGES)
        if not picked:
            # A real and correct outcome for some categories: search for
            # "meal planning app pricing" returns almost nothing but
            # listicles. Saying so beats comparing against a blog post.
            ctx.status = "no_pages"
            await _broadcast_benchmark(ctx, "no_pages", {"query": market.query})
            return

        pages = [
            MarketPage(
                label="Your page",
                is_yours=True,
                panel_size=len(personas),
                text=own_text,
            )
        ]
        fetched: list[tuple[MarketPage, str]] = []
        for candidate in picked:
            try:
                text = await asyncio.to_thread(fetch_url_text, candidate.url)
            except Exception as e:  # noqa: BLE001 -- one bad page is not fatal
                print(f"benchmark: skipping {candidate.url}: {type(e).__name__}: {e}")
                continue
            text = text.strip()
            # Same floor as URL input. A script-rendered page extracts to a
            # nav bar or nothing, and a panel reading that would report
            # "the page is empty" as a confusion the market shares.
            if len(text) < MIN_ARTIFACT_CHARS:
                print(f"benchmark: skipping {candidate.url}: only {len(text)} characters of text")
                continue
            page = MarketPage(
                label=_host_label(candidate.url),
                url=candidate.url,
                panel_size=len(personas),
                text=text,
            )
            pages.append(page)
            fetched.append((page, text))

        if not fetched:
            ctx.status = "no_pages"
            await _broadcast_benchmark(ctx, "no_pages", {"query": market.query})
            return

        ctx.pages = pages
        await _broadcast_benchmark(
            ctx, "pages", {"pages": [p.model_dump(mode="json") for p in pages]}
        )

        ctx.status = "reading"
        breakdowns: dict[str, dict] = {}
        # The parent's own reactions are already paid for; reuse them
        # rather than re-running the user's page.
        own_reactions = [
            r for r in parent.run.reactions if r.persona_id in {p.id for p in personas}
        ]
        breakdowns["Your page"] = await theme_breakdown(own_reactions)
        await _benchmark_page_done(ctx, "Your page")

        for page, text in fetched:
            ctx.personas_sent += len(personas)
            reactions = await _panel_on_text(text, personas, f"{ctx.benchmark_id}:{page.label}")
            answered = len(usable(reactions))
            if answered < len(personas) * MIN_USABLE_SHARE:
                # Same rule as a run's report: themes from a handful of
                # survivors would be compared as if they were the panel.
                print(
                    f"benchmark {ctx.benchmark_id}: dropping {page.label}, "
                    f"only {answered} of {len(personas)} answered"
                )
                pages.remove(page)
            else:
                breakdowns[page.label] = await theme_breakdown(reactions)
            await _benchmark_page_done(ctx, page.label)

        if len(pages) < 2:
            ctx.status = "no_pages"
            await _broadcast_benchmark(ctx, "no_pages", {"query": market.query})
            return

        ctx.status = "comparing"
        await _broadcast_benchmark(ctx, "comparing", {})
        ctx.comparison = await build_comparison(ctx.query, pages, breakdowns)
        ctx.status = "done"
        await _broadcast_benchmark(ctx, "comparison", ctx.comparison)
        await _broadcast_benchmark(ctx, "done", {})
    except asyncio.CancelledError:
        if ctx.status != "cancelled":
            ctx.status = "cancelled"
            await _broadcast_benchmark(ctx, "cancelled", {})
        raise
    except Exception as e:  # noqa: BLE001
        print(f"benchmark {ctx.benchmark_id} failed: {type(e).__name__}: {e}", flush=True)
        ctx.status = "error"
        ctx.error_message = "the market comparison failed; see server logs"
        await _broadcast_benchmark(ctx, "error", {"message": ctx.error_message})


async def _benchmark_page_done(ctx: BenchmarkContext, label: str) -> None:
    ctx.pages_done.append(label)
    await _broadcast_benchmark(ctx, "page_done", {"label": label})


def _host_label(url: str) -> str:
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or url).lower().removeprefix("www.")
    return host[:40]


@app.post("/benchmark")
async def create_benchmark(request: Request, req: BenchmarkRequest) -> dict:
    """Runs the panel over the user's copy and a few competitor pages.

    Rate-limited against the run bucket, not the cheap check bucket: this
    is three panel runs plus Tavily calls, so it costs more than a run,
    not less.
    """
    if not TAVILY_API_KEY:
        raise HTTPException(503, "the market comparison is not configured on this server")
    parent = RUNS.get(req.run_id)
    if parent is None:
        raise HTTPException(404, "run not found")
    if parent.run.status != "done":
        raise HTTPException(409, "the run must finish before comparing it to the market")
    if not parent.run.reactions:
        raise HTTPException(409, "that run has no reactions to compare")
    client_ip = _client_ip(request)
    if _client_is_busy(client_ip):
        raise HTTPException(
            429,
            "you already have a run or market comparison in progress; "
            "wait for it to finish or cancel it",
        )
    if _active_run_count() >= MAX_CONCURRENT_RUNS:
        raise HTTPException(429, "too many runs in flight; try again shortly")
    _check_rate_limit(client_ip)
    # Reserved for the most it can read; settled to the pages it actually
    # ran once it stops (a search can find fewer pages, or none).
    spend = _reserve_spend(
        client_ip, COMPETITOR_PAGES * min(MARKET_PANEL_SIZE, len(parent.personas))
    )

    # Unguessable for the same reason run ids are (see _new_run_id): a
    # benchmark id is the only credential for reading the comparison,
    # which embeds the user's own copy's themes.
    benchmark_id = f"b_{secrets.token_urlsafe(16)}"
    ctx = BenchmarkContext(
        benchmark_id=benchmark_id, run_id=req.run_id, spend=spend, client_ip=client_ip
    )
    BENCHMARKS[benchmark_id] = ctx
    _evict_old_benchmarks()
    ctx.task = asyncio.create_task(_execute_benchmark(ctx))
    ctx.task.add_done_callback(
        lambda _t: setattr(spend, "personas", min(spend.personas, ctx.personas_sent))
    )
    return {"benchmark_id": benchmark_id}


def _evict_old_benchmarks() -> None:
    """Oldest finished benchmarks first, as _evict_old_runs does for runs.
    A running one is never dropped: BENCHMARKS holds the only strong
    reference to its context, and with it the task's."""
    removable = [
        bid
        for bid, b in BENCHMARKS.items()
        if b.status not in BENCHMARK_ACTIVE_STATUSES and not b.subscribers
    ]
    for bid in removable[: max(0, len(BENCHMARKS) - MAX_RETAINED_RUNS)]:
        del BENCHMARKS[bid]


@app.post("/benchmark/{benchmark_id}/cancel")
async def cancel_benchmark(benchmark_id: str) -> dict:
    """Stops the server-side work, as POST /run/{id}/cancel does: closing
    the stream alone leaves every remaining page read running."""
    ctx = BENCHMARKS.get(benchmark_id)
    if ctx is None:
        raise HTTPException(404, "benchmark not found")
    if ctx.status not in BENCHMARK_ACTIVE_STATUSES:
        return {"benchmark_id": benchmark_id, "status": ctx.status, "cancelled": False}
    if ctx.task is not None:
        ctx.task.cancel()
    # Marked here too, so the caller sees the new status while it unwinds.
    if ctx.status != "cancelled":
        ctx.status = "cancelled"
        await _broadcast_benchmark(ctx, "cancelled", {})
    return {"benchmark_id": benchmark_id, "status": ctx.status, "cancelled": True}


@app.get("/benchmark/{benchmark_id}")
async def get_benchmark(benchmark_id: str) -> dict:
    ctx = BENCHMARKS.get(benchmark_id)
    if ctx is None:
        raise HTTPException(404, "benchmark not found")
    return {
        "benchmark_id": ctx.benchmark_id,
        "run_id": ctx.run_id,
        "status": ctx.status,
        "query": ctx.query,
        "pages": [p.model_dump(mode="json") for p in ctx.pages],
        "comparison": ctx.comparison.model_dump(mode="json") if ctx.comparison else None,
        "error": ctx.error_message,
    }


@app.get("/benchmark/{benchmark_id}/stream")
async def stream_benchmark(benchmark_id: str) -> EventSourceResponse:
    ctx = BENCHMARKS.get(benchmark_id)
    if ctx is None:
        raise HTTPException(404, "benchmark not found")

    async def generator():
        queue: asyncio.Queue = asyncio.Queue()
        ctx.subscribers.append(queue)
        try:
            # Replay enough for a reconnect to catch up, same
            # subscribe-before-replay ordering as stream_run.
            if ctx.query:
                yield _sse_event("query", {"query": ctx.query})
            if ctx.pages:
                yield _sse_event(
                    "pages", {"pages": [p.model_dump(mode="json") for p in ctx.pages]}
                )
            for label in list(ctx.pages_done):
                yield _sse_event("page_done", {"label": label})
            if ctx.status == "comparing":
                yield _sse_event("comparing", {})
            if ctx.comparison is not None:
                yield _sse_event("comparison", ctx.comparison)
            if ctx.status in ("done", "error", "no_pages", "cancelled"):
                yield _sse_event(ctx.status, {"message": ctx.error_message or ""})
                return
            while True:
                event = await queue.get()
                yield event
                if event["event"] in ("done", "error", "no_pages", "cancelled"):
                    break
        finally:
            if queue in ctx.subscribers:
                ctx.subscribers.remove(queue)

    return EventSourceResponse(generator())


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
        _check_service_cap("compare", DAILY_COMPARE_BUDGET)
        task = asyncio.create_task(delta(ctx_a.run, ctx_b.run))
        ctx_b.compares[req.run_a] = task
    # Shielded: one client disconnecting must not cancel a result another
    # request is waiting on.
    try:
        return await asyncio.shield(task)
    except Exception as e:  # noqa: BLE001 -- a failed summary call is a 502, not a crash
        # Logged in full, reported generically (same reasoning as
        # _fail_run). The failed task isn't reused, so a retry recomputes.
        print(f"compare {req.run_a} -> {req.run_b} failed: {type(e).__name__}: {e}", flush=True)
        raise HTTPException(502, "the comparison could not be summarised; try again") from e


@app.post("/check")
async def check_text(request: Request, req: CheckRequest) -> ArtifactCheck:
    """Advisory pre-submission check (panel/classify.py). Deliberately not
    called from inside POST /run: it advises, and the user decides."""
    _check_rate_limit(_client_ip(request), bucket="check", limit=RATE_LIMIT_CHECKS)
    _check_service_cap("check", DAILY_CHECK_BUDGET)
    return await check_artifact(req.text)


@app.get("/features")
async def get_features() -> dict:
    """What this deployment can do, so the UI hides what it can't rather
    than offering a button that fails."""
    return {"market": bool(TAVILY_API_KEY)}


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
            # The one panel size its cached fallback can replay (see
            # _cache_covers), so the client only offers it there.
            "cached_persona_count": _cached_persona_count(path.stem),
        }
        for path in sorted(config.EXAMPLES_DIR.glob("*.txt"))
    ]


# Mounted last: Starlette matches in registration order, so a catch-all
# static mount registered first would shadow the API routes.
if config.WEB_DIST_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(config.WEB_DIST_DIR), html=True), name="web")
