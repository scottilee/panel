"""delta(run_a, run_b) -> CompareResult: who changed their mind between two
runs of the same panel, plus a one-sentence Ultra summary."""

from __future__ import annotations

import difflib

from pydantic import BaseModel

from panel.config import MODEL_ULTRA
from panel.prompts import compare_summary_user
from panel.schemas import CompareDelta, CompareResult, Run
from panel.stats import usable
from panel.tf_client import chat_json


class _CompareSummary(BaseModel):
    """The summary is one sentence of plain text, wrapped in a one-field
    schema so it goes through chat_json's retries and validation like
    every other model call."""

    summary: str


COMPARE_SUMMARY_SYSTEM_JSON = (
    "One sentence. No preamble. Output only a JSON object matching the "
    'schema: {"summary": "..."}. No code fences.'
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

    # Only personas with a usable reaction in BOTH runs are compared: a
    # call that failed in one run and succeeded in the other is not a
    # change of mind, and counting it would invent flips.
    by_id_a = {r.persona_id: r for r in usable(run_a.reactions)}
    by_id_b = {r.persona_id: r for r in usable(run_b.reactions)}
    shared_ids = sorted(set(by_id_a) & set(by_id_b))

    # "Flipped" is about the would_act decision, not sentiment sign.
    deltas = [
        CompareDelta(
            persona_id=pid,
            sentiment_before=by_id_a[pid].sentiment,
            sentiment_after=by_id_b[pid].sentiment,
            flipped_to_act=(not by_id_a[pid].would_act) and by_id_b[pid].would_act,
        )
        for pid in shared_ids
    ]
    flipped_positive_ids = [d.persona_id for d in deltas if d.flipped_to_act]
    flipped_negative_ids = [
        pid for pid in shared_ids if by_id_a[pid].would_act and not by_id_b[pid].would_act
    ]

    def act_pct(by_id: dict) -> float:
        if not shared_ids:
            return 0.0
        return 100 * sum(1 for pid in shared_ids if by_id[pid].would_act) / len(shared_ids)

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
        would_act_delta_pct=act_pct(by_id_b) - act_pct(by_id_a),
        summary=result.data.summary,
    )
