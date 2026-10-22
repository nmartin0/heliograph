# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The TA-Lib indicators: one sample at a time, bit-identical to TA-Lib's batch values."""

from collections.abc import Callable, Sequence
from fractions import Fraction
from typing import Any, Literal

import numpy as np
import pytest
import talib
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from heliograph.core.errors import InvalidValueError
from heliograph.plugins.indicators import Ema, PeriodParams, Sma
from heliograph.plugins.talib_indicators import (
    Atr,
    AtrParams,
    Bands,
    BandsParams,
    Macd,
    MacdParams,
    Rsi,
    RsiParams,
    StdDev,
    StdDevParams,
)
from heliograph.testkit.checks import bars

LONG = 400


def feed(indicator: Any, updates: Sequence[Sequence[float | None]]) -> list[float | None]:
    return [indicator.update(list(update)) for update in updates]


def batch(values: Any) -> list[float | None]:
    return [None if np.isnan(v) else float(v) for v in values]


def columns(updates: Sequence[Sequence[float | None]]) -> list[Any]:
    return [np.array(column, dtype=np.float64) for column in zip(*updates, strict=True)]


# Each plug-in, its parameters, the bar fields it reads, and TA-Lib's batch function
# giving the same series.
type Case = tuple[Callable[[Any], Any], Any, tuple[str, ...], Callable[..., Any]]


def cases(period: int, fast: int, slow: int, signal: int) -> list[Case]:
    out: list[Case] = [
        (Rsi, RsiParams(period=period), ("close",), lambda c: talib.RSI(c, timeperiod=period)),
        (
            Atr,
            AtrParams(period=period),
            ("high", "low", "close"),
            lambda h, lo, c: talib.ATR(h, lo, c, timeperiod=period),
        ),
        (
            StdDev,
            StdDevParams(period=period),
            ("close",),
            lambda c: talib.STDDEV(c, timeperiod=period, nbdev=1.0),
        ),
    ]
    lines: tuple[Literal["macd", "signal", "histogram"], ...] = ("macd", "signal", "histogram")
    for index, line in enumerate(lines):
        params = MacdParams(fast=fast, slow=slow, signal=signal, output=line)
        out.append(
            (
                Macd,
                params,
                ("close",),
                lambda c, i=index: talib.MACD(
                    c, fastperiod=fast, slowperiod=slow, signalperiod=signal
                )[i],
            )
        )
    bands: tuple[Literal["upper", "middle", "lower"], ...] = ("upper", "middle", "lower")
    for index, band in enumerate(bands):
        out.append(
            (
                Bands,
                BandsParams(period=period, deviations=1.5, output=band),
                ("close",),
                lambda c, i=index: talib.BBANDS(c, timeperiod=period, nbdevup=1.5, nbdevdn=1.5)[i],
            )
        )
    return out


@settings(max_examples=25, deadline=None)
@given(
    period=st.integers(2, 40),
    fast=st.integers(2, 20),
    gap=st.integers(1, 20),
    signal=st.integers(1, 15),
    seed=st.integers(0, 2**32 - 1),
)
def test_every_indicator_and_output_equals_ta_libs_batch_values_bit_for_bit(
    period: int, fast: int, gap: int, signal: int, seed: int
) -> None:
    for plugin, params, fields, function in cases(period, fast, fast + gap, signal):
        updates = bars(fields, LONG, seed)
        assert feed(plugin(params), updates) == batch(function(*columns(updates))), plugin


def test_the_first_atr_is_the_mean_true_range_of_the_bars_after_the_first() -> None:
    """True ranges of bars 2, 3 and 4 of the conformance bars: 0.7, 0.7, 0.8."""
    out = feed(Atr(AtrParams(period=3)), Atr.conformance.references[0].inputs)
    assert out[:3] == [None, None, None]
    assert out[3] == pytest.approx(float(Fraction(22, 30)), abs=1e-12)


def test_the_first_rsi_is_wilders_ratio_of_average_gain_to_average_loss() -> None:
    """Changes -0.25, +0.06, -0.54: average gain 0.02, loss 0.79/3, RSI 100 - 100/(1 + RS)."""
    out = feed(Rsi(RsiParams(period=3)), Rsi.conformance.references[0].inputs)
    rs = Fraction(6, 100) / Fraction(79, 100)
    assert out[3] == pytest.approx(float(100 - 100 / (1 + rs)), abs=1e-9)


@pytest.mark.parametrize(
    ("ours", "theirs"),
    [
        (Sma, lambda c, n: talib.SMA(c, timeperiod=n)),
        (Ema, lambda c, n: talib.EMA(c, timeperiod=n)),
    ],
)
def test_the_plain_averages_agree_with_ta_lib(
    ours: type[Sma | Ema], theirs: Callable[[Any, int], Any]
) -> None:
    """D1's averages: TA-Lib keeps a running sum and Sma sums afresh, so close, not equal."""
    for period in (2, 5, 20):
        updates = bars(("close",), LONG, period)
        got = feed(ours(PeriodParams(period=period)), updates)
        want = batch(theirs(columns(updates)[0], period))
        assert [g is None for g in got] == [w is None for w in want]
        assert [g for g in got if g is not None] == pytest.approx(
            [w for w in want if w is not None], rel=1e-12
        )


def test_an_input_without_a_sample_gives_nothing_and_is_not_kept() -> None:
    updates = bars(("high", "low", "close"), 60, 11)
    after_none = Atr(AtrParams(period=3))
    assert after_none.update([None, 1.0, 1.5]) is None
    assert feed(after_none, updates) == feed(Atr(AtrParams(period=3)), updates)


def test_a_stream_is_opened_once_its_lookback_is_held_not_tried_on_every_sample(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The warm-up costs one opening; trying on every sample would cost O(n²)."""
    attempts: list[int] = []
    real = talib.stream.MACD

    def counted(*columns: Any, **params: Any) -> Any:
        attempts.append(len(columns[0]))
        return real(*columns, **params)

    monkeypatch.setattr(talib.stream, "MACD", counted)
    macd = Macd(MacdParams(fast=12, slow=200, signal=50))
    feed(macd, bars(("close",), 400, 5))
    assert attempts == [200 - 1 + 50 - 1 + 1]


def test_the_history_held_while_warming_up_is_let_go_once_the_stream_opens() -> None:
    rsi = Rsi(RsiParams(period=14))
    feed(rsi, bars(("close",), 200, 7))
    assert rsi._stream._history == []  # the stream holds the state


def test_an_indicator_given_another_number_of_inputs_is_refused() -> None:
    with pytest.raises(InvalidValueError, match="atr reads 3 inputs, given 1"):
        Atr(AtrParams()).update([1.0])


def test_a_macd_whose_fast_average_is_not_faster_is_refused() -> None:
    with pytest.raises(ValidationError, match=r"fast \(26\) must be less than slow \(12\)"):
        MacdParams(fast=26, slow=12)
