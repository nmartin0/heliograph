# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The Halted state: a live session that refuses to add risk until its owner says so (D55).

Anything may halt a session: the owner by hand, or a watch that notices something wrong (no
prices, too many signals, a feed that gave up). Halting is the owner's to undo: only a
``Cleared`` from the owner ends it, and nothing clears itself.

While halted a strategy still sends signals that only take risk off (a stop, an exit to flat),
as with every risk gate (D34); entries and additions are refused and journalled as
``SignalRefused``. ``Halt`` is that gate: a live session puts it first on every strategy.

Two watches halt a session by themselves: ``SilenceWatch`` when no price has come for a set time,
and ``RateWatch`` when a strategy has signalled more often than it should (D56).

Both messages are stamped when they became known. The owner's are inputs, replayed; a watch's
are derived from the data and the clock, so a replay produces them again.
"""

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from heliograph.core.alarms import AlarmFired, Scheduler
from heliograph.core.bus import MessageBus
from heliograph.core.errors import InvalidValueError
from heliograph.core.messages import Instrument, Point
from heliograph.core.pipeline import Proposal, Signal
from heliograph.core.time import Timestamp, format_span

OWNER = "owner"


@dataclass(frozen=True, slots=True, kw_only=True)
class Halted:
    """The session was halted at ``ts`` by ``by`` ("owner", or the name of a watch)."""

    ts: Timestamp
    reason: str
    by: str = OWNER


@dataclass(frozen=True, slots=True, kw_only=True)
class Cleared:
    """The owner cleared the halt at ``ts``."""

    ts: Timestamp
    by: str = OWNER


class Halt:
    """A risk gate that refuses to add risk while the session is halted."""

    __slots__ = ("_halted",)

    def __init__(self, bus: MessageBus) -> None:
        self._halted: Halted | None = None
        bus.subscribe(Halted, self._on_halted)
        bus.subscribe(Cleared, self._on_cleared)

    @property
    def halted(self) -> Halted | None:
        """The halt in force (the first, if several came), or None."""
        return self._halted

    def _on_halted(self, message: Halted) -> None:
        if self._halted is None:
            self._halted = message

    def _on_cleared(self, message: Cleared) -> None:
        self._halted = None

    def check(self, proposal: Proposal) -> str | None:
        if self._halted is None:
            return None
        return f"halted by {self._halted.by}: {self._halted.reason}"

    def sent(self, proposal: Proposal) -> None:
        return None


class SilenceWatch:
    """Halts the session when no price of ``instrument`` has come for ``span`` nanoseconds.

    Each price pushes one alarm (under this watch's own key) ``span`` on. It starts with the
    first price: a feed that never gives one is for the feed's own checks. It halts once per
    silence; after a price it is armed again. A resumed session (D53) is silent for as long
    as it was down, so resuming after longer than ``span`` halts at once.
    """

    def __init__(
        self,
        name: str,
        instrument: Instrument,
        span: int,
        bus: MessageBus,
        scheduler: Scheduler,
        now: Callable[[], Timestamp],
    ) -> None:
        if span <= 0:
            raise InvalidValueError(f"the silence to halt after must be positive, not {span}")
        self._by = f"silence:{name}"
        self._instrument = instrument
        self._span = span
        self._bus = bus
        self._scheduler = scheduler
        self._now = now
        scheduler.claim(self._by)
        bus.subscribe(Point, self._on_point)
        bus.subscribe(AlarmFired, self._on_alarm)

    def _on_point(self, point: Point) -> None:
        if point.instrument == self._instrument:
            # Not before now: a live clock may be a little past the price it just read.
            self._scheduler.at(Timestamp(max(point.ts + self._span, self._now())), self._by)

    def _on_alarm(self, alarm: AlarmFired) -> None:
        if alarm.key == self._by:
            self._bus.publish(
                Halted(
                    ts=alarm.ts,
                    reason=f"no price of {self._instrument.symbol} for {format_span(self._span)}",
                    by=self._by,
                ),
                origin="derived",
            )


class RateWatch:
    """Halts the session when ``strategy`` signals more than ``count`` times within ``span``."""

    def __init__(self, strategy: str, count: int, span: int, bus: MessageBus) -> None:
        if count < 1 or span <= 0:
            raise InvalidValueError("a signal-rate watch needs a count of 1 or more and a span")
        self._strategy = strategy
        self._count = count
        self._span = span
        self._bus = bus
        self._times: deque[Timestamp] = deque()
        bus.subscribe(Signal, self._on_signal)

    def _on_signal(self, signal: Signal) -> None:
        if signal.strategy != self._strategy:
            return
        self._times.append(signal.ts)
        while self._times[0] <= signal.ts - self._span:
            self._times.popleft()
        if len(self._times) > self._count:
            self._times.clear()  # one halt for a burst; the next burst counts afresh
            self._bus.publish(
                Halted(
                    ts=signal.ts,
                    reason=(
                        f"{self._strategy} signalled more than {self._count} times "
                        f"in {format_span(self._span)}"
                    ),
                    by=f"rate:{self._strategy}",
                ),
                origin="derived",
            )
