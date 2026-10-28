"""Route-level tests for api/main.py using FastAPI's TestClient.

Deliberately avoids the POST /run success path: that starts a background
task that makes real Nano/Ultra calls, so asserting on it would spend real
credits every test run. Input validation, lookups, and the error paths all
return before any model call and are what actually tend to break.
"""

from fastapi.testclient import TestClient

from api.main import PERSONA_POOL, RUNS, RunContext, app
from panel.schemas import Artifact, Run

client = TestClient(app)

LONG_ENOUGH = "x" * 60  # RunRequest requires 50-20,000 chars


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


def test_run_rejects_persona_count_above_pool_size_with_a_clear_message():
    r = client.post("/run", json={"text": LONG_ENOUGH, "persona_count": len(PERSONA_POOL) + 1})
    assert r.status_code == 400
    assert "pool size" in r.json()["detail"]


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
