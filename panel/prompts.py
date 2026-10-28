"""Prompt string constants. See build spec section 4.

All four of section 4's prompts live here: 4.1 (persona generation), 4.2
(reaction), 4.3 (synthesis) and 4.4 (compare summary). The shard-digest
prompt for panels above 150 personas is not one of section 4's four, so it
sits in synthesize.py next to the only code that uses it.

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


def persona_generation_user(
    slice_description: str,
    avoid_names: list[str] | None = None,
    avoid_occupations: list[str] | None = None,
) -> str:
    """avoid_names / avoid_occupations: everything already used by earlier
    batches. Each batch is otherwise generated blind to the other 990 at
    n=1000, so Super's heavy mode collapse on names and jobs ("barista"
    came up 5 times in just 100) produced collisions that then had to be
    repaired one persona at a time. Telling it what's taken prevents them
    instead.
    """
    prompt = PERSONA_GENERATION_USER_TEMPLATE.format(
        slice_description=slice_description,
        persona_schema_json=PERSONA_SCHEMA_JSON,
    )
    if avoid_names:
        prompt += (
            "\nThese full names are already taken by other personas. Do not reuse any "
            "of them, and do not reuse their first names either:\n"
            + ", ".join(sorted(avoid_names))
            + "\n"
        )
    if avoid_occupations:
        # No example jobs here on purpose. An earlier version named three
        # ("a dialysis technician, a crane operator, a school bus
        # dispatcher") and Super used all three verbatim in the very next
        # batch -- across 90 batches that would have replaced one kind of
        # mode collapse with another, authored by this prompt.
        prompt += (
            "\nThese occupations are already used. Do not reuse them, and do not "
            "reach for the usual stock jobs (barista, graphic designer, software "
            "engineer, nurse, teacher). Pick ordinary, specific work that fits each "
            "person's region and income band -- the kind of job that rarely shows up "
            "in a marketing deck but employs a lot of people:\n"
            + ", ".join(sorted(avoid_occupations))
            + "\n"
        )
    return prompt


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


# --- Synthesis and compare (sections 4.3 and 4.4) ---------------------------

# "a reaction study", not "a 100-person reaction study": the panel is any
# size from 10 to 1,000, and the real count is in the aggregate stats the
# user prompt supplies. Naming a fixed 100 here had Ultra writing up
# hundred-person language for a 400-person run.
SYNTHESIS_SYSTEM = (
    "You are a senior qualitative researcher writing up a reaction study "
    "for a founder who has ten minutes. Be specific, quantitative, and "
    "blunt. Every claim must cite persona IDs as evidence. Output only a "
    "JSON object matching the schema, no prose, no code fences."
)

SYNTHESIS_USER_TEMPLATE = """The artifact tested:
---
{artifact_text}
---

The personas (id, age, age_band, region, income_band, tech_comfort, skepticism, occupation):
{persona_summary_csv}

{reactions_label}
{reactions_json}

Aggregate stats: mean sentiment {mean_sentiment:.3f}, {would_act_pct:.1f}% would act, sentiment by region {by_region}, by tech_comfort {by_tech_comfort}, by skepticism {by_skepticism}.

Produce the schema:
{synthesis_schema_json}

Rules:
- headline: one sentence with a number and a segment, the single most important thing.
- findings: exactly 3, ordered by severity. affected_pct is computed from the reactions, not estimated. affected_segment names the segment in plain words. affected_segment_filter is the same segment as a structured, machine-checkable filter: a list of {{"field": one of "region"/"age_band"/"income_band"/"tech_comfort"/"skepticism", "op": one of "=="/"!="/"<="/">="/"<"/">"/"in", "value": a matching literal or, for "in", a list of literals}} clauses, ANDed together -- this must exactly match affected_segment, since it is what gets recounted to check affected_pct. For region, age_band and income_band the value MUST be copied character-for-character from the table above; the only legal age_band values are "18-24", "25-34", "35-49", "50-64" and "65+", so "users 50 and older" is {{"field": "age_band", "op": "in", "value": ["50-64", "65+"]}} -- a band you invent ("50-59", "70+") matches nobody and the finding is recounted to 0%. evidence lists 3 to 5 bare persona ID strings whose top_confusion or top_objection directly supports the claim -- exactly like ["p042", "p017", "p088"], ID only. Do NOT append the quote, a colon, a name, or any explanation to an ID: "p042: \\"it doesn't say the price\\"" is wrong, "p042" is right. tech_comfort and skepticism are on a 1-100 scale (not 1-5) -- pick thresholds that actually split the real distribution you're given, not a guess like ">= 4".
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
    reactions_label: str = "Their reactions:",
) -> str:
    """reactions_label exists because above a few hundred personas the raw
    reactions no longer fit in one prompt and synthesize() sends Super-written
    digests of them instead (see SHARD_THRESHOLD). Ultra has to be told which
    of the two it's looking at, or it reports digest theme counts as if they
    were individual reactions."""
    return SYNTHESIS_USER_TEMPLATE.format(
        artifact_text=artifact_text,
        persona_summary_csv=persona_summary_csv,
        reactions_json=reactions_json,
        reactions_label=reactions_label,
        mean_sentiment=mean_sentiment,
        would_act_pct=would_act_pct,
        by_region=by_region,
        by_tech_comfort=by_tech_comfort,
        by_skepticism=by_skepticism,
        synthesis_schema_json=SYNTHESIS_SCHEMA_JSON,
    )


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


# --- Pre-submission check (not one of section 4's four prompts) -------------

ARTIFACT_CHECK_SYSTEM = (
    "You identify what a piece of text is, for a tool that tests marketing "
    "and public-facing copy on a panel of synthetic readers. You are a "
    "gatekeeper only against obvious mistakes, not a judge of quality. "
    "Output only a JSON object matching the schema, no prose, no code fences."
)

ARTIFACT_CHECK_USER_TEMPLATE = """Someone pasted this into a tool that shows copy to a panel of ordinary people and reports their reactions:
---
{artifact_text}
---

Answer with this schema:
{schema}

- kind: what this text is, in two to five plain words ("SaaS landing page", "job posting", "Python source code", "random characters").
- is_reviewable: true if showing this to ordinary readers and asking "what confused you, what would stop you" would produce useful answers. Be generous: landing pages, ads, pricing pages, emails, policy and legal notices, product descriptions, fundraising appeals, job postings, app store listings, press releases, onboarding and error messages, and rough drafts of any of those are ALL reviewable, however short, unpolished or unusual. Writing meant to be read by people is reviewable.
- Set is_reviewable false only when a panel of readers genuinely has nothing to react to: random or meaningless characters, placeholder text like lorem ipsum, source code, configuration, logs, raw data or tables, machine output, or text that addresses no reader and asks nothing of one. Fiction, jokes and personal writing are not reviewable either -- there is no action for a reader to take or decline.
- reason: one short sentence addressed to the person who pasted it, saying what you think this is and, when is_reviewable is false, what the panel would fail to tell them. Plain and non-judgmental; never scold, and never comment on how good the writing is.
"""


def artifact_check_user(artifact_text: str, schema_json: str) -> str:
    return ARTIFACT_CHECK_USER_TEMPLATE.format(
        artifact_text=artifact_text, schema=schema_json
    )
