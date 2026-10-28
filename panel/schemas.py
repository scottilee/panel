"""Pydantic models for every data contract: personas, reactions, the
synthesis report, runs, compares and HTTP request bodies.

Field names are shared with the frontend (web/src) and with the JSON
schemas shown to the models, so renaming one is a contract change.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Union

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic.json_schema import SkipJsonSchema

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
#
# Wider than what the synthesis prompt actually offers Ultra, on purpose:
# the prompt lists only region/age_band/income_band/tech_comfort/
# skepticism, because those are the five the UI has filter chips for
# (web/src/state.js's SEGMENT_FIELD_TO_GROUP) and so the five where
# clicking a finding's segment does something. A Big Five clause still
# validates and still recounts correctly (synthesize.py's
# NUMERIC_SEGMENT_FIELDS covers all seven) -- it just isn't clickable.
SEGMENT_FIELDS = Literal[
    "region",
    "age_band",
    "income_band",
    "tech_comfort",
    "skepticism",
    "openness",
    "conscientiousness",
    "extraversion",
    "agreeableness",
    "neuroticism",
]
SEGMENT_OPS = Literal["==", "!=", "<=", ">=", "<", ">", "in"]


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
    tech_comfort: int = Field(ge=1, le=100)
    skepticism: int = Field(ge=1, le=100)
    # Big Five / OCEAN, 1-100: psychological grounding beyond skepticism.
    openness: int = Field(ge=1, le=100)
    conscientiousness: int = Field(ge=1, le=100)
    extraversion: int = Field(ge=1, le=100)
    agreeableness: int = Field(ge=1, le=100)
    neuroticism: int = Field(ge=1, le=100)
    values: list[str]
    buying_triggers: list[str]
    pet_peeves: list[str]
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
    # Share of the segment's members (usable reactions only) whose
    # sentiment was negative. The model proposes it; synthesize.py
    # recomputes it from the reactions whenever the filter can be
    # recounted, so the shipped number is counted, not estimated.
    affected_pct: int = Field(ge=0, le=100)
    affected_segment: str
    # Structured twin of affected_segment, so the segment can be recounted
    # exactly instead of parsed out of prose.
    affected_segment_filter: list[SegmentClause]
    evidence: list[str] = Field(min_length=3, max_length=5)
    severity: Literal["high", "medium", "low"]
    # Filled in by synthesize.py, never by the model (hidden from the JSON
    # schema it is shown): how many panel personas match the filter, and
    # that count as a percentage of the panel. None when the filter cites
    # values no persona has, so it can't be recounted.
    segment_size: SkipJsonSchema[int | None] = None
    segment_pct: SkipJsonSchema[float | None] = None


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
    # Over usable reactions only (see panel/stats.py).
    mean_sentiment: float = 0.0
    would_act_pct: float = 0.0
    failed_reactions: int = 0
    # Completed Token Factory requests per tier, retries included.
    nano_calls: int = 0
    super_calls: int = 0
    ultra_calls: int = 0
    nano_ms_total: int = 0
    # Wall time of the whole synthesis stage: Super digests (large panels
    # only) plus the Ultra call.
    ultra_ms: int = 0


class Run(BaseModel):
    run_id: str
    artifact: Artifact
    persona_set: str
    # "cancelled" is distinct from "error": a deliberate stop is not a
    # failure, and the UI must not show "something went wrong" for it.
    status: Literal[
        "pending", "streaming", "synthesizing", "done", "error", "cancelled"
    ] = "pending"
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


class ArtifactCheck(BaseModel):
    """Pre-submission sanity check on what the user pasted.

    A panel will dutifully react to anything -- gibberish, a config file,
    a joke -- and produce a confident, meaningless report. The check only
    ever advises: the user can always run anyway, and a failed check never
    blocks a run (see panel/classify.py).
    """

    kind: str = Field(max_length=60)
    is_reviewable: bool
    reason: str = Field(max_length=240)


# --- HTTP request bodies ----------------------------------------------------

# Bounds on an artifact's text, shared by POST /run, POST /check and the
# URL-fetch path.
MIN_ARTIFACT_CHARS = 50
MAX_ARTIFACT_CHARS = 20_000


class RunRequest(BaseModel):
    text: str | None = Field(
        default=None, min_length=MIN_ARTIFACT_CHARS, max_length=MAX_ARTIFACT_CHARS
    )
    url: str | None = None
    label: str | None = None
    persona_count: int | None = Field(default=None, ge=10)

    @model_validator(mode="after")
    def _text_xor_url(self) -> "RunRequest":
        if bool(self.text) == bool(self.url):
            raise ValueError("send exactly one of text or url")
        return self

    @field_validator("text")
    @classmethod
    def _strip_text(cls, v: str | None) -> str | None:
        return v.strip() if v else v


class CompareRequest(BaseModel):
    run_a: str
    run_b: str


class CheckRequest(BaseModel):
    # Same bounds as RunRequest.text, so anything /run would accept can be
    # checked first and nothing else wastes a model call.
    text: str = Field(min_length=MIN_ARTIFACT_CHARS, max_length=MAX_ARTIFACT_CHARS)

    @field_validator("text")
    @classmethod
    def _strip_text(cls, v: str) -> str:
        return v.strip()
