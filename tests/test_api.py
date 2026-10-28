"""Route-level tests for api/main.py using FastAPI's TestClient.

Deliberately avoids the POST /run success path: that starts a background
task that makes real Nano/Ultra calls, so asserting on it would spend real
credits every test run. Input validation, lookups, and the error paths all
return before any model call and are what actually tend to break.
"""

import pytest
from fastapi.testclient import TestClient

from api.main import MAX_PERSONA_COUNT, PERSONA_POOL, RUNS, RunContext, app
from panel.schemas import Artifact, Run

client = TestClient(app)

LONG_ENOUGH = "x" * 60  # RunRequest requires 50-20,000 chars


@pytest.fixture(autouse=True)
def _reset_server_state():
    """The rate limiter and RUNS are process-global, and every POST /run in
    this file counts against the per-client window -- without this, adding
    a seventh POST test would make an unrelated test start getting 429s.
    """
    from api.main import _LIVE_RUN_SPEND, _RATE_WINDOWS, _SERVICE_CALLS

    for state in (_RATE_WINDOWS, _LIVE_RUN_SPEND, _SERVICE_CALLS, RUNS):
        state.clear()
    yield
    for state in (_RATE_WINDOWS, _LIVE_RUN_SPEND, _SERVICE_CALLS, RUNS):
        state.clear()


def test_personas_returns_the_whole_pool():
    r = client.get("/personas")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == len(PERSONA_POOL)
    assert all("id" in p and "skepticism" in p for p in body)


def test_personas_are_on_the_1_100_scale():
    """All seven numeric traits share the 1-100 scale."""
    body = client.get("/personas").json()
    traits = [
        "tech_comfort",
        "skepticism",
        "openness",
        "conscientiousness",
        "extraversion",
        "agreeableness",
        "neuroticism",
    ]
    for p in body:
        for t in traits:
            assert 1 <= p[t] <= 100, f"{p['id']}.{t} = {p[t]}"


def test_examples_returns_three_with_text():
    r = client.get("/examples")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 3
    assert all(e["text"].strip() for e in body)
    assert {e["id"] for e in body} == {"pricing_v1", "ad_v1", "policy_v1"}


def test_run_requires_text_or_url():
    assert client.post("/run", json={}).status_code == 422


def test_run_rejects_text_under_the_minimum():
    assert client.post("/run", json={"text": "too short"}).status_code == 422


def test_run_rejects_text_over_the_maximum():
    assert client.post("/run", json={"text": "x" * 20_001}).status_code == 422


def test_run_rejects_persona_count_below_ten():
    r = client.post("/run", json={"text": LONG_ENOUGH, "persona_count": 5})
    assert r.status_code == 422


def test_run_rejects_persona_count_above_the_ceiling_with_a_clear_message():
    """The ceiling is min(MAX_PERSONA_COUNT, pool size) -- the deploy-time
    cap exists so one unauthenticated POST can't ask for the whole pool
    repeatedly."""
    r = client.post("/run", json={"text": LONG_ENOUGH, "persona_count": len(PERSONA_POOL) + 1})
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "exceeds the limit" in detail
    assert str(min(MAX_PERSONA_COUNT, len(PERSONA_POOL))) in detail


def test_run_with_cached_true_rejects_an_artifact_with_no_cache():
    """?cached=true is matched against the real example files, so an
    arbitrary artifact can't be used to serve someone else's canned
    result."""
    r = client.post("/run?cached=true", json={"text": LONG_ENOUGH})
    assert r.status_code == 404
    assert "cached" in r.json()["detail"]


def test_unknown_run_id_is_404():
    assert client.get("/run/does-not-exist").status_code == 404
    assert client.get("/run/does-not-exist/stream").status_code == 404


def test_compare_unknown_runs_is_404():
    r = client.post("/compare", json={"run_a": "nope-a", "run_b": "nope-b"})
    assert r.status_code == 404


def test_compare_requires_both_runs_done():
    """Comparing mid-run would diff against a partial reaction set."""
    for run_id, status in (("t-a", "done"), ("t-b", "streaming")):
        run = Run(
            run_id=run_id,
            artifact=Artifact(source="text", text=LONG_ENOUGH),
            persona_set="v1-10",
            status=status,
        )
        RUNS[run_id] = RunContext(run=run, personas=PERSONA_POOL[:10])
    try:
        r = client.post("/compare", json={"run_a": "t-a", "run_b": "t-b"})
        assert r.status_code == 409
        assert "done" in r.json()["detail"]
    finally:
        RUNS.pop("t-a", None)
        RUNS.pop("t-b", None)


