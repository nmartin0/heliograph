# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The next-open fill model: half the spread and the slippage against the trader, and commission."""

import pytest
from pydantic import ValidationError

from heliograph.plugins.fills import NextOpen, NextOpenParams


def model(**params: float) -> NextOpen:
    return NextOpen(NextOpenParams(**params))


def test_buying_pays_and_selling_receives_half_the_spread_and_the_slippage() -> None:
    m = model(spread=0.02, slippage=0.005)
    assert m.price(200.0, 3.0) == pytest.approx(200.0 * 1.015)
    assert m.price(200.0, -3.0) == pytest.approx(200.0 * 0.985)


def test_the_commission_is_a_fraction_of_the_value_traded_either_way() -> None:
    m = model(commission=0.001)
    assert m.commission(250.0, 4.0) == pytest.approx(1.0)
    assert m.commission(250.0, -4.0) == pytest.approx(1.0)


def test_by_default_a_fill_is_at_the_open_and_free() -> None:
    m = model()
    assert (m.price(123.0, 1.0), m.price(123.0, -1.0), m.commission(123.0, 5.0)) == (
        123.0,
        123.0,
        0.0,
    )


@pytest.mark.parametrize(
    "params",
    [
        {"spread": -0.01},
        {"slippage": -0.1},
        {"commission": 1.5},
        {"slippage": 1.0},
        {"spread": 1.0, "slippage": 0.5},
    ],
)
def test_costs_that_cannot_be_are_refused(params: dict[str, float]) -> None:
    with pytest.raises(ValidationError):
        model(**params)


def test_a_sale_always_fetches_something() -> None:
    with pytest.raises(ValidationError, match="a sale would fetch nothing"):
        model(spread=1.2, slippage=0.4)
    assert model(spread=1.2, slippage=0.39).price(100.0, -1.0) > 0
