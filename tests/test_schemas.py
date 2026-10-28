import pytest
from pydantic import ValidationError

from panel.schemas import (
    Artifact,
    CompareDelta,
    CompareRequest,
    CompareResult,
    Finding,
    HighestLeverageEdit,
    Persona,
    Reaction,
    Run,
    RunRequest,
    SegmentClause,
    Synthesis,
)


def round_trip(model_cls, instance):
    restored = model_cls.model_validate_json(instance.model_dump_json())
    assert restored == instance
    return restored


PERSONA_KWARGS = dict(
    id="p042",
    name="Dolores Reyes",
    age=61,
    gender="female",
    location="Tucson, AZ",
    region="Southwest US",
    income_band="40-75k",
    occupation="retired school secretary",
    household="lives with spouse, two adult children nearby",
    tech_comfort=30,
    skepticism=78,
    openness=35,
    conscientiousness=82,
    extraversion=40,
    agreeableness=55,
    neuroticism=60,
    values=["frugality", "family", "distrust of fine print"],
    buying_triggers=["recommendation from daughter", "clear cancellation policy"],
    pet_peeves=["auto-renewal", "jargon", "having to call to cancel"],
    voice="I read everything twice and I still assume there's a catch.",
)


def test_persona_round_trip():
    round_trip(Persona, Persona(**PERSONA_KWARGS))


@pytest.mark.parametrize(
    "field",
    [
        "tech_comfort",
        "skepticism",
        "openness",
        "conscientiousness",
        "extraversion",
        "agreeableness",
        "neuroticism",
    ],
)
def test_persona_rejects_out_of_range_scores(field):
    kwargs = {**PERSONA_KWARGS, field: 101}
    with pytest.raises(ValidationError):
        Persona(**kwargs)


def test_persona_rejects_unknown_region():
    with pytest.raises(ValidationError):
        Persona(**{**PERSONA_KWARGS, "region": "Antarctica"})


REACTION_KWARGS = dict(
    persona_id="p042",
    sentiment=-1,
    would_act=False,
    gut_reaction="Is this a subscription? It doesn't say, which means yes.",
    top_confusion="Whether the $29 is monthly or one-time",
    top_objection="No visible cancellation policy",
    what_would_change_my_mind="Say 'cancel anytime, one click' next to the price",
)


def test_reaction_round_trip():
    round_trip(Reaction, Reaction(**REACTION_KWARGS))


def test_reaction_allows_null_confusion_and_objection_only_at_sentiment_2():
    r = Reaction(
        **{
            **REACTION_KWARGS,
            "sentiment": 2,
            "top_confusion": None,
            "top_objection": None,
        }
    )
    round_trip(Reaction, r)


def test_reaction_rejects_null_confusion_below_sentiment_2():
    with pytest.raises(ValidationError):
        Reaction(**{**REACTION_KWARGS, "sentiment": 1, "top_confusion": None})


def test_reaction_backend_fields_default_to_none():
    r = Reaction(**REACTION_KWARGS)
    assert r.run_id is None
    assert r.latency_ms is None
    assert r.raw_error is None


FINDING_KWARGS = dict(
    claim="The $29 figure is read as monthly by most low-tech-comfort personas",
    affected_pct=41,
    affected_segment="tech_comfort <= 2",
    affected_segment_filter=[SegmentClause(field="tech_comfort", op="<=", value=2)],
    evidence=["p042", "p017", "p088"],
    severity="high",
)


def test_finding_round_trip():
    round_trip(Finding, Finding(**FINDING_KWARGS))


def test_finding_rejects_fewer_than_3_evidence_ids():
    with pytest.raises(ValidationError):
        Finding(**{**FINDING_KWARGS, "evidence": ["p042", "p017"]})


def test_segment_clause_in_requires_list_value():
    with pytest.raises(ValidationError):
        SegmentClause(field="region", op="in", value="Southwest US")
    round_trip(
        SegmentClause,
        SegmentClause(field="region", op="in", value=["Southwest US", "West US"]),
    )


def test_synthesis_round_trip_requires_exactly_3_findings():
    synthesis = Synthesis(
        headline="Pricing reads as a subscription to 41% of personas over 55",
        findings=[Finding(**FINDING_KWARGS) for _ in range(3)],
        highest_leverage_edit=HighestLeverageEdit(
            current_text="$29",
            proposed_text="$29 one-time. No subscription.",
            rationale="Resolves the top confusion for 38 personas without changing price.",
            predicted_flips=22,
        ),
        who_you_are_losing="Older, price-sensitive, low-trust readers.",
        who_you_are_winning="Tech-comfortable 25-40 year olds who skim.",
    )
    round_trip(Synthesis, synthesis)

    with pytest.raises(ValidationError):
        Synthesis(
            headline="x",
            findings=[Finding(**FINDING_KWARGS) for _ in range(2)],
            highest_leverage_edit=synthesis.highest_leverage_edit,
            who_you_are_losing="x",
            who_you_are_winning="x",
        )


def test_run_round_trip_with_defaults():
    run = Run(
        run_id="r_20261028_1b3f",
        artifact=Artifact(source="text", text="$29/mo", label="Pricing page v1"),
        persona_set="v1-100",
    )
    restored = round_trip(Run, run)
    assert restored.status == "pending"
    assert restored.reactions == []
    assert restored.stats.nano_calls == 0


def test_compare_result_round_trip():
    result = CompareResult(
        run_a="r_a",
        run_b="r_b",
        deltas=[
            CompareDelta(
                persona_id="p042",
                sentiment_before=-1,
                sentiment_after=1,
                flipped_to_act=True,
            )
        ],
        compared=100,
        flipped_positive=22,
        flipped_negative=3,
        would_act_delta_pct=19.0,
        summary="One sentence from Ultra on what the edit did and for whom.",
    )
    round_trip(CompareResult, result)


def test_run_request_requires_text_or_url():
    with pytest.raises(ValidationError):
        RunRequest()
    RunRequest(url="https://example.com/pricing")
    RunRequest(text="x" * 50)


def test_compare_request_round_trip():
    round_trip(CompareRequest, CompareRequest(run_a="r_a", run_b="r_b"))
