"""Nebius Serverless Job entrypoint wrapping react.fan_out. Section 5.7.

Reads artifact text and a persona_set (persona_count) from job arguments,
runs the fan-out, writes the reactions JSON to the job's output file.
Same signature/behavior as the in-process path -- this script is meant to
run standalone inside a Nebius Serverless Job container, not be imported.

Verified: this script runs end-to-end locally (`python jobs/react_job.py
--artifact-text ... --persona-count 10 --output-path out.json`) against
the real Token Factory API -- that part reuses panel.react.fan_out
directly, nothing job-platform-specific about it.

NOT verified: actually submitting this as a Nebius Serverless Job and
polling for its result (api/main.py's USE_SERVERLESS_JOB path). That needs
the real Nebius Serverless Job submission API (how a job is created, how
arguments are passed into the container, how its output artifact is
fetched once it finishes) -- this sandbox only has a Token Factory API
key, not Nebius Job/Endpoint credentials, so that part is written as a
best-effort placeholder, not something I could test against the real
service. Flagging rather than silently shipping untested integration code.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from panel.personas import sample_stable  # noqa: E402
from panel.react import fan_out  # noqa: E402


async def run(artifact_text: str, persona_count: int, run_id: str, output_path: Path) -> None:
    personas = sample_stable(persona_count)
    reactions = []
    async for reaction in fan_out(artifact_text, personas, run_id):
        reactions.append(reaction)
        # Written after every reaction, not just at the end: section 5.7
        # says the API "polls for completion, streaming reactions as they
        # land in the output file" -- that only works if the file is
        # updated incrementally, not produced once at job completion.
        output_path.write_text(
            json.dumps(
                {"done": len(reactions), "total": len(personas), "reactions": [r.model_dump(mode="json") for r in reactions]},
                indent=2,
            )
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-text", required=True)
    parser.add_argument("--persona-count", type=int, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-path", required=True, type=Path)
    args = parser.parse_args()

    asyncio.run(run(args.artifact_text, args.persona_count, args.run_id, args.output_path))


if __name__ == "__main__":
    main()
