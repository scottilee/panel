"""Tests for panel/compare.py's delta logic. The one Ultra call (the
summary sentence) is stubbed -- what's worth testing here is the flip
accounting, which is pure.
"""

import pytest

from panel import compare as compare_mod
from panel.compare import delta
from panel.schemas import Artifact, Reaction, Run


def make_reaction(pid, *, sentiment=0, would_act=False):
    return Reaction(
        persona_id=pid,
        sentiment=sentiment,
        would_act=would_act,
        gut_reaction="g",
        top_confusion="c",
        top_objection="o",
        what_would_change_my_mind="w",
    )


def make_run(run_id, reactions, *, text="artifact text", persona_set="v1-10"):
    return Run(
        run_id=run_id,
        artifact=Artifact(source="text", text=text),
        persona_set=persona_set,
        status="done",
        reactions=reactions,
    )


@pytest.fixture(autouse=True)
def stub_ultra(monkeypatch):
    """compare.delta makes one Ultra call for the summary sentence."""

    class Result:
        def __init__(self):
            self.data = compare_mod._CompareSummary(summary="stubbed summary")

    async def fake_chat_json(**kwargs):
        return Result()

    monkeypatch.setattr(compare_mod, "chat_json", fake_chat_json)


@pytest.mark.asyncio
async def test_counts_flips_in_both_directions():
    a = [
        make_reaction("p001", would_act=False),
        make_reaction("p002", would_act=True),
        make_reaction("p003", would_act=False),
        make_reaction("p004", would_act=True),
    ]
    b = [
        make_reaction("p001", would_act=True),  # -> flipped positive
        make_reaction("p002", would_act=False),  # -> flipped negative
        make_reaction("p003", would_act=False),  # unchanged (no)
        make_reaction("p004", would_act=True),  # unchanged (yes)
    ]
    result = await delta(make_run("a", a), make_run("b", b))
    assert result.flipped_positive == 1
    assert result.flipped_negative == 1


@pytest.mark.asyncio
async def test_flipped_to_act_is_about_would_act_not_sentiment():
    """A persona whose sentiment improves but who still won't act has NOT
    flipped -- section 4.4 pins "flipped" to the would_act decision."""
    a = [make_reaction("p001", sentiment=-2, would_act=False)]
    b = [make_reaction("p001", sentiment=2, would_act=False)]
    result = await delta(make_run("a", a), make_run("b", b))
    assert result.flipped_positive == 0
    assert result.deltas[0].flipped_to_act is False
    assert result.deltas[0].sentiment_before == -2
    assert result.deltas[0].sentiment_after == 2


@pytest.mark.asyncio
async def test_would_act_delta_pct_is_percentage_points():
    a = [make_reaction(f"p{i:03d}", would_act=False) for i in range(1, 11)]
    b = [make_reaction(f"p{i:03d}", would_act=i <= 3) for i in range(1, 11)]
    result = await delta(make_run("a", a), make_run("b", b))
    assert result.would_act_delta_pct == pytest.approx(30.0)


@pytest.mark.asyncio
async def test_only_personas_present_in_both_runs_are_compared():
    a = [make_reaction("p001"), make_reaction("p002")]
    b = [make_reaction("p002"), make_reaction("p003")]
    result = await delta(make_run("a", a), make_run("b", b))
    assert [d.persona_id for d in result.deltas] == ["p002"]


@pytest.mark.asyncio
async def test_rejects_mismatched_persona_sets():
    """Comparing runs with different N isn't apples-to-apples (section 3)."""
    a = [make_reaction("p001")]
    b = [make_reaction("p001")]
    with pytest.raises(ValueError, match="persona_set"):
        await delta(make_run("a", a, persona_set="v1-10"), make_run("b", b, persona_set="v1-20"))


@pytest.mark.asyncio
async def test_result_carries_both_run_ids_and_the_summary():
    a = [make_reaction("p001")]
    b = [make_reaction("p001")]
    result = await delta(make_run("run-a", a), make_run("run-b", b))
    assert (result.run_a, result.run_b) == ("run-a", "run-b")
    assert result.summary == "stubbed summary"
