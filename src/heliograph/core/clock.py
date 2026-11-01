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


class ResumedClock:
    """Stands at the instants of an earlier session while it is rebuilt, then follows ``live``.

    A session resumed from its journal feeds the old inputs back at the times they
    were first known (``stand_at``, forwards only); ``release`` then lets time run
    on from ``live``. It never goes back: if ``live`` is behind where the old
    session stopped, time holds there until ``live`` catches up.
    """

    __slots__ = ("_held", "_latest", "_live")

    def __init__(self, live: Clock) -> None:
        self._live = live
        self._latest = Timestamp(0)
        self._held = True

    def stand_at(self, ts: Timestamp) -> None:
        """Hold the clock at ``ts``.

        Raises:
            ClockError: if ``ts`` is earlier than the clock has been, or it was released.
        """
        if not self._held:
            raise ClockError("the clock was released and now follows the live one")
        if ts < self._latest:
            raise ClockError(f"cannot move the clock back from {self._latest} to {ts}")
        self._latest = ts

    def release(self) -> None:
        """Follow the live clock from now on."""
        self._held = False

    def now(self) -> Timestamp:
        if not self._held:
            self._latest = Timestamp(max(self._latest, self._live.now()))
        return self._latest


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
