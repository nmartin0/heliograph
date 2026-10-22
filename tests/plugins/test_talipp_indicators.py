# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The talipp indicators: their definitions, and memory that stays bounded."""

import math
from collections.abc import Callable, Sequence
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from talipp.indicators import ALMA, HMA

from heliograph.core.errors import InvalidValueError
from heliograph.plugins.talipp_indicators import (
    Alma,
    AlmaParams,
    Hull,
    HullParams,
    keep_for,
)
from heliograph.testkit.checks import bars

LONG = 3000


def closes(length: int, seed: int) -> list[float]:
    return [update[0] for update in bars(("close",), length, seed)]  # type: ignore[misc]


def feed(indicator: Any, values: Sequence[float]) -> list[float | None]:
    return [indicator.update([value]) for value in values]


# -- the definitions, written out independently ---------------------------------------


def wma(window: Sequence[float]) -> float:
    weights = range(1, len(window) + 1)
    return math.fsum(w * x for w, x in zip(weights, window, strict=True)) / sum(weights)


def hull(values: Sequence[float], period: int) -> list[float | None]:
    """2·WMA(n/2) - WMA(n), averaged by WMA(√n).

    talipp takes differences only once WMA(n) has √n values, so its first
    √n - 1 differences are never averaged.
    """
    half, root = period // 2, math.isqrt(period)
    diffs: list[float] = []
    out: list[float | None] = []
    for t in range(len(values)):
        if t + 1 >= period:
            diffs.append(
                2 * wma(values[t + 1 - half : t + 1]) - wma(values[t + 1 - period : t + 1])
            )
        usable = diffs[root - 1 :]
        out.append(wma(usable[-root:]) if len(usable) >= root else None)
    return out


def alma(values: Sequence[float], period: int, offset: float, sigma: float) -> list[float | None]:
    m, s = (period - 1) * offset, period / sigma
    weights = [math.exp(-((i - m) ** 2) / (2 * s * s)) for i in range(period)]
    total = math.fsum(weights)
    return [
        math.fsum(w * x for w, x in zip(weights, values[t + 1 - period : t + 1], strict=True))
        / total
        if t + 1 >= period
        else None
        for t in range(len(values))
    ]


def agree(got: list[float | None], want: list[float | None]) -> None:
    assert [g is None for g in got] == [w is None for w in want]
    assert [g for g in got if g is not None] == pytest.approx(
        [w for w in want if w is not None], rel=1e-9, abs=1e-9
    )


@settings(max_examples=20, deadline=None)
@given(period=st.integers(2, 30), seed=st.integers(0, 2**32 - 1))
def test_each_indicator_follows_its_definition(period: int, seed: int) -> None:
    values = closes(300, seed)
    agree(feed(Hull(HullParams(period=period)), values), hull(values, period))
    params = AlmaParams(period=period, offset=0.85, sigma=6.0)
    agree(feed(Alma(params), values), alma(values, period, 0.85, 6.0))


# -- bounded memory -------------------------------------------------------------------

type Make = Callable[[int], tuple[Any, Any]]
MAKERS: list[tuple[str, Make]] = [
    ("hma", lambda n: (Hull(HullParams(period=n)), HMA(n))),
    ("alma", lambda n: (Alma(AlmaParams(period=n, offset=0.85, sigma=6.0)), ALMA(n, 0.85, 6.0))),
]


def held(indicator: Any) -> int:
    """Every value the talipp indicator and its parts hold."""
    own = len(indicator.input_values) + len(indicator.output_values)
    parts = [*indicator.sub_indicators, *indicator.managed_sequences]
    return own + sum(held(part) for part in parts)


@pytest.mark.parametrize(("name", "make"), MAKERS)
@pytest.mark.parametrize("period", [2, 9, 50])
def test_dropping_old_values_changes_no_value_and_keeps_memory_bounded(
    name: str, make: Make, period: int
) -> None:
    ours, unbounded = make(period)
    biggest = 0
    for value in closes(LONG, period):
        unbounded.add(value)
        got = ours.update([value])
        assert got == unbounded[-1], name
        biggest = max(biggest, held(ours._talipp._indicator))
    assert held(unbounded) > biggest
    assert biggest <= 2 * 6 * (2 * keep_for(period) + 1)


@pytest.mark.parametrize(("name", "make"), MAKERS)
def test_an_input_without_a_sample_gives_nothing_and_adds_nothing(name: str, make: Make) -> None:
    values = closes(60, 11)
    after_none, _ = make(4)
    assert after_none.update([None]) is None
    plain, _ = make(4)
    assert feed(after_none, values) == feed(plain, values), name


def test_an_indicator_given_another_number_of_inputs_is_refused() -> None:
    with pytest.raises(InvalidValueError, match="alma reads one input, given 2"):
        Alma(AlmaParams()).update([1.0, 2.0])
