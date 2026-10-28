"""Tests for panel/react.py's fan-out. chat_json is stubbed -- what
matters here is the concurrency cap, the per-persona failure handling, and
that every persona yields exactly one reaction. No API calls.
"""

import asyncio

import pytest

from panel import react as react_mod
from panel.react import fan_out
from panel.schemas import Reaction
from tests.test_synthesize import make_persona


class FakeResult:
    def __init__(self, persona_id):
        self.latency_ms = 123
        self.data = Reaction(
            persona_id=persona_id,
            sentiment=1,
            would_act=True,
            gut_reaction="g",
            top_confusion="c",
            top_objection="o",
            what_would_change_my_mind="w",
        )


@pytest.fixture
def personas():
    return [make_persona(f"p{i:03d}") for i in range(1, 13)]


async def collect(artifact, personas, **kwargs):
    return [r async for r in fan_out(artifact, personas, "run-x", **kwargs)]


async def test_yields_one_reaction_per_persona(monkeypatch, personas):
    async def fake(**kwargs):
        # persona_json is embedded in the user prompt; pull the id back out
        # of it so the stub returns a matching reaction.
        pid = kwargs["user"].split('"id":"')[1].split('"')[0]
        return FakeResult(pid)

    monkeypatch.setattr(react_mod, "chat_json", fake)
    out = await collect("artifact", personas)
    assert len(out) == len(personas)
    assert {r.persona_id for r in out} == {p.id for p in personas}


async def test_stamps_run_id_and_latency(monkeypatch, personas):
    async def fake(**kwargs):
        pid = kwargs["user"].split('"id":"')[1].split('"')[0]
        return FakeResult(pid)

    monkeypatch.setattr(react_mod, "chat_json", fake)
    out = await collect("artifact", personas[:3])
    assert all(r.run_id == "run-x" for r in out)
    assert all(r.latency_ms == 123 for r in out)


async def test_one_failing_persona_does_not_stall_the_run(monkeypatch, personas):
    """A persona whose call fails after retries still yields a Reaction
    (sentiment 0, raw_error set) so the grid fills and the run completes
    -- one bad call never stalls the run."""
    calls = {"n": 0}

    async def fake(**kwargs):
        pid = kwargs["user"].split('"id":"')[1].split('"')[0]
        calls["n"] += 1
        if pid == "p005":
            raise react_mod.ChatJsonError("boom", 42)
        return FakeResult(pid)

    monkeypatch.setattr(react_mod, "chat_json", fake)
    out = await collect("artifact", personas)

    assert len(out) == len(personas)
    failed = [r for r in out if r.raw_error]
    assert len(failed) == 1
    assert failed[0].persona_id == "p005"
    assert failed[0].sentiment == 0
    assert failed[0].would_act is False
    assert "(no response)" in failed[0].gut_reaction


async def test_a_failed_reaction_does_not_publish_the_error_text(monkeypatch, personas):
    """Reactions go out on the public stream and GET /run; provider error
    text (401s naming the account, internal hosts) must stay in the log."""

    async def fake(**kwargs):
        raise RuntimeError("AuthenticationError: Error code: 401 key sk-live-123 at tf-internal-7")

    monkeypatch.setattr(react_mod, "chat_json", fake)
    out = await collect("artifact", personas[:1])
    assert out[0].raw_error == react_mod.REACTION_FAILED
    assert "sk-live" not in out[0].model_dump_json()


async def test_respects_the_concurrency_cap(monkeypatch, personas):
    in_flight = {"now": 0, "peak": 0}

    async def fake(**kwargs):
        pid = kwargs["user"].split('"id":"')[1].split('"')[0]
        in_flight["now"] += 1
        in_flight["peak"] = max(in_flight["peak"], in_flight["now"])
        await asyncio.sleep(0.01)
        in_flight["now"] -= 1
        return FakeResult(pid)

    monkeypatch.setattr(react_mod, "chat_json", fake)
    await collect("artifact", personas, concurrency=3)
    assert in_flight["peak"] <= 3


def test_seed_is_stable_across_processes():
    """Regression guard: this used to be `hash(persona.id)`, and Python
    randomizes string hashing per process (PYTHONHASHSEED), so the "fixed
    seed per persona" the spec asks for silently changed on every server
    restart. A literal expected value catches any switch back to a
    process-dependent hash -- recomputing it the same way the code does
    would pass even if the code regressed.
    """
    import zlib

    from panel.react import seed_for

    assert seed_for("p001") == zlib.crc32(b"p001") % (2**31) == 1619569881
    assert seed_for("p042") != seed_for("p043")


async def test_uses_the_measured_token_budget(monkeypatch, personas):
    """max_tokens=400 (the figure the spec originally stated) truncated
    essentially every Nano call before it emitted any content."""
    seen = {}

    async def fake(**kwargs):
        seen.update(kwargs)
        pid = kwargs["user"].split('"id":"')[1].split('"')[0]
        return FakeResult(pid)

    monkeypatch.setattr(react_mod, "chat_json", fake)
    await collect("artifact", personas[:1])
    assert seen["max_tokens"] == react_mod.REACTION_MAX_TOKENS
    assert react_mod.REACTION_MAX_TOKENS >= 3000
