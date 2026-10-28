"""synthesize(artifact_text, personas, reactions) -> Synthesis. Section 5.3."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import re
from collections import defaultdict

from typing import get_args

from pydantic import BaseModel, Field

from panel.config import MODEL_NANO, MODEL_ULTRA
from panel.personas import AGE_BANDS, age_band_for
from panel.prompts import SYNTHESIS_SYSTEM, synthesis_user
from panel.schemas import INCOME_BANDS, Persona, REGIONS, Reaction, SegmentClause, Synthesis
from panel.tf_client import ChatJsonError, ChatJsonTruncatedError, chat_json


def apply_edit(artifact_text: str, current_text: str, proposed_text: str) -> str | None:
    """Replaces the first occurrence of current_text with proposed_text.
    Tries an exact match first, then falls back to matching with internal
    whitespace runs treated as flexible (\\s+) -- measured (2026-10-27)
    that Ultra's current_text, when it spans a line break, sometimes
    doesn't match the artifact's exact whitespace/newlines verbatim even
    though every word is right, which otherwise silently no-ops the edit
    (compare then reruns against the *unchanged* text and reports near-zero
    flips, which looks like the edit failed when the applier did). Returns
    None if even the flexible match fails, so callers can tell "nothing to
    apply" apart from "produced an unedited string".
    """
    if current_text in artifact_text:
        return artifact_text.replace(current_text, proposed_text, 1)
    tokens = current_text.split()
    if not tokens:
        return None
    pattern = r"\s+".join(re.escape(t) for t in tokens)
    match = re.search(pattern, artifact_text)
    if not match:
        return None
    return artifact_text[: match.start()] + proposed_text + artifact_text[match.end() :]

# Ultra reasons before emitting content (tf_client.py's module docstring);
# a Synthesis object is much bigger than a Reaction, so start with real
# headroom instead of relying solely on chat_json's truncation-retry
# doubling to get there eventually.
SYNTHESIS_MAX_TOKENS = 6000

# How much reasoning-token budget a single call burns is stochastic, not
# just a function of prompt size -- measured (2026-10-27) the identical
# synthesis call truncate at max_tokens=24000 (after chat_json's own 2
# doubling retries from 6000) on one attempt, and succeed cleanly at 6000
# on a fresh attempt of the exact same prompt. So on top of chat_json's
# per-call budget escalation, synthesize() retries the whole call a
# couple of times outer-loop style -- a plain retry can simply get
# luckier, which escalating the budget further does not reliably fix.
SYNTHESIS_OUTER_RETRIES = 2


async def _synthesis_call(system: str, user: str) -> Synthesis:
    last_exc: ChatJsonTruncatedError | None = None
    for attempt in range(SYNTHESIS_OUTER_RETRIES + 1):
        try:
            result = await chat_json(
                model=MODEL_ULTRA,
                system=system,
                user=user,
                schema=Synthesis,
                temperature=0.2,
                max_tokens=SYNTHESIS_MAX_TOKENS,
            )
            return result.data
        except ChatJsonTruncatedError as e:
            last_exc = e
            print(f"synthesize: attempt {attempt + 1} truncated, retrying: {e}")
    raise last_exc  # type: ignore[misc]

AFFECTED_PCT_TOLERANCE = 10


NUMERIC_SEGMENT_FIELDS = {
    "tech_comfort",
    "skepticism",
    "openness",
    "conscientiousness",
    "extraversion",
    "agreeableness",
    "neuroticism",
}


def _persona_field(persona: Persona, field: str):
    if field == "age_band":
        return age_band_for(persona.age)
    return getattr(persona, field)


def _coerce_target(field: str, target):
    """SegmentClause.value is typed Union[str, int, list[...]] because it
    has to cover both numeric fields (tech_comfort, skepticism) and
    string ones (region, income_band, age_band) -- pydantic accepts
    either representation for a numeric field (observed: Ultra emitting
    {"field": "skepticism", "op": ">=", "value": "4"}, value as a string),
    which then crashes a direct >= comparison against persona.skepticism
    (an int). Coerce by field, not by the value's own type, since the
    value's type is exactly what's unreliable here.
    """
    if field not in NUMERIC_SEGMENT_FIELDS:
        return target
    if isinstance(target, list):
        return [int(v) for v in target]
    return int(target)


def _clause_matches(persona: Persona, clause: SegmentClause) -> bool:
    value = _persona_field(persona, clause.field)
    target = _coerce_target(clause.field, clause.value)
    if clause.op == "==":
        return value == target
    if clause.op == "!=":
        return value != target
    if clause.op == "<=":
        return value <= target
    if clause.op == ">=":
        return value >= target
    if clause.op == "<":
        return value < target
    if clause.op == ">":
        return value > target
    if clause.op == "in":
        return value in target
    raise ValueError(f"unknown segment op: {clause.op}")


def recount_affected_pct(personas_by_id: dict[str, Persona], clauses: list[SegmentClause]) -> float:
    if not personas_by_id:
        return 0.0
    matched = sum(1 for p in personas_by_id.values() if all(_clause_matches(p, c) for c in clauses))
    return 100 * matched / len(personas_by_id)


# The legal values for the three enumerated segment fields, derived from
# the schema Literals rather than retyped, so they cannot drift from it.
_ENUM_SEGMENT_VALUES: dict[str, set[str]] = {
    "region": set(get_args(REGIONS)),
    "income_band": set(get_args(INCOME_BANDS)),
    "age_band": set(AGE_BANDS),
}


def unknown_clause_values(clauses: list[SegmentClause]) -> list[str]:
    """Values in enum-field clauses that match no real persona attribute.

    SegmentClause.value is typed `str | int | list[...]` -- wide enough to
    cover numeric and string fields both -- so a band name Ultra invents
    validates fine and then matches nobody. Observed live (2026-10-28,
    N=10): {"field": "age_band", "op": "in", "value": ["50-59", "70+"]}
    for a finding whose own prose said "users aged 50 and older". The real
    bands are 50-64 and 65+, so the recount found 0 personas and
    _apply_recounted_pct overwrote a true 40% with 0% -- a confidently
    wrong number, which is worse than the estimate it replaced.
    """
    unknown: list[str] = []
    for clause in clauses:
        legal = _ENUM_SEGMENT_VALUES.get(clause.field)
        if legal is None:
            continue
        values = clause.value if isinstance(clause.value, list) else [clause.value]
        unknown.extend(str(v) for v in values if str(v) not in legal)
    return unknown


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _tech_band(tech_comfort: int) -> str:
    # tech_comfort and skepticism are 1-100 (not the spec's original 1-5,
    # widened for more variability per request on 2026-10-28). Tri-section
    # the new range instead of the old literal 1/2/3 cutoffs.
    return "low" if tech_comfort <= 33 else "mid" if tech_comfort <= 66 else "high"


def _skepticism_band(skepticism: int) -> str:
    return "low" if skepticism <= 33 else "mid" if skepticism <= 66 else "high"


def _by_segment(personas_by_id: dict[str, Persona], reactions: list[Reaction], key_fn) -> dict:
    buckets: dict[str, list[int]] = defaultdict(list)
    for r in reactions:
        p = personas_by_id.get(r.persona_id)
        if p is None:
            continue
        buckets[key_fn(p)].append(r.sentiment)
    return {k: round(_mean(v), 2) for k, v in sorted(buckets.items())}


def build_persona_summary_csv(personas: list[Persona]) -> str:
    """Compact CSV, not the full persona JSON, to keep the prompt small:
    ~70 bytes a persona against ~400 tokens for a full Persona JSON
    (section 4.3). Emitted in full at every N -- 1,000 rows is ~18k
    tokens, which is still the cheap half of the synthesis prompt.

    Written with csv.writer rather than an f-string join because
    `occupation` is free text from Super: a single comma in it ("Chef, line"
    did appear) shifted every later column of that row, silently handing
    Ultra a persona table where ages and regions no longer lined up with
    their headers.
    """
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    # age_band is included alongside age even though it's derived from it.
    # Without it Ultra had never seen a legal band name -- only raw ages --
    # and invented its own ("50-59", "70+"), which matched no persona, so
    # the recount below replaced a real 40% with 0%. Showing the band is
    # cheaper and more reliable than asking it to bucket ages itself.
    writer.writerow(
        [
            "id",
            "age",
            "age_band",
            "region",
            "income_band",
            "tech_comfort",
            "skepticism",
            "occupation",
        ]
    )
    for p in personas:
        writer.writerow(
            [
                p.id,
                p.age,
                age_band_for(p.age),
                p.region,
                p.income_band,
                p.tech_comfort,
                p.skepticism,
                p.occupation,
            ]
        )
    return buf.getvalue().rstrip("\n")


_EVIDENCE_ID_RE = re.compile(r"\bp\d{3,}\b")


def _normalize_evidence(synthesis: Synthesis, personas_by_id: dict[str, Persona]) -> Synthesis:
    """Measured (2026-10-28, ad_v1): Ultra sometimes decorates evidence
    entries with the supporting quote instead of returning a bare ID --
    e.g. 'p004: "I don\\'t get how the FDIC pass-through works"' -- and a
    retry asking for bare IDs produced the same thing again. Those
    decorated strings match no persona, which silently breaks the
    Findings panel's evidence avatars (they highlight tiles by exact
    persona_id). Pull the first real ID out of each entry rather than
    discarding otherwise-correct evidence over formatting; entries with
    no recoverable ID are dropped, and _invalid_evidence still reports
    anything left unresolvable.
    """
    new_findings = []
    for finding in synthesis.findings:
        fixed: list[str] = []
        for entry in finding.evidence:
            if entry in personas_by_id:
                fixed.append(entry)
                continue
            match = _EVIDENCE_ID_RE.search(entry)
            if match and match.group(0) in personas_by_id:
                print(f"synthesize: normalized evidence entry {entry[:60]!r} -> {match.group(0)}")
                fixed.append(match.group(0))
        # Finding.evidence requires 3-5 entries; if normalization leaves
        # too few, keep the originals so _invalid_evidence can report it
        # rather than raising a validation error on the model_copy here.
        new_findings.append(finding.model_copy(update={"evidence": fixed}) if 3 <= len(fixed) <= 5 else finding)
    return synthesis.model_copy(update={"findings": new_findings})


def _invalid_evidence(synthesis: Synthesis, personas_by_id: dict[str, Persona]) -> list[str]:
    problems: list[str] = []
    for i, finding in enumerate(synthesis.findings):
        unknown = [pid for pid in finding.evidence if pid not in personas_by_id]
        if unknown:
            problems.append(f"finding {i}: evidence IDs not found in this run: {unknown}")
    return problems


def _apply_recounted_pct(synthesis: Synthesis, personas_by_id: dict[str, Persona]) -> Synthesis:
    """Section 5.3 says affected_pct should be "computed from the reactions,
    not estimated," and specifies retrying once on a >10-point discrepancy.
    Measured (2026-10-27, pricing_v1, 100 reactions): Ultra's claimed
    percentages were off by 12-70 points, and a retry with the exact
    discrepancy spelled out produced the *identical* wrong numbers --
    Ultra is estimating from the narrative, not actually counting over a
    100-item JSON blob, and asking it to try again doesn't fix that. We
    already have the real count (that's what affected_segment_filter is
    for), so there's no reason to keep asking the model to get arithmetic
    right that Python can just get right. affected_segment_filter (which
    Ultra proposes) and the qualitative claim are kept as-is; only the
    number is overwritten.
    """
    new_findings = []
    headline = synthesis.headline
    for finding in synthesis.findings:
        # A filter citing a band/region/income value that doesn't exist
        # cannot be recounted -- it would match zero personas for a reason
        # that has nothing to do with the data. Keep Ultra's own estimate
        # in that case: an unverified number beats a verified-wrong one.
        unknown = unknown_clause_values(finding.affected_segment_filter)
        if unknown:
            print(
                f"synthesize: keeping Ultra's affected_pct {finding.affected_pct} for segment "
                f"{finding.affected_segment!r} -- its filter cites value(s) that match no "
                f"persona attribute: {unknown}"
            )
            new_findings.append(finding)
            continue
        recount = round(recount_affected_pct(personas_by_id, finding.affected_segment_filter))
        if abs(recount - finding.affected_pct) > AFFECTED_PCT_TOLERANCE:
            print(
                f"synthesize: overwriting affected_pct {finding.affected_pct} -> "
                f"{recount} for segment {finding.affected_segment!r} (Ultra's claimed "
                f"number was off by {abs(recount - finding.affected_pct)} points)"
            )
            # The headline usually cites the same (wrong) number verbatim
            # ("85% of..."), since it's written by the same call that got
            # the finding's number wrong. Patch it too, or the demo's most
            # prominent line visibly contradicts the finding card right
            # under it.
            headline = re.sub(rf"\b{finding.affected_pct}%", f"{recount}%", headline)
        new_findings.append(finding.model_copy(update={"affected_pct": recount}))
    return synthesis.model_copy(update={"findings": new_findings, "headline": headline})


# --- Sharded digest stage (large panels) ------------------------------------
#
# One Reaction serializes to roughly 400 tokens, so the single-call prompt
# in section 4.3 is ~40k tokens at 100 personas -- fine -- and ~400k at
# 1000, which is both past Ultra's usable context and the most expensive
# way imaginable to ask one question. Above SHARD_THRESHOLD the reactions
# are condensed into themes in batches first, and Ultra reads those.
#
# Condensed by NANO, not Super, which is not where this started. Super is
# the mid tier and "summarize a batch" looks like a mid-tier job, so it
# got the work first. Raced head to head on the same real 50-reaction
# shard (2026-10-28):
#
#   Nano   83s, 10 themes, 5 quotes
#   Super  157s, 13 themes, 0 quotes  (dropped a required field)
#
# Twice the wall-clock for a worse answer, and in a live N=200 run Super
# exhausted the entire truncation ladder (12000 -> 24000 -> 48000) on some
# shards. Super keeps the job it is genuinely needed for -- writing the
# persona pool offline, where quality compounds into every future run --
# and the per-batch condensing goes to the cheap tier that measurably does
# it better.
#
# Below the threshold nothing changes at all -- the 100-persona demo path
# is byte-for-byte the prompt it has always been.

SHARD_THRESHOLD = 150
# 50, not the 100 this started at, with the concurrency raised to match so
# the smaller shards cost no extra wall-clock. The real fix for the
# failures described above _DIGEST_USER_TEMPLATE was simplifying the task,
# not shrinking the batch -- but a shorter batch is less to get through
# whatever reasoning the model does do, and at 20 shards for a full 1,000
# panel they still run in two waves.
SHARD_SIZE = 50
SHARD_CONCURRENCY = 10
# 24000. Measured, not guessed, and larger than it looks like it should
# be: the prompt for a 50-reaction shard is only ~5k tokens and the output
# well under 2k, yet Nano truncated with *zero* content at 8000 and
# succeeded at 16000. (Super, before it lost this job, truncated at 8000,
# 12000 and 24000 alike.)
#
# This is the third time on this project that a budget which looked
# generous against prompt-plus-output was far too small against reasoning
# (reactions 400 -> 3000, persona batches 8000 -> 16000, digests 8000 ->
# 24000). Size Nemotron budgets from measured truncation, never from how
# big the visible text is: max_tokens is a ceiling, not a charge, so
# erring high costs nothing and erring low costs ~2.3x in silent retry.
DIGEST_MAX_TOKENS = 24000

# Example ids per theme. Enforced in the schema AND stated in the prompt
# from this one constant, so the two cannot drift: the cap is what keeps
# the task cheap (find a few instances, not classify all N), and a model
# that ignores it fails validation and gets chat_json's repair retry with
# the reason, rather than quietly grinding through the whole shard.
DIGEST_EXAMPLES_PER_THEME = 5
DIGEST_THEMES_PER_CATEGORY = 6


class _DigestTheme(BaseModel):
    """One recurring theme, with a few people who expressed it as examples.

    Note what is NOT here: a count, and an exhaustive membership list.
    Both were tried and both were too expensive -- see
    _DIGEST_USER_TEMPLATE. `persona_ids` is explicitly a handful of
    examples for Ultra to cite from, never a tally, and nothing downstream
    treats its length as a quantity.
    """

    theme: str
    persona_ids: list[str] = Field(max_length=DIGEST_EXAMPLES_PER_THEME)


class _ShardDigest(BaseModel):
    confusions: list[_DigestTheme]
    objections: list[_DigestTheme]
    requested_changes: list[_DigestTheme]
    quotes: list[str]


_DIGEST_SYSTEM = (
    "You group a batch of first-reaction responses by what they say, for a "
    "senior researcher who will write the final report. Never merge two "
    "genuinely different complaints into one theme. Output only a JSON "
    "object matching the schema, no prose, no code fences."
)

# Deliberately asks for themes and a handful of examples -- never for a
# count, an ordering, or an exhaustive membership list. Two measured
# failures got it here, on real N=200 runs:
#
# 1. Asking for `count` per theme ("count, do not estimate"), plus "most
#    common first" and "at most 8 each", both of which also require
#    counting and then sorting: Super spent 32000 completion tokens --
#    every rung of chat_json's retry ladder -- and emitted zero content,
#    losing half the panel's data.
# 2. Dropping the count but asking for *every* id under each theme: no
#    truncation, but the digest stage still ran for minutes, because
#    exhaustively classifying 50 reactions into 3 categories is the same
#    long serial task wearing a different hat.
#
# So the model is asked only for what it is actually good at -- "what did
# these people say" -- bounded to a handful of examples. Every number in
# the final report comes from Ultra's affected_segment_filter being
# recounted over the real personas in Python (see _apply_recounted_pct),
# which is where the project's numbers have come from all along.
_DIGEST_USER_TEMPLATE = """These are {n} people's first reactions to the same piece of text, one per line.

