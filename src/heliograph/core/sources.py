# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Data sources: the port through which market data enters Heliograph.

A source is a plug-in that says what it can do (its ``Capabilities``) and
does it asynchronously: ``history`` for a past interval, ``subscribe`` for
live data. The core declares the protocol and owns no event loop; the app
drives sources and feeds what they produce into the synchronous bus
(docs/design.md, "Adapters"; decision D13).

When a message became known is not the source's to say. A source yields
trades as ``Point``s and vendor bars as ``Bar``s with their event times; the
bridge stamps ``received_at`` in one place, with the rules here:

- history is known at its event time plus the source's declared
  ``live_delay``, so a backtest never sees data sooner than a live run would
  (decision D24);
- live data is known when it arrives, by the run's clock.

Capabilities are declared, never discovered: the run configuration is
checked against them before anything is built, and the problems are all
reported at once (``Capabilities.problems``).

Limits are declared by the source and enforced by the app, as Hummingbot's
connectors declare rate-limit rules for a framework throttler: before
building any source the app binds it to a ``Throttle`` made from its declared
rate limit and concurrency, and the source enters the throttle around every
request it makes, each page of a long history included.
"""

import dataclasses
import math
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, get_args, overload

from heliograph.core.errors import HeliographError, InvalidValueError
from heliograph.core.messages import Bar, Instrument, Message, Point
from heliograph.core.time import Timestamp, format_span

type DataKind = Literal["trades", "bars"]
"""What a source delivers: trades as ``Point``s, or the vendor's own ``Bar``s."""

type Term = Literal[
    "attribution", "no_live_trading", "no_redistribution", "non_commercial", "personal_only"
]
"""A condition of the data's terms of use that the configuration must respect."""

DATA_KINDS: frozenset[str] = frozenset(get_args(DataKind.__value__))
TERMS: frozenset[str] = frozenset(get_args(Term.__value__))


