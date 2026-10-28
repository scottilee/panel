"""Rerun noise: how many would_act flips an UNCHANGED artifact produces.

    python scripts/measure_noise.py --artifact data/examples/pricing_v1.txt --n 100 --trials 5

Apply-and-compare reports how many personas flipped after an edit. That
number only means something next to the flips you get from rerunning the
identical text against the identical panel, which is what this measures:
each trial runs the same artifact twice and counts flips in both
directions between the two runs, over personas usable in both.

Calls the live API: each trial costs 2 x N Nano calls (no synthesis).
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from panel.personas import sample_stable  # noqa: E402
from panel.react import REACTION_TEMPERATURE, fan_out  # noqa: E402
from panel.stats import usable  # noqa: E402


async def _react(text: str, personas, run_id: str) -> dict:
    return {r.persona_id: r async for r in fan_out(text, personas, run_id)}


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--n", type=int, default=100)
    parser.add_argument("--trials", type=int, default=5)
    args = parser.parse_args()

    text = Path(args.artifact).read_text()
    # The same personas the web app uses for a panel of this size.
    personas = sample_stable(args.n)
    print(f"temperature={REACTION_TEMPERATURE} n={args.n} trials={args.trials}")

    totals = []
    for trial in range(args.trials):
        a = await _react(text, personas, f"noise-{trial}-a")
        b = await _react(text, personas, f"noise-{trial}-b")
        ok_a = {r.persona_id: r for r in usable(list(a.values()))}
        ok_b = {r.persona_id: r for r in usable(list(b.values()))}
        shared = set(ok_a) & set(ok_b)
        to_yes = sum(1 for p in shared if not ok_a[p].would_act and ok_b[p].would_act)
        to_no = sum(1 for p in shared if ok_a[p].would_act and not ok_b[p].would_act)
        totals.append(to_yes + to_no)
        print(f"trial {trial + 1}: {to_yes} moved to yes, {to_no} moved to no, over {len(shared)} personas")

    print(
        f"flips per unchanged rerun: min {min(totals)}, median {statistics.median(totals)}, "
        f"max {max(totals)} (per {args.n})"
    )


if __name__ == "__main__":
    asyncio.run(main())
