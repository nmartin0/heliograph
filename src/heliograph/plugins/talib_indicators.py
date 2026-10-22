# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Indicators on TA-Lib's streams: O(1) per sample, identical to TA-Lib's batch values.

Each is a plug-in of kind ``indicators`` and an ``Indicator`` for the graph
(core/graph.py). It holds a TA-Lib stream (``talib.stream``, from TA-Lib 0.8):
samples are kept until the stream can be opened on them, and from then on
each sample is one ``update``. Opening is first tried once the samples reach
the function's lookback plus one, then on each new sample until TA-Lib stops
answering ``InsufficientHistory`` (a few functions seed on a little more), so
the warm-up costs one opening, not one per sample. Every value a stream gives is
bit-identical to TA-Lib's Function API over the same samples (decision D29).

An indicator reading several fields of a bar (ATR reads high, low and close)
gives nothing until every input has a sample. A TA-Lib function with several
outputs (MACD, Bollinger Bands) is one indicator per output, chosen by the
``output`` parameter, so each node of the graph is one series.
"""

from collections.abc import Sequence
from typing import Any, Literal

import numpy as np
import talib
from pydantic import model_validator
from talib import abstract, stream

from heliograph.core.errors import InvalidValueError
from heliograph.plugins.conformance import Conformance, Reference
from heliograph.plugins.manifest import Manifest, Params, Plugin, param

# Exported at runtime (talib.__all__) but missing from TA-Lib's type stubs.
INSUFFICIENT_HISTORY: type[Exception] = talib.InsufficientHistory  # type: ignore[attr-defined]
# The Function API's description of a function (its lookback); also absent from the stubs.
FUNCTION: Any = abstract.Function  # type: ignore[attr-defined]

SOURCE = "TA-Lib 0.8.2's Function API (talib.{}) over the same samples"


class _Stream:
    """One TA-Lib stream, opened once its samples are enough history."""

    __slots__ = ("_function", "_handle", "_history", "_needed", "_output", "_params")

    def __init__(self, function: str, params: dict[str, Any], output: int | None = None) -> None:
        self._function = function
        self._params = params
        self._output = output
        self._history: list[Sequence[float]] = []
        self._handle: Any = None
        lookback = FUNCTION(function)
        lookback.set_parameters(params)
        self._needed: int = lookback.lookback + 1

    def update(self, name: str, arity: int, inputs: Sequence[float | None]) -> float | None:
        if len(inputs) != arity:
            raise InvalidValueError(f"{name} reads {arity} inputs, given {len(inputs)}")
        if any(value is None for value in inputs):
            return None  # an input with no sample yet: wait for every one
        values = [float(value) for value in inputs if value is not None]
        if self._handle is not None:
            return self._pick(self._handle.update(*values))
        self._history.append(values)
        if len(self._history) < self._needed:
            return None
        columns = [
            np.array(column, dtype=np.float64) for column in zip(*self._history, strict=True)
        ]
        try:
            self._handle = getattr(stream, self._function)(*columns, **self._params)
        except INSUFFICIENT_HISTORY:
            return None
        self._history = []  # the stream holds what it needs from here on
        return self._pick(self._handle.value)

    def _pick(self, out: Any) -> float:
        return float(out if self._output is None else out[self._output])


def _reference(inputs: list[list[float]], outputs: list[float | None], function: str) -> Reference:
    return Reference(inputs=inputs, outputs=outputs, source=SOURCE.format(function))


# A fixed series for the references: twelve closes, and bars around them.
_CLOSES = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03]
_BARS = [
    [44.60, 43.90, 44.34],
    [44.50, 43.80, 44.09],
    [44.40, 43.70, 44.15],
    [44.20, 43.40, 43.61],
    [44.50, 43.50, 44.33],
    [45.00, 44.20, 44.83],
    [45.30, 44.70, 45.10],
    [45.60, 44.90, 45.42],
    [46.00, 45.30, 45.84],
    [46.30, 45.70, 46.08],
    [46.20, 45.60, 45.89],
    [46.40, 45.80, 46.03],
]


class RsiParams(Params):
    period: int = param(14, description="Samples in Wilder's smoothing", ge=2, le=10_000, step=1)


class Rsi(Plugin[RsiParams]):
    """Wilder's relative strength index, 0 to 100.

    On closes that do not move at all it is 0, as TA-Lib's is: no gains read
    as fully oversold.
    """

    manifest = Manifest(
        kind="indicators",
        name="rsi",
        version="1",
        summary="Relative strength index (TA-Lib)",
        params=RsiParams,
    )
    conformance = Conformance(
        params={"period": 3},
        references=[
            _reference(
                [[c] for c in _CLOSES],
                [
                    None,
                    None,
                    None,
                    7.058823529411243,
                    59.06735751295326,
                    74.1407528641571,
                    80.08194138039714,
                    85.85813381069593,
                    90.99779218004419,
                    93.13603040999185,
                    72.64564019208689,
                    77.99616695059639,
                ],
                "RSI",
            )
        ],
    )
    arity = 1

    def __init__(self, params: RsiParams) -> None:
        super().__init__(params)
        self._stream = _Stream("RSI", {"timeperiod": params.period})

    def update(self, inputs: Sequence[float | None]) -> float | None:
        return self._stream.update("rsi", self.arity, inputs)


class AtrParams(Params):
    period: int = param(14, description="Bars in Wilder's smoothing", ge=1, le=10_000, step=1)


class Atr(Plugin[AtrParams]):
    """Wilder's average true range. Reads a bar's high, low and close, in that order.

    Give it the three fields of one stream: from streams of different sizes,
    a slower close held between its bars would be read as each bar's close.
    """

    manifest = Manifest(
        kind="indicators",
        name="atr",
        version="1",
        summary="Average true range (TA-Lib); inputs: high, low, close",
        params=AtrParams,
    )
    conformance = Conformance(
        params={"period": 3},
        fields=("high", "low", "close"),
        references=[
            _reference(
                _BARS,
                [
                    None,
                    None,
                    None,
                    0.7333333333333343,
                    0.8222222222222229,
                    0.8148148148148143,
                    0.7432098765432077,
                    0.7288065843621394,
                    0.7192043895747605,
                    0.6794695930498384,
                    0.6529797286998927,
                    0.6353198191332623,
                ],
                "ATR",
            )
        ],
    )
    arity = 3

    def __init__(self, params: AtrParams) -> None:
        super().__init__(params)
        self._stream = _Stream("ATR", {"timeperiod": params.period})

    def update(self, inputs: Sequence[float | None]) -> float | None:
        return self._stream.update("atr", self.arity, inputs)


class MacdParams(Params):
    fast: int = param(12, description="Samples in the fast average", ge=2, le=10_000, step=1)
    slow: int = param(26, description="Samples in the slow average", ge=2, le=10_000, step=1)
    signal: int = param(9, description="Samples in the signal line", ge=1, le=10_000, step=1)
    output: Literal["macd", "signal", "histogram"] = param(
        "macd", description="Which line: macd, signal, or their difference (histogram)"
    )

    @model_validator(mode="after")
    def _fast_before_slow(self) -> "MacdParams":
        if self.fast >= self.slow:
            # TA-Lib swaps them silently; a configuration saying otherwise is a mistake.
            raise ValueError(f"fast ({self.fast}) must be less than slow ({self.slow})")
        return self


class Macd(Plugin[MacdParams]):
    """Moving average convergence/divergence: one of its three lines, as ``output`` says."""

    manifest = Manifest(
        kind="indicators",
        name="macd",
        version="1",
        summary="MACD line, signal or histogram (TA-Lib)",
        params=MacdParams,
    )
    conformance = Conformance(
        params={"fast": 2, "slow": 4, "signal": 3, "output": "histogram"},
        references=[
            _reference(
                [[c] for c in _CLOSES],
                [
                    None,
                    None,
                    None,
                    None,
                    None,
                    0.17268888888888748,
                    0.11422666666666478,
                    0.07325007407407352,
                    0.05944288395061592,
                    0.023790750946502903,
                    -0.05553683886968144,
                    -0.04551901461582203,
                ],
                "MACD",
            )
        ],
    )
    arity = 1
    OUTPUTS = ("macd", "signal", "histogram")

    def __init__(self, params: MacdParams) -> None:
        super().__init__(params)
        self._stream = _Stream(
            "MACD",
            {"fastperiod": params.fast, "slowperiod": params.slow, "signalperiod": params.signal},
            self.OUTPUTS.index(params.output),
        )

    def update(self, inputs: Sequence[float | None]) -> float | None:
        return self._stream.update("macd", self.arity, inputs)


class BandsParams(Params):
    period: int = param(20, description="Samples in the middle band", ge=2, le=10_000, step=1)
    deviations: float = param(
        2.0, description="Standard deviations from the middle band", ge=0.0, le=100.0, step=0.1
    )
    output: Literal["upper", "middle", "lower"] = param(
        "middle", description="Which band: upper, middle (the simple average) or lower"
    )


class Bands(Plugin[BandsParams]):
    """Bollinger Bands on a simple average: one band, as ``output`` says."""

    manifest = Manifest(
        kind="indicators",
        name="bbands",
        version="1",
        summary="Bollinger Bands: upper, middle or lower (TA-Lib)",
        params=BandsParams,
    )
    conformance = Conformance(
        params={"period": 3, "output": "upper"},
        references=[
            _reference(
                [[c] for c in _CLOSES],
                [
                    None,
                    None,
                    44.406458231508616,
                    44.4333218389438,
                    44.64188234163114,
                    45.25817663777142,
                    45.3913162658811,
                    45.59897592660497,
                    46.05937696945941,
                    46.32552726787946,
                    46.14344083225697,
                    46.160831174424224,
                ],
                "BBANDS",
            )
        ],
    )
    arity = 1
    OUTPUTS = ("upper", "middle", "lower")

    def __init__(self, params: BandsParams) -> None:
        super().__init__(params)
        self._stream = _Stream(
            "BBANDS",
            {
                "timeperiod": params.period,
                "nbdevup": params.deviations,
                "nbdevdn": params.deviations,
                "matype": 0,
            },
            self.OUTPUTS.index(params.output),
        )

    def update(self, inputs: Sequence[float | None]) -> float | None:
        return self._stream.update("bbands", self.arity, inputs)


class StdDevParams(Params):
    period: int = param(20, description="Samples in the window", ge=2, le=10_000, step=1)


class StdDev(Plugin[StdDevParams]):
    """The population standard deviation of the last ``period`` samples."""

    manifest = Manifest(
        kind="indicators",
        name="stddev",
        version="1",
        summary="Standard deviation (TA-Lib)",
        params=StdDevParams,
    )
    conformance = Conformance(
        params={"period": 3},
        references=[
            _reference(
                [[c] for c in _CLOSES],
                [
                    None,
                    None,
                    0.10656244908763919,
                    0.24166091947189197,
                    0.3059411708155666,
                    0.5007549855523707,
                    0.3189914662738743,
                    0.24115462996914672,
                    0.30302181806302814,
                    0.27276363393971514,
                    0.10338708279513599,
                    0.08041558721209713,
                ],
                "STDDEV",
            )
        ],
    )
    arity = 1

    def __init__(self, params: StdDevParams) -> None:
        super().__init__(params)
        self._stream = _Stream("STDDEV", {"timeperiod": params.period, "nbdev": 1.0})

    def update(self, inputs: Sequence[float | None]) -> float | None:
        return self._stream.update("stddev", self.arity, inputs)
