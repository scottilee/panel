"""Re-records the cached demo fallbacks in data/cached/ from live runs.

    python scripts/record_cached.py                 # every example
    python scripts/record_cached.py pricing_v1      # just one

Each example is run exactly as POST /run would run it at the default
panel size (same personas, same fan-out, same synthesis), and the result
is written in the shape api/main.py replays. Re-record whenever the
reaction or synthesis prompts change: a cached report written under an
older prompt can say things the current recount contradicts.

Calls the live API: each example costs N Nano calls plus one Ultra call.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from panel import config  # noqa: E402
from panel.personas import sample_stable  # noqa: E402
from panel.react import fan_out  # noqa: E402
from panel.stats import usable  # noqa: E402
from panel.synthesize import synthesize  # noqa: E402


async def record(stem: str) -> None:
    text = (config.EXAMPLES_DIR / f"{stem}.txt").read_text().strip()
    personas = sample_stable(config.DEFAULT_PERSONA_COUNT)
    reactions = [r async for r in fan_out(text, personas, f"cached-{stem}")]
    failed = len(reactions) - len(usable(reactions))
    # A fallback is what a judge sees when the live service is down, so it
    # must be a clean run, not one with holes in the grid.
    if failed:
        raise SystemExit(f"{stem}: {failed} reactions failed; not writing a cache with gaps")
    synthesis = await synthesize(text, personas, reactions)
    out = config.CACHED_DIR / f"{stem}.json"
    out.write_text(
        json.dumps(
            {
                "example_id": stem,
                "reactions": [r.model_dump(mode="json") for r in reactions],
                "synthesis": synthesis.model_dump(mode="json"),
            },
            indent=2,
        )
        + "\n"
    )
    print(f"{stem}: wrote {out} ({len(reactions)} reactions)")
    print(f"  headline: {synthesis.headline}")


async def main() -> None:
    stems = sys.argv[1:] or sorted(p.stem for p in config.EXAMPLES_DIR.glob("*.txt"))
    for stem in stems:
        await record(stem)


if __name__ == "__main__":
    asyncio.run(main())
