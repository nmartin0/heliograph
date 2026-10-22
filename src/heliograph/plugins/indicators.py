# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Moving averages in plain Python, one sample at a time: simple, exponential, zero-lag.

Each is a plug-in of kind ``indicators`` and an ``Indicator`` for the graph
(core/graph.py): it reads one input and produces a sample once it has seen
``period`` of them. The exponential average starts from the simple average
of its first ``period`` samples, as TA-Lib's does, so its values can be
checked against TA-Lib's; like TA-Lib's, both take a period of 2 or more.
"""

import math
from collections import deque
from collections.abc import Sequence

from heliograph.core.errors import InvalidValueError
from heliograph.plugins.conformance import Conformance, Reference
from heliograph.plugins.manifest import Manifest, Params, Plugin, param


class PeriodParams(Params):
    period: int = param(20, description="Samples averaged", ge=2, le=10_000, step=1)


def _one(name: str, inputs: Sequence[float | None]) -> float | None:
    if len(inputs) != 1:
        raise InvalidValueError(f"{name} reads one input, given {len(inputs)}")
    return inputs[0]


class Sma(Plugin[PeriodParams]):
    """The mean of the last ``period`` samples."""

    manifest = Manifest(
        kind="indicators",
        name="sma",
        version="1",
        summary="Simple moving average",
        params=PeriodParams,
    )
    conformance = Conformance(
        params={"period": 3},
        references=[
            Reference(
                inputs=[[1.0], [2.0], [3.0], [4.0], [10.0]],
                outputs=[None, None, 2.0, 3.0, 17 / 3],
                source="worked by hand",
            )
        ],
    )
    arity = 1

    def __init__(self, params: PeriodParams) -> None:
        super().__init__(params)
        self._window: deque[float] = deque(maxlen=params.period)

    def update(self, inputs: Sequence[float | None]) -> float | None:
        value = _one("sma", inputs)
        if value is None:
            return None
        window = self._window
        window.append(value)
        if len(window) < self.params.period:
            return None
        # Summed afresh (fsum, exactly rounded): a running sum drifts with every bar.
        return math.fsum(window) / len(window)


class Ema(Plugin[PeriodParams]):
    """The exponential average with weight ``2 / (period + 1)``, from a simple start."""

    manifest = Manifest(
        kind="indicators",
        name="ema",
        version="1",
        summary="Exponential moving average",
        params=PeriodParams,
    )
    conformance = Conformance(
        params={"period": 3},
        references=[
            Reference(
                inputs=[[1.0], [2.0], [3.0], [4.0], [10.0]],
                outputs=[None, None, 2.0, 3.0, 6.5],
                source="worked by hand: seed (1+2+3)/3, then weight 2/(3+1)",
            )
        ],
    )
    arity = 1

    def __init__(self, params: PeriodParams) -> None:
        super().__init__(params)
        self._alpha = 2.0 / (params.period + 1)
        self._first: list[float] = []
        self._value: float | None = None

    def update(self, inputs: Sequence[float | None]) -> float | None:
        value = _one("ema", inputs)
        if value is None:
            return None
        if self._value is None:
            self._first.append(value)
            if len(self._first) < self.params.period:
                return None
            self._value = math.fsum(self._first) / len(self._first)
            return self._value
        self._value += self._alpha * (value - self._value)
        return self._value


class ZeroLag(Plugin[PeriodParams]):
    """The zero-lag exponential average: an EMA of 2·x(t) - x(t - lag), lag = (period - 1) // 2.

    The EMA is seeded, like ``Ema``, from the simple average of its first
    ``period`` values. The lag is rounded down, as Ehlers and most charting
    tools do (talipp rounds half to even, so differs for periods of 4, 8, 12...).
    """

    manifest = Manifest(
        kind="indicators",
        name="zlema",
        version="1",
        summary="Zero-lag exponential moving average",
        params=PeriodParams,
    )
    conformance = Conformance(
        params={"period": 4},
        references=[
            Reference(
                inputs=[[1.0], [3.0], [2.0], [6.0], [4.0], [8.0]],
                outputs=[None, None, None, None, 4.5, 7.5],
                source=(
                    "worked by hand: lag (4-1)//2 = 1, so 5, 1, 10, 2, 12; "
                    "seed (5+1+10+2)/4, then 0.4·12 + 0.6·4.5"
                ),
            )
        ],
    )
    arity = 1

    def __init__(self, params: PeriodParams) -> None:
        super().__init__(params)
        self._lag = (params.period - 1) // 2
        self._recent: deque[float] = deque(maxlen=self._lag + 1)
        self._ema = Ema(params)

    def update(self, inputs: Sequence[float | None]) -> float | None:
        value = _one("zlema", inputs)
        if value is None:
            return None
        self._recent.append(value)
        if len(self._recent) <= self._lag:
            return None
        return self._ema.update([2 * value - self._recent[0]])