{reactions_text}

Summarize them into this schema:
{schema}

Rules:
- confusions: the distinct things people did not understand. objections: the distinct things that would stop them. requested_changes: the distinct changes they asked for. Give at most {max_themes} of each, as short phrases in plain words. Only include something more than one person raised.
- persona_ids: for each theme, 2 to {max_examples} ids from the reactions above as examples, exactly as given (e.g. "p042"). Examples only -- do not try to list everyone, do not count, and do not order the themes.
- quotes: 3 to 6 verbatim gut_reaction strings that best capture the range of this batch, each prefixed with its persona id and a colon.
- Do not editorialize, recommend, or evaluate the text. You are summarizing what these people said.
"""


def _reactions_for_digest(reactions: list[Reaction]) -> str:
    """One line per reaction, carrying only the five fields a thematic
    summary can use.

    Not the full Reaction JSON, which is what this sent at first: that
    repeats ten field names per record and ships run_id, latency_ms and
    raw_error to a model being asked what people said. 37% fewer input
    tokens for strictly less irrelevant material to wade through.
    (Measuring this is also what showed the input was never the problem
    here -- see DIGEST_MAX_TOKENS.)
    """
    return "\n".join(
        f"{r.persona_id} | gut: {r.gut_reaction} | confused by: {r.top_confusion} "
        f"| stopped by: {r.top_objection} | would change mind: {r.what_would_change_my_mind}"
        for r in reactions
    )


async def _digest_shard(
    shard: list[Reaction], sem: asyncio.Semaphore
) -> _ShardDigest | None:
    """One Nano call per shard. Returns None on failure rather than
    raising: a lost shard narrows the themes Ultra sees (and is declared
    to it in the label), while raising would throw away the whole run
    after every Nano call has already been paid for. Verified on a real
    run where one of two shards failed -- the report still came out
    coherent and correctly segmented."""
    usable = [r for r in shard if not r.raw_error]
    if not usable:
        print("synthesize: shard had no usable reactions, skipping its digest")
        return None
    user = _DIGEST_USER_TEMPLATE.format(
        n=len(usable),
        reactions_text=_reactions_for_digest(usable),
        schema=json.dumps(_ShardDigest.model_json_schema()),
        max_themes=DIGEST_THEMES_PER_CATEGORY,
        max_examples=DIGEST_EXAMPLES_PER_THEME,
    )
    async with sem:
        try:
            result = await chat_json(
                model=MODEL_NANO,
                system=_DIGEST_SYSTEM,
                user=user,
                schema=_ShardDigest,
                temperature=0.2,
                max_tokens=DIGEST_MAX_TOKENS,
            )
            return result.data
        except ChatJsonError as e:
            print(f"synthesize: shard digest failed, continuing without it: {e}")
            return None


def _clean_digest(digest: _ShardDigest, shard: list[Reaction]) -> _ShardDigest:
    """Drops ids that aren't in this shard, and themes left with none.

    The digest's whole value to Ultra is that the ids under a theme are a
    real evidence pool it can cite from, and a hallucinated id would get
    cited into a Finding and then fail _invalid_evidence two stages later,
    where the cause is no longer visible. Cheaper to check here, where the
    shard is right at hand.
    """
    real = {r.persona_id for r in shard}

    def clean(themes: list[_DigestTheme]) -> list[_DigestTheme]:
        out = []
        for t in themes:
            ids = [i for i in dict.fromkeys(t.persona_ids) if i in real]
            if ids:
                out.append(t.model_copy(update={"persona_ids": ids}))
        return out

    return digest.model_copy(
        update={
            "confusions": clean(digest.confusions),
            "objections": clean(digest.objections),
            "requested_changes": clean(digest.requested_changes),
        }
    )


def _format_digests(digests: list[tuple[int, _ShardDigest]]) -> str:
    """No counts printed, deliberately.

    persona_ids is a handful of examples, not a tally, so rendering
    "(3/50)" beside a theme would invent a statistic out of how many
    examples the model happened to give -- the exact confusion between
    evidence and quantity that broke the first two versions of this stage.
    Every number Ultra reports gets recounted from its
    affected_segment_filter instead.
    """
    lines = []
    for n, digest in digests:
        lines.append(f"--- a batch of {n} reactions ---")
        for label, themes in (
            ("confusions", digest.confusions),
            ("objections", digest.objections),
            ("requested changes", digest.requested_changes),
        ):
            lines.append(f"{label}:")
            for t in themes:
                lines.append(f"  - {t.theme} [e.g. {', '.join(t.persona_ids)}]")
        lines.append("representative quotes:")
        lines.extend(f"  {q}" for q in digest.quotes)
    return "\n".join(lines)


async def _digest_reactions(reactions: list[Reaction]) -> tuple[str, str]:
    """-> (block, label) for the synthesis prompt's reactions section."""
    shards = [reactions[i : i + SHARD_SIZE] for i in range(0, len(reactions), SHARD_SIZE)]
    sem = asyncio.Semaphore(SHARD_CONCURRENCY)
    print(f"synthesize: digesting {len(reactions)} reactions in {len(shards)} shards via Nano")
    results = await asyncio.gather(*(_digest_shard(s, sem) for s in shards))
    digests = []
    for shard, d in zip(shards, results):
        if d is None:
            continue
        cleaned = _clean_digest(d, shard)
        # A digest whose every theme was dropped contributes nothing but
        # would still inflate the "covering N of M reactions" claim below,
        # which is the one thing telling Ultra how much of the panel it is
        # actually reasoning about.
        if cleaned.confusions or cleaned.objections or cleaned.requested_changes:
            digests.append((len(shard), cleaned))
        else:
            print("synthesize: dropping an empty shard digest (no usable themes)")
    if not digests:
        raise ChatJsonError("every shard digest failed", 0)
    counted = sum(n for n, _ in digests)
    label = (
        f"Their reactions, digested by a first-pass model in {len(digests)} batches "
        f"covering {counted} of the {len(reactions)} reactions. These are themes, "
        "not tallies: the ids beside each one are a few examples, so do not treat "
        "them as counts. Cite evidence from the ids shown, and let "
        "affected_segment_filter carry every quantity -- it is recounted against the "
        "real personas, so an accurate filter matters more than an accurate guess:"
    )
    return _format_digests(digests), label


