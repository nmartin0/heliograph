# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Averages TA-Lib does not have, on talipp: Hull and Arnaud Legoux.

Each is a plug-in of kind ``indicators`` and an ``Indicator`` for the graph
(core/graph.py), reading one input. talipp computes incrementally but keeps
every input and output it has seen; a run of years would grow without end.
So each indicator here drops its oldest values once it holds more than twice
what it needs (``purge_oldest``), which a test shows changes no value
(decision D29).
"""

from collections.abc import Sequence
from typing import Any

from talipp.indicators import ALMA, HMA

from heliograph.core.errors import InvalidValueError
from heliograph.plugins.conformance import Conformance, Reference
from heliograph.plugins.manifest import Manifest, Params, Plugin, param


class _Bounded:
    """A talipp indicator holding at most ``2 * keep`` values."""

    __slots__ = ("_indicator", "_keep")

    def __init__(self, indicator: Any, keep: int) -> None:
        self._indicator = indicator
        self._keep = keep

    def update(self, name: str, inputs: Sequence[float | None]) -> float | None:
        if len(inputs) != 1:
            raise InvalidValueError(f"{name} reads one input, given {len(inputs)}")
        (value,) = inputs
        if value is None:
            return None
        indicator = self._indicator
        indicator.add(float(value))
        out = indicator[-1]
        if len(indicator) > 2 * self._keep:
            indicator.purge_oldest(len(indicator) - self._keep)
        return None if out is None else float(out)


def keep_for(period: int) -> int:
    """How many values an indicator over ``period`` samples keeps: far more than it reads."""
    return 4 * period + 64


class HullParams(Params):
    period: int = param(20, description="Samples averaged", ge=2, le=10_000, step=1)


class Hull(Plugin[HullParams]):
    """Hull's moving average: a weighted average of 2·WMA(period/2) - WMA(period).

    As talipp computes it, it starts isqrt(period) - 1 updates after the
    definition could (talipp averages the differences only once WMA(period)
    has isqrt(period) values); where both give a value, they agree.
    """

    manifest = Manifest(
        kind="indicators",
        name="hma",
        version="1",
        summary="Hull moving average (talipp)",
        params=HullParams,
    )
    conformance = Conformance(
        params={"period": 4},
        references=[
            Reference(
                inputs=[[1.0], [2.0], [3.0], [4.0], [5.0], [6.0], [7.0]],
                outputs=[None, None, None, None, None, 6.0, 7.0],
                source="worked by hand: a Hull average follows a straight line exactly",
            ),
            Reference(
                inputs=[[4.0], [8.0], [6.0], [2.0], [10.0], [6.0]],
                outputs=[None, None, None, None, None, 8.2],
                source=(
                    "worked by hand from WMA(2), WMA(4) and WMA(2) of 2·WMA(2) - WMA(4); "
                    "talipp averages differences once WMA(4) has two values, "
                    "so starts one update later (91/15 at the fifth)"
                ),
            ),
        ],
    )
    arity = 1

    def __init__(self, params: HullParams) -> None:
        super().__init__(params)
        self._talipp = _Bounded(HMA(params.period), keep_for(params.period))

    def update(self, inputs: Sequence[float | None]) -> float | None:
        return self._talipp.update("hma", inputs)


class AlmaParams(Params):
    period: int = param(9, description="Samples averaged", ge=1, le=10_000, step=1)
    offset: float = param(
        0.85,
        description="Where the weights peak: 0 the oldest sample, 1 the newest",
        ge=0.0,
        le=1.0,
    )
    sigma: float = param(
        6.0, description="Width of the weights: larger is flatter", ge=0.1, le=100.0
    )


class Alma(Plugin[AlmaParams]):
    """Arnaud Legoux's moving average: Gaussian weights peaking at ``offset``."""

    manifest = Manifest(
        kind="indicators",
        name="alma",
        version="1",
        summary="Arnaud Legoux moving average (talipp)",
        params=AlmaParams,
    )
    conformance = Conformance(
        params={"period": 3, "offset": 0.5, "sigma": 6.0},
        references=[
            Reference(
                inputs=[[1.0], [4.0], [7.0], [10.0]],
                outputs=[None, None, 4.0, 7.0],
                source="worked by hand: offset 0.5 weights the window symmetrically",
            )
        ],
    )
    arity = 1

    def __init__(self, params: AlmaParams) -> None:
        super().__init__(params)
        indicator = ALMA(params.period, params.offset, params.sigma)
        self._talipp = _Bounded(indicator, keep_for(params.period))

    def update(self, inputs: Sequence[float | None]) -> float | None:
        return self._talipp.update("alma", inputs)