def test_get_run_returns_the_stored_record():
    run = Run(
        run_id="t-get",
        artifact=Artifact(source="text", text=LONG_ENOUGH, label="a label"),
        persona_set="v1-10",
        status="done",
    )
    RUNS["t-get"] = RunContext(run=run, personas=PERSONA_POOL[:10])
    try:
        body = client.get("/run/t-get").json()
        assert body["run_id"] == "t-get"
        assert body["artifact"]["label"] == "a label"
        assert body["status"] == "done"
    finally:
        RUNS.pop("t-get", None)


# --- Run retention (added with the 1000-persona scale-up, 2026-10-28) -------


def _finished_ctx(rid, status="done"):
    run = Run(
        run_id=rid,
        artifact=Artifact(source="text", text=LONG_ENOUGH),
        persona_set="v1-10",
        status=status,
    )
    return RunContext(run=run, personas=[])


def test_eviction_drops_oldest_finished_runs():
    from api.main import MAX_RETAINED_RUNS, _evict_old_runs

    RUNS.clear()
    for i in range(MAX_RETAINED_RUNS + 10):
        RUNS[f"r{i:03d}"] = _finished_ctx(f"r{i:03d}")
    _evict_old_runs()
    assert len(RUNS) == MAX_RETAINED_RUNS
    # Oldest gone, newest kept.
    assert "r000" not in RUNS
    assert f"r{MAX_RETAINED_RUNS + 9:03d}" in RUNS
    RUNS.clear()


def test_eviction_never_drops_an_unfinished_run():
    """An in-flight run is the one thing in RUNS that cannot be recovered
    from disk, so it has to outlive a flood of newer requests."""
    from api.main import MAX_RETAINED_RUNS, _evict_old_runs

    RUNS.clear()
    RUNS["streaming-one"] = _finished_ctx("streaming-one", status="streaming")
    for i in range(MAX_RETAINED_RUNS + 10):
        RUNS[f"r{i:03d}"] = _finished_ctx(f"r{i:03d}")
    _evict_old_runs()
    assert "streaming-one" in RUNS
    RUNS.clear()


def test_eviction_never_drops_a_run_with_a_live_subscriber():
    import asyncio

    from api.main import MAX_RETAINED_RUNS, _evict_old_runs

    RUNS.clear()
    watched = _finished_ctx("watched")
    watched.subscribers.append(asyncio.Queue())
    RUNS["watched"] = watched
    for i in range(MAX_RETAINED_RUNS + 10):
        RUNS[f"r{i:03d}"] = _finished_ctx(f"r{i:03d}")
    _evict_old_runs()
    assert "watched" in RUNS
    RUNS.clear()


# --- Public-deployment spend limits ----------------------------------------
#
# The service is deployed unauthenticated for six weeks of judging and
# every run spends real credits, so these guards are load-bearing rather
# than hygiene.


def test_rejected_cached_request_leaves_nothing_behind():
    """A failed ?cached=true used to register the run and write its JSON
    before raising, so repeating it filled RUNS and RUNS_DIR for free."""
    RUNS.clear()
    before = len(RUNS)
    r = client.post("/run?cached=true", json={"text": "no example looks like this " * 3})
    assert r.status_code == 404
    assert len(RUNS) == before
    RUNS.clear()


def test_rate_limit_rejects_a_flood_from_one_client():
    from api.main import RATE_LIMIT_RUNS

    RUNS.clear()
    # Every request here fails for its own reason (no cached example), but
    # the rate limiter runs first, which is the point: it must not be
    # possible to probe the expensive path for free.
    codes = [
        client.post("/run?cached=true", json={"text": LONG_ENOUGH}).status_code
        for _ in range(RATE_LIMIT_RUNS + 2)
    ]
    assert codes[-1] == 429
    assert codes.count(429) >= 2
    RUNS.clear()


def test_concurrency_limit_rejects_when_runs_are_in_flight():
    from api.main import MAX_CONCURRENT_RUNS

    RUNS.clear()
    for i in range(MAX_CONCURRENT_RUNS):
        RUNS[f"busy{i}"] = _finished_ctx(f"busy{i}", status="streaming")
    r = client.post("/run", json={"text": LONG_ENOUGH})
    assert r.status_code == 429
    assert "in flight" in r.json()["detail"]
    RUNS.clear()


def test_cache_coverage_is_required():
    """A cached file recorded at 100 personas must not be replayed into a
    bigger grid -- it would stream 100 reactions and report progress
    against the wrong denominator."""
    from api.main import _cache_covers

    # Pool file order is shuffled, so take the ids from the pool itself
    # rather than assuming p001/p002 come first.
    covered = PERSONA_POOL[:2]
    cached = {"reactions": [{"persona_id": p.id} for p in covered]}
    assert _cache_covers(cached, covered)
    assert not _cache_covers(cached, PERSONA_POOL[:3])
    # Nor into a smaller one: the report's prose and evidence ids describe
    # the whole recorded panel, and recounting only fixes the numbers.
    assert not _cache_covers(cached, PERSONA_POOL[:1])


