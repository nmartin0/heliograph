# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""A person's answer to a signal: what is a valid one."""

import pytest

from heliograph.core.answers import SignalAnswer
from heliograph.core.errors import InvalidValueError
from heliograph.core.time import Timestamp

AT = Timestamp(5)


def test_took_may_say_what_was_paid_and_traded() -> None:
    answer = SignalAnswer(ts=AT, signal="a1", verdict="took", price=67500.0, quantity=-0.25)
    assert (answer.price, answer.quantity) == (67500.0, -0.25)
    assert SignalAnswer(ts=AT, signal="a1", verdict="took").price is None


def test_skipped_has_no_price_or_quantity() -> None:
    assert SignalAnswer(ts=AT, signal="a1", verdict="skipped").quantity is None
    with pytest.raises(InvalidValueError, match="skipped signal has no price or quantity"):
        SignalAnswer(ts=AT, signal="a1", verdict="skipped", price=1.0)
    with pytest.raises(InvalidValueError, match="skipped signal has no price or quantity"):
        SignalAnswer(ts=AT, signal="a1", verdict="skipped", quantity=1.0)


def test_an_answer_names_its_signal_and_is_one_of_the_two() -> None:
    with pytest.raises(InvalidValueError, match="needs the id of the signal"):
        SignalAnswer(ts=AT, signal="", verdict="took")
    with pytest.raises(InvalidValueError, match=r"took or skipped, not 'maybe'"):
        SignalAnswer(ts=AT, signal="a1", verdict="maybe")  # type: ignore[arg-type]


@pytest.mark.parametrize("price", [0.0, -1.0, float("nan"), float("inf")])
def test_a_price_is_a_positive_number(price: float) -> None:
    with pytest.raises(InvalidValueError, match="price must be positive"):
        SignalAnswer(ts=AT, signal="a1", verdict="took", price=price)


@pytest.mark.parametrize("quantity", [0.0, float("nan"), float("-inf")])
def test_a_quantity_is_a_number_other_than_zero(quantity: float) -> None:
    with pytest.raises(InvalidValueError, match="quantity must be a number other than 0"):
        SignalAnswer(ts=AT, signal="a1", verdict="took", quantity=quantity)
