"""check_artifact(text) -> ArtifactCheck. Pre-submission sanity check.

Not in the build spec. A panel will react to literally anything -- paste
in a config file and a thousand personas will dutifully tell you they're
confused by it, and Ultra will write a confident report about copy that
was never copy. That output is worse than a refusal, because it looks
exactly as authoritative as a real one.

So one cheap Nano call, before the expensive fan-out, says what the text
appears to be. Three rules govern it, in priority order:

1. **It only ever advises.** The caller is free to run anyway, and the UI
   offers exactly that. Nothing here blocks a run.
2. **It fails open.** Any error -- a truncation, a bad schema, the API
   being down -- resolves to "reviewable" with a reason saying the check
   didn't run. A broken checker must never cost someone a legitimate run.
3. **It is generous.** The prompt lists far more reviewable kinds than
   unreviewable ones, because a false "this isn't copy" on someone's real
   landing page is a much worse failure than letting gibberish through.
"""

from __future__ import annotations

import json

from panel.config import MODEL_NANO
from panel.prompts import ARTIFACT_CHECK_SYSTEM, artifact_check_user
from panel.schemas import ArtifactCheck
from panel.tf_client import chat_json

# Generous for a tiny output: the schema is three short fields, but Nano
# reasons first and its budget consumption is stochastic (see tf_client).
# The measured-safe floor for the smallest schema in this codebase.
CHECK_MAX_TOKENS = 3000

# Low, not zero: this is a classification, so run-to-run agreement matters
# more than variety, but zero has no advantage over this on these models.
CHECK_TEMPERATURE = 0.1

_SCHEMA_JSON = json.dumps(ArtifactCheck.model_json_schema())


async def check_artifact(text: str) -> ArtifactCheck:
    """Never raises. See rule 2 in the module docstring."""
    try:
        result = await chat_json(
            model=MODEL_NANO,
            system=ARTIFACT_CHECK_SYSTEM,
            user=artifact_check_user(text, _SCHEMA_JSON),
            schema=ArtifactCheck,
            temperature=CHECK_TEMPERATURE,
            max_tokens=CHECK_MAX_TOKENS,
        )
        return result.data
    except Exception as e:  # noqa: BLE001 -- failing open is the whole point
        print(f"classify: check failed, treating as reviewable: {type(e).__name__}: {e}")
        return ArtifactCheck(
            kind="unknown",
            is_reviewable=True,
            reason="The pre-submission check could not run, so nothing was verified.",
        )
