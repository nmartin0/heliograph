"""Clocks: the only source of the current time.

Nothing else in Heliograph reads the system time; a lint rule enforces it.
A backtest runs on a SimulatedClock driven by the data, paper trading on a
WallClock, and the rest of the system cannot tell the difference.
"""

import time
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
    """The real current time, from the operating system."""

    __slots__ = ()

    def now(self) -> Timestamp:
        return Timestamp(time.time_ns())
