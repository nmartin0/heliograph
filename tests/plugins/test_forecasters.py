# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Forecasters and gates, against their definitions."""

import math
from collections.abc import Sequence

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from heliograph.plugins.forecasters import (
    Ewmac,
    EwmacParams,
    Linear,
    LinearParams,
    Threshold,
    ThresholdParams,
)
from heliograph.testkit.checks import bars


def feed(node: Ewmac | Linear | Threshold, values: Sequence[float | None]) -> list[float | None]:
    return [node.update([v]) for v in values]


def floored(vols: Sequence[float], long: float, window: int = 500) -> float:
    """The last volatility, floored (from 100 changes on) at the 5% quantile of the last
    ``window``, numpy's linear quantile as pandas' rolling quantile gives it, and at half
    the long-run volatility ``long``."""
    if len(vols) < 100:
        return vols[-1]
    return max(vols[-1], float(np.quantile(vols[-window:], 0.05)), 0.5 * long)


def ewmac(
    prices: Sequence[float], fast: int, slow: int, span: int, scalar: float
) -> list[float | None]:
    """The definition, written out: EWMAs from the first price, EW variance of the
    changes but those of a stall (the third unchanged price in a row on), its root
    floored."""
    out: list[float | None] = [None]
    f = s = prices[0]
    var: float | None = None
    long_var: float | None = None
    squares: list[float] = []
    vols: list[float] = []
    unchanged = 0
    for t in range(1, len(prices)):
        change = prices[t] - prices[t - 1]
        f = f + 2 / (fast + 1) * (prices[t] - f)
        s = s + 2 / (slow + 1) * (prices[t] - s)
        unchanged = unchanged + 1 if change == 0 else 0
        if unchanged < 3:  # from the third unchanged price in a row, a stall: left out
            var = change**2 if var is None else var + 2 / (span + 1) * (change**2 - var)
            squared = change**2 if not squares else min(change**2, 25 * long_var)  # type: ignore[operator]
            squares.append(squared)
            long_var = (
                sum(squares) / len(squares)  # the mean, for the first 100
                if len(squares) <= 100
                else long_var + 2 / 501 * (squared - long_var)  # type: ignore[operator]
            )
            vols.append(math.sqrt(var))
        vol = floored(vols, math.sqrt(long_var or 0.0)) if vols else None
        if t + 1 < slow:
            out.append(None)
        elif not vol:
            out.append(0.0)
        else:
            out.append(scalar * (f - s) / vol)
    return out


@settings(max_examples=30, deadline=None)
@given(
    fast=st.integers(1, 20),
    gap=st.integers(1, 40),
    span=st.integers(2, 50),
    seed=st.integers(0, 2**32 - 1),
)
def test_ewmac_follows_its_definition(fast: int, gap: int, span: int, seed: int) -> None:
    prices = [b[0] for b in bars(("close",), 200, seed)]
    params = EwmacParams(fast=fast, slow=fast + gap, vol_span=span, scalar=3.75)
    got = feed(Ewmac(params), prices)
    want = ewmac(prices, fast, fast + gap, span, 3.75)  # type: ignore[arg-type]
    assert [g is None for g in got] == [w is None for w in want]
    assert [g for g in got if g is not None] == pytest.approx(
        [w for w in want if w is not None], rel=1e-9, abs=1e-12
    )


def test_ewmac_on_prices_that_do_not_move_forecasts_nothing() -> None:
    out = feed(Ewmac(EwmacParams(fast=2, slow=4)), [100.0] * 6)
    assert out == [None, None, None, 0.0, 0.0, 0.0]


def test_ewmac_rising_prices_forecast_long_and_falling_short() -> None:
    up = feed(Ewmac(EwmacParams(fast=2, slow=8)), [100.0 + t + (t % 2) for t in range(30)])
    down = feed(Ewmac(EwmacParams(fast=2, slow=8)), [200.0 - t - (t % 2) for t in range(30)])
    assert up[-1] is not None
    assert up[-1] > 0
    assert down[-1] is not None
    assert down[-1] < 0


