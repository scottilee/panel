"""Find comparable pages via Tavily, so the panel can read the market.

Two Tavily capabilities, each doing what it is good at: Search to discover
candidate pages, Extract to pull clean text from the ones we pick.

The hard part is not the plumbing, it is the picking. Measured against the
live API (2026-10-29), a query like "meal planning app pricing page"
returns 8 results of which exactly ONE is a company's own product page --
the rest are listicles, trade news and market-research profiles. Running
the panel on a CB Insights company profile would produce a confident
report about a page nobody is trying to sell anything on.

So candidates pass three gates, cheapest first:

1. Tavily's own exclude_domains, for the review sites we already know.
2. Local heuristics on host, path and title (this file).
3. One Nano call that picks the best few from what survives.

And then the UI shows exactly which URLs were used, because none of this
is reliable enough to hide. A bad pick the user can see and dismiss is a
far better failure than a bad pick presented as the market.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urlparse

import requests
from pydantic import BaseModel, Field

from panel.config import MODEL_NANO
from panel.tf_client import chat_json, env

TAVILY_API_KEY = env("TAVILY_API_KEY")
TAVILY_SEARCH_URL = "https://api.tavily.com/search"
REQUEST_TIMEOUT_S = 30

# Ask for plenty, because most get filtered out. Measured hit rate for
# "company's own product page" was 1-3 in 8.
SEARCH_MAX_RESULTS = 12
# How many competitor pages the panel actually reads. Each one is a real
# panel run, so this is the main cost lever.
COMPETITOR_PAGES = 3


class MarketQuery(BaseModel):
    """What to search for, derived from the user's own copy."""

    category: str = Field(max_length=80)
    query: str = Field(max_length=120)


class Candidate(BaseModel):
    url: str
    title: str
    score: float = 0.0


# Review sites, aggregators, news and research shops. Passed to Tavily as
# exclude_domains so they never consume a result slot, and checked again
# locally because the API's exclusion is best-effort on subdomains.
EXCLUDED_DOMAINS = [
    "nerdwallet.com", "cnbc.com", "forbes.com", "usnews.com", "investopedia.com",
    "yahoo.com", "finance.yahoo.com", "investing.com", "reddit.com", "medium.com",
    "wikipedia.org", "techcrunch.com", "g2.com", "capterra.com", "getapp.com",
    "trustpilot.com", "producthunt.com", "cbinsights.com", "tracxn.com",
    "crunchbase.com", "pcmag.com", "cnet.com", "zdnet.com", "businessinsider.com",
    "bankrate.com", "wsj.com", "nytimes.com", "theverge.com", "wired.com",
    "quora.com", "youtube.com", "linkedin.com", "facebook.com", "x.com",
    "twitter.com", "instagram.com", "tiktok.com", "github.com",
    # App-store listings are laid out by the store, not the company, so a
    # panel reading one is reacting to Google's or Apple's page design
    # rather than to anybody's copy.
    "play.google.com", "apps.apple.com",
]

# Path segments that mean "an article about the category" rather than
# "a page selling something".
_EDITORIAL_PATH = re.compile(
    r"/(blog|blogs|news|article|articles|press|stories|story|resources|guides?|"
    r"learn|academy|magazine|insights?|updates?|posts?|tips|help|support|docs|"
    r"documentation|reviews?|compare|comparison|alternatives?|vs|versus|"
    r"best-|top-\d)",
    re.I,
)

# Titles that give away a listicle or explainer even on a plausible host.
# "best" ANYWHERE, not anchored at the start. The anchored version let
# through "I Tried the Best Meal Planning Apps -- These Are My Favorites",
# which is as editorial as a title gets.
_EDITORIAL_TITLE = re.compile(
    r"\bbest\b|^\s*top\s+\d|\bvs\.?\b|\bversus\b|\balternatives?\b|"
    r"\breview(s|ed)?\b|\bwhat is\b|\bhow (to|much)\b|\bguide\b|\branked\b|"
    r"\bround-?up\b|\bi tried\b|\bmy favou?rites?\b|\bshould you\b|"
    r"\(20\d\d\)|\b20\d\d\s*[:|-]",
    re.I,
)


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def _is_excluded_host(url: str) -> bool:
    host = _host(url)
    return any(host == d or host.endswith("." + d) for d in EXCLUDED_DOMAINS)


