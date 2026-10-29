"""panel/market.py: picking comparable pages and comparing the panels.

No network and no model calls -- the Tavily and Nano stages are probed
separately (scripts and the notes in market.py). What is tested here is
every judgement the module makes in pure Python, because those are the
ones that decide whether the output is honest.
"""

import pytest

from panel.market import (
    CONSOLIDATE_SIMILARITY,
    Candidate,
    _consolidate,
    _group_across_pages,
    build_comparison,
    looks_like_a_product_page,
)
from panel.schemas import MarketPage


def c(url, title=""):
    return Candidate(url=url, title=title)


# --- Which search results are a company's own page -------------------------


@pytest.mark.parametrize(
    "url,title",
    [
        ("https://www.salliemae.com/savings/high-yield-savings-account", "High-Yield Savings"),
        ("https://www.ally.com/bank/online-savings-account", "Online Savings Account"),
        ("https://plantoeat.com", "Meal Planner and Grocery Lists"),
        ("https://www.sfmta.com/permits/residential-parking-permits-rpp", "Residential Permits"),
        # Locale folders aren't depth (dropped by a live search, 2026-10-29).
        ("https://www.marcus.com/us/en/savings/high-yield-savings", "High Yield Online Savings Account"),
    ],
)
def test_real_product_pages_are_kept(url, title):
    assert looks_like_a_product_page(c(url, title))


@pytest.mark.parametrize(
    "url,title,why",
    [
        ("https://www.nerdwallet.com/banking/best/high-yield", "x", "review site host"),
        ("https://example.com/blog/best-meal-planning-apps", "x", "blog path"),
        ("https://example.com/news/we-raised-a-round", "x", "news path"),
        ("https://example.com/updates/how-much-to-charge", "x", "updates path"),
        ("https://play.google.com/store/apps/details?id=x", "x", "store listing"),
        # Reported from a real run: survived the first version because
        # "best" was anchored to the start of the title.
        (
            "https://theyummybowl.com/i-tried-the-best-meal-planning-apps",
            "I Tried the Best Meal Planning Apps - These Are My Favorites",
            "editorial title, 'best' mid-string",
        ),
        ("https://example.com/p", "Top 10 Savings Accounts", "listicle title"),
        ("https://example.com/x", "Acme vs Beta: Which Is Better?", "comparison title"),
        ("https://example.com/a/b/c/d/e", "Deep", "deep path"),
        ("https://example.com/en-us/a/b/c/d", "Deep", "deep path under a locale"),
    ],
)
def test_editorial_results_are_rejected(url, title, why):
    assert not looks_like_a_product_page(c(url, title)), why


# --- Grouping themes across pages ------------------------------------------


def test_phrasings_of_one_theme_group_across_pages():
    rows = _group_across_pages(
        [
            ("Your page", [("price too high", 3, ["p001"])]),
            ("a.com", [("the price is too high", 2, ["p010"])]),
            ("b.com", [("Price is too high", 1, ["p020"])]),
        ]
    )
    assert len(rows) == 1
    theme, pages, ids = rows[0]
    assert pages == ["Your page", "a.com", "b.com"]
    assert ids == ["p001", "p010", "p020"]


def test_alignment_map_is_applied_before_grouping():
    """The model's cross-page alignment is what catches wordings that
    share no words -- the lexical rule cannot."""
    canonical = {
        "no proof": "lack of evidence",
        "nothing backs up the claims": "lack of evidence",
    }
    rows = _group_across_pages(
        [
            ("Your page", [("no proof", 2, ["p001"])]),
            ("a.com", [("nothing backs up the claims", 1, ["p010"])]),
        ],
        canonical,
    )
    assert len(rows) == 1
    assert rows[0][1] == ["Your page", "a.com"]


# --- Consolidating near-duplicate rows -------------------------------------


