# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Clocks: the only source of the current time.

Nothing else in Heliograph reads the system time; a lint rule enforces it.
A backtest runs on a SimulatedClock driven by the data, paper trading on a
WallClock, and the rest of the system cannot tell the difference.
"""

import time
from collections.abc import Callable
from typing import Protocol

from heliograph.core.errors import ClockError
from heliograph.core.time import Timestamp


class Clock(Protocol):
    """Tells the current time."""

    def now(self) -> Timestamp:
        """The current instant."""
        ...


class SimulatedClock:
    """A clock that moves only when told to, and only forwards."""

    __slots__ = ("_now",)

    def __init__(self, start: Timestamp) -> None:
        self._now = start

    def now(self) -> Timestamp:
        return self._now

    def advance_to(self, ts: Timestamp) -> None:
        """Move the clock to ``ts``. Staying at the same instant is allowed.

        Raises:
            ClockError: if ``ts`` is earlier than the current time.
        """
        if ts < self._now:
            raise ClockError(f"cannot move the clock back from {self._now} to {ts}")
        self._now = ts


class WallClock:
    """The real current time, from the operating system, never going backwards.

    The system clock can step back, for example when NTP corrects it. The bus
    refuses any message stamped later than now, so a step back would turn
    every message already stamped into a look-ahead error and stop a live run.
    This clock therefore holds at the latest time it has reported until the
    system clock catches up. Live data is stamped with this clock when it is
    received, not with the venue's own timestamp, for the same reason.
    """

    __slots__ = ("_latest", "_source")

    def __init__(self, source: Callable[[], int] = time.time_ns) -> None:
        self._source = source
        self._latest = Timestamp(source())

    def now(self) -> Timestamp:
        self._latest = Timestamp(max(self._latest, self._source()))
        return self._latest