def looks_like_a_product_page(candidate: Candidate) -> bool:
    """Local gate. Conservative in the direction of rejecting: a rejected
    good page costs nothing (there are more candidates), while an accepted
    listicle costs a whole panel run and produces a meaningless comparison.
    """
    if _is_excluded_host(candidate.url):
        return False
    path = urlparse(candidate.url).path or "/"
    if _EDITORIAL_PATH.search(path):
        return False
    if _EDITORIAL_TITLE.search(candidate.title or ""):
        return False
    # Deep paths are usually articles; product and pricing pages sit near
    # the root. Measured: /pricing, /savings/high-yield-savings, / -- all
    # 0-2 segments.
    segments = [s for s in path.split("/") if s]
    return len(segments) <= 3


def search_candidates(query: str, exclude_url: str | None = None) -> list[Candidate]:
    """Tavily Search, with the obvious editorial sources excluded up front."""
    if not TAVILY_API_KEY:
        raise RuntimeError("TAVILY_API_KEY is not set")
    exclude = list(EXCLUDED_DOMAINS)
    if exclude_url:
        # Never compare the user's page against itself.
        own = _host(exclude_url)
        if own:
            exclude.append(own)
    resp = requests.post(
        TAVILY_SEARCH_URL,
        json={
            "query": query,
            "max_results": SEARCH_MAX_RESULTS,
            "search_depth": "basic",
            "exclude_domains": exclude,
        },
        headers={
            "Authorization": f"Bearer {TAVILY_API_KEY}",
            "Content-Type": "application/json",
        },
        timeout=REQUEST_TIMEOUT_S,
    )
    resp.raise_for_status()
    results = resp.json().get("results") or []
    candidates = [
        Candidate(
            url=r.get("url") or "",
            title=r.get("title") or "",
            score=float(r.get("score") or 0.0),
        )
        for r in results
        if r.get("url")
    ]
    # One page per host: three pricing pages from the same company tell
    # you nothing about a market.
    seen: set[str] = set()
    unique = []
    for c in candidates:
        host = _host(c.url)
        if host in seen:
            continue
        seen.add(host)
        unique.append(c)
    return unique


_QUERY_SYSTEM = (
    "You turn a piece of marketing copy into a web search that will find "
    "competing companies' own pages for the same kind of product. Output "
    "only a JSON object matching the schema, no prose, no code fences."
)

_QUERY_USER = """This is copy from one company's page:
---
{artifact_text}
---

Answer with this schema:
{schema}

- category: what kind of product or service this is, in two to five plain words, as a customer would say it ("meal planning app", "high yield savings account", "city parking permit scheme").
- query: a web search that would surface OTHER companies' own pages for that same category -- their pricing or product pages, not articles about the category. Name the category and the kind of page. Do not include this company's own name.
"""


async def derive_query(artifact_text: str) -> MarketQuery:
    """One Nano call: copy -> what to search for."""
    result = await chat_json(
        model=MODEL_NANO,
        system=_QUERY_SYSTEM,
        user=_QUERY_USER.format(
            artifact_text=artifact_text[:4000],
            schema=json.dumps(MarketQuery.model_json_schema()),
        ),
        schema=MarketQuery,
        temperature=0.1,
        max_tokens=3000,
    )
    return result.data


class _Picked(BaseModel):
    urls: list[str] = Field(max_length=COMPETITOR_PAGES)


_PICK_SYSTEM = (
    "You select which search results are a company's own product or "
    "pricing page, as opposed to an article, review or listicle about the "
    "category. Output only a JSON object matching the schema, no prose, "
    "no code fences."
)