def test_examples_say_which_panel_size_their_cache_replays():
    for ex in client.get("/examples").json():
        assert "cached_persona_count" in ex


def test_unsafe_url_is_rejected_before_any_fetch():
    RUNS.clear()
    r = client.post("/run", json={"url": "http://169.254.169.254/latest/meta-data/"})
    assert r.status_code == 400
    assert "non-public" in r.json()["detail"]
    assert len(RUNS) == 0


def test_a_failed_run_does_not_leak_the_exception_text():
    import asyncio

    from api.main import _fail_run

    ctx = _finished_ctx("leaky", status="streaming")
    asyncio.run(_fail_run(ctx, RuntimeError("TF_API_KEY=sk-secret-123 rejected by host db-7")))
    assert "sk-secret" not in (ctx.error_message or "")
    assert ctx.run.status == "error"


def test_cached_404_names_the_size_the_fallback_covers():
    """This path is only reached after a live run has already failed on a
    judge, so the message has to say what would work, not just refuse."""
    import json

    from api import main as M

    example = next(M.config.EXAMPLES_DIR.glob("*.txt"))
    cache_path = M.config.CACHED_DIR / f"{example.stem}.json"
    if not cache_path.is_file():
        import pytest as _pytest

        _pytest.skip("no cached example on disk")
    covered = len(json.loads(cache_path.read_text())["reactions"])

    r = client.post(
        "/run?cached=true",
        json={"text": example.read_text(), "persona_count": covered + 10},
    )
    assert r.status_code == 404
    detail = r.json()["detail"]
    assert str(covered) in detail and str(covered + 10) in detail


def test_cached_replay_reports_zero_cost(monkeypatch):
    """A replay makes no model calls, so every cost figure must read zero,
    even though the cached reactions carry latencies recorded on the
    original run. Drives the real replay path end to end."""
    import asyncio
    import json

    from api import main as M

    example = next(M.config.EXAMPLES_DIR.glob("*.txt"))
    cache_path = M.config.CACHED_DIR / f"{example.stem}.json"
    if not cache_path.is_file():
        pytest.skip("no cached example on disk")
    cached = json.loads(cache_path.read_text())
    ids = {r["persona_id"] for r in cached["reactions"]}
    # The recorded latencies are real, or this test would pass for the
    # wrong reason.
    assert sum(r.get("latency_ms") or 0 for r in cached["reactions"]) > 0

    ctx = _finished_ctx("replay", status="pending")
    ctx.run.reactions = []
    ctx.personas = [p for p in PERSONA_POOL if p.id in ids]
    monkeypatch.setattr(M, "CACHED_REACTION_INTERVAL_S", 0)
    monkeypatch.setattr(M, "_persist", lambda ctx: None)
    asyncio.run(M._execute_cached_run(ctx, cached, skip_synthesis=False))

    stats = ctx.run.stats
    assert ctx.run.status == "done"
    assert (stats.nano_calls, stats.super_calls, stats.ultra_calls) == (0, 0, 0)
    assert stats.nano_ms_total == 0 and stats.ultra_ms == 0
    # The sentiment figures stay real -- they describe what is on screen.
    assert stats.mean_sentiment != 0
    assert ctx.run.synthesis is not None


# --- Pre-submission check (POST /check) ------------------------------------


def test_check_rejects_text_below_the_run_minimum():
    """Same bounds as RunRequest.text, so anything /run would accept can be
    checked first and nothing else wastes a model call."""
    assert client.post("/check", json={"text": "too short"}).status_code == 422
    assert client.post("/check", json={"text": "x" * 20_001}).status_code == 422
    assert client.post("/check", json={}).status_code == 422


def test_check_has_its_own_rate_limit_bucket():
    """A check is one Nano call, roughly a thousandth of a run. Spending
    checks must not leave a user unable to run."""
    from api.main import RATE_LIMIT_CHECKS, RATE_LIMIT_RUNS, _check_rate_limit

    assert RATE_LIMIT_CHECKS > RATE_LIMIT_RUNS
    for _ in range(RATE_LIMIT_RUNS):
        _check_rate_limit("1.2.3.4", bucket="check", limit=RATE_LIMIT_CHECKS)
    # The run bucket is untouched by all of that.
    _check_rate_limit("1.2.3.4", bucket="run")


def test_check_rate_limit_eventually_refuses():
    import pytest as _pytest
    from fastapi import HTTPException

    from api.main import _check_rate_limit

    for _ in range(3):
        _check_rate_limit("9.9.9.9", bucket="check", limit=3)
    with _pytest.raises(HTTPException) as exc:
        _check_rate_limit("9.9.9.9", bucket="check", limit=3)
    assert exc.value.status_code == 429
    assert "check requests" in exc.value.detail


