"""Pure-function tests for panel/synthesize.py -- segment recounting, the
evidence/percentage repairs, and edit application. No API calls.
"""

import pytest

from panel.schemas import (
    Finding,
    HighestLeverageEdit,
    Persona,
    SegmentClause,
    Synthesis,
)
from panel.synthesize import (
    _apply_recounted_pct,
    _invalid_evidence,
    _normalize_evidence,
    _skepticism_band,
    _tech_band,
    apply_edit,
    build_persona_summary_csv,
    recount_affected_pct,
)

BASE_PERSONA = dict(
    name="Test Person",
    gender="female",
    location="Somewhere",
    occupation="tester",
    household="lives alone",
    openness=50,
    conscientiousness=50,
    extraversion=50,
    agreeableness=50,
    neuroticism=50,
    values=["a", "b"],
    buying_triggers=["x"],
    pet_peeves=["y"],
    voice="I test things.",
)


def make_persona(pid, *, age=40, region="West US", income="40-75k", tech=50, skep=50):
    return Persona(
        id=pid,
        age=age,
        region=region,
        income_band=income,
        tech_comfort=tech,
        skepticism=skep,
        **BASE_PERSONA,
    )


@pytest.fixture
def pool():
    # 10 personas: tech_comfort 10..100 by 10, skepticism inverted.
    return {
        f"p{i:03d}": make_persona(f"p{i:03d}", tech=i * 10, skep=110 - i * 10, age=20 + i * 5)
        for i in range(1, 11)
    }


def test_recount_single_clause(pool):
    clause = [SegmentClause(field="tech_comfort", op="<=", value=33)]
    # tech_comfort 10, 20, 30 -> 3 of 10
    assert recount_affected_pct(pool, clause) == 30.0


def test_recount_ands_multiple_clauses(pool):
    clauses = [
        SegmentClause(field="tech_comfort", op=">=", value=50),
        SegmentClause(field="skepticism", op=">=", value=50),
    ]
    manual = sum(1 for p in pool.values() if p.tech_comfort >= 50 and p.skepticism >= 50)
    assert recount_affected_pct(pool, clauses) == 100 * manual / len(pool)


def test_recount_in_operator(pool):
    clause = [SegmentClause(field="region", op="in", value=["West US", "Midwest US"])]
    assert recount_affected_pct(pool, clause) == 100.0  # all are West US


def test_recount_derived_age_band(pool):
    clause = [SegmentClause(field="age_band", op="==", value="65+")]
    manual = sum(1 for p in pool.values() if p.age >= 65)
    assert recount_affected_pct(pool, clause) == 100 * manual / len(pool)


def test_recount_coerces_string_valued_numeric_clause(pool):
    """Ultra has returned {"field": "skepticism", "op": ">=", "value": "60"}
    with the number as a string; the schema's Union allows it, so a raw
    comparison would raise TypeError on str >= int."""
    as_int = recount_affected_pct(pool, [SegmentClause(field="skepticism", op=">=", value=60)])
    as_str = recount_affected_pct(pool, [SegmentClause(field="skepticism", op=">=", value="60")])
    assert as_int == as_str


def test_recount_empty_pool_is_zero():
    assert recount_affected_pct({}, [SegmentClause(field="tech_comfort", op="<=", value=33)]) == 0.0


@pytest.mark.parametrize(
    "value,expected",
    [(1, "low"), (33, "low"), (34, "mid"), (66, "mid"), (67, "high"), (100, "high")],
)
def test_bands_trisect_the_1_100_scale(value, expected):
    assert _tech_band(value) == expected
    assert _skepticism_band(value) == expected


def test_persona_csv_has_header_and_one_row_each(pool):
    csv = build_persona_summary_csv(list(pool.values()))
    lines = csv.splitlines()
    assert lines[0] == "id,age,region,income_band,tech_comfort,skepticism,occupation"
    assert len(lines) == len(pool) + 1


# --- apply_edit -------------------------------------------------------


def test_apply_edit_exact_match():
    assert apply_edit("price is $29 today", "$29", "$29/mo") == "price is $29/mo today"


def test_apply_edit_tolerates_differing_whitespace():
    """Ultra's current_text quote sometimes collapses or changes the
    whitespace when it spans a line break; every word is right."""
    artifact = "the price\nis  $29 today"
    out = apply_edit(artifact, "price is $29", "price is $39")
    assert out is not None
    assert "$39" in out


def test_apply_edit_returns_none_when_text_absent():
    assert apply_edit("nothing here", "missing phrase", "x") is None


def test_apply_edit_replaces_only_first_occurrence():
    assert apply_edit("$29 and $29", "$29", "FREE") == "FREE and $29"


# --- evidence normalization / pct recounting --------------------------


def make_synthesis(evidence, *, affected_pct=50, headline="50% of people balk"):
    finding = Finding(
        claim="claim",
        affected_pct=affected_pct,
        affected_segment="tech_comfort <= 33",
        affected_segment_filter=[SegmentClause(field="tech_comfort", op="<=", value=33)],
        evidence=evidence,
        severity="high",
    )
    return Synthesis(
        headline=headline,
        findings=[finding] * 3,
        highest_leverage_edit=HighestLeverageEdit(
            current_text="$29", proposed_text="$29/mo", rationale="r", predicted_flips=1
        ),
        who_you_are_losing="x",
        who_you_are_winning="y",
    )


def test_normalize_evidence_strips_quotes_appended_to_ids(pool):
    """Observed from Ultra: 'p004: "I don\\'t get it"' instead of 'p004'."""
    decorated = [
        'p001: "I don\'t get how this works"',
        "p002 (West US): “needs proof”",
        "p003",
    ]
    fixed = _normalize_evidence(make_synthesis(decorated), pool)
    assert fixed.findings[0].evidence == ["p001", "p002", "p003"]
    assert _invalid_evidence(fixed, pool) == []


def test_normalize_evidence_keeps_already_clean_ids(pool):
    clean = ["p001", "p002", "p003"]
    fixed = _normalize_evidence(make_synthesis(clean), pool)
    assert fixed.findings[0].evidence == clean


def test_invalid_evidence_reports_unresolvable_ids(pool):
    bogus = ["p999", "nobody", "p001"]
    problems = _invalid_evidence(make_synthesis(bogus), pool)
    assert len(problems) == 3  # one per finding
    assert "p999" in problems[0]


def test_apply_recounted_pct_overwrites_claimed_number(pool):
    # Claimed 90%, but tech_comfort <= 33 is only 3 of 10 personas.
    out = _apply_recounted_pct(make_synthesis(["p001", "p002", "p003"], affected_pct=90), pool)
    assert all(f.affected_pct == 30 for f in out.findings)


def test_apply_recounted_pct_also_fixes_the_headline(pool):
    """The headline cites the same wrong number, so patching only the
    finding would leave the page's biggest line contradicting the card
    directly under it."""
    out = _apply_recounted_pct(
        make_synthesis(["p001", "p002", "p003"], affected_pct=90, headline="90% of people balk"),
        pool,
    )
    assert "30%" in out.headline
    assert "90%" not in out.headline


def test_apply_recounted_pct_leaves_accurate_numbers_alone(pool):
    out = _apply_recounted_pct(make_synthesis(["p001", "p002", "p003"], affected_pct=30), pool)
    assert all(f.affected_pct == 30 for f in out.findings)
