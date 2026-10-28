"""delta(run_a, run_b) -> CompareResult. Section 5.5."""

from __future__ import annotations

import difflib

from pydantic import BaseModel

from panel.prompts import compare_summary_user
from panel.schemas import CompareDelta, CompareResult, Run
from panel.tf_client import chat_json, env

MODEL_ULTRA = env("MODEL_ULTRA", "nvidia/Nemotron-3-Ultra-550b-a55b")


class _CompareSummary(BaseModel):
    """Section 4.4's literal prompt says "Output plain text", but section
    5.1 requires every model call to go through chat_json, which only
    knows how to enforce a JSON schema. Wrapping the one-sentence summary
    in a trivial single-field schema keeps the one-call-path architecture
    (retries, backoff, latency tracking) instead of adding a second,
    schema-less calling path for just this one prompt -- flagging the
    deviation from the literal prompt text rather than silently building
    a separate text-completion path.
    """

    summary: str


COMPARE_SUMMARY_SYSTEM_JSON = (
    "One sentence. No preamble. Output only a JSON object matching the "
    'schema: {"summary": "..."}. No code fences.'
)


def _delta_for_persona(reaction_a, reaction_b) -> CompareDelta:
    return CompareDelta(
        persona_id=reaction_a.persona_id,
        sentiment_before=reaction_a.sentiment,
        sentiment_after=reaction_b.sentiment,
        # "flipped_to_act" and the flipped_positive/negative counts below
        # are about the would_act decision, not sentiment sign -- that's
        # the one place section 4.4's prompt wording ("N personas moved to
        # would_act... N moved away") pins the definition unambiguously.
        flipped_to_act=(not reaction_a.would_act) and reaction_b.would_act,
    )


def _summarize_ids(ids: list[str], limit: int = 5) -> str:
    if not ids:
        return "none"
    shown = ", ".join(ids[:limit])
    more = f" and {len(ids) - limit} more" if len(ids) > limit else ""
    return f"{len(ids)} personas ({shown}{more})"


async def delta(run_a: Run, run_b: Run) -> CompareResult:
    if run_a.persona_set != run_b.persona_set:
        raise ValueError("compare requires both runs to share the same persona_set")

    by_id_a = {r.persona_id: r for r in run_a.reactions}
    by_id_b = {r.persona_id: r for r in run_b.reactions}
    shared_ids = sorted(set(by_id_a) & set(by_id_b))

    deltas = [_delta_for_persona(by_id_a[pid], by_id_b[pid]) for pid in shared_ids]

    flipped_positive_ids = [
        pid for pid in shared_ids if not by_id_a[pid].would_act and by_id_b[pid].would_act
    ]
    flipped_negative_ids = [
        pid for pid in shared_ids if by_id_a[pid].would_act and not by_id_b[pid].would_act
    ]

    would_act_pct_a = (
        100 * sum(1 for r in by_id_a.values() if r.would_act) / len(by_id_a) if by_id_a else 0.0
    )
    would_act_pct_b = (
        100 * sum(1 for r in by_id_b.values() if r.would_act) / len(by_id_b) if by_id_b else 0.0
    )

    unified_diff = "\n".join(
        difflib.unified_diff(
            run_a.artifact.text.splitlines(),
            run_b.artifact.text.splitlines(),
            lineterm="",
        )
    )

    result = await chat_json(
        model=MODEL_ULTRA,
        system=COMPARE_SUMMARY_SYSTEM_JSON,
        user=compare_summary_user(
            unified_diff=unified_diff or "(no textual diff)",
            flipped_positive=len(flipped_positive_ids),
            flipped_negative=len(flipped_negative_ids),
            flipped_pos_summary=_summarize_ids(flipped_positive_ids),
            flipped_neg_summary=_summarize_ids(flipped_negative_ids),
        ),
        schema=_CompareSummary,
        temperature=0.2,
        max_tokens=2000,
    )

    return CompareResult(
        run_a=run_a.run_id,
        run_b=run_b.run_id,
        deltas=deltas,
        flipped_positive=len(flipped_positive_ids),
        flipped_negative=len(flipped_negative_ids),
        would_act_delta_pct=would_act_pct_b - would_act_pct_a,
        summary=result.data.summary,
    )
