# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The moving averages, against values worked out by hand."""

from collections.abc import Sequence

import pytest
from pydantic import ValidationError

from heliograph.core.errors import InvalidValueError
from heliograph.core.graph import Indicator
from heliograph.plugins.indicators import Ema, PeriodParams, Sma
from heliograph.plugins.registry import Registry


def feed(indicator: Indicator, samples: Sequence[float]) -> list[float | None]:
    return [indicator.update([s]) for s in samples]


def test_the_simple_average_is_the_mean_of_the_last_period_samples() -> None:
    sma = Sma(PeriodParams(period=3))
    assert feed(sma, [1, 2, 3, 4, 10]) == [None, None, 2.0, 3.0, pytest.approx(17 / 3)]


def test_the_simple_average_is_summed_exactly_whatever_the_magnitudes() -> None:
    """1e16 + 1 - 1e16 is 0 in floating point added left to right; exactly, it is 1."""
    sma = Sma(PeriodParams(period=3))
    assert feed(sma, [1e16, 1.0, -1e16]) == [None, None, 1 / 3]


def test_the_exponential_average_starts_from_the_simple_one_then_weights_two_over_n_plus_one() -> (
    None
):
    """Period 3: weight 0.5. Seed (1+2+3)/3 = 2, then 2 + 0.5*(4-2) = 3, 3 + 0.5*(10-3) = 6.5."""
    ema = Ema(PeriodParams(period=3))
    assert feed(ema, [1, 2, 3, 4, 10]) == [None, None, 2.0, 3.0, 6.5]


@pytest.mark.parametrize("kind", [Sma, Ema])
def test_an_input_without_a_sample_gives_none_and_changes_nothing(kind: type[Sma | Ema]) -> None:
    indicator = kind(PeriodParams(period=2))
    assert indicator.update([None]) is None
    assert feed(indicator, [4, 6]) == [None, 5.0]


@pytest.mark.parametrize("kind", [Sma, Ema])
def test_an_average_reads_one_input(kind: type[Sma | Ema]) -> None:
    indicator = kind(PeriodParams(period=2))
    assert indicator.arity == 1
    with pytest.raises(InvalidValueError, match="reads one input, given 2"):
        indicator.update([1.0, 2.0])


def test_a_period_below_two_is_refused_as_ta_lib_refuses_it() -> None:
    with pytest.raises(ValidationError, match="greater than or equal to 2"):
        PeriodParams(period=1)


def test_the_averages_are_installed_as_indicators() -> None:
    registry = Registry()
    registry.discover("indicators")
    assert registry.get("indicators", "sma") is Sma
    assert registry.get("indicators", "ema") is Ema
