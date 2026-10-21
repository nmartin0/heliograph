# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The backtest event loop: replays recorded feeds through the bus in time order."""

import heapq
from collections.abc import Iterable, Iterator
from typing import Literal

from heliograph.core.alarms import AlarmFired, AlarmQueue, Scheduler, SchedulerView
from heliograph.core.bus import MessageBus
from heliograph.core.clock import SimulatedClock
from heliograph.core.errors import (
    AlarmLoopError,
    ClockError,
    InvalidTypeError,
    InvalidValueError,
    OutOfOrderError,
    ReentrancyError,
)
from heliograph.core.messages import Message, RunEnded, Settled
from heliograph.core.time import Timestamp


class BacktestEngine:
    """Merges time-ordered feeds and publishes each message at its own instant.

    Before a message is published, the simulated clock is moved to the
    message's timestamp, so every component sees exactly what was known at
    that moment and nothing later. Messages with equal timestamps are
    published in the order of the feeds passed to :meth:`run`.

    One loop takes whatever comes next in time, an alarm set through
    :attr:`scheduler` or a feed message; an alarm due at the same instant as a
    message fires first. When everything at an instant has been delivered,
    and before time moves on or the run ends, the engine publishes
    :class:`Settled` for that instant. Settling is final: a message or alarm
    for a settled instant ends the run with ClockError. Each run ends with a
    :class:`RunEnded` mark, which journals keep so a replay stops where the
    run did.
    """

    MAX_ALARMS_PER_INSTANT = 10_000
    """More alarms than this at one instant means a handler keeps re-setting one."""

    __slots__ = (
        "_alarms",
        "_bus",
        "_clock",
        "_instant",
        "_running",
        "_scheduler",
        "_settled_up_to",
        "_unsettled",
    )

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
        self._instant: Timestamp | None = None
        self._settled_up_to: Timestamp | None = None
        self._unsettled = False

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
            InvalidTypeError: if a feed holds an engine message (AlarmFired,
                Settled, RunEnded), which only the engine may produce.
            ClockError: if a feed starts before the clock's current time, holds
                a message for an instant that has settled, or ``until`` is
                earlier than the clock's current time.
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
                self._advance(message.ts, final=False)
                self._deliver(message, "input")
                published += 1
            end = until if until is not None else self._clock.now()
            self._advance(end, final=True)
            self._clock.advance_to(end)
            self._bus.publish(RunEnded(end), origin="engine")
            return published
        finally:
            self._running = False

    def _advance(self, limit: Timestamp, *, final: bool) -> None:
        """Deliver every alarm due by ``limit`` and settle every instant before it.

        With ``final``, the instant at ``limit`` is settled too. Settling may
        set new alarms, so the loop looks again after each step.
        """
        instant: Timestamp | None = None
        fired_here = 0
        while True:
            due = self._alarms.next_due()
            moving_on = self._unsettled and self._instant is not None
            if due is not None and due <= limit:
                if moving_on and self._instant is not None and due > self._instant:
                    self._settle()
                    continue
                alarm = self._alarms.pop_due(limit)
                assert alarm is not None
                fired_here = fired_here + 1 if alarm.ts == instant else 1
                instant = alarm.ts
                if fired_here > self.MAX_ALARMS_PER_INSTANT:
                    raise AlarmLoopError(
                        f"more than {self.MAX_ALARMS_PER_INSTANT} alarms fired at {alarm.ts}; "
                        f"a handler probably re-sets {alarm.key!r} for the current instant"
                    )
                self._deliver(alarm, "engine")
                continue
            if moving_on and self._instant is not None and (self._instant < limit or final):
                self._settle()
                continue
            return

    def _deliver(self, message: Message, origin: Literal["input", "engine"]) -> None:
        if self._settled_up_to is not None and message.ts <= self._settled_up_to:
            raise ClockError(
                f"{type(message).__name__} at {message.ts}, an instant that has already settled"
            )
        self._clock.advance_to(message.ts)
        self._bus.publish(message, origin=origin)
        self._instant = message.ts
        self._unsettled = True

    def _settle(self) -> None:
        assert self._instant is not None
        self._bus.publish(Settled(self._instant), origin="engine")
        self._unsettled = False
        self._settled_up_to = self._instant
        due = self._alarms.next_due()
        if due is not None and due <= self._instant:
            raise ClockError(f"an alarm was set for {due}, an instant that has already settled")


ENGINE_MESSAGES = (AlarmFired, Settled, RunEnded)
"""Messages only the engine produces; a feed holding one is refused."""


def _timestamp(message: Message) -> Timestamp:
    return message.ts


def _in_order(index: int, feed: Iterable[Message]) -> Iterator[Message]:
    previous: Timestamp | None = None
    for message in feed:
        if isinstance(message, ENGINE_MESSAGES):
            raise InvalidTypeError(
                f"feed {index} holds a {type(message).__name__}, which only the engine produces"
            )
        if previous is not None and message.ts < previous:
            raise OutOfOrderError(f"feed {index} went back in time from {previous} to {message.ts}")
        previous = message.ts
        yield message
