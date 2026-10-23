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
from heliograph.plugins.conformance import Conformance
from heliograph.plugins.manifest import Manifest, Params, Plugin, param
from heliograph.plugins.volatility import FlooredVol, FloorParams

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


class VolTargetParams(FloorParams):
    capital: float = param(10_000.0, description="Capital, in the quote currency", gt=0.0)
    target: float = param(
        0.20, description="Annual volatility to aim for, as a fraction of capital", gt=0.0, le=5.0
    )
    bars_per_year: float = param(
        ...,
        description=(
            "Bars of the price stream in a year: 8760 for hourly bars around the clock, "
            "365 for daily ones, about 252 for a stock exchange's days"
        ),
        ge=1.0,
    )
    max_leverage: float = param(
        1.0,
        description="Never a position worth more than this times capital (1: no borrowing)",
        gt=0.0,
        le=100.0,
    )
    vol_span: int = param(
        36, description="Span of the volatility of price changes, in bars", ge=2, le=10_000
    )
    buffer: float = param(
        0.10,
        description="No trade while the position held is this close (x the average) to the target",
        ge=0.0,
        lt=1.0,
    )
    to_edge: bool = param(
        True, description="Outside the buffer, trade to its nearest edge (Carver's default)"
    )


class VolTarget(Plugin[VolTargetParams]):
    """Volatility targeting with a no-trade buffer (Carver).

    The volatility of price changes per bar is estimated like Ewmac's (an
    exponential average of squared changes, weight 2 / (vol_span + 1)),
    floored at a low quantile of its recent values as Carver's
    ``robust_vol_calc`` does (plugins/volatility.py), so a quiet or stalled
    market cannot size a position without limit. The average position, at a
    forecast of 10, is the one whose annual volatility is ``target *
    capital``:

        average = target * capital / (vol per bar * sqrt(bars_per_year))

    and the target is ``average * forecast / 10``. While the position held
    is within ``buffer * average`` of it, the position held is kept: small
    changes do not trade. Outside, it trades to the nearest edge of that
    band (``to_edge``, as pysystemtrade's ``buffer_trade_to_edge: True``),
    or to the target itself. The wanted position is first held to
    ``max_leverage * capital`` in value at the price (a signal asking for
    more than the account holds would be obeyed), and the buffer applies
    to that, so a capped position does not trade at every close as the
    price moves; a position held may so exceed the cap by up to the
    buffer. When the cap is below the average position, the buffer is
    ``buffer`` times the cap instead. A stall's unchanged prices are not
    changes (plugins/volatility.py): a stall neither drains the volatility
    nor sizes up. Before ``floor_min`` changes the volatility has no floor,
    and positions are bounded by the cap alone. It sizes once it has seen
    ``vol_span`` changes, and not at a price that is not positive; while
    it cannot size, the strategy's exits still act on the position held.
    """

    manifest = Manifest(
        kind="sizers",
        name="vol_target",
        version="2",
        summary="Volatility targeting with a no-trade buffer (Carver)",
        params=VolTargetParams,
    )
    conformance = Conformance(params={"bars_per_year": 8760.0})

    def __init__(self, params: VolTargetParams) -> None:
        super().__init__(params)
        self._vol = FlooredVol(params.vol_span, params)
        self._last: float | None = None

    def average(self) -> float | None:
        """The position at a forecast of 10, or None before it can say."""
        vol = self._vol.vol
        if vol is None or self._vol.count < self.params.vol_span or vol == 0:
            return None
        per_year = vol * math.sqrt(self.params.bars_per_year)
        return self.params.target * self.params.capital / per_year

    def target(self, forecast: float | None, price: float, held: float) -> float | None:
        if self._last is not None:
            self._vol.add(price - self._last)
        self._last = price
        average = self.average()
        if forecast is None or average is None or price <= 0:
            return None
        most = self.params.max_leverage * self.params.capital / price
        # Capped before the buffer, so a capped position does not trade every bar.
        wanted = max(-most, min(most, average * forecast / 10.0))
        # The buffer is a share of the position at a forecast of 10, or of the cap if
        # that is smaller: a buffer wider than the capped position would never trade.
        band = self.params.buffer * min(average, most)
        if abs(wanted - held) <= band:
            return held
        if not self.params.to_edge:
            return wanted
        edge = wanted - band if held < wanted else wanted + band
        return max(-most, min(most, edge))


# -- exits ------------------------------------------------------------------------------


class TrailingStopParams(Params):
    multiple: float = param(
        3.0, description="Stop this many units of the input (an ATR) from the best price", ge=0.0
    )