_PICK_USER = """Someone is comparing their own {category} page against competitors'.

These are search results. Pick up to {limit} that are a company's OWN page selling a {category} -- the kind of page a customer would land on and be asked to sign up or buy.

{numbered}

Answer with this schema:
{schema}

- urls: the chosen URLs, copied exactly, best first. Pick fewer than {limit}, or none at all, rather than including an article, a "best X" roundup, a review site, a news story or a company directory. A wrong pick is worse than a short list.
"""


async def pick_pages(
    candidates: list[Candidate], category: str, limit: int = COMPETITOR_PAGES
) -> list[Candidate]:
    """Model gate, after the local one. Classification over a short list,
    which these models do well -- unlike counting over a long one."""
    survivors = [c for c in candidates if looks_like_a_product_page(c)]
    if not survivors:
        return []

    # The model gate runs even when there are few survivors. Short-
    # circuiting here when len(survivors) <= limit was a real bug: on the
    # meal-planning category only two candidates survived the heuristics
    # and BOTH were blog posts the heuristics had missed, so skipping the
    # better filter returned exactly the two pages it existed to reject.
    numbered = "\n".join(f"{i + 1}. {c.url}\n   title: {c.title}" for i, c in enumerate(survivors))
    try:
        result = await chat_json(
            model=MODEL_NANO,
            system=_PICK_SYSTEM,
            user=_PICK_USER.format(
                category=category,
                limit=limit,
                numbered=numbered,
                schema=json.dumps(_Picked.model_json_schema()),
            ),
            schema=_Picked,
            temperature=0.1,
            max_tokens=3000,
        )
        by_url = {c.url: c for c in survivors}
        chosen = [by_url[u] for u in result.data.urls if u in by_url]
        if chosen:
            return chosen[:limit]
    except Exception as e:  # noqa: BLE001
        print(f"market: page pick failed, using the local ranking: {type(e).__name__}: {e}")
    # Fall back to Tavily's own relevance order. Worse, but never empty
    # when there were survivors.
    return sorted(survivors, key=lambda c: -c.score)[:limit]


# --- Comparing the panels ---------------------------------------------------


def _group_across_pages(
    per_page: list[tuple[str, list[tuple[str, int, list[str]]]]],
    canonical: dict[str, str] | None = None,
) -> list[tuple[str, list[str], list[str]]]:
    """-> [(label, page_labels, evidence_ids)], most widely raised first.

    Grouped with synthesize.theme_key, the same normalization that merges
    phrasings within one page, so "price too high" on your page and "the
    price is too high" on a competitor's count as one theme rather than
    two.

    All of this is Python. The model's job was to say what each page's
    readers were confused by; how many pages share a confusion is
    arithmetic over short lists, and this project's whole stance is that
    arithmetic belongs here (see synthesize._apply_recounted_pct).
    """
    from panel.synthesize import theme_key

    merged: dict[str, dict] = {}
    for page_label, themes in per_page:
        for theme, _shards, ids in themes:
            # align_themes has already mapped equivalent wordings to one
            # canonical phrase; theme_key then still catches anything it
            # missed, so the two filters compose rather than compete.
            aligned = (canonical or {}).get(theme, theme)
            entry = merged.setdefault(theme_key(aligned), {"labels": [], "pages": [], "ids": []})
            entry["labels"].append(aligned)
            if page_label not in entry["pages"]:
                entry["pages"].append(page_label)
            entry["ids"].extend(ids)

    rows = []
    for entry in merged.values():
        # Shortest phrasing reads best, same rule as the within-page merge.
        label = min(entry["labels"], key=len)
        rows.append((label, entry["pages"], list(dict.fromkeys(entry["ids"]))[:5]))
    rows.sort(key=lambda r: (-len(r[1]), r[0]))
    return rows


# Final consolidation of near-duplicate rows, after alignment and lexical
# grouping have both run.
#
# Observed on a real benchmark: the "only your page" column listed
#
#   "How FDIC pass-through coverage works across multiple partner banks"
#   "How FDIC pass-through coverage works beyond $250k across multiple banks"
#
# as two separate findings. They are one. Neither earlier filter catches
# it: theme_key's subset rule needs the longer phrase to add at most one
# word, and neither phrase is a subset of the other ("partner" is only in
# the first). The alignment model is asked for overlaps and simply missed
# this pair.
#
# Word-set overlap does catch it, at 0.73. The guards keep it from
# over-merging: both rows need at least 3 significant words, since short
# themes have noisy overlap, and the pairs that must stay apart measure
# far below the threshold ("price too high" vs "price unclear" is 0.33,
# "hidden fees" vs "unclear fees" 0.33).
CONSOLIDATE_SIMILARITY = 0.6
CONSOLIDATE_MIN_WORDS = 3