def test_check_fails_open_when_the_model_call_fails(monkeypatch):
    """A broken checker must never cost someone a legitimate run, so every
    failure resolves to is_reviewable=True."""
    import asyncio

    from panel import classify

    async def boom(**kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(classify, "chat_json", boom)
    verdict = asyncio.run(classify.check_artifact("x" * 60))
    assert verdict.is_reviewable is True
    assert "could not run" in verdict.reason


def test_check_returns_the_models_verdict_when_it_works(monkeypatch):
    import asyncio

    from panel import classify
    from panel.schemas import ArtifactCheck
    from panel.tf_client import ChatJsonResult

    async def fake(**kwargs):
        return ChatJsonResult(
            data=ArtifactCheck(
                kind="Python source code",
                is_reviewable=False,
                reason="This is code, which readers cannot meaningfully react to.",
            ),
            latency_ms=5,
            attempts=1,
        )

    monkeypatch.setattr(classify, "chat_json", fake)
    verdict = asyncio.run(classify.check_artifact("def f(): pass\n" * 10))
    assert verdict.is_reviewable is False
    assert verdict.kind == "Python source code"


# --- Cancelling a run --------------------------------------------------------
#
# The frontend's cancel used to only close its EventSource, so the server
# carried on through every remaining Nano call and the synthesis -- at
# 1,000 personas, most of a run's cost spent on a result nobody would see.


def test_cancel_unknown_run_is_404():
    assert client.post("/run/does-not-exist/cancel").status_code == 404


def test_cancel_marks_the_run_and_cancels_its_task():
    import asyncio

    async def scenario():
        ctx = _finished_ctx("to-cancel", status="streaming")
        started = asyncio.Event()

        async def long_run():
            started.set()
            await asyncio.sleep(3600)

        ctx.task = asyncio.create_task(long_run())
        await started.wait()
        RUNS["to-cancel"] = ctx

        from api.main import cancel_run

        result = await cancel_run("to-cancel")
        assert result["cancelled"] is True
        assert result["status"] == "cancelled"
        assert ctx.run.status == "cancelled"
        # The driving task is actually stopped, which is what stops the spend.
        await asyncio.sleep(0)
        assert ctx.task.cancelled() or ctx.task.done()

    asyncio.run(scenario())
    RUNS.clear()


def test_cancel_is_idempotent():
    """A double-click, or a cancel racing the run's own completion, is not
    an error."""
    import asyncio

    from api.main import cancel_run

    RUNS["already"] = _finished_ctx("already", status="done")
    result = asyncio.run(cancel_run("already"))
    assert result["cancelled"] is False
    assert result["status"] == "done"
    RUNS.clear()


def test_cancelled_runs_do_not_count_against_the_concurrency_limit():
    from api.main import MAX_CONCURRENT_RUNS, _active_run_count

    RUNS.clear()
    for i in range(MAX_CONCURRENT_RUNS + 2):
        RUNS[f"c{i}"] = _finished_ctx(f"c{i}", status="cancelled")
    assert _active_run_count() == 0
    r = client.post("/run", json={"text": LONG_ENOUGH, "persona_count": 10})
    # Not a 429 -- cancelled runs are finished runs.
    assert r.status_code != 429
    RUNS.clear()


def test_a_cancelled_run_is_evictable():
    from api.main import MAX_RETAINED_RUNS, _evict_old_runs

    RUNS.clear()
    for i in range(MAX_RETAINED_RUNS + 5):
        RUNS[f"c{i:03d}"] = _finished_ctx(f"c{i:03d}", status="cancelled")
    _evict_old_runs()
    assert len(RUNS) == MAX_RETAINED_RUNS
    RUNS.clear()


# --- Spend and privacy controls ----------------------------------------------


def test_run_rejects_text_and_url_together():
    r = client.post("/run", json={"text": LONG_ENOUGH, "url": "https://example.com"})
    assert r.status_code == 422


def test_run_ids_are_unguessable():
    from api.main import _new_run_id

    ids = {_new_run_id() for _ in range(200)}
    assert len(ids) == 200
    assert all(i.startswith("r_") and len(i) >= 20 for i in ids)


def test_client_ip_ignores_forwarded_for_by_default(monkeypatch):
    from api import main as M

    monkeypatch.setattr(M, "TRUSTED_PROXY_HOPS", 0)
    req = type("R", (), {"headers": {"x-forwarded-for": "6.6.6.6"}, "client": type("C", (), {"host": "10.0.0.1"})()})()
    assert M._client_ip(req) == "10.0.0.1"


def test_client_ip_takes_the_proxy_appended_hop_not_a_spoofed_one(monkeypatch):
    """Behind Cloud Run every request arrives from Google's front end, so
    the socket peer would put every visitor in one rate-limit window. The
    trusted hop is the rightmost entry; anything to its left came from the
    client and is ignored."""
    from api import main as M

    monkeypatch.setattr(M, "TRUSTED_PROXY_HOPS", 1)
    peer = type("C", (), {"host": "169.254.1.1"})()
    spoofed = type("R", (), {"headers": {"x-forwarded-for": "1.1.1.1, 203.0.113.7"}, "client": peer})()
    assert M._client_ip(spoofed) == "203.0.113.7"
    missing = type("R", (), {"headers": {}, "client": peer})()
    assert M._client_ip(missing) == "169.254.1.1"


def test_rejected_persona_count_does_not_use_up_the_allowance():
    from api.main import RATE_LIMIT_RUNS, _RATE_WINDOWS

    for _ in range(RATE_LIMIT_RUNS + 2):
        r = client.post("/run", json={"text": LONG_ENOUGH, "persona_count": len(PERSONA_POOL) + 1})
        assert r.status_code == 400
    assert not any(_RATE_WINDOWS.values())


def test_url_with_too_little_text_is_a_clear_400(monkeypatch):
    from api import main as M

    monkeypatch.setattr(M, "fetch_url_text", lambda url: "  tiny  ")
    r = client.post("/run", json={"url": "https://example.com"})
    assert r.status_code == 400
    assert "readable text" in r.json()["detail"]


def test_compare_is_computed_once_per_pair(monkeypatch):
    """Each compare is an Ultra call on an unauthenticated endpoint, so a
    repeated request must reuse the first result rather than pay again."""
    from api import main as M
    from panel.schemas import CompareResult

    calls = []

    async def fake_delta(a, b):
        calls.append((a.run_id, b.run_id))
        return CompareResult(
            run_a=a.run_id, run_b=b.run_id, deltas=[], compared=0, flipped_positive=0,
            flipped_negative=0, would_act_delta_pct=0.0, summary="s",
        )

    monkeypatch.setattr(M, "delta", fake_delta)
    RUNS["ra"] = _finished_ctx("ra")
    RUNS["rb"] = _finished_ctx("rb")
    for _ in range(5):
        r = client.post("/compare", json={"run_a": "ra", "run_b": "rb"})
        assert r.status_code == 200
    assert calls == [("ra", "rb")]


def test_compare_has_a_rate_limit(monkeypatch):
    from api import main as M

    monkeypatch.setattr(M, "RATE_LIMIT_COMPARES", 2)

    async def fake_delta(a, b):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(M, "delta", fake_delta)
    RUNS["ra"] = _finished_ctx("ra")
    RUNS["rb"] = _finished_ctx("rb")
    # A failed compare is a readable 502, is retried (not cached), and each
    # retry is charged.
    codes = [client.post("/compare", json={"run_a": "ra", "run_b": "rb"}).status_code for _ in range(3)]
    assert codes == [502, 502, 429]


def test_no_cors_headers_by_default():
    r = client.get("/examples", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}


def test_one_client_cannot_hold_two_runs_at_once():
    """Otherwise one caller can fill every service-wide slot with
    1,000-persona runs and lock every other visitor out for minutes."""
    busy = _finished_ctx("mine", status="streaming")
    busy.client_ip = "testclient"
    RUNS["mine"] = busy
    r = client.post("/run", json={"text": LONG_ENOUGH})
    assert r.status_code == 429
    assert "already have a run" in r.json()["detail"]


def test_cached_replay_is_not_blocked_by_a_busy_service():
    """The cached fallback makes no model calls, so it must still work
    when other people's runs fill every slot."""
    from api.main import MAX_CONCURRENT_RUNS

    for i in range(MAX_CONCURRENT_RUNS):
        ctx = _finished_ctx(f"busy{i}", status="streaming")
        ctx.client_ip = f"10.0.0.{i}"
        RUNS[f"busy{i}"] = ctx
    example = client.get("/examples").json()[0]
    r = client.post("/run?cached=true", json={"text": example["text"], "persona_count": 100})
    assert r.status_code == 200


def test_daily_budget_refuses_live_runs_once_spent(monkeypatch):
    from api import main as M

    monkeypatch.setattr(M, "DAILY_PERSONA_BUDGET", 150)
    M._LIVE_RUN_SPEND.append(M._Spend(at=__import__("time").monotonic(), client_ip="other", personas=100))
    r = client.post("/run", json={"text": LONG_ENOUGH, "persona_count": 100})
    assert r.status_code == 429
    assert "daily panel budget" in r.json()["detail"]
    # A smaller panel still fits.
    monkeypatch.setattr(M, "_execute_run", lambda ctx, skip: __import__("asyncio").sleep(0))
    assert client.post("/run", json={"text": LONG_ENOUGH, "persona_count": 50}).status_code == 200


def test_one_client_cannot_spend_the_whole_daily_budget(monkeypatch):
    """The service budget alone let one caller exhaust it for everyone."""
    from api import main as M

    monkeypatch.setattr(M, "DAILY_CLIENT_PERSONA_BUDGET", 150)
    M._LIVE_RUN_SPEND.append(
        M._Spend(at=__import__("time").monotonic(), client_ip="testclient", personas=100)
    )
    r = client.post("/run", json={"text": LONG_ENOUGH, "persona_count": 100})
    assert r.status_code == 429
    assert "daily allowance" in r.json()["detail"]
    # Another client's spend doesn't count against this one.
    M._LIVE_RUN_SPEND[0].client_ip = "someone-else"
    monkeypatch.setattr(M, "_execute_run", lambda ctx, skip: __import__("asyncio").sleep(0))
    assert client.post("/run", json={"text": LONG_ENOUGH, "persona_count": 100}).status_code == 200


def test_a_cancelled_run_is_charged_for_what_it_sent_not_what_it_reserved():
    """Charging the full reservation let a chain of cancelled 1,000-persona
    runs burn the day's budget in seconds while spending almost nothing."""
    import asyncio

    from api import main as M

    async def scenario():
        ctx = _finished_ctx("partial", status="streaming")
        ctx.personas = PERSONA_POOL[:1000]
        ctx.spend = M._reserve_spend("testclient", 1000)

        async def never_finishes():
            await asyncio.sleep(3600)

        ctx.task = asyncio.create_task(never_finishes())
        ctx.task.add_done_callback(lambda _t: M._settle_run_spend(ctx))
        await asyncio.sleep(0)
        await M._cancel(ctx)
        await asyncio.gather(ctx.task, return_exceptions=True)
        await asyncio.sleep(0)
        return ctx

    ctx = asyncio.run(scenario())
    # Two reactions in (from _finished_ctx) plus up to a full batch in flight.
    assert ctx.spend.personas == len(ctx.run.reactions) + M.config.MAX_CONCURRENCY
    assert M._settled_personas(1000, 1000, finished=True) == 1000
    assert M._settled_personas(30, 2, finished=False) == 30


def test_compare_and_check_have_service_wide_daily_caps(monkeypatch):
    from api import main as M

    monkeypatch.setattr(M, "DAILY_CHECK_BUDGET", 0)
    r = client.post("/check", json={"text": LONG_ENOUGH})
    assert r.status_code == 429
    assert "daily check budget" in r.json()["detail"]

    monkeypatch.setattr(M, "DAILY_COMPARE_BUDGET", 0)
    RUNS["a"] = _finished_ctx("a")
    RUNS["b"] = _finished_ctx("b")
    r = client.post("/compare", json={"run_a": "a", "run_b": "b"})
    assert r.status_code == 429
    assert "daily compare budget" in r.json()["detail"]
    M._SERVICE_CALLS.clear()


def test_a_url_fetch_in_progress_blocks_a_second_from_the_same_client():
    from api import main as M

    M._FETCHING_CLIENTS.add("testclient")
    try:
        r = client.post("/run", json={"url": "https://example.com/"})
        assert r.status_code == 429
        assert "already fetching" in r.json()["detail"]
    finally:
        M._FETCHING_CLIENTS.clear()


def test_no_report_is_written_when_most_of_the_panel_failed(monkeypatch):
    """Ultra used to synthesize even when every reaction had failed,
    producing a confident report about nobody."""
    import asyncio

    from api import main as M
    from panel.react import REACTION_FAILED

    ctx = _finished_ctx("broken", status="streaming")
    ctx.personas = PERSONA_POOL[:10]
    ctx.run.reactions = []

    async def fake_fan_out(text, personas, run_id, **_kwargs):
        for i, p in enumerate(personas):
            r = M.Reaction(
                persona_id=p.id,
                sentiment=1,
                would_act=True,
                gut_reaction="ok",
                top_confusion="none",
                top_objection="none",
                what_would_change_my_mind="none",
            )
            yield r if i < 3 else r.model_copy(update={"raw_error": REACTION_FAILED})

    async def must_not_synthesize(*a, **k):
        raise AssertionError("synthesize was called")

    monkeypatch.setattr(M, "fan_out", fake_fan_out)
    monkeypatch.setattr(M, "synthesize", must_not_synthesize)
    monkeypatch.setattr(M, "_persist", lambda ctx: None)
    asyncio.run(M._execute_run(ctx, skip_synthesis=False))
    assert ctx.run.status == "error"
    assert "only 3 of 10" in ctx.error_message
    assert ctx.run.synthesis is None
    assert ctx.run.stats is not None and ctx.run.stats.failed_reactions == 7


def test_label_and_url_lengths_are_bounded():
    """Every field is held in RUNS and persisted to tmpfs, including on a
    free cached replay, so an unbounded label was a memory-exhaustion path."""
    assert client.post("/run", json={"text": LONG_ENOUGH, "label": "x" * 201}).status_code == 422
    assert client.post("/run", json={"url": "https://e.com/" + "a" * 2048}).status_code == 422


def test_skip_synthesis_finishes_without_a_report():
    """The compare's edited run never shows its report, so it shouldn't
    pay for one."""
    import asyncio

    from api import main as M

    ctx = _finished_ctx("skip", status="pending")
    ctx.personas = PERSONA_POOL[:2]

    async def fake_fan_out(text, personas, run_id, **_kwargs):
        from panel.schemas import Reaction

        for p in personas:
            yield Reaction(
                persona_id=p.id, sentiment=0, would_act=False, gut_reaction="g",
                top_confusion="c", top_objection="o", what_would_change_my_mind="w",
            )

    async def boom(*a, **k):
        raise AssertionError("synthesize must not run")

    orig_fan, orig_syn = M.fan_out, M.synthesize
    M.fan_out, M.synthesize = fake_fan_out, boom
    try:
        asyncio.run(M._execute_run(ctx, skip_synthesis=True))
    finally:
        M.fan_out, M.synthesize = orig_fan, orig_syn
    assert ctx.run.status == "done"
    assert ctx.run.synthesis is None
    assert len(ctx.run.reactions) == 2


def test_a_new_run_can_replace_the_clients_previous_one(monkeypatch):
    """Clicking Run again cancels the old run with a fire-and-forget
    request that can lose the race to the new POST; naming the old run in
    `replaces` makes the server stop it first instead of answering 429."""
    from api import main as M

    busy = _finished_ctx("old", status="streaming")
    busy.client_ip = "testclient"
    RUNS["old"] = busy
    monkeypatch.setattr(M, "_execute_run", lambda ctx, skip: __import__("asyncio").sleep(0))
    r = client.post("/run", json={"text": LONG_ENOUGH, "replaces": "old"})
    assert r.status_code == 200
    assert RUNS["old"].run.status == "cancelled"


def test_every_handler_annotation_actually_resolves():
    """Guards a failure mode that does not raise on import.

    With `from __future__ import annotations` a handler's parameter
    annotation is just a string, so a MISSING import never raises --
    Python has nothing to resolve until FastAPI tries. FastAPI then gives
    up and treats the parameter as a query string, and the endpoint 422s
    with "Field required / loc: query". POST /benchmark shipped broken
    exactly this way, with a green test suite and a clean import.
    """
    import typing

    from fastapi.routing import APIRoute

    from api.main import app

    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        # Raises NameError if any annotation names something unimported.
        typing.get_type_hints(route.endpoint)


def test_the_benchmark_driver_can_reach_everything_it_calls():
    """Companion to the annotation guard above, for names used inside
    function bodies rather than in signatures. Two missing imports got
    into POST /benchmark this way -- `BenchmarkRequest` (caught by the
    annotation guard) and `theme_breakdown` (not, because it is called
    rather than annotated), and neither raised at import time."""
    import api.main as M

    for name in (
        "theme_breakdown",
        "build_comparison",
        "derive_query",
        "pick_pages",
        "search_candidates",
        "fetch_url_text",
        "fan_out",
        "MarketPage",
        "MarketComparison",
    ):
        assert hasattr(M, name), f"api.main is missing {name}"


def test_benchmark_is_refused_and_hidden_without_a_tavily_key(monkeypatch):
    from api import main as M

    monkeypatch.setattr(M, "TAVILY_API_KEY", None)
    assert client.get("/features").json() == {"market": False}
    RUNS["done"] = _finished_ctx("done")
    r = client.post("/benchmark", json={"run_id": "done"})
    assert r.status_code == 503


def test_benchmark_is_charged_to_the_callers_daily_budget(monkeypatch):
    """A benchmark is three more panel reads, so it draws on the same
    budgets as a run, keyed on the same resolved client address."""
    from api import main as M

    monkeypatch.setattr(M, "TAVILY_API_KEY", "test-key")
    monkeypatch.setattr(M, "DAILY_CLIENT_PERSONA_BUDGET", 100)
    parent = _finished_ctx("parent")
    parent.personas = PERSONA_POOL[:50]
    parent.run.reactions = [
        M.Reaction(
            persona_id=p.id,
            sentiment=0,
            would_act=False,
            gut_reaction="meh",
            top_confusion="none",
            top_objection="none",
            what_would_change_my_mind="none",
        )
        for p in parent.personas
    ]
    RUNS["parent"] = parent
    # 3 pages x 50 personas is over this client's 100.
    r = client.post("/benchmark", json={"run_id": "parent"})
    assert r.status_code == 429
    assert "daily allowance" in r.json()["detail"]


def _benchmark_ctx(bid, status="reading", client_ip="testclient"):
    from api.main import BenchmarkContext

    return BenchmarkContext(benchmark_id=bid, run_id="parent", status=status, client_ip=client_ip)


def test_a_running_benchmark_holds_the_clients_slot_and_a_service_slot():
    """A benchmark is three panel reads at 50 concurrent calls each; before
    this, any number could run alongside any number of runs."""
    from api import main as M

    M.BENCHMARKS["b1"] = _benchmark_ctx("b1")
    try:
        r = client.post("/run", json={"text": LONG_ENOUGH})
        assert r.status_code == 429
        assert "market comparison in progress" in r.json()["detail"]
        assert M._active_run_count() == 1
    finally:
        M.BENCHMARKS.clear()


def test_cancelling_a_benchmark_stops_its_task():
    import asyncio

    from api import main as M

    async def scenario():
        ctx = _benchmark_ctx("b2")
        ctx.task = asyncio.create_task(asyncio.sleep(3600))
        M.BENCHMARKS["b2"] = ctx
        await asyncio.sleep(0)
        out = await M.cancel_benchmark("b2")
        await asyncio.gather(ctx.task, return_exceptions=True)
        return ctx, out

    try:
        ctx, out = asyncio.run(scenario())
        assert out["cancelled"] is True
        assert ctx.status == "cancelled"
        assert ctx.task.cancelled()
    finally:
        M.BENCHMARKS.clear()


def test_benchmark_eviction_never_drops_a_running_one():
    from api import main as M

    try:
        M.BENCHMARKS["running"] = _benchmark_ctx("running")
        for i in range(M.MAX_RETAINED_RUNS + 5):
            M.BENCHMARKS[f"done{i}"] = _benchmark_ctx(f"done{i}", status="done")
        M._evict_old_benchmarks()
        assert "running" in M.BENCHMARKS
        assert len(M.BENCHMARKS) == M.MAX_RETAINED_RUNS
    finally:
        M.BENCHMARKS.clear()


def test_benchmark_skips_thin_pages_and_pages_most_readers_failed(monkeypatch):
    """A script-rendered page extracts to almost nothing, and a page whose
    reads mostly failed has no panel behind its themes; neither may be
    compared as if it were the market."""
    import asyncio

    from api import main as M
    from panel.market import Candidate, MarketQuery
    from panel.react import REACTION_FAILED

    parent = _finished_ctx("parent")
    parent.personas = PERSONA_POOL[:10]
    RUNS["parent"] = parent
    ctx = _benchmark_ctx("b3", status="pending")

    async def derive(text):
        return MarketQuery(category="thing", query="thing pricing")

    async def pick(c, cat, n):
        return [Candidate(url=f"https://{h}.example/", title=h) for h in ("thin", "broken")]

    def fetch(url):
        return "too short" if "thin" in url else "a real competitor page " * 10

    async def panel(text, personas, run_id, **_kwargs):
        return [
            M.Reaction(
                persona_id=p.id, sentiment=0, would_act=False, gut_reaction="-",
                top_confusion="-", top_objection="-", what_would_change_my_mind="-",
                raw_error=REACTION_FAILED,
            )
            for p in personas
        ]

    async def breakdown(reactions):
        return {"confusions": [], "objections": [], "requested_changes": []}

    monkeypatch.setattr(M, "derive_query", derive)
    monkeypatch.setattr(M, "search_candidates", lambda q, u: [])
    monkeypatch.setattr(M, "pick_pages", pick)
    monkeypatch.setattr(M, "fetch_url_text", fetch)
    monkeypatch.setattr(M, "_panel_on_text", panel)
    monkeypatch.setattr(M, "theme_breakdown", breakdown)
    asyncio.run(M._execute_benchmark(ctx))
    # The thin page is never read; the broken one is read and dropped.
    assert ctx.status == "no_pages"
    assert ctx.personas_sent == 10
    assert [p.label for p in ctx.pages] == ["Your page"]


def test_fast_flag_reaches_the_fan_out_and_the_record():
    import asyncio

    from api import main as M

    ctx = _finished_ctx("fast", status="pending")
    ctx.personas = PERSONA_POOL[:10]
    ctx.run.reactions = []
    ctx.run.fast = True
    seen = {}

    async def fake_fan_out(text, personas, run_id, **kwargs):
        seen.update(kwargs)
        for p in personas:
            yield M.Reaction(
                persona_id=p.id,
                sentiment=0,
                would_act=False,
                gut_reaction="ok",
                top_confusion="none",
                top_objection="none",
                what_would_change_my_mind="none",
            )

    orig = (M.fan_out, M._persist)
    M.fan_out, M._persist = fake_fan_out, lambda ctx: None
    try:
        asyncio.run(M._execute_run(ctx, skip_synthesis=True))
    finally:
        M.fan_out, M._persist = orig
    assert seen == {"fast": True}
    assert ctx.run.status == "done"


def test_run_request_defaults_to_reasoning_on():
    from panel.schemas import RunRequest

    assert RunRequest(text=LONG_ENOUGH).fast is False
    assert RunRequest(text=LONG_ENOUGH, fast=True).fast is True