def test_near_duplicate_rows_are_consolidated():
    """Both of these appeared as separate findings on a real benchmark.
    Neither theme_key's subset rule nor the alignment call caught them."""
    rows = [
        ("How FDIC pass-through coverage works across multiple partner banks", ["Your page"], ["p1"]),
        ("How FDIC pass-through coverage works beyond $250k across multiple banks", ["Your page"], ["p2"]),
    ]
    out = _consolidate(rows)
    assert len(out) == 1
    assert out[0][2] == ["p1", "p2"], "evidence from both rows is kept"


@pytest.mark.parametrize(
    "a,b",
    [
        ("price too high", "price unclear"),
        ("hidden fees", "unclear fees"),
        ("no proof of savings", "no clear next step"),
        ("cannot find the price", "the price is too high"),
    ],
)
def test_different_themes_are_not_consolidated(a, b):
    """The dangerous direction: merging "I can't find the price" into
    "the price is too high" would invent a finding."""
    out = _consolidate([(a, ["A"], []), (b, ["B"], [])])
    assert len(out) == 2, f"{a!r} and {b!r} must stay apart"


def test_consolidation_can_promote_a_theme_to_shared():
    """If two pages phrased one theme differently enough to survive
    grouping, consolidating them must also union their pages -- otherwise
    a shared theme stays filed as unique to the user's page, which is the
    one claim this feature must not get wrong."""
    out = _consolidate(
        [
            ("no proof of savings offered", ["Your page"], []),
            ("no proof of the savings offered", ["ally.com"], []),
        ]
    )
    assert len(out) == 1
    assert sorted(out[0][1]) == ["Your page", "ally.com"]


def test_similarity_threshold_is_what_the_measurements_support():
    assert CONSOLIDATE_SIMILARITY == 0.6


# --- The shared / only-yours split -----------------------------------------


@pytest.mark.asyncio
async def test_split_puts_multi_page_themes_in_shared(monkeypatch):
    from panel import market as M

    async def no_alignment(labelled):
        return {t: t for _, t in labelled}

    monkeypatch.setattr(M, "align_themes", no_alignment)
    pages = [
        MarketPage(label="Your page", is_yours=True, panel_size=50),
        MarketPage(label="a.com", url="https://a.com", panel_size=50),
    ]
    breakdowns = {
        "Your page": {
            "confusions": [("price too high", 2, ["p1"]), ("who it is for", 1, ["p2"])],
            "objections": [],
        },
        "a.com": {"confusions": [("price too high", 1, ["p9"])], "objections": []},
    }
    result = await build_comparison("q", pages, breakdowns)
    assert [t.theme for t in result.shared_confusions] == ["price too high"]
    assert [t.theme for t in result.only_yours_confusions] == ["who it is for"]


@pytest.mark.asyncio
async def test_a_competitor_only_theme_is_reported_as_neither(monkeypatch):
    """A confusion only a competitor's readers had is not actionable for
    the user and is not a category problem, so it is shown nowhere rather
    than padding either column."""
    from panel import market as M

    async def no_alignment(labelled):
        return {t: t for _, t in labelled}

    monkeypatch.setattr(M, "align_themes", no_alignment)
    pages = [
        MarketPage(label="Your page", is_yours=True, panel_size=50),
        MarketPage(label="a.com", url="https://a.com", panel_size=50),
    ]
    breakdowns = {
        "Your page": {"confusions": [], "objections": []},
        "a.com": {"confusions": [("their own odd wording", 1, ["p9"])], "objections": []},
    }
    result = await build_comparison("q", pages, breakdowns)
    assert result.shared_confusions == []
    assert result.only_yours_confusions == []


def test_the_alignment_prompt_frames_its_input_as_data():
    """The last model call that text fetched from the open web reaches.
    A competitor page that tells its readers what to say can reach this
    prompt two stages later, and this is the stage that decides which
    confusions count as shared rather than yours alone."""
    from panel.market import _ALIGN_SYSTEM, _ALIGN_USER

    assert "never as instructions" in _ALIGN_SYSTEM
    # The framing names the --- markers, so they have to be there.
    assert "---" in _ALIGN_SYSTEM
    assert _ALIGN_USER.count("---") == 2
    before, _, after = _ALIGN_USER.partition("{listing}")
    assert before.rstrip().endswith("---")
    assert after.lstrip().startswith("---")