def _consolidate(rows: list[tuple[str, list[str], list[str]]]) -> list[tuple[str, list[str], list[str]]]:
    from panel.synthesize import theme_key

    out: list[tuple[str, list[str], list[str]]] = []
    for theme, pages, ids in rows:
        words = set(theme_key(theme).split())
        merged_into = None
        if len(words) >= CONSOLIDATE_MIN_WORDS:
            for i, (kept_theme, kept_pages, kept_ids) in enumerate(out):
                other = set(theme_key(kept_theme).split())
                if len(other) < CONSOLIDATE_MIN_WORDS:
                    continue
                overlap = len(words & other) / len(words | other)
                if overlap >= CONSOLIDATE_SIMILARITY:
                    merged_into = i
                    # Keep the shorter phrasing and union the pages, so a
                    # theme that turns out to be shared is not left
                    # looking unique to one page.
                    label = kept_theme if len(kept_theme) <= len(theme) else theme
                    pages_union = kept_pages + [p for p in pages if p not in kept_pages]
                    out[i] = (label, pages_union, list(dict.fromkeys(kept_ids + ids))[:5])
                    break
        if merged_into is None:
            out.append((theme, pages, ids))
    # Re-sorted: consolidation can turn a one-page row into a two-page
    # one, which changes the ordering _group_across_pages established and,
    # more importantly, which side of the shared/only-yours split it lands
    # on.
    out.sort(key=lambda r: (-len(r[1]), r[0]))
    return out


# Caps on what reaches the UI. The point of this feature is "the two or
# three things every page in your category gets wrong", not an exhaustive
# audit, and a long list buries the finding.
MAX_SHARED = 5
MAX_ONLY_YOURS = 5


async def build_comparison(
    query: str,
    pages: list[MarketPage],
    breakdowns: dict[str, dict[str, list[tuple[str, int, list[str]]]]],
) -> "MarketComparison":
    """breakdowns maps page label -> synthesize.theme_breakdown output."""
    from panel.schemas import MarketComparison, MarketTheme

    yours = next((p.label for p in pages if p.is_yours), None)

    # One alignment call per category, over a few dozen short phrases.
    alignments: dict[str, dict[str, str]] = {}
    for category in ("confusions", "objections"):
        labelled = [
            (p.label, theme)
            for p in pages
            for theme, _s, _i in breakdowns.get(p.label, {}).get(category, [])
        ]
        alignments[category] = await align_themes(labelled)

    def rows_for(category: str) -> list[tuple[str, list[str], list[str]]]:
        return _consolidate(
            _group_across_pages(
                [(p.label, breakdowns.get(p.label, {}).get(category, [])) for p in pages],
                alignments.get(category),
            )
        )

    def split(category: str) -> tuple[list[MarketTheme], list[MarketTheme]]:
        shared, only_yours = [], []
        for theme, page_labels, ids in rows_for(category):
            entry = MarketTheme(theme=theme, pages=page_labels, evidence=ids)
            if len(page_labels) >= 2:
                shared.append(entry)
            elif yours is not None and page_labels == [yours]:
                # Raised on the user's page and on no competitor's. The
                # actionable half: everything else is a category-wide
                # problem they cannot fix by editing their own page.
                only_yours.append(entry)
        return shared[:MAX_SHARED], only_yours[:MAX_ONLY_YOURS]

    shared_c, only_c = split("confusions")
    shared_o, only_o = split("objections")
    return MarketComparison(
        query=query,
        pages=pages,
        shared_confusions=shared_c,
        shared_objections=shared_o,
        only_yours_confusions=only_c,
        only_yours_objections=only_o,
    )


