"""Pydantic models for every data contract in the build spec, section 3.

Field names must not change without updating the spec. The one deliberate
addition beyond the spec's literal examples is `Finding.affected_segment_filter`
(see note below) — everything else here is a direct transcription.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Union

from pydantic import BaseModel, Field, field_validator, model_validator

REGIONS = Literal[
    "Northeast US",
    "Southeast US",
    "Midwest US",
    "Southwest US",
    "West US",
    "Western Europe",
    "Asia-Pacific",
    "Latin America",
]

INCOME_BANDS = Literal["<25k", "25-40k", "40-75k", "75-150k", "150k+"]

# Fields a Finding's affected_segment_filter may constrain. Deliberately
# excludes free-text persona fields (occupation, household, values, ...)
# since those can't be recounted as an exact segment match.
SEGMENT_FIELDS = Literal["region", "age_band", "income_band", "tech_comfort", "skepticism"]
SEGMENT_OPS = Literal["==", "!=", "<=", ">=", "<", ">", "in"]

AGE_BANDS = Literal["18-24", "25-34", "35-49", "50-64", "65+"]


class Persona(BaseModel):
    id: str
    name: str
    age: int = Field(ge=0, le=120)
    gender: str
    location: str
    region: REGIONS
    income_band: INCOME_BANDS
    occupation: str
    household: str
    tech_comfort: int = Field(ge=1, le=5)
    values: list[str]
    buying_triggers: list[str]
    pet_peeves: list[str]
    skepticism: int = Field(ge=1, le=5)
    voice: str


class Reaction(BaseModel):
    persona_id: str
    sentiment: int = Field(ge=-2, le=2)
    would_act: bool
    gut_reaction: str = Field(max_length=200)
    top_confusion: str | None = Field(default=None, max_length=200)
    top_objection: str | None = Field(default=None, max_length=200)
    what_would_change_my_mind: str = Field(max_length=200)

    # Added by the backend, not by the model.
    run_id: str | None = None
    latency_ms: int | None = None
    raw_error: str | None = None

    @model_validator(mode="after")
    def _confusion_and_objection_required_unless_sentiment_is_2(self) -> "Reaction":
        if self.sentiment != 2:
            if self.top_confusion is None or self.top_objection is None:
                raise ValueError(
                    "top_confusion and top_objection may only be null when sentiment is 2"
                )
        return self


class SegmentClause(BaseModel):
    """One AND-ed predicate in a Finding's affected_segment_filter."""

    field: SEGMENT_FIELDS
    op: SEGMENT_OPS
    value: Union[str, int, list[Union[str, int]]]

    @model_validator(mode="after")
    def _in_requires_list(self) -> "SegmentClause":
        if self.op == "in" and not isinstance(self.value, list):
            raise ValueError('op "in" requires a list value')
        return self


class Finding(BaseModel):
    claim: str
    affected_pct: int = Field(ge=0, le=100)
    affected_segment: str
    # Additive beyond the spec's literal JSON example: a structured,
    # machine-checkable twin of affected_segment so synthesize.py can
    # recount affected_pct exactly instead of parsing free text (see
    # section 5.3's "within 10 points of a recount" check).
    affected_segment_filter: list[SegmentClause]
    evidence: list[str] = Field(min_length=3, max_length=5)
    severity: Literal["high", "medium", "low"]


class HighestLeverageEdit(BaseModel):
    current_text: str
    proposed_text: str
    rationale: str
    predicted_flips: int


class Synthesis(BaseModel):
    headline: str
    findings: list[Finding] = Field(min_length=3, max_length=3)
    highest_leverage_edit: HighestLeverageEdit
    who_you_are_losing: str
    who_you_are_winning: str


class Artifact(BaseModel):
    source: Literal["text", "url"]
    text: str
    url: str | None = None
    label: str | None = None


class RunStats(BaseModel):
    mean_sentiment: float = 0.0
    would_act_pct: float = 0.0
    nano_calls: int = 0
    nano_ms_total: int = 0
    ultra_ms: int = 0


class Run(BaseModel):
    run_id: str
    artifact: Artifact
    persona_set: str
    status: Literal["pending", "streaming", "synthesizing", "done", "error"] = "pending"
    reactions: list[Reaction] = Field(default_factory=list)
    synthesis: Synthesis | None = None
    stats: RunStats = Field(default_factory=RunStats)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CompareDelta(BaseModel):
    persona_id: str
    sentiment_before: int = Field(ge=-2, le=2)
    sentiment_after: int = Field(ge=-2, le=2)
    flipped_to_act: bool


class CompareResult(BaseModel):
    run_a: str
    run_b: str
    deltas: list[CompareDelta]
    flipped_positive: int
    flipped_negative: int
    would_act_delta_pct: float
    summary: str


# --- HTTP request bodies (section 3's API table) ---------------------------


class RunRequest(BaseModel):
    text: str | None = Field(default=None, min_length=50, max_length=20000)
    url: str | None = None
    label: str | None = None
    persona_count: int | None = Field(default=None, ge=10)

    @model_validator(mode="after")
    def _text_xor_url(self) -> "RunRequest":
        if not self.text and not self.url:
            raise ValueError("either text or url is required")
        return self

    @field_validator("text")
    @classmethod
    def _strip_text(cls, v: str | None) -> str | None:
        return v.strip() if v else v


class CompareRequest(BaseModel):
    run_a: str
    run_b: str
