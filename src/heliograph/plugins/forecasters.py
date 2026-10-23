# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Forecasters and gates: graph nodes whose samples a strategy reads (core/pipeline.py).

A forecaster gives a forecast on Carver's scale, an average absolute value
of about 10 (its ``scalar`` sets that); the pipeline caps it at ±20. A gate
gives 1 while it passes and 0 while it vetoes. Both are ``Indicator``s, so
the graph runs them and the conformance kit checks them as it does
indicators (decision D30).
"""

from collections.abc import Sequence

from pydantic import model_validator

from heliograph.core.errors import InvalidValueError
from heliograph.plugins.conformance import Conformance, Reference
from heliograph.plugins.manifest import Manifest, Params, Plugin, param
from heliograph.plugins.volatility import FlooredVol, FloorParams


def _one(name: str, inputs: Sequence[float | None]) -> float | None:
    if len(inputs) != 1:
        raise InvalidValueError(f"{name} reads one input, given {len(inputs)}")
    return inputs[0]


class EwmacParams(FloorParams):
    fast: int = param(16, description="Span of the fast average", ge=1, le=10_000, step=1)
    slow: int = param(64, description="Span of the slow average", ge=2, le=10_000, step=1)
    vol_span: int = param(
        36, description="Span of the volatility of price changes", ge=2, le=10_000, step=1
    )
    scalar: float = param(
        3.75,
        description="Forecast scalar: sets the average absolute forecast to 10; negative inverts",
    )

    @model_validator(mode="after")
    def _fast_before_slow(self) -> "EwmacParams":
        if self.fast >= self.slow:
            raise ValueError(f"fast ({self.fast}) must be less than slow ({self.slow})")
        if self.slow > 2 * self.floor_window:
            raise ValueError(
                f"slow ({self.slow}) must be at most twice floor_window ({self.floor_window}), "
                "or a quiet spell could make the forecast grow without limit"
            )
        return self


class Ewmac(Plugin[EwmacParams]):
    """Carver's exponentially weighted moving-average crossover.

    The fast average less the slow, divided by the volatility of price
    changes, times ``scalar`` (negative to invert it). Each average is an
    exponential one with weight 2 / (span + 1), starting at the first price;
    the volatility is the root mean square of price changes, averaged
    exponentially with weight 2 / (vol_span + 1) from the first change.
    Carver takes their standard deviation, which subtracts their mean; in a
    steady trend the root mean square is the larger, so forecasts here are a
    little smaller there. The volatility is floored as Carver's
    ``robust_vol_calc`` floors it (plugins/volatility.py): without a floor,
    when prices stop moving the volatility decays faster than the averages
    converge (whenever ``slow`` is more than about twice ``vol_span``) and
    the forecast grows without limit, so a stalled feed would read as the
    strongest trend there is. A stall's unchanged prices are left out of
    the volatility, so a stall leaves it as it was while the averages
    converge and the forecast fades. It gives a forecast once it has seen ``slow``
    prices; on prices that have not moved at all, 0.
    """

    manifest = Manifest(
        kind="forecasters",
        name="ewmac",
        version="2",
        summary="EWMA crossover, volatility-normalised (Carver)",
        params=EwmacParams,
    )
    conformance = Conformance(
        params={"fast": 1, "slow": 3, "vol_span": 3, "scalar": 1.0},
        references=[
            Reference(
                inputs=[[10.0], [12.0], [11.0], [14.0]],
                outputs=[None, None, 0.0, 1.5 / 5.75**0.5],
                source=(
                    "worked by hand: fast = price; slow 10, 11, 11, 12.5; squared changes "
                    "4, 1, 9 averaged to 4, 2.5, 5.75; "
                    "so (11 - 11) / sqrt(2.5), then (14 - 12.5) / sqrt(5.75)"
                ),
            )
        ],
    )
    arity = 1

    def __init__(self, params: EwmacParams) -> None:
        super().__init__(params)
        self._fast_weight = 2.0 / (params.fast + 1)
        self._slow_weight = 2.0 / (params.slow + 1)
        self._vol = FlooredVol(params.vol_span, params)
        self._fast: float | None = None
        self._slow: float | None = None
        self._last: float | None = None
        self._seen = 0

    def update(self, inputs: Sequence[float | None]) -> float | None:
        price = _one("ewmac", inputs)
        if price is None:
            return None
        self._seen += 1
        if self._fast is None or self._slow is None or self._last is None:
            self._fast = self._slow = self._last = price
            return None
        self._vol.add(price - self._last)
        self._last = price
        self._fast += self._fast_weight * (price - self._fast)
        self._slow += self._slow_weight * (price - self._slow)
        if self._seen < self.params.slow:
            return None
        vol = self._vol.vol
        if not vol:
            return 0.0
        return self.params.scalar * (self._fast - self._slow) / vol


class LinearParams(Params):
    scalar: float = param(1.0, description="Forecast per unit away from the centre")
    centre: float = param(0.0, description="Where the forecast is 0")


class Linear(Plugin[LinearParams]):
    """A forecast proportional to an indicator's distance from a centre.

    ``scalar * (x - centre)``: an RSI with centre 50 and scalar 0.4 gives
    -20 to +20, for instance (negate the scalar to fade it).
    """

    manifest = Manifest(
        kind="forecasters",
        name="linear",
        version="1",
        summary="A forecast proportional to an indicator's distance from a centre",
        params=LinearParams,
    )
    conformance = Conformance(
        params={"scalar": 0.4, "centre": 50.0},
        references=[
            Reference(
                inputs=[[50.0], [75.0], [20.0]],
                outputs=[0.0, 10.0, -12.0],
                source="worked by hand: 0.4 * (x - 50)",
            )
        ],
    )
    arity = 1

    def update(self, inputs: Sequence[float | None]) -> float | None:
        value = _one("linear", inputs)
        if value is None:
            return None
        return self.params.scalar * (value - self.params.centre)


class ThresholdParams(Params):
    above: float | None = param(None, description="Pass only while the input is above this")
    below: float | None = param(None, description="Pass only while the input is below this")

    @model_validator(mode="after")
    def _a_bound(self) -> "ThresholdParams":
        if self.above is None and self.below is None:
            raise ValueError("a threshold needs above, below, or both")
        if self.above is not None and self.below is not None and self.above >= self.below:
            raise ValueError(f"above ({self.above}) must be less than below ({self.below})")
        return self


class Threshold(Plugin[ThresholdParams]):
    """A gate passing (1) while its input is strictly above ``above`` and below ``below``."""

    manifest = Manifest(
        kind="gates",
        name="threshold",
        version="1",
        summary="Pass while an indicator is above and/or below a level",
        params=ThresholdParams,
    )
    conformance = Conformance(
        params={"above": 20.0, "below": 80.0},
        references=[
            Reference(
                inputs=[[10.0], [20.0], [50.0], [80.0], [90.0]],
                outputs=[0.0, 0.0, 1.0, 0.0, 0.0],
                source="worked by hand: strictly between 20 and 80",
            )
        ],
    )
    arity = 1

    def update(self, inputs: Sequence[float | None]) -> float | None:
        value = _one("threshold", inputs)
        if value is None:
            return None
        above, below = self.params.above, self.params.below
        passes = (above is None or value > above) and (below is None or value < below)
        return 1.0 if passes else 0.0
