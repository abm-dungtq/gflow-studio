"""Run one config runner's rows on several signed-in profiles side by side ("lanes").

``gflow run --profiles a,b`` and ``gflow video batch --profiles a,b`` deal the rows that are
still to make round-robin across the profiles. Each lane opens its own browser on its own
profile and works through its rows in order, exactly as a single-profile run would; lanes
start ``LANE_STAGGER_S`` apart so two sessions never submit in the same instant.

A lane that hits an error which ends its session (WAF, auth, quota, ``--fail-fast``) sets
the shared :class:`LaneStop`; every other lane finishes the row it is on and skips the
rest, so a throttled account is not hammered from a second browser.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TypeVar

import structlog

from gflow_cli.errors import ConfigurationError

logger = structlog.get_logger(__name__)

LANE_STAGGER_S = 25.0

T = TypeVar("T")
R = TypeVar("R")


@dataclass
class LaneStop:
    """Shared between lanes: the first lane to stop records why, the others follow."""

    reason: str | None = None

    def set(self, reason: str) -> None:
        if self.reason is None:
            self.reason = reason
            logger.warning("lanes.stopped", reason=reason)


def parse_profiles(raw: str) -> tuple[str, ...]:
    """``"a, b"`` -> ``("a", "b")``; an empty name or a repeated profile is exit 11."""
    names = tuple(part.strip() for part in raw.split(","))
    if not all(names):
        raise ConfigurationError(
            detail=f"--profiles has an empty name: {raw!r}",
            remediation_hint="Separate profile names with commas: --profiles meo-muop,meo-muop-2",
        )
    if len(set(names)) != len(names):
        raise ConfigurationError(
            detail=f"--profiles names a profile twice: {raw!r}",
            remediation_hint="One browser can hold a profile at a time; list each profile once.",
        )
    return names


def split_rows(profiles: Sequence[str], rows: Sequence[T]) -> list[tuple[str, tuple[T, ...]]]:
    """Deal ``rows`` round-robin to ``profiles``; a profile left with no row gets no lane."""
    if not profiles:
        return []
    dealt: list[list[T]] = [[] for _ in profiles]
    for pos, row in enumerate(rows):
        dealt[pos % len(profiles)].append(row)
    return [(name, tuple(lane)) for name, lane in zip(profiles, dealt, strict=True) if lane]


async def run_lanes(
    lanes: Sequence[tuple[str, tuple[T, ...]]],
    run_lane: Callable[[str, tuple[T, ...], LaneStop], Awaitable[list[R]]],
    *,
    stagger: float | None = None,
) -> list[R]:
    """Run every lane concurrently and return all their outcomes.

    A lane that raises stops the others (after their current row); once every lane has
    settled the first exception is re-raised, so no browser is left running behind it.
    """
    stop = LaneStop()
    gap = LANE_STAGGER_S if stagger is None else stagger

    async def one(pos: int, profile: str, rows: tuple[T, ...]) -> list[R]:
        if pos:
            await asyncio.sleep(pos * gap)
        logger.info("lanes.start", profile=profile, rows=len(rows))
        try:
            return await run_lane(profile, rows, stop)
        except Exception as exc:
            stop.set(f"lane {profile} failed: {type(exc).__name__}")
            raise

    results = await asyncio.gather(
        *(one(pos, profile, rows) for pos, (profile, rows) in enumerate(lanes)),
        return_exceptions=True,
    )
    outcomes: list[R] = []
    for result in results:
        if isinstance(result, BaseException):
            raise result
        outcomes.extend(result)
    return outcomes