class TrailingStop(Plugin[TrailingStopParams]):
    """A trailing stop ``multiple`` * its input (an ATR, say) behind the best close held.

    Long, the best close is the highest since the position held went long,
    the close the strategy decided to enter on included; when a close falls
    to ``best - multiple * input``, the target becomes flat and stays flat
    while the strategy still wants to be long. It may go long again once the
    strategy's own target is flat or short. The stop only acts while the
    strategy wants to keep the side it holds: leaving or reversing on its
    own is its call. Short, the mirror.

    The best close follows the position held, not what was proposed: a
    proposal a risk gate refused, or one not yet filled, changes nothing.
    """

    manifest = Manifest(
        kind="exits",
        name="trailing_stop",
        version="2",
        summary="Trailing stop a multiple of an indicator (an ATR) behind the best close",
        params=TrailingStopParams,
    )
    arity = 1

    def __init__(self, params: TrailingStopParams) -> None:
        super().__init__(params)
        self._holding = 0.0  # the side held at the last close (1 long, -1 short, 0 flat)
        self._best: float | None = None  # the best close since the side held was taken
        self._entry: tuple[float, float] | None = None  # the side last proposed, and its close
        self._stopped = 0.0  # the side stopped out (1 long, -1 short), until it is let go

    def adjust(
        self, target: float | None, held: float, price: float, values: Sequence[float | None]
    ) -> float | None:
        if len(values) != 1:
            raise InvalidValueError(f"trailing_stop reads one input, given {len(values)}")
        (distance,) = values
        holding = math.copysign(1.0, held) if held else 0.0
        if holding != self._holding:
            # The side held changed since the last close: the best close starts at the
            # close the strategy decided to take that side on, if it proposed it.
            self._holding = holding
            entry = self._entry
            self._best = entry[1] if entry is not None and entry[0] == holding else None
            self._entry = None
        if target is None:
            return None  # nothing to stop while the sizer cannot size; a stop still stands
        side = math.copysign(1.0, target) if target else 0.0
        if self._stopped:
            if side == self._stopped:
                return 0.0  # still wanting the side it was stopped out of
            self._stopped = 0.0
        if side != holding:
            # Entering, leaving or reversing on the strategy's own decision.
            self._entry = (side, price) if side else None
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
            return 0.0
        return target


# -- risk gates -------------------------------------------------------------------------


class LimitsParams(Params):
    max_jump: float = param(
        0.25,
        description="Doubt a price that moved more than this fraction from the last one trusted",
        gt=0.0,
    )
    max_doubts: int = param(
        3, description="Never doubt more closes than this in a row", ge=1, le=100
    )
    max_per_day: int = param(24, description="Refuse more signals than this in 24 hours", ge=1)


class Limits(Plugin[LimitsParams]):
    """Doubt a price jump (a bad print, likely); refuse adding risk past a rate.

    A close more than ``max_jump`` from the last close trusted is doubted
    (``suspect``): it is not acted on, not even to leave, so nothing trades
    on a price that may never have been there. The next close is trusted
    again if it is back within ``max_jump`` of the last trusted (the print
    is gone), or beyond it on the same side as the doubted one (the move
    was real: a crash costs one close). A close the other way (a second
    bad print) is doubted too, but never more than ``max_doubts`` in a
    row, so a broken feed cannot keep exits from acting for long. The rate
    counts the signals sent in the 24 hours up to now, and refuses only
    signals that add risk.
    """

    manifest = Manifest(
        kind="risk_gates",
        name="limits",
        version="2",
        summary="Doubt price jumps; refuse adding risk past a rate of signals",
        params=LimitsParams,
    )

    def __init__(self, params: LimitsParams) -> None:
        super().__init__(params)
        self._sent: deque[int] = deque()
        self._trusted: float | None = None  # the last close trusted
        self._doubts = 0  # closes doubted in a row
        self._direction = 0.0  # the side of the trusted close the first doubt was on

    def suspect(self, proposal: Proposal) -> str | None:
        price, trusted = proposal.price, self._trusted
        if trusted is not None and trusted > 0:
            move = price / trusted - 1.0
            side = math.copysign(1.0, move)
            jumped = abs(move) > self.params.max_jump
            confirmed = bool(self._doubts) and side == self._direction  # the move was real
            if jumped and not confirmed and self._doubts < self.params.max_doubts:
                if not self._doubts:
                    self._direction = side
                self._doubts += 1
                return (
                    f"the price moved {abs(move):.1%} from the last close trusted "
                    f"({trusted} to {price}); held until it is confirmed"
                )
        # No jump, a print gone again, a move confirmed, or doubted long enough.
        self._trusted, self._doubts = price, 0
        return None

    def check(self, proposal: Proposal) -> str | None:
        while self._sent and self._sent[0] <= proposal.ts - NANOS_PER_DAY:
            self._sent.popleft()
        if len(self._sent) >= self.params.max_per_day:
            return f"{len(self._sent)} signals in the last 24 hours already"
        return None

    def sent(self, proposal: Proposal) -> None:
        self._sent.append(proposal.ts)