async def synthesize(artifact_text: str, personas: list[Persona], reactions: list[Reaction]) -> Synthesis:
    personas_by_id = {p.id: p for p in personas}

    mean_sentiment = _mean([r.sentiment for r in reactions])
    would_act_pct = (
        100 * sum(1 for r in reactions if r.would_act) / len(reactions) if reactions else 0.0
    )
    by_region = _by_segment(personas_by_id, reactions, lambda p: p.region)
    by_tech = _by_segment(personas_by_id, reactions, lambda p: _tech_band(p.tech_comfort))
    by_skep = _by_segment(personas_by_id, reactions, lambda p: _skepticism_band(p.skepticism))

    if len(reactions) > SHARD_THRESHOLD:
        reactions_block, reactions_label = await _digest_reactions(reactions)
    else:
        reactions_block = json.dumps([r.model_dump(mode="json") for r in reactions])
        reactions_label = "Their reactions:"

    user = synthesis_user(
        artifact_text=artifact_text,
        persona_summary_csv=build_persona_summary_csv(personas),
        reactions_json=reactions_block,
        reactions_label=reactions_label,
        mean_sentiment=mean_sentiment,
        would_act_pct=would_act_pct,
        by_region=json.dumps(by_region),
        by_tech_comfort=json.dumps(by_tech),
        by_skepticism=json.dumps(by_skep),
    )

    synthesis = await _synthesis_call(SYNTHESIS_SYSTEM, user)
    synthesis = _normalize_evidence(synthesis, personas_by_id)

    # Evidence IDs require judgment (which reactions actually support the
    # claim) so a retry is the right tool there -- but only for evidence
    # that's genuinely wrong, not merely badly formatted, which
    # _normalize_evidence has already repaired above. affected_pct needs
    # neither: see _apply_recounted_pct.
    problems = _invalid_evidence(synthesis, personas_by_id)
    if problems:
        retry_user = (
            user
            + "\n\nYour previous answer had discrepancies, fix them exactly:\n"
            + "\n".join(problems)
        )
        synthesis = _normalize_evidence(
            await _synthesis_call(SYNTHESIS_SYSTEM, retry_user), personas_by_id
        )
        remaining = _invalid_evidence(synthesis, personas_by_id)
        if remaining:
            print(f"WARNING: invalid evidence IDs remain after retry: {remaining}")

    return _apply_recounted_pct(synthesis, personas_by_id)
