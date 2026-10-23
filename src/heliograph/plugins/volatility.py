# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The volatility of price changes, with floors (after Carver's ``robust_vol_calc``).

An exponential average of squared price changes, weight ``2 / (span + 1)``;
its square root is the volatility per bar. Once the price has not changed
for ``stall_after`` (3) bars in a row, the feed is taken to have stalled
(or the market to be shut between its bars), and further unchanged prices
are left out until it moves: a stall neither drains the estimate nor makes
what is divided by it grow (a position sized to it, a forecast normalised
by it), and the first move after is measured against the volatility from
before. A market that often closes unchanged, but not for long, is
measured per bar as it trades.

Small changes still drain it (a quiet market, a price bouncing one tick),
so two floors hold it up once ``floor_min`` (100) changes are known:

- as pysystemtrade does, the ``floor_quantile`` (5%) of its own last
  ``floor_window`` (500) values (pysystemtrade counts those in days; here
  they are changes of the stream it reads). Before the window is full, the
  quantile is of the values known so far;
- ``floor_long`` (half) of the long-run volatility: the same average with a
  span of ``floor_window``, started from the mean of the first
  ``floor_min`` squared changes, and taking no change larger than five
  times its own volatility (a bad print, a gap), so that one large change
  does not hold it up for hundreds of bars. The quantile falls with a
  long quiet spell, once its values are more than 5% of the window; the long-run volatility falls by
  ``1 / (floor_window + 1)`` a change, more slowly than an exponential
  average of span under ``2 * floor_window`` converges, so a crossover of
  such averages divided by it shrinks in a quiet spell instead of growing.

Positions are also capped where they are sized (``vol_target``'s
``max_leverage``).
"""

import bisect
import math
from collections import deque
from typing import Self

from pydantic import model_validator

from heliograph.core.errors import InvalidValueError
from heliograph.plugins.manifest import Params, param

_LARGEST = 5.0
"""The largest change the long-run level takes, in its own volatilities."""


class FloorParams(Params):
    """The floors' settings, shared by the plug-ins that estimate volatility."""

    floor_window: int = param(
        500,
        description="Recent volatilities the floor is taken from, and the long-run span, in bars",
        ge=2,
        le=100_000,
    )
    floor_quantile: float = param(
        0.05, description="A floor: this quantile of the recent volatilities", ge=0.0, le=1.0
    )
    floor_long: float = param(
        0.5, description="A floor: this fraction of the long-run volatility", ge=0.0, le=1.0
    )
    floor_min: int = param(
        100, description="No floor until this many price changes are known", ge=1, le=100_000
    )
    stall_after: int = param(
        3,
        description="Unchanged prices in a row after which further ones are left out",
        ge=1,
        le=100_000,
    )

    @model_validator(mode="after")
    def _a_floor_that_can_apply(self) -> Self:
        if self.floor_min > self.floor_window:
            raise ValueError(
                f"floor_min ({self.floor_min}) must not exceed floor_window "
                f"({self.floor_window}), or the quantile floor never applies"
            )
        return self


class FlooredVol:
    """The volatility per change of a series of prices; see the module."""

    __slots__ = (
        "_count",
        "_floor_long",
        "_floor_min",
        "_long",
        "_long_weight",
        "_quantile",
        "_recent",
        "_sorted",
        "_stall_after",
        "_unchanged",
        "_variance",
        "_weight",
    )

    def __init__(self, span: int, params: FloorParams) -> None:
        if span < 1:
            raise InvalidValueError("a volatility needs a span of 1 or more")
        self._weight = 2.0 / (span + 1)
        self._long_weight = 2.0 / (params.floor_window + 1)
        self._quantile = params.floor_quantile
        self._floor_long = params.floor_long
        self._floor_min = params.floor_min
        self._stall_after = params.stall_after
        self._unchanged = 0  # unchanged prices in a row
        self._count = 0
        self._variance: float | None = None
        self._long = 0.0
        self._recent: deque[float] = deque(maxlen=params.floor_window)
        self._sorted: list[float] = []

    @property
    def count(self) -> int:
        """The changes taken in (unchanged prices in a stall are not)."""
        return self._count

    def add(self, change: float) -> None:
        """Take in one price change; one of 0 in a stall is left out."""
        if change == 0:
            self._unchanged += 1
            if self._unchanged >= self._stall_after:
                return
        else:
            self._unchanged = 0
        self._count += 1
        squared = change * change
        if self._variance is None:
            self._variance = squared
        else:
            self._variance += self._weight * (squared - self._variance)
        if self._count > 1:
            squared = min(squared, _LARGEST**2 * self._long)  # one bad print holds nothing up
        if self._count <= self._floor_min:
            self._long += (squared - self._long) / self._count  # the mean so far
        else:
            self._long += self._long_weight * (squared - self._long)
        raw = math.sqrt(self._variance)
        if len(self._recent) == self._recent.maxlen:
            oldest = self._recent[0]
            del self._sorted[bisect.bisect_left(self._sorted, oldest)]
        self._recent.append(raw)
        bisect.insort(self._sorted, raw)

    @property
    def raw(self) -> float | None:
        """The volatility before its floors, or None before the first change."""
        return None if self._variance is None else math.sqrt(self._variance)

    @property
    def floor(self) -> float | None:
        """The higher floor, or None while fewer than ``floor_min`` changes are known."""
        known = len(self._sorted)
        if self._count < self._floor_min:
            return None
        # The quantile as pandas' rolling quantile gives it: linear between ranks.
        at = self._quantile * (known - 1)
        low = math.floor(at)
        high = min(low + 1, known - 1)
        quantile = self._sorted[low] + (self._sorted[high] - self._sorted[low]) * (at - low)
        return max(quantile, self._floor_long * math.sqrt(self._long))

    @property
    def vol(self) -> float | None:
        """The volatility, floored once the floors are known; None before the first change."""
        raw = self.raw
        floor = self.floor
        if raw is None or floor is None:
            return raw
        return max(raw, floor)
