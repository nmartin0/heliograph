# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The first indicators: moving averages, in plain Python, one sample at a time.

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
