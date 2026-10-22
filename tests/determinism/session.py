# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""One journalled session, run as its own process by the determinism tests.

    python -m tests.determinism.session <journal path> [--set-order | --local-time]

The inputs are fixed (a seeded generator, not the process's own randomness),
so any difference between two runs comes from the process: its hash seed or
its time zone. Two variants exist only so the tests can show the comparison
sees such differences: ``--set-order`` publishes in the iteration order of a
set of strings, which follows the hash seed, and ``--local-time`` publishes
each trade's time as local wall-clock text, which follows the time zone.
"""

import random
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from heliograph.core.journal import Journal
from heliograph.core.messages import Message, Point
from heliograph.core.time import NANOS_PER_MINUTE, Timestamp
from heliograph.plugins.sqlite_journal import SqliteJournal, SqliteJournalParams
from tests.support.world import END, points, toy_session, world

NAMES = frozenset({"alpha", "beta", "gamma", "delta", "epsilon"})

type Segment = tuple[list[Message], Timestamp]


@dataclass(frozen=True, slots=True)
class Named:
    ts: Timestamp
    name: str


def segments() -> tuple[Segment, Segment]:
    """300 trades over 700 minutes from a fixed seed, run as two runs cut at minute 300."""
    rng = random.Random(20261022)
    feed = points((rng.randrange(700), rng.randrange(90, 121)) for _ in range(300))
    cut = Timestamp(300 * NANOS_PER_MINUTE)
    return (
        ([p for p in feed if p.ts <= cut], cut),
        ([p for p in feed if p.ts > cut], END),
    )


def set_order_session(journal: Journal, *runs: Segment) -> None:
    """Publish one message per name in a set of strings for every trade: hash-order output."""
    w = world(journal=journal)

    def announce(point: Point) -> None:
        for name in NAMES:  # deliberately in hash order
            w.bus.publish(Named(point.ts, name))

    w.bus.subscribe(Point, announce)
    for feed, until in runs:
        w.engine.run(feed, until=until)


def local_time_session(journal: Journal, *runs: Segment) -> None:
    """Publish each trade's time as local wall-clock text: time-zone-dependent output."""
    w = world(journal=journal)

    def announce(point: Point) -> None:
        local = datetime.fromtimestamp(point.ts / 1e9)  # noqa: DTZ006 - deliberately local
        w.bus.publish(Named(point.ts, local.isoformat()))

    w.bus.subscribe(Point, announce)
    for feed, until in runs:
        w.engine.run(feed, until=until)


def main(argv: list[str]) -> int:
    path = Path(argv[0])
    with SqliteJournal(SqliteJournalParams(path=str(path))) as journal:
        if "--set-order" in argv[1:]:
            set_order_session(journal, *segments())
        elif "--local-time" in argv[1:]:
            local_time_session(journal, *segments())
        else:
            toy_session(journal, *segments())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
