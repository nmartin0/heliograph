# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""A data source that yields what a test gives it and records how it was used."""

from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field

from heliograph.core.messages import Message, Point
from heliograph.core.sources import (
    Capabilities,
    Health,
    HistoryRequest,
    Subscription,
    Throttle,
)
from heliograph.core.time import NANOS_PER_MINUTE, Timestamp
from tests.support.world import BTC

TRADES = Capabilities(history=frozenset({"trades"}), live=frozenset({"trades"}))

type Step = Message | Callable[[], Awaitable[object]]
"""What a scripted source does next: yield a message, or await something (a gate)."""


@dataclass
class ScriptedSource:
    """Yields ``history`` for any request and plays ``live`` for any subscription.

    A step that is a callable is awaited instead of yielded, so a test can
    hold a source at a chosen point (an ``asyncio.Event().wait``) and force
    an interleaving rather than hope for one.
    """

    history_messages: Sequence[Message] = ()
    live: Sequence[Step] = ()
    caps: Capabilities = TRADES
    requests: list[HistoryRequest] = field(default_factory=list)
    yielded: int = 0
    streams_closed: int = 0
    closed: bool = False
    fail_after: int | None = None
    failure: Exception = field(default_factory=lambda: RuntimeError("source failed"))
    throttle: Throttle | None = None

    @property
    def capabilities(self) -> Capabilities:
        return self.caps

    def bind(self, throttle: Throttle) -> None:
        self.throttle = throttle

    async def history(self, request: HistoryRequest) -> AsyncIterator[Message]:
        self.requests.append(request)
        try:
            for message in self.history_messages:
                if self.fail_after is not None and self.yielded >= self.fail_after:
                    raise self.failure
                self.yielded += 1
                yield message
        finally:
            self.streams_closed += 1

    async def subscribe(self, subscriptions: Sequence[Subscription]) -> AsyncIterator[Message]:
        try:
            for step in self.live:
                if callable(step):
                    await step()
                else:
                    if self.fail_after is not None and self.yielded >= self.fail_after:
                        raise self.failure
                    self.yielded += 1
                    yield step
        finally:
            self.streams_closed += 1

    async def health_check(self) -> Health:
        return Health(ok=True)

    async def close(self) -> None:
        self.closed = True


def trade(minute: float, price: float = 100.0) -> Point:
    """A BTC trade at ``minute``, stamped as received then (the bridge restamps it)."""
    at = Timestamp(int(minute * NANOS_PER_MINUTE))
    return Point(
        instrument=BTC, event_time=at, received_at=at, values={"price": price, "size": 1.0}
    )