def test_an_ewmac_whose_fast_average_is_not_faster_is_refused() -> None:
    with pytest.raises(ValidationError, match=r"fast \(8\) must be less than slow \(8\)"):
        EwmacParams(fast=8, slow=8)


def test_linear_is_the_scaled_distance_from_the_centre() -> None:
    assert feed(Linear(LinearParams(scalar=-2.0, centre=1.0)), [None, 1.0, 4.0]) == [
        None,
        0.0,
        -6.0,
    ]


@pytest.mark.parametrize(
    ("params", "passes"),
    [
        ({"above": 1.0}, [0.0, 0.0, 1.0]),
        ({"below": 1.0}, [1.0, 0.0, 0.0]),
        ({"above": 0.0, "below": 2.0}, [0.0, 1.0, 0.0]),
    ],
)
def test_a_threshold_passes_strictly_inside_its_bounds(
    params: dict[str, float], passes: list[float]
) -> None:
    assert feed(Threshold(ThresholdParams(**params)), [0.0, 1.0, 2.0]) == passes


@pytest.mark.parametrize(
    ("params", "reason"),
    [({}, "needs above, below, or both"), ({"above": 2.0, "below": 1.0}, "must be less than")],
)
def test_a_threshold_that_can_never_pass_or_bounds_nothing_is_refused(
    params: dict[str, float], reason: str
) -> None:
    with pytest.raises(ValidationError, match=reason):
        ThresholdParams(**params)


@pytest.mark.parametrize(("fast", "slow"), [(16, 64), (32, 128), (64, 256)])
def test_ewmac_stays_bounded_when_prices_stop_moving(fast: int, slow: int) -> None:
    """A stalled feed: 64/256 once reached -170,163 after 400 flat bars. Unchanged prices
    are left out of the volatility, so the forecast only fades as the averages meet."""
    rng = np.random.default_rng(7)
    moving = list(100.0 + np.cumsum(rng.normal(0, 1, 600)))
    out = feed(Ewmac(EwmacParams(fast=fast, slow=slow)), moving + [moving[-1]] * 400)
    stalled = [abs(x) for x in out[600:] if x is not None]
    assert max(stalled) <= 1.1 * stalled[0]  # two unchanged prices count, then a stall
    assert stalled[-1] < 0.5 * stalled[0]


def test_ewmac_shrinks_on_a_price_bouncing_one_tick() -> None:
    """A thin feed whose last price bounces 0.01: never stale, and the quantile floor falls
    with it; half the long-run volatility holds the forecast down as it fades."""
    rng = np.random.default_rng(11)
    moving = list(100.0 * np.exp(np.cumsum(rng.normal(0, 0.005, 1500))))
    bouncing = [moving[-1] + (0.01 if i % 2 else 0.0) for i in range(400)]
    out = feed(Ewmac(EwmacParams(fast=64, slow=256)), moving + bouncing)
    while_moving = max(abs(x) for x in out[:1500] if x is not None)
    late = [abs(x) for x in out[1700:] if x is not None]
    assert max(abs(x) for x in out[1500:] if x is not None) <= while_moving
    assert late[-1] < late[0]  # fading, not growing


def test_an_ewmac_slower_than_its_floor_can_hold_is_refused() -> None:
    with pytest.raises(ValidationError, match="at most twice floor_window"):
        EwmacParams(fast=64, slow=1200)


def test_ewmac_recovers_soon_from_one_bad_print() -> None:
    """One close at 1.5 times the price, then back: the long-run floor takes no change
    larger than five of its volatilities, so 200 bars on the forecast is itself again
    (without that cap, it was 0.44 of the clean run's)."""
    rng = np.random.default_rng(5)
    clean = list(100.0 * np.exp(np.cumsum(rng.normal(0, 0.005, 1200))))
    printed = [*clean[:600], clean[600] * 1.5, *clean[601:]]
    params = EwmacParams(fast=16, slow=64)
    a = feed(Ewmac(params), clean)[800:1000]
    b = feed(Ewmac(params), printed)[800:1000]
    ratio = sum(abs(x) for x in b if x is not None) / sum(abs(x) for x in a if x is not None)
    assert ratio > 0.9
