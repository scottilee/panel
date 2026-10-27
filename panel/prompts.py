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

from panel.schemas import INCOME_BANDS, REGIONS, Reaction

REACTION_SCHEMA_JSON = json.dumps(Reaction.model_json_schema(), indent=2)


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
    tech_comfort: int = Field(ge=1, le=5)
    values: list[str]
    buying_triggers: list[str]
    pet_peeves: list[str]
    skepticism: int = Field(ge=1, le=5)
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
- Spread tech_comfort and skepticism across 1 to 5; at least 3 of the 10 must have skepticism >= 4.
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
- what_would_change_my_mind: one concrete change to the text, not a general wish.
- Each string under 200 characters.
"""


def reaction_user(persona_json: str, artifact_text: str) -> str:
    return REACTION_USER_TEMPLATE.format(
        persona_json=persona_json,
        artifact_text=artifact_text,
        reaction_schema_json=REACTION_SCHEMA_JSON,
    )


# --- Stage 6 ----------------------------------------------------------------

SYNTHESIS_SYSTEM = None  # TODO stage 6, section 4.3
SYNTHESIS_USER_TEMPLATE = None  # TODO stage 6, section 4.3

COMPARE_SUMMARY_SYSTEM = None  # TODO stage 6, section 4.4
COMPARE_SUMMARY_USER_TEMPLATE = None  # TODO stage 6, section 4.4