class SourceUnavailableError(HeliographError):
    """A source could not deliver now: unreachable, refused, or over its limit.

    ``retry_after`` is how long the source asked to wait, in nanoseconds, when
    it said.
    """

    def __init__(self, message: str, *, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


@dataclass(frozen=True, slots=True, kw_only=True)
class RateLimit:
    """At most ``calls`` requests in any ``period`` nanoseconds."""

    calls: int
    period: int

    def __post_init__(self) -> None:
        for name in ("calls", "period"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise InvalidValueError(f"rate limit {name} must be a positive int, got {value!r}")


@dataclass(frozen=True, slots=True, kw_only=True)
class Capabilities:
    """What a source can deliver, and on what terms.

    ``bar_sizes`` are the vendor bar sizes offered, in nanoseconds; a source
    offering bars must name at least one. ``lookback`` is how far back history
    reaches, in nanoseconds before now (None: no limit). ``live_delay`` is how
    long after an event the source delivers it live; history is stamped with
    it. ``rate_limit`` and ``concurrency`` are enforced by the app, never by
    the source itself. ``withheld`` says why a kind of history the source
    could deliver is not offered (shown when one is asked for).
    """

    history: frozenset[DataKind] = frozenset()
    live: frozenset[DataKind] = frozenset()
    bar_sizes: frozenset[int] = frozenset()
    lookback: int | None = None
    live_delay: int = 0
    rate_limit: RateLimit | None = None
    concurrency: int = 1
    terms: frozenset[Term] = frozenset()
    withheld: str = ""

    def __post_init__(self) -> None:
        for name in ("history", "live", "bar_sizes", "terms"):
            object.__setattr__(self, name, frozenset(getattr(self, name)))
        unknown = (self.history | self.live) - DATA_KINDS
        if unknown:
            raise InvalidValueError(
                f"unknown data kinds {sorted(unknown)}; known: {sorted(DATA_KINDS)}"
            )
        if self.terms - TERMS:
            raise InvalidValueError(
                f"unknown terms {sorted(self.terms - TERMS)}; known: {sorted(TERMS)}"
            )
        if "bars" in self.history | self.live and not self.bar_sizes:
            raise InvalidValueError("a source offering bars must name its bar sizes")
        if any(type(s) is not int or s <= 0 for s in self.bar_sizes):
            raise InvalidValueError("bar sizes must be positive ints of nanoseconds")
        if self.lookback is not None and (type(self.lookback) is not int or self.lookback <= 0):
            raise InvalidValueError("lookback must be a positive int of nanoseconds, or None")
        if type(self.live_delay) is not int or self.live_delay < 0:
            raise InvalidValueError("live delay must be an int of nanoseconds, at least 0")
        if type(self.concurrency) is not int or self.concurrency < 1:
            raise InvalidValueError("concurrency must be an int, at least 1")

    def problems(
        self, kind: DataKind, *, live: bool, bar_size: int | None = None, reach: int | None = None
    ) -> list[str]:
        """Why this source cannot serve a request, or nothing if it can.

        ``reach`` is how far back a history request starts, in nanoseconds
        before now; ``bar_size`` is required for bars.
        """
        mode, offered = ("live", self.live) if live else ("history", self.history)
        found = []
        if kind not in offered:
            why = f" ({self.withheld})" if self.withheld and not live else ""
            found.append(f"offers no {kind} {mode}{why}")
        if kind == "bars" and bar_size not in self.bar_sizes:
            sizes = ", ".join(format_span(s) for s in sorted(self.bar_sizes)) or "none"
            wanted = format_span(bar_size) if bar_size else "no size"
            found.append(f"offers no {wanted} bars (sizes: {sizes})")
        if live and "no_live_trading" in self.terms:
            found.append("its terms forbid live use")
        if not live and reach is not None and self.lookback is not None and reach > self.lookback:
            found.append(f"reaches back {format_span(self.lookback)}, not {_span(reach)}")
        return found


def _span(nanos: int) -> str:
    """``nanos`` as a span, or as a count when no span can hold it (over 292 years)."""
    return format_span(nanos) if nanos <= _INT64_MAX else f"{nanos} ns"


_INT64_MAX = 2**63 - 1


@dataclass(frozen=True, slots=True, kw_only=True)
class HistoryRequest:
    """Data of one kind for one instrument with event times in ``[start, end)``."""

    instrument: Instrument
    kind: DataKind
    start: Timestamp
    end: Timestamp
    bar_size: int | None = None

    def __post_init__(self) -> None:
        if self.kind not in DATA_KINDS:
            raise InvalidValueError(f"unknown data kind {self.kind!r}")
        if self.end <= self.start:
            raise InvalidValueError("a history request must end after it starts")
        if (self.kind == "bars") != (self.bar_size is not None):
            raise InvalidValueError("a bar size is given for bars, and only for bars")
        if self.bar_size is not None and (type(self.bar_size) is not int or self.bar_size <= 0):
            raise InvalidValueError("bar size must be a positive int of nanoseconds")


@dataclass(frozen=True, slots=True, kw_only=True)
class Subscription:
    """Live data of one kind for one instrument."""

    instrument: Instrument
    kind: DataKind
    bar_size: int | None = None

    def __post_init__(self) -> None:
        if self.kind not in DATA_KINDS:
            raise InvalidValueError(f"unknown data kind {self.kind!r}")
        if (self.kind == "bars") != (self.bar_size is not None):
            raise InvalidValueError("a bar size is given for bars, and only for bars")
        if self.bar_size is not None and (type(self.bar_size) is not int or self.bar_size <= 0):
            raise InvalidValueError("bar size must be a positive int of nanoseconds")


class Throttle(Protocol):
    """Admission for one request at a time, within a source's declared limits.

    Used as ``async with throttle:`` around each request; entering waits
    until the rate limit allows another request and a concurrency slot is
    free.
    """

    async def __aenter__(self) -> None: ...

    async def __aexit__(self, *exc: object) -> None: ...


@dataclass(frozen=True, slots=True)
class Health:
    """A source's own report on whether it can deliver."""

    ok: bool
    detail: str = ""


class DataSource(Protocol):
    """Market data from outside, asynchronously.

    The messages a source yields are ``Point``s (trades, with at least
    ``price``) and ``Bar``s. Their ``received_at`` is replaced by the bridge
    (see the module docstring), so a source may set it to the event time.
    """

    @property
    def capabilities(self) -> Capabilities: ...

    def bind(self, throttle: Throttle) -> None:
        """Use ``throttle`` around every request from now on; called once, before use."""
        ...

    def history(self, request: HistoryRequest) -> AsyncIterator[Message]:
        """The messages with event times in the request's interval, in event-time order.

        Raises:
            SourceUnavailableError: if the source cannot deliver now.
        """
        ...

    def subscribe(self, subscriptions: Sequence[Subscription]) -> AsyncIterator[Message]:
        """Live messages, as they arrive, until the iterator is closed.

        Raises:
            SourceUnavailableError: if the source cannot deliver now.
        """
        ...

    async def health_check(self) -> Health: ...

    async def close(self) -> None:
        """Release connections; called once, after the last use."""
        ...


@dataclass(frozen=True, slots=True, kw_only=True)
class Listing:
    """An instrument as one venue lists it: our instrument and the venue's own symbol."""

    instrument: Instrument
    venue_symbol: str
    price_increment: float | None = None
    size_increment: float | None = None

    def __post_init__(self) -> None:
        if not self.venue_symbol.strip():
            raise InvalidValueError(f"{self.instrument.key}: the venue symbol must not be empty")
        for name in ("price_increment", "size_increment"):
            value = getattr(self, name)
            if value is not None and not 0 < value < math.inf:
                key = self.instrument.key
                raise InvalidValueError(f"{key}: {name} must be positive and finite")


class InstrumentProvider(Protocol):
    """Where instruments and the venue's names for them come from."""

    async def load(self) -> Sequence[Listing]:
        """Every listing this provider knows, each instrument once."""
        ...


@overload
def known_in_history(message: Point, live_delay: int) -> Point: ...
@overload
def known_in_history(message: Bar, live_delay: int) -> Bar: ...
@overload
def known_in_history(message: Message, live_delay: int) -> Message: ...


def known_in_history(message: Message, live_delay: int) -> Message:
    """``message`` as a backtest should see it: known ``live_delay`` after it happened.

    Points are known at their event time plus the delay. Bars are known at
    their end plus the delay; with no delay, at their end, which a bar records
    as no separate received time.

    Raises:
        InvalidValueError: for a message that is neither a Point nor a Bar.
    """
    if isinstance(message, Point):
        return dataclasses.replace(message, received_at=Timestamp(message.event_time + live_delay))
    if isinstance(message, Bar):
        known = None if live_delay == 0 else Timestamp(message.end + live_delay)
        return dataclasses.replace(message, received_at=known)
    raise InvalidValueError(f"a source yielded a {type(message).__name__}, not a Point or Bar")


@overload
def known_now(message: Point, now: Timestamp) -> Point: ...
@overload
def known_now(message: Bar, now: Timestamp) -> Bar: ...
@overload
def known_now(message: Message, now: Timestamp) -> Message: ...


def known_now(message: Message, now: Timestamp) -> Message:
    """``message`` as a live run sees it: known now, when it arrived.

    Raises:
        InvalidValueError: for a message that is neither a Point nor a Bar, or
            a bar that arrived before it closed (a partial bar).
    """
    if isinstance(message, Point):
        return dataclasses.replace(message, received_at=now)
    if isinstance(message, Bar):
        return dataclasses.replace(message, received_at=None if now == message.end else now)
    raise InvalidValueError(f"a source yielded a {type(message).__name__}, not a Point or Bar")
