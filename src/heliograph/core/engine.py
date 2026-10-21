# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The backtest event loop: replays recorded feeds through the bus in time order."""

import heapq
from collections.abc import Iterable, Iterator

from heliograph.core.alarms import AlarmQueue, Scheduler, SchedulerView
from heliograph.core.bus import MessageBus
from heliograph.core.clock import SimulatedClock
from heliograph.core.errors import (
    AlarmLoopError,
    ClockError,
    InvalidValueError,
    OutOfOrderError,
    ReentrancyError,
)
from heliograph.core.messages import Message
from heliograph.core.time import Timestamp


class BacktestEngine:
    """Merges time-ordered feeds and publishes each message at its own instant.

    Before a message is published, the simulated clock is moved to the
    message's timestamp, so every component sees exactly what was known at
    that moment and nothing later. Messages with equal timestamps are
    published in the order of the feeds passed to :meth:`run`.

    Alarms set through :attr:`scheduler` are fired in time order between feed
    messages; an alarm due at the same instant as a message fires first.
    """

    MAX_ALARMS_PER_INSTANT = 10_000
    """More alarms than this at one instant means a handler keeps re-setting one."""

    __slots__ = ("_alarms", "_bus", "_clock", "_running", "_scheduler")

    def __init__(
        self, clock: SimulatedClock, bus: MessageBus, alarms: AlarmQueue | None = None
    ) -> None:
        if not bus.runs_on(clock):
            raise InvalidValueError("the engine and the bus must share one clock")
        if alarms is not None and not alarms.runs_on(clock):
            raise InvalidValueError("the engine and its alarms must share one clock")
        self._clock = clock
        self._bus = bus
        self._alarms = alarms if alarms is not None else AlarmQueue(clock)
        self._scheduler = SchedulerView(self._alarms)
        self._running = False

    @property
    def scheduler(self) -> Scheduler:
        """Where components set and cancel alarms; only the engine fires them."""
        return self._scheduler

    def run(self, *feeds: Iterable[Message], until: Timestamp | None = None) -> int:
        """Replay ``feeds`` to the end and return how many feed messages were published.

        Alarms due up to the last message are fired on the way. With ``until``,
        the run stops there: messages stamped later are not published, and if
        the feeds end earlier the run continues to ``until``, firing the alarms
        due by then and leaving the clock there; that is how the last bars of a
        replay get closed.

        Raises:
            OutOfOrderError: if a feed goes back in time.
            ClockError: if a feed starts before the clock's current time.
            ClockError: if ``until`` is earlier than the clock's current time.
            AlarmLoopError: if alarms keep firing at one instant.
            ReentrancyError: if called from a handler or during another run.
                Moving the clock while a message is being handled would let
                that handler see a later time than its message's.
        """
        if self._running or self._bus.dispatching:
            raise ReentrancyError("a run cannot start inside another run or a handler")
        if until is not None and until < self._clock.now():
            raise ClockError(f"cannot run until {until}, which is before now ({self._clock.now()})")
        merged = heapq.merge(
            *(_in_order(index, feed) for index, feed in enumerate(feeds)),
            key=_timestamp,
        )
        self._running = True
        try:
            published = 0
            for message in merged:
                if until is not None and message.ts > until:
                    break
                self._fire_alarms_due_by(message.ts)
                self._clock.advance_to(message.ts)
                self._bus.publish(message)
                published += 1
            end = until if until is not None else self._clock.now()
            self._fire_alarms_due_by(end)
            self._clock.advance_to(end)
            return published
        finally:
            self._running = False

    def _fire_alarms_due_by(self, limit: Timestamp) -> None:
        # A handler may set new alarms; any due by the limit fire in this loop too.
        instant: Timestamp | None = None
        fired_here = 0
        while (alarm := self._alarms.pop_due(limit)) is not None:
            fired_here = fired_here + 1 if alarm.ts == instant else 1
            instant = alarm.ts
            if fired_here > self.MAX_ALARMS_PER_INSTANT:
                raise AlarmLoopError(
                    f"more than {self.MAX_ALARMS_PER_INSTANT} alarms fired at {alarm.ts}; "
                    f"a handler probably re-sets {alarm.key!r} for the current instant"
                )
            self._clock.advance_to(alarm.ts)
            self._bus.publish(alarm)


def _timestamp(message: Message) -> Timestamp:
    return message.ts


def _in_order(index: int, feed: Iterable[Message]) -> Iterator[Message]:
    previous: Timestamp | None = None
    for message in feed:
        if previous is not None and message.ts < previous:
            raise OutOfOrderError(f"feed {index} went back in time from {previous} to {message.ts}")
        previous = message.ts
        yield message
