# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The bridge between asynchronous sources and the synchronous bus.

Sources are async (core/sources.py); the bus and engine are synchronous and
deterministic (decision D4). The bridge joins them in two ways.

**History.** A ``HistoryFeed`` is an ordinary iterator the engine reads like
any other feed. A private event loop, owned by ``SourceLoop``, advances the
source's stream one message per ``next()``, so the engine still reads at most
one message ahead and the run stays deterministic; no threads are involved.
Each message is checked (the kind requested: Points for trades, Bars of the
requested size for bars; for the requested instrument; inside the requested
interval; in event-time order) and stamped as known its source's declared
delay after it happened (decision D24).

**Live.** ``run_live`` runs every subscription as a task feeding one queue.
A single consumer takes each message off the queue, checks it is something
that source was subscribed to, stamps it with the clock at that moment, and
publishes it, so stamps follow publication order and never go back. A bar
can never be known before it closes; when one arrives up to ``skew`` before
its end by our clock (the venue's clock runs ahead of ours) it is held until
our clock reaches its end, and one arriving earlier than that is a partial
bar and refused. The first source to fail stops the rest and its error is
raised.
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Iterator, Sequence
from types import TracebackType
from typing import Any, Self

from heliograph.core.clock import Clock
from heliograph.core.errors import InvalidValueError, OutOfOrderError
from heliograph.core.messages import Bar, Message, Point
from heliograph.core.sources import (
    DataSource,
    HistoryRequest,
    Subscription,
    known_in_history,
    known_now,
)
from heliograph.core.time import NANOS_PER_SECOND, Timestamp, format_span

type Sleep = Callable[[float], Awaitable[None]]

SKEW = NANOS_PER_SECOND
"""How far ahead of ours a venue's clock may run before its bars are refused as partial."""


class SourceLoop:
    """One private event loop that drives sources from synchronous code; a context manager.

    It must not be used from inside a running event loop: there, use the
    sources directly.
    """

    def __init__(self) -> None:
        self._runner = asyncio.Runner()

    def run[T](self, coroutine: Coroutine[Any, Any, T]) -> T:
        """Run ``coroutine`` to completion on this loop."""
        return self._runner.run(coroutine)

    def close(self) -> None:
        self._runner.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        self.close()


async def _next(stream: AsyncIterator[Message]) -> Message | None:
    try:
        return await anext(stream)
    except StopAsyncIteration:
        return None


def _source_data(message: Message) -> Point | Bar:
    """``message`` if it is a Point or Bar, the only things a source may yield."""
    if isinstance(message, Point | Bar):
        return message
    raise InvalidValueError(f"a source yielded a {type(message).__name__}, not a Point or Bar")


def _what(data: Point | Bar) -> tuple[str, str, int | None]:
    """What ``data`` is, to compare with what was asked for: instrument, kind, bar size."""
    if isinstance(data, Point):
        return data.instrument.key, "trades", None
    return data.instrument.key, "bars", data.end - data.start


def _describe(what: tuple[str, str, int | None]) -> str:
    key, kind, size = what
    return f"{key} {kind}" if size is None else f"{key} {format_span(size)} {kind}"


async def _close(stream: AsyncIterator[Message]) -> None:
    """Close a source's stream if it can be closed (async generators can)."""
    aclose = getattr(stream, "aclose", None)
    if aclose is not None:
        await aclose()


class HistoryFeed(Iterator[Message]):
    """A source's history as a feed for the engine: checked, stamped, read lazily.

    Use it as a context manager, or call ``close`` when done, so the source's
    stream is closed even when the engine stops before the end.
    """

    def __init__(self, loop: SourceLoop, source: DataSource, request: HistoryRequest) -> None:
        self._loop = loop
        self._request = request
        self._delay = source.capabilities.live_delay
        self._stream = source.history(request)
        self._previous: Timestamp | None = None
        self._done = False
        self.delivered = 0
        """How many messages the source has delivered so far."""

    def __iter__(self) -> Self:
        return self

    def __next__(self) -> Message:
        if self._done:
            raise StopIteration
        message = self._loop.run(_next(self._stream))
        if message is None:
            self.close()
            raise StopIteration
        known = known_in_history(self._checked(message), self._delay)
        self.delivered += 1
        return known

    def _checked(self, message: Message) -> Point | Bar:
        request = self._request
        data = _source_data(message)
        happened = data.event_time if isinstance(data, Point) else data.start
        instrument = data.instrument
        if instrument != request.instrument:
            raise InvalidValueError(
                f"asked for {request.instrument.key}, the source sent {instrument.key}"
            )
        asked = (request.instrument.key, request.kind, request.bar_size)
        if _what(data) != asked:
            raise InvalidValueError(
                f"asked for {_describe(asked)}, the source sent {_describe(_what(data))}"
            )
        if not request.start <= happened < request.end:
            raise InvalidValueError(
                f"{request.instrument.key}: the source sent data from {happened}, "
                f"outside [{request.start}, {request.end})"
            )
        if isinstance(data, Bar) and data.end > request.end:
            raise InvalidValueError(
                f"{request.instrument.key}: the source sent a bar ending at {data.end}, "
                f"after the request does ({request.end}): a bar must be whole within it"
            )
        if self._previous is not None and happened < self._previous:
            raise OutOfOrderError(
                f"{request.instrument.key}: the source went back in time "
                f"from {self._previous} to {happened}"
            )
        self._previous = happened
        return data

    def close(self) -> None:
        """Close the source's stream; safe to call more than once."""
        if not self._done:
            self._done = True
            self._loop.run(_close(self._stream))

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


async def run_live(
    feeds: Sequence[tuple[DataSource, Sequence[Subscription]]],
    clock: Clock,
    publish: Callable[[Message], None],
    *,
    sleep: Sleep = asyncio.sleep,
    skew: int = SKEW,
) -> None:
    """Publish live data from every source, stamped on arrival, until all end.

    ``clock`` and ``sleep`` must agree: the real time and ``asyncio.sleep``
    in use, or a simulated clock and a sleep that advances it in tests.

    Raises:
        InvalidValueError: for data a source was not subscribed to, or a bar
            arriving more than ``skew`` before it closed (a partial bar).
        Whatever the first failing source raised; the others are cancelled.
    """
    type Wanted = frozenset[tuple[str, str, int | None]]
    queue: asyncio.Queue[tuple[Wanted, Message] | None] = asyncio.Queue()

    async def produce(source: DataSource, subscriptions: Sequence[Subscription]) -> None:
        wanted = frozenset((s.instrument.key, s.kind, s.bar_size) for s in subscriptions)
        stream = source.subscribe(subscriptions)
        try:
            async for message in stream:
                await queue.put((wanted, message))
        finally:
            await _close(stream)
            await queue.put(None)

    async def consume() -> None:
        remaining = len(feeds)
        while remaining:
            item = await queue.get()
            if item is None:
                remaining -= 1
                continue
            wanted, message = item
            data = _source_data(message)
            if _what(data) not in wanted:
                raise InvalidValueError(f"a source sent {_describe(_what(data))} unasked")
            if isinstance(data, Bar):
                await _until_closed(data, clock, sleep, skew)
            publish(known_now(data, clock.now()))

    try:
        async with asyncio.TaskGroup() as group:
            for source, subscriptions in feeds:
                group.create_task(produce(source, subscriptions))
            group.create_task(consume())
    except* Exception as failures:
        # The task group gathers every failure; the first is the cause, and
        # the rest are usually its consequences.
        raise failures.exceptions[0] from failures


async def _until_closed(bar: Bar, clock: Clock, sleep: Sleep, skew: int) -> None:
    """Wait until our clock reaches the bar's end, if it is at most ``skew`` away."""
    early = bar.end - clock.now()
    if early > skew:
        raise InvalidValueError(
            f"bar for {bar.instrument.key} arrived {format_span(early)} before it closed "
            f"at {bar.end}: a partial bar, or a venue clock more than "
            f"{format_span(skew)} ahead of ours"
        )
    while (now := clock.now()) < bar.end:
        await sleep((bar.end - now) / NANOS_PER_SECOND)
