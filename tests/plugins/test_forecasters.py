# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Forecasters and gates, against their definitions."""

import math
from collections.abc import Sequence

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


def ewmac(
    prices: Sequence[float], fast: int, slow: int, span: int, scalar: float
) -> list[float | None]:
    """The definition, written out: EWMAs from the first price, EW variance of changes."""
    out: list[float | None] = [None]
    f = s = prices[0]
    var: float | None = None
    for t in range(1, len(prices)):
        change = prices[t] - prices[t - 1]
        f = f + 2 / (fast + 1) * (prices[t] - f)
        s = s + 2 / (slow + 1) * (prices[t] - s)
        var = change**2 if var is None else var + 2 / (span + 1) * (change**2 - var)
        if t + 1 < slow:
            out.append(None)
        elif var == 0:
            out.append(0.0)
        else:
            out.append(scalar * (f - s) / math.sqrt(var))
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
