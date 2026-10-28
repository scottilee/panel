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
    from api.main import _RATE_WINDOWS

    _RATE_WINDOWS.clear()
    RUNS.clear()
    yield
    _RATE_WINDOWS.clear()
    RUNS.clear()


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


def test_cached_replay_reports_zero_cost():
    """A replay makes no model calls, so every cost figure must read zero.
    _compute_stats would otherwise sum the cached reactions' recorded
    latencies -- half an hour of Nano time printed next to nano_calls=0,
    describing calls made on a different run days earlier."""
    import json

    from api import main as M

    example = next(M.config.EXAMPLES_DIR.glob("*.txt"))
    cache_path = M.config.CACHED_DIR / f"{example.stem}.json"
    if not cache_path.is_file():
        import pytest as _pytest

        _pytest.skip("no cached example on disk")
    cached = json.loads(cache_path.read_text())
    reactions = [M.Reaction.model_validate(r) for r in cached["reactions"]]
    # The cached reactions do carry real recorded latencies, or this test
    # would pass for the wrong reason.
    assert sum(r.latency_ms or 0 for r in reactions) > 0

    stats = M._compute_stats(reactions, {}, nano_ms_total=0, ultra_ms=0)
    assert stats.nano_calls == 0
    assert stats.nano_ms_total == 0
    assert stats.ultra_ms == 0
    # The sentiment figures stay real -- they describe what is on screen.
    assert stats.mean_sentiment != 0


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
            run_a=a.run_id, run_b=b.run_id, deltas=[], flipped_positive=0,
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
    # A failed compare is retried (not cached), and each retry is charged.
    nonfatal = TestClient(app, raise_server_exceptions=False)
    codes = [nonfatal.post("/compare", json={"run_a": "ra", "run_b": "rb"}).status_code for _ in range(3)]
    assert codes[-1] == 429


def test_no_cors_headers_by_default():
    r = client.get("/examples", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}
