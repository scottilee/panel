"""Tests for panel/personas.py's sampling and banding. No API calls."""

import pytest

from panel.personas import age_band_for, load_personas, sample, sample_stable

POOL = load_personas()


@pytest.mark.parametrize(
    "age,band",
    [(18, "18-24"), (24, "18-24"), (25, "25-34"), (34, "25-34"), (35, "35-49"),
     (49, "35-49"), (50, "50-64"), (64, "50-64"), (65, "65+"), (90, "65+")],
)
def test_age_band_boundaries(age, band):
    assert age_band_for(age) == band


def test_sample_is_deterministic_for_a_given_seed():
    assert [p.id for p in sample(20, seed=7)] == [p.id for p in sample(20, seed=7)]


def test_sample_differs_across_seeds():
    assert [p.id for p in sample(20, seed=7)] != [p.id for p in sample(20, seed=99)]


def test_sample_stable_is_the_pool_prefix():
    """The real API uses sample_stable precisely because the browser can
    reproduce it as pool.slice(0, n) with no shared PRNG."""
    assert [p.id for p in sample_stable(15)] == [p.id for p in POOL[:15]]


def test_sample_stable_is_prefix_consistent_across_sizes():
    """Growing N must only append -- otherwise moving the persona-count
    slider would reshuffle which personas the grid is showing."""
    small = [p.id for p in sample_stable(10)]
    bigger = [p.id for p in sample_stable(30)]
    assert bigger[:10] == small


@pytest.mark.parametrize("fn", [sample_stable, lambda n: sample(n, seed=0)])
def test_sampling_rejects_n_larger_than_pool(fn):
    with pytest.raises(ValueError, match="pool"):
        fn(len(POOL) + 1)


def test_pool_has_full_stratification_coverage():
    regions = {p.region for p in POOL}
    incomes = {p.income_band for p in POOL}
    bands = {age_band_for(p.age) for p in POOL}
    assert len(regions) == 8
    assert len(incomes) == 5
    assert len(bands) == 5


def test_pool_ids_are_unique():
    ids = [p.id for p in POOL]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("cols", [10, 32])
def test_pool_order_is_demographically_mixed(cols):
    """Regression guard: the generator assigns age/income bands in a
    repeating 5-cycle, so unshuffled on-disk order put a single age+income
    cohort in every grid column -- visible as vertical colour stripes
    rather than a mixed crowd. Any column holding one single band again
    means the shuffle was lost.

    Checked at both column counts gridLayoutFor actually produces: 10 at
    N=100 (where the original striping showed up, since 5 divides 10) and
    32 at N=1000.
    """
    for col in range(cols):
        column_bands = {age_band_for(p.age) for i, p in enumerate(POOL) if i % cols == col}
        assert len(column_bands) > 1, f"grid column {col} of {cols} is a single age band again"


# --- The 1000-persona scale-up (2026-10-28) ---------------------------------


def test_build_slots_offset_continues_the_cycles():
    """An --append run offsets the grid by the existing pool size so the
    modular cycles continue instead of restarting -- without this, 100 +
    900 re-overweighted the same first four regions twice."""
    from collections import Counter

    from panel.personas import build_slots

    slots = build_slots(100) + build_slots(900, offset=100)
    assert set(Counter(s["region"] for s in slots).values()) == {125}
    assert set(Counter(s["age_band"] for s in slots).values()) == {200}
    assert set(Counter(s["income_band"] for s in slots).values()) == {200}


def test_build_slots_offset_zero_matches_no_offset():
    from panel.personas import build_slots

    assert build_slots(40, offset=0) == build_slots(40)


def test_find_duplicate_names_is_global_not_per_region():
    """The old check was region-scoped, which is why the shipped 100-pool
    carried three personas called 'Diego Morales' across three regions
    without ever failing C1."""
    from panel.personas import find_duplicate_names
    from panel.schemas import Persona

    base = dict(
        age=40,
        gender="female",
        location="Somewhere",
        income_band="40-75k",
        occupation="tester",
        household="lives alone",
        tech_comfort=50,
        skepticism=50,
        openness=50,
        conscientiousness=50,
        extraversion=50,
        agreeableness=50,
        neuroticism=50,
        values=["a"],
        buying_triggers=["b"],
        pet_peeves=["c"],
        voice="I test.",
    )
    people = [
        Persona(id="p001", name="Ada Pike", region="West US", **base),
        Persona(id="p002", name="  ADA PIKE ", region="Asia-Pacific", **base),
        Persona(id="p003", name="Bo Vance", region="West US", **base),
    ]
    dupes = find_duplicate_names(people)
    assert len(dupes) == 1
    name, ids = dupes[0]
    assert name == "ada pike"
    assert ids == ["p001", "p002"]


def test_pool_has_no_duplicate_names():
    from panel.personas import find_duplicate_names

    assert find_duplicate_names(POOL) == []


def test_pool_occupation_diversity():
    """Occupations may repeat -- there really is more than one nurse in a
    thousand people -- so this guards against collapse onto a handful of
    stock jobs, not against repetition itself.

    Concentration is the metric that matters, and the unique *ratio* is
    the weaker one: it barely moved across the scale-up (71% of 100 ->
    73% of 1000) while the thing a reader actually notices improved 5.5x.
    The pre-scale-up pool's most common job was 'barista' at 5 of 100
    (5.0% of the pool); at 1000 the worst is 9 (0.9%). A ratio threshold
    alone would have called that a failure.
    """
    from panel.personas import occupation_stats

    stats = occupation_stats(POOL)
    most_common_job, most_common_count = stats["top_repeats"][0]
    share = most_common_count / stats["total"]
    assert share <= 0.015, f"{most_common_job!r} is {share:.1%} of the pool: {stats['top_repeats']}"
    # Strictly better than the 0.71 the 100-persona pool managed.
    assert stats["ratio"] > 0.71, stats["top_repeats"]


def test_avoid_sample_is_bounded_and_deterministic():
    from panel.personas import avoid_sample

    used = {f"name {i}" for i in range(500)}
    a = avoid_sample(used, 100, seed=3)
    b = avoid_sample(used, 100, seed=3)
    assert len(a) == 100
    assert a == b
    assert avoid_sample(used, 100, seed=4) != a
    small = {"one", "two"}
    assert avoid_sample(small, 100, seed=3) == ["one", "two"]
