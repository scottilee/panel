"""synthesize(artifact_text, personas, reactions) -> Synthesis. Section 5.3."""

from __future__ import annotations

import json
import re
from collections import defaultdict

from panel.personas import age_band_for
from panel.prompts import SYNTHESIS_SYSTEM, synthesis_user
from panel.schemas import Persona, Reaction, SegmentClause, Synthesis
from panel.tf_client import ChatJsonTruncatedError, chat_json, env

MODEL_ULTRA = env("MODEL_ULTRA", "nvidia/Nemotron-3-Ultra-550b-a55b")


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
    """Compact CSV, not the full persona JSON, to keep the prompt under
    ~40k tokens for 100 personas (section 4.3)."""
    header = "id,age,region,income_band,tech_comfort,skepticism,occupation"
    rows = [
        f"{p.id},{p.age},{p.region},{p.income_band},{p.tech_comfort},{p.skepticism},{p.occupation}"
        for p in personas
    ]
    return "\n".join([header, *rows])


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


async def synthesize(artifact_text: str, personas: list[Persona], reactions: list[Reaction]) -> Synthesis:
    personas_by_id = {p.id: p for p in personas}

    mean_sentiment = _mean([r.sentiment for r in reactions])
    would_act_pct = (
        100 * sum(1 for r in reactions if r.would_act) / len(reactions) if reactions else 0.0
    )
    by_region = _by_segment(personas_by_id, reactions, lambda p: p.region)
    by_tech = _by_segment(personas_by_id, reactions, lambda p: _tech_band(p.tech_comfort))
    by_skep = _by_segment(personas_by_id, reactions, lambda p: _skepticism_band(p.skepticism))

    user = synthesis_user(
        artifact_text=artifact_text,
        persona_summary_csv=build_persona_summary_csv(personas),
        reactions_json=json.dumps([r.model_dump(mode="json") for r in reactions]),
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
