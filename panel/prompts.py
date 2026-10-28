"""Prompt string constants. See build spec section 4.

4.1 (persona generation) and 4.2 (reaction) are implemented here, needed by
stage 4's anti-sycophancy check. 4.3 (synthesis) and 4.4 (compare summary)
are stage 6, left as stubs.

Note: section 4 says this file lives at panel/prompts.py, but section 2's
tree listing omits it — flagging that spec gap rather than silently
working around it.
"""

import json

from pydantic import BaseModel, Field, RootModel

from panel.schemas import INCOME_BANDS, REGIONS, Reaction, Synthesis

REACTION_SCHEMA_JSON = json.dumps(Reaction.model_json_schema(), indent=2)
SYNTHESIS_SCHEMA_JSON = json.dumps(Synthesis.model_json_schema(), indent=2)


class PersonaDraft(BaseModel):
    """Persona (schemas.py) minus `id`: the backend assigns ids after
    generation (scripts/gen_personas.py), the same way it assigns
    Reaction.run_id/latency_ms/raw_error — the model is never asked for
    it, so it's not in the schema shown in the prompt or enforced via
    response_format.
    """

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
    openness: int = Field(ge=1, le=100)
    conscientiousness: int = Field(ge=1, le=100)
    extraversion: int = Field(ge=1, le=100)
    agreeableness: int = Field(ge=1, le=100)
    neuroticism: int = Field(ge=1, le=100)
    values: list[str]
    buying_triggers: list[str]
    pet_peeves: list[str]
    voice: str


class PersonaDraftBatch(RootModel[list[PersonaDraft]]):
    pass


PERSONA_SCHEMA_JSON = json.dumps(PersonaDraft.model_json_schema(), indent=2)

PERSONA_GENERATION_SYSTEM = (
    "You create realistic, specific, opinionated consumer personas for "
    "product research. Every persona must feel like a real individual, "
    "not a demographic average. Output only a JSON array, no prose, no "
    "code fences."
)

PERSONA_GENERATION_USER_TEMPLATE = """Generate exactly 10 personas matching this slice:
{slice_description}

Rules:
- Each persona follows this schema exactly: {persona_schema_json}
- No two personas in this batch share an occupation or a first name.
- Spread tech_comfort and skepticism across the full 1 to 100 range -- not clustered near a few round numbers, genuinely varied person to person. At least 3 of the 10 must have skepticism >= 70.
- openness, conscientiousness, extraversion, agreeableness, neuroticism (the Big Five, 1-100 each): also genuinely varied, not clustered at 50. These must actually be consistent with voice, values, buying_triggers, and pet_peeves -- a persona with low conscientiousness shouldn't have a voice about carefully reading every clause; a persona with low agreeableness should read as more combative or dismissive, not just skeptical; high neuroticism should show up as anxiety or impatience, not generic distrust (that's what skepticism is for); high openness personas are more willing to try an unfamiliar offer than their skepticism score alone would suggest.
- values, buying_triggers, pet_peeves: 2 to 4 items each, concrete, not abstract ("hates calling to cancel", not "values convenience").
- voice: 1 to 3 first-person sentences that reveal how this person actually reads marketing. Include a specific habit, limit, or trigger. No two voices may share a sentence structure.
- Avoid stereotypes tied to gender, race, or nationality. Variety comes from circumstance and temperament.
"""


def persona_generation_user(slice_description: str) -> str:
    return PERSONA_GENERATION_USER_TEMPLATE.format(
        slice_description=slice_description,
        persona_schema_json=PERSONA_SCHEMA_JSON,
    )


REACTION_SYSTEM = (
    "You are role-playing one specific person reading something for the "
    "first time. Stay fully in character. React the way this person "
    "actually would in the first 20 seconds, including boredom, "
    "confusion, suspicion, or indifference. Do not be polite or helpful. "
    "Do not evaluate the writing quality; react as a reader. Output only "
    "a JSON object matching the schema, no prose, no code fences."
)

REACTION_USER_TEMPLATE = """You are this person:
{persona_json}

You just encountered this:
---
{artifact_text}
---

Answer as this person. Schema:
{reaction_schema_json}

Rules:
- sentiment: -2 strongly negative, -1 negative, 0 indifferent, 1 positive, 2 strongly positive. Indifference is common and acceptable.
- would_act: would you take the main action offered (buy, sign up, read on, support)? Default to false unless something specifically moved you.
- gut_reaction: one sentence, in your own voice, as you would say it out loud.
- top_confusion: the one thing you did not understand, or null only if sentiment is 2.
- top_objection: the one thing that would stop you, or null only if sentiment is 2.
- what_would_change_my_mind: one concrete change to the words on the page itself -- a fact, number, guarantee, or claim this text could state. Not a request for a different medium (no "show a video", "add a screenshot", "link a live dashboard", "let me talk to someone") -- if a video would change your mind, say what the video would need to prove, in words.
- Each string under 200 characters.
"""


