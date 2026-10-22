# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The per-strategy stages of the pipeline: combiners, sizers, exits and risk gates.

Each is a plug-in whose object a strategy calls at the closes of its price
stream (core/pipeline.py, decision D30). They keep their own state, so a
run gives each to one strategy only.
"""

import math
from collections import deque
from collections.abc import Mapping, Sequence
from types import MappingProxyType

from pydantic import field_serializer, field_validator

from heliograph.core.errors import InvalidValueError
from heliograph.core.pipeline import Proposal
from heliograph.core.time import NANOS_PER_DAY
from heliograph.plugins.manifest import Manifest, Params, Plugin, param

# -- combiners --------------------------------------------------------------------------


class WeightedParams(Params):
    weights: Mapping[str, float] = param(
        {}, description="Weight of each forecaster, by name, summing to 1; empty: equal"
    )
    multiplier: float = param(
        1.0,
        description="Forecast diversification multiplier (Carver caps it at 2.5)",
        ge=1.0,
        le=2.5,
    )

    @field_validator("weights", mode="after")
    @classmethod
    def _sum_to_one(cls, weights: Mapping[str, float]) -> Mapping[str, float]:
        if weights:
            if any(w < 0 or not math.isfinite(w) for w in weights.values()):
                raise ValueError("weights must be finite and not negative")
            if not math.isclose(math.fsum(weights.values()), 1.0, abs_tol=1e-9):
                raise ValueError(f"weights must sum to 1, not {math.fsum(weights.values())}")
        return MappingProxyType(dict(weights))

    @field_serializer("weights")
    def _serialize_weights(self, weights: Mapping[str, float]) -> dict[str, float]:
        return dict(weights)


class Weighted(Plugin[WeightedParams]):
    """The weighted sum of the forecasts, times the diversification multiplier.

    With no weights, every forecast weighs the same. The pipeline caps the
    result at ±20.
    """

    manifest = Manifest(
        kind="combiners",
        name="weighted",
        version="1",
        summary="Weighted sum of forecasts * diversification multiplier (Carver)",
        params=WeightedParams,
    )

    def expects(self) -> frozenset[str] | None:
        """The forecaster names the weights are for, or None if any will do."""
        return frozenset(self.params.weights) or None

    def combine(self, forecasts: Mapping[str, float]) -> float:
        weights = self.params.weights
        if not forecasts:
            raise InvalidValueError("weighted combines at least one forecast")
        if weights and set(weights) != set(forecasts):
            raise InvalidValueError(
                f"weighted has weights for {sorted(weights)}, given {sorted(forecasts)}"
            )
        if weights:
            total = math.fsum(weights[name] * f for name, f in forecasts.items())
        else:
            total = math.fsum(forecasts.values()) / len(forecasts)
        return self.params.multiplier * total


# -- sizers -----------------------------------------------------------------------------


class VolTargetParams(Params):
    capital: float = param(10_000.0, description="Capital, in the quote currency", ge=0.0)
    target: float = param(
        0.20, description="Annual volatility to aim for, as a fraction of capital", ge=0.0, le=5.0
    )
    bars_per_year: float = param(
        8_760.0, description="Price bars in a year: 8760 for hourly bars traded every hour", ge=1.0
    )
    vol_span: int = param(
        36, description="Span of the volatility of price changes, in bars", ge=2, le=10_000
    )
    buffer: float = param(
        0.10,
        description="No trade while the position held is this close (x the average) to the target",
        ge=0.0,
    )
    to_edge: bool = param(
        True, description="Outside the buffer, trade to its nearest edge (Carver's default)"
    )


class VolTarget(Plugin[VolTargetParams]):
    """Volatility targeting with a no-trade buffer (Carver).

    The volatility of price changes per bar is estimated like Ewmac's (an
    exponential average of squared changes, weight 2 / (vol_span + 1)).
    The average position, at a forecast of 10, is the one whose annual
    volatility is ``target * capital``:

        average = target * capital / (vol per bar * sqrt(bars_per_year))

    and the target is ``average * forecast / 10``. While the position held
    is within ``buffer * average`` of it, the position held is kept: small
    changes do not trade. Outside, it trades to the nearest edge of that
    band (``to_edge``, as pysystemtrade's ``buffer_trade_to_edge: True``),
    or to the target itself. It sizes once it has seen ``vol_span``
    changes, and not while prices have not moved at all.
    """

    manifest = Manifest(
        kind="sizers",
        name="vol_target",
        version="1",
        summary="Volatility targeting with a no-trade buffer (Carver)",
        params=VolTargetParams,
    )

    def __init__(self, params: VolTargetParams) -> None:
        super().__init__(params)
        self._weight = 2.0 / (params.vol_span + 1)
        self._last: float | None = None
        self._variance: float | None = None
        self._changes = 0

    def average(self) -> float | None:
        """The position at a forecast of 10, or None before it can say."""
        if self._variance is None or self._changes < self.params.vol_span or self._variance == 0:
            return None
        per_year = math.sqrt(self._variance) * math.sqrt(self.params.bars_per_year)
        return self.params.target * self.params.capital / per_year

    def target(self, forecast: float | None, price: float, held: float) -> float | None:
        if self._last is not None:
            squared = (price - self._last) ** 2
            if self._variance is None:
                self._variance = squared
            else:
                self._variance += self._weight * (squared - self._variance)
            self._changes += 1
        self._last = price
        average = self.average()
        if forecast is None or average is None:
            return None
        wanted = average * forecast / 10.0
        band = self.params.buffer * average
        if abs(wanted - held) <= band:
            return held
        if not self.params.to_edge:
            return wanted
        return wanted - band if held < wanted else wanted + band


# -- exits ------------------------------------------------------------------------------


class TrailingStopParams(Params):
    multiple: float = param(
        3.0, description="Stop this many units of the input (an ATR) from the best price", ge=0.0
    )


class TrailingStop(Plugin[TrailingStopParams]):
    """A trailing stop ``multiple`` * its input (an ATR, say) behind the best close held.

    Long, the best close is the highest since the position was entered,
    the entry close included; when a close falls to ``best - multiple *
    input``, the target becomes flat and stays flat while the strategy
    still wants to be long. It may go long again once the strategy's own
    target is flat or short. The stop only acts while the strategy wants to
    keep the side it holds: leaving or reversing on its own is its call.
    Short, the mirror.
    """

    manifest = Manifest(
        kind="exits",
        name="trailing_stop",
        version="1",
        summary="Trailing stop a multiple of an indicator (an ATR) behind the best close",
        params=TrailingStopParams,
    )
    arity = 1

    def __init__(self, params: TrailingStopParams) -> None:
        super().__init__(params)
        self._best: float | None = None
        self._stopped = 0.0  # the side stopped out (1 long, -1 short), until it is let go

    def adjust(
        self, target: float | None, held: float, price: float, values: Sequence[float | None]
    ) -> float | None:
        if len(values) != 1:
            raise InvalidValueError(f"trailing_stop reads one input, given {len(values)}")
        (distance,) = values
        side = math.copysign(1.0, target) if target else 0.0
        if self._stopped:
            if target is not None and side == self._stopped:
                return 0.0  # still wanting the side it was stopped out of
            self._stopped = 0.0
        if target is None:
            return None
        direction = math.copysign(1.0, held) if held else 0.0
        if side != direction:
            # Entering, leaving or reversing on the strategy's own decision: an entry
            # starts the best close at this one.
            self._best = price if side else None
            return target
        if not side:
            return target
        best = price if self._best is None else self._best
        self._best = max(best, price) if side > 0 else min(best, price)
        if distance is None:
            return target
        stop = self._best - side * self.params.multiple * distance
        if (price - stop) * side <= 0:
            self._stopped = side
            self._best = None
            return 0.0
        return target


# -- risk gates -------------------------------------------------------------------------


class LimitsParams(Params):
    max_jump: float = param(
        0.25,
        description="Refuse when the price moved more than this fraction since the last",
        ge=0.0,
    )
    max_per_day: int = param(24, description="Refuse more signals than this in 24 hours", ge=1)


class Limits(Plugin[LimitsParams]):
    """Refuse a signal that adds risk on a price jump (a bad print, likely) or past a rate.

    The jump is measured from the previous close of the strategy's price
    stream, so a bad print refuses the signal on it and the one on the close
    after (which jumps back); the rate counts the signals sent in the 24
    hours up to now. Signals that only reduce risk never reach a risk gate.
    """

    manifest = Manifest(
        kind="risk_gates",
        name="limits",
        version="1",
        summary="Refuse signals on a price jump or past a rate",
        params=LimitsParams,
    )

    def __init__(self, params: LimitsParams) -> None:
        super().__init__(params)
        self._sent: deque[int] = deque()

    def check(self, proposal: Proposal) -> str | None:
        last = proposal.previous
        if last:
            jump = abs(proposal.price / last - 1.0)
            if jump > self.params.max_jump:
                return (
                    f"the price moved {jump:.1%} since the previous close "
                    f"({last} to {proposal.price})"
                )
        while self._sent and self._sent[0] <= proposal.ts - NANOS_PER_DAY:
            self._sent.popleft()
        if len(self._sent) >= self.params.max_per_day:
            return f"{len(self._sent)} signals in the last 24 hours already"
        return None

    def sent(self, proposal: Proposal) -> None:
        self._sent.append(proposal.ts)
