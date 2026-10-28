"""chat_json's retry behaviour. No API calls -- the SDK layer is faked.

The truncation branch is the part worth pinning: `finish_reason == "length"`
covers two different failures that want opposite responses, and getting
that backwards is measurably expensive (see the comment in chat_json).
"""

import pytest
from pydantic import BaseModel

import panel.tf_client as T


class Tiny(BaseModel):
    x: int


class _FakeChoice:
    def __init__(self, content, finish_reason):
        self.message = type("M", (), {"content": content})()
        self.finish_reason = finish_reason


class _FakeResp:
    def __init__(self, content, finish_reason):
        self.choices = [_FakeChoice(content, finish_reason)]


@pytest.fixture
def budgets(monkeypatch):
    """Records the max_tokens of every attempt."""
    seen: list[int] = []

    def make(content, finish_reason="length"):
        async def fake(client, **kw):
            seen.append(kw["max_tokens"])
            return _FakeResp(content, finish_reason)

        monkeypatch.setattr(T, "_call_with_backoff", fake)
        monkeypatch.setattr(T, "get_client", lambda: None)
        return seen

    make.seen = seen
    return make


async def _expect_truncated(**kw):
    with pytest.raises(T.ChatJsonTruncatedError) as exc:
        await T.chat_json(model="m", system="s", user="u", schema=Tiny, **kw)
    return str(exc.value)


@pytest.mark.asyncio
async def test_truncation_doubles_the_budget(budgets):
    """Measured at 10 trials per budget (scripts/probe_nemotron.py): the
    per-attempt success rate rises with the ceiling, 3/10 at 8000 to 7/10
    at 48000, so doubling is the right response to a truncation.

    An earlier version split on whether the truncated response held
    partial content and retried the empty ones at the *same* budget. That
    rested on a 3-trial sample showing 0/3 at 24000, which 10 trials
    contradicted. Content-emptiness cannot tell "ran away" from "needed
    slightly more", because content only begins after reasoning ends.
    """
    seen = budgets("")
    message = await _expect_truncated(max_tokens=1000)
    assert seen == [1000, 2000, 4000]
    assert "after 2 retries" in message


@pytest.mark.asyncio
async def test_partial_content_truncation_doubles_too(budgets):
    """Same path -- recorded separately so a future change that
    reintroduces a content-based split has to decide about this case
    explicitly."""
    seen = budgets('{"x": ')
    await _expect_truncated(max_tokens=1000)
    assert seen == [1000, 2000, 4000]


@pytest.mark.asyncio
async def test_a_clean_response_makes_exactly_one_call(budgets):
    seen = budgets('{"x": 7}', finish_reason="stop")
    result = await T.chat_json(model="m", system="s", user="u", schema=Tiny, max_tokens=1000)
    assert result.data.x == 7
    assert seen == [1000]
    assert result.attempts == 1


@pytest.mark.asyncio
async def test_backoff_sleeps_between_attempts_not_before_the_first(monkeypatch):
    """The sleep list is what happens *between* attempts, so a call that
    succeeds first time never sleeps. The old loop enumerated the list and
    skipped index 0, which made the first element dead code and the
    attempt count an accident of the list's length."""
    from openai import APIConnectionError

    slept: list[float] = []

    async def fake_sleep(s):
        slept.append(s)

    attempts = {"n": 0}

    class _Client:
        class chat:
            class completions:
                @staticmethod
                async def create(**kw):
                    attempts["n"] += 1
                    if attempts["n"] <= 2:
                        raise APIConnectionError(request=None)
                    return _FakeResp('{"x": 1}', "stop")

    monkeypatch.setattr(T.asyncio, "sleep", fake_sleep)
    resp = await T._call_with_backoff(_Client())
    assert attempts["n"] == 3
    assert slept == T._BACKOFF_SECONDS[:2]
    assert resp.choices[0].finish_reason == "stop"


@pytest.mark.asyncio
async def test_track_calls_counts_every_request_including_retries(budgets):
    """RunStats reports real request counts, so a truncation retry must be
    counted as the extra call it is."""
    budgets('{"x": 1}', finish_reason="stop")
    counts = T.track_calls()
    await T.chat_json(model="nano", system="s", user="u", schema=Tiny)
    assert counts["nano"] == 1
    budgets("", finish_reason="length")
    await _expect_truncated()
    assert counts["m"] == 1 + T.MAX_TRUNCATION_RETRIES
