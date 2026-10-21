# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The backtest event loop: replays recorded feeds through the bus in time order."""

import heapq
from collections.abc import Iterable, Iterator

from heliograph.core.bus import MessageBus
from heliograph.core.clock import SimulatedClock
from heliograph.core.errors import InvalidValueError, OutOfOrderError, ReentrancyError
from heliograph.core.messages import Message
from heliograph.core.time import Timestamp


class BacktestEngine:
    """Merges time-ordered feeds and publishes each message at its own instant.

    Before a message is published, the simulated clock is moved to the
    message's timestamp, so every component sees exactly what was known at
    that moment and nothing later. Messages with equal timestamps are
    published in the order of the feeds passed to :meth:`run`.
    """

    __slots__ = ("_bus", "_clock", "_running")

    def __init__(self, clock: SimulatedClock, bus: MessageBus) -> None:
        if not bus.runs_on(clock):
            raise InvalidValueError("the engine and the bus must share one clock")
        self._clock = clock
        self._bus = bus
        self._running = False

    def run(self, *feeds: Iterable[Message]) -> int:
        """Replay ``feeds`` to the end and return how many messages were published.

        Raises:
            OutOfOrderError: if a feed goes back in time.
            ClockError: if a feed starts before the clock's current time.
            ReentrancyError: if called from a handler or during another run.
                Moving the clock while a message is being handled would let
                that handler see a later time than its message's.
        """
        if self._running or self._bus.dispatching:
            raise ReentrancyError("a run cannot start inside another run or a handler")
        merged = heapq.merge(
            *(_in_order(index, feed) for index, feed in enumerate(feeds)),
            key=_timestamp,
        )
        self._running = True
        try:
            published = 0
            for message in merged:
                self._clock.advance_to(message.ts)
                self._bus.publish(message)
                published += 1
            return published
        finally:
            self._running = False


def _timestamp(message: Message) -> Timestamp:
    return message.ts


def _in_order(index: int, feed: Iterable[Message]) -> Iterator[Message]:
    previous: Timestamp | None = None
    for message in feed:
        if previous is not None and message.ts < previous:
            raise OutOfOrderError(f"feed {index} went back in time from {previous} to {message.ts}")
        previous = message.ts
        yield message
