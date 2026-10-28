"""Reaction arithmetic shared by the API, synthesis, compare and the CLI.

A reaction whose model call failed (raw_error set) carries placeholder
values -- sentiment 0, would_act False -- that describe nothing anyone
said. Every aggregate here is over usable reactions only, so a burst of
API errors can't masquerade as an indifferent panel.
"""

from __future__ import annotations

from panel.schemas import Reaction


def usable(reactions: list[Reaction]) -> list[Reaction]:
    return [r for r in reactions if not r.raw_error]


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def mean_sentiment(reactions: list[Reaction]) -> float:
    return mean([r.sentiment for r in usable(reactions)])


def would_act_pct(reactions: list[Reaction]) -> float:
    ok = usable(reactions)
    return 100 * sum(1 for r in ok if r.would_act) / len(ok) if ok else 0.0