# --- Aligning themes across pages ------------------------------------------
#
# Lexical grouping alone is not enough here, and the direction it fails in
# is the dangerous one. Within one page the themes come from a single
# model call and phrase consistently, so theme_key merges them well.
# Across pages they come from different calls: "no proof", "no proof of
# savings" and "nothing backs the claims" are one objection in three
# wordings, and theme_key keeps all three apart.
#
# Under-merging does not just lose a shared theme -- it reports it as
# "raised only on your page", which is a confident claim that the user's
# copy is uniquely bad when it is not. So one small model call aligns the
# wordings first, and Python still does every count.
#
# This is a short-list classification (a few dozen phrases), not the
# long-list bookkeeping that broke the condensing stage three times.


class _ThemeGroup(BaseModel):
    theme: str = Field(max_length=120)
    members: list[str]


class _Alignment(BaseModel):
    groups: list[_ThemeGroup]


_ALIGN_SYSTEM = (
    "You group differently-worded descriptions of the same reader reaction. "
    "Output only a JSON object matching the schema, no prose, no code fences."
)

# Asks ONLY for the overlaps, never for a partition.
#
# The first version ended with "every line must appear in exactly one
# group", which is exhaustive assignment over a list -- the exact pattern
# that broke the condensing stage three times and is written up in
# FEEDBACK.md as a thing not to do. It duly ran away: zero content at
# 4000, 8000 and 16000 tokens in turn, then fell back to lexical
# grouping, which is what alignment exists to improve on.
#
# Asking only "which of these mean the same thing as each other" is a far
# smaller job and a far smaller answer. Lines the model does not mention
# keep their own identity in align_themes, so nothing is lost by leaving
# them out -- which means the completeness constraint bought nothing even
# when it worked.
_ALIGN_USER = """Readers reacted to several different pages selling the same kind of thing. Each page's reactions were summarized separately, so the same reaction is often worded differently from page to page.

{listing}

Find only the lines above that mean the same thing as each other, and report them with this schema:
{schema}

Rules:
- One group per reaction that appears more than once in different words. theme is the clearest short wording; members lists the matching lines, copied exactly.
- "No proof", "no proof of savings" and "nothing backs up the claims" are one group. "Price is too high" and "I can't tell what the price is" are NOT -- one is about the amount, the other about not finding it.
- Leave out any line that means something nothing else on the list means. Do not group a line with itself, do not list every line, and do not count or order anything. Groups of one are not wanted.
"""


async def align_themes(labelled: list[tuple[str, str]]) -> dict[str, str]:
    """[(page_label, theme)] -> {theme: canonical theme}.

    Falls back to identity on any failure, which degrades to the lexical
    grouping that _group_across_pages does anyway.
    """
    themes = list(dict.fromkeys(t for _, t in labelled))
    if len(themes) < 2:
        return {t: t for t in themes}
    listing = "\n".join(f"- {t}" for t in themes)
    try:
        result = await chat_json(
            model=MODEL_NANO,
            system=_ALIGN_SYSTEM,
            user=_ALIGN_USER.format(
                listing=listing, schema=json.dumps(_Alignment.model_json_schema())
            ),
            schema=_Alignment,
            temperature=0.1,
            # Small because the answer is small -- only the overlaps. Per
            # the budget measurements in tf_client, a modest ceiling with
            # retries beats a large one: a runaway eats whatever it is
            # given, so the ceiling's job is to make a doomed attempt cheap.
            max_tokens=4000,
        )
    except Exception as e:  # noqa: BLE001
        print(f"market: theme alignment failed, falling back to lexical: {type(e).__name__}: {e}")
        return {t: t for t in themes}

    canonical: dict[str, str] = {}
    for group in result.data.groups:
        for member in group.members:
            if member in canonical:
                continue  # first group wins; the prompt asks for exactly one
            if member in themes:
                canonical[member] = group.theme.strip() or member
    # Anything the model dropped keeps its own identity rather than
    # vanishing from the comparison.
    for t in themes:
        canonical.setdefault(t, t)
    return canonical
