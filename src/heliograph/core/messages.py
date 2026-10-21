# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Immutable messages exchanged between layers.

Every message published on the bus says when it became known (``ts``). A
layer can never change a message another layer sent: messages are frozen.

Market data is bitemporal: an observation has the time it happened by its
source's clock and the time Heliograph received it. Only the second is
``ts``, so a backtest sees the past as it was known, and a source whose clock
runs ahead of ours cannot trip the look-ahead guard.
"""

import math
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from numbers import Real
from typing import Any, Protocol

from heliograph.core.errors import InvalidValueError
from heliograph.core.time import Timestamp


class Message(Protocol):
    """Anything that can be published on the message bus."""

    @property
    def ts(self) -> Timestamp:
        """The instant this message became known."""
        ...


@dataclass(frozen=True, slots=True)
class Settled:
    """Everything stamped ``ts`` has been delivered; nothing more will be at ``ts``.

    The engine publishes it once per instant at which anything happened,
    before time moves on. A consumer that combines streams (a 1-hour and a
    4-hour bar both closing at 16:00) acts on this rather than on whichever
    arrived first. What a handler publishes in reaction is still stamped
    ``ts`` and is delivered before time moves on.
    """

    ts: Timestamp


@dataclass(frozen=True, slots=True)
class RunEnded:
    """A run stopped at ``ts``; journals keep it so a replay can stop at the same place.

    Published by the engine as the last message of each run, after the final
    Settled; the one message allowed at an instant that has settled.
    """

    ts: Timestamp


@dataclass(frozen=True, slots=True, kw_only=True)
class Instrument:
    """Something that can be priced. Markets are described by data, never by code."""

    symbol: str
    venue: str
    asset_class: str
    quote_currency: str

    def __post_init__(self) -> None:
        for name in ("symbol", "venue", "asset_class", "quote_currency"):
            if not getattr(self, name).strip():
                raise InvalidValueError(f"Instrument.{name} must not be empty")
        # The key joins venue and symbol with ":". Symbols may contain one
        # (CCXT writes "BTC/USDT:USDT"), so the venue may not, or two
        # instruments could share a key.
        if ":" in self.venue:
            raise InvalidValueError(f"Instrument.venue {self.venue!r} must not contain ':'")

    @property
    def key(self) -> str:
        """A unique, stable name such as ``binance:BTC/USDT``."""
        return f"{self.venue}:{self.symbol}"


class FrozenValues(Mapping[str, float]):
    """An immutable, hashable mapping of names to floats that pickles and copies."""

    __slots__ = ("_items",)

    def __init__(self, items: Mapping[str, float]) -> None:
        self._items = dict(items)

    def __getitem__(self, name: str) -> float:
        return self._items[name]

    def __iter__(self) -> Iterator[str]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __hash__(self) -> int:
        return hash(frozenset(self._items.items()))

    def __repr__(self) -> str:
        return f"FrozenValues({self._items!r})"

    def __reduce__(self) -> tuple[type["FrozenValues"], tuple[dict[str, float]]]:
        return (FrozenValues, (dict(self._items),))


@dataclass(frozen=True, slots=True, kw_only=True)
class Point:
    """One observation of an instrument: a trade, a quote, a reading.

    ``values`` holds whatever the source reports under plain names, such as
    ``{"price": 101.5, "size": 0.25}``; bars and everything else are derived
    from points by plug-ins. ``event_time`` is when it happened according to
    the source; ``received_at`` is when Heliograph learned it, and is ``ts``.
    """

    instrument: Instrument
    event_time: Timestamp
    received_at: Timestamp
    values: Mapping[str, float]

    def __post_init__(self) -> None:
        key = self.instrument.key
        if not isinstance(self.values, Mapping):
            raise InvalidValueError(f"point for {key}: values must be a mapping of names")
        if not self.values:
            raise InvalidValueError(f"point for {key} has no values")
        what = f"point for {key}"
        clean = {name: _finite_number(what, name, value) for name, value in self.values.items()}
        object.__setattr__(self, "values", FrozenValues(clean))

    @property
    def ts(self) -> Timestamp:
        """A point is known when it is received."""
        return self.received_at


@dataclass(frozen=True, slots=True, kw_only=True)
class Bar:
    """Open, high, low, close and volume over the interval ``[start, end)``.

    A bar is known once its interval closes. A bar that arrived later than
    that, such as one downloaded from a vendor, records when in
    ``received_at``; it can never be known before it closes.
    """

    instrument: Instrument
    start: Timestamp
    end: Timestamp
    open: float
    high: float
    low: float
    close: float
    volume: float
    received_at: Timestamp | None = None

    def __post_init__(self) -> None:
        if self.received_at is not None and self.received_at < self.end:
            raise InvalidValueError(
                f"bar for {self.instrument.key} received at {self.received_at}, "
                f"before it closed at {self.end}"
            )
        if self.end <= self.start:
            raise InvalidValueError(f"bar for {self.instrument.key} does not end after it starts")
        # Stored as floats, so a bar journalled and read back is the same bar.
        for name in ("open", "high", "low", "close", "volume"):
            number = _finite_number(f"bar for {self.instrument.key}", name, getattr(self, name))
            object.__setattr__(self, name, number)
        if not self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high:
            raise InvalidValueError(f"bar for {self.instrument.key} has prices outside [low, high]")
        if self.volume < 0:
            raise InvalidValueError(f"bar for {self.instrument.key} has negative volume")

    @property
    def ts(self) -> Timestamp:
        """When the bar became known: when received, or else when it closed."""
        return self.end if self.received_at is None else self.received_at


def _finite_number(key: str, name: object, value: Any) -> float:
    """``value`` as a float, refusing anything that is not a finite real number."""
    if not isinstance(name, str) or not name.strip():
        raise InvalidValueError(f"{key} has a value without a name: {name!r}")
    if isinstance(value, bool) or not isinstance(value, Real):
        raise InvalidValueError(f"{key}: {name} is {value!r}, not a number")
    try:
        number = float(value)
    except OverflowError as exc:
        raise InvalidValueError(f"{key}: {name} is too large for a float") from exc
    if not math.isfinite(number):
        raise InvalidValueError(f"{key}: {name} is not finite")
    return number
