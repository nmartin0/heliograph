# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Immutable messages exchanged between layers.

Every message published on the bus says when it became known (``ts``). A
layer can never change a message another layer sent: messages are frozen.
"""

import math
from dataclasses import dataclass
from typing import Protocol

from heliograph.core.errors import InvalidValueError
from heliograph.core.time import Timestamp


class Message(Protocol):
    """Anything that can be published on the message bus."""

    @property
    def ts(self) -> Timestamp:
        """The instant this message became known."""
        ...


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


@dataclass(frozen=True, slots=True, kw_only=True)
class Bar:
    """Open, high, low, close and volume over the interval ``[start, end)``."""

    instrument: Instrument
    start: Timestamp
    end: Timestamp
    open: float
    high: float
    low: float
    close: float
    volume: float

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise InvalidValueError(f"bar for {self.instrument.key} does not end after it starts")
        prices = (self.open, self.high, self.low, self.close)
        if not all(math.isfinite(value) for value in (*prices, self.volume)):
            raise InvalidValueError(f"bar for {self.instrument.key} has a non-finite value")
        if not self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high:
            raise InvalidValueError(f"bar for {self.instrument.key} has prices outside [low, high]")
        if self.volume < 0:
            raise InvalidValueError(f"bar for {self.instrument.key} has negative volume")

    @property
    def ts(self) -> Timestamp:
        """A bar is known only once its interval has closed."""
        return self.end
