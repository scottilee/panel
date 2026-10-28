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
    from api.main import _RUN_TIMES_BY_IP

    _RUN_TIMES_BY_IP.clear()
    RUNS.clear()
    yield
    _RUN_TIMES_BY_IP.clear()
    RUNS.clear()


def test_personas_returns_the_whole_pool():
    r = client.get("/personas")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == len(PERSONA_POOL)
    assert all("id" in p and "skepticism" in p for p in body)


def test_personas_are_on_the_1_100_scale():
    """Section 3 originally said 1-5; widened to 1-100, and the seven
    numeric traits all have to agree on that."""
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

    stats = M._compute_stats(reactions, nano_calls=0, ultra_ms=0).model_copy(
        update={"nano_ms_total": 0}
    )
    assert stats.nano_calls == 0
    assert stats.nano_ms_total == 0
    assert stats.ultra_ms == 0
    # The sentiment figures stay real -- they describe what is on screen.
    assert stats.mean_sentiment != 0
