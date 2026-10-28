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


def test_pool_order_is_demographically_mixed():
    """Regression guard: the generator assigns age/income bands in a
    repeating 5-cycle, and the grid is 10 columns wide, so unshuffled
    on-disk order put a single age+income cohort in every grid column --
    visible as vertical colour stripes rather than a mixed crowd. Any
    column holding one single band again means the shuffle was lost.
    """
    cols = 10
    for col in range(cols):
        column_bands = {age_band_for(p.age) for i, p in enumerate(POOL) if i % cols == col}
        assert len(column_bands) > 1, f"grid column {col} is a single age band again"