def reaction_user(persona_json: str, artifact_text: str) -> str:
    return REACTION_USER_TEMPLATE.format(
        persona_json=persona_json,
        artifact_text=artifact_text,
        reaction_schema_json=REACTION_SCHEMA_JSON,
    )


# --- Stage 6 ----------------------------------------------------------------

SYNTHESIS_SYSTEM = (
    "You are a senior qualitative researcher writing up a 100-person "
    "reaction study for a founder who has ten minutes. Be specific, "
    "quantitative, and blunt. Every claim must cite persona IDs as "
    "evidence. Output only a JSON object matching the schema, no prose, "
    "no code fences."
)

SYNTHESIS_USER_TEMPLATE = """The artifact tested:
---
{artifact_text}
---

The personas (id, age, region, income_band, tech_comfort, skepticism, occupation):
{persona_summary_csv}

Their reactions:
{reactions_json}

Aggregate stats: mean sentiment {mean_sentiment:.3f}, {would_act_pct:.1f}% would act, sentiment by region {by_region}, by tech_comfort {by_tech_comfort}, by skepticism {by_skepticism}.

Produce the schema:
{synthesis_schema_json}

Rules:
- headline: one sentence with a number and a segment, the single most important thing.
- findings: exactly 3, ordered by severity. affected_pct is computed from the reactions, not estimated. affected_segment names the segment in plain words. affected_segment_filter is the same segment as a structured, machine-checkable filter: a list of {{"field": one of "region"/"age_band"/"income_band"/"tech_comfort"/"skepticism", "op": one of "=="/"!="/"<="/">="/"<"/">"/"in", "value": a matching literal or, for "in", a list of literals}} clauses, ANDed together -- this must exactly match affected_segment, since it is what gets recounted to check affected_pct. evidence lists 3 to 5 persona IDs whose top_confusion or top_objection directly supports the claim. tech_comfort and skepticism are on a 1-100 scale (not 1-5) -- pick thresholds that actually split the real distribution you're given, not a guess like ">= 4".
- highest_leverage_edit: current_text must be a short phrase or single sentence quoted verbatim from the artifact (a few words to one sentence, never a full paragraph) so it can be applied as one exact find-and-replace -- "$29" or "No refunds after 7 days", not a block of text. proposed_text must be a drop-in replacement for exactly that phrase, not advice. predicted_flips is your count of personas whose what_would_change_my_mind this edit satisfies.
- who_you_are_losing / who_you_are_winning: one sentence each, segment-level, no persona names.
- Never praise the artifact. If reactions are mostly positive, the findings are about the remaining objections and the segment that is still cold.
"""


def synthesis_user(
    artifact_text: str,
    persona_summary_csv: str,
    reactions_json: str,
    mean_sentiment: float,
    would_act_pct: float,
    by_region: str,
    by_tech_comfort: str,
    by_skepticism: str,
) -> str:
    return SYNTHESIS_USER_TEMPLATE.format(
        artifact_text=artifact_text,
        persona_summary_csv=persona_summary_csv,
        reactions_json=reactions_json,
        mean_sentiment=mean_sentiment,
        would_act_pct=would_act_pct,
        by_region=by_region,
        by_tech_comfort=by_tech_comfort,
        by_skepticism=by_skepticism,
        synthesis_schema_json=SYNTHESIS_SCHEMA_JSON,
    )


COMPARE_SUMMARY_SYSTEM = "One sentence. No preamble. Output plain text."

COMPARE_SUMMARY_USER_TEMPLATE = """Artifact A and B differ as follows:
{unified_diff}
Between A and B, {flipped_positive} personas moved to would_act and {flipped_negative} moved away. Those who flipped positive: {flipped_pos_summary}. Those who flipped negative: {flipped_neg_summary}.
In one sentence, state what the edit did and for whom, with the numbers.
"""


def compare_summary_user(
    unified_diff: str,
    flipped_positive: int,
    flipped_negative: int,
    flipped_pos_summary: str,
    flipped_neg_summary: str,
) -> str:
    return COMPARE_SUMMARY_USER_TEMPLATE.format(
        unified_diff=unified_diff,
        flipped_positive=flipped_positive,
        flipped_negative=flipped_negative,
        flipped_pos_summary=flipped_pos_summary,
        flipped_neg_summary=flipped_neg_summary,
    )
