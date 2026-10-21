# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

import copy
import dataclasses
import math
import pickle
from fractions import Fraction
from typing import Any

import pytest

from heliograph.core.errors import InvalidValueError
from heliograph.core.messages import Bar, Instrument, Point
from heliograph.core.time import NANOS_PER_HOUR, Timestamp

BTC = Instrument(symbol="BTC/USDT", venue="binance", asset_class="crypto", quote_currency="USDT")


def make_bar(**changes: Any) -> Bar:
    fields: dict[str, Any] = {
        "instrument": BTC,
        "start": Timestamp(0),
        "end": Timestamp(NANOS_PER_HOUR),
        "open": 100.0,
        "high": 110.0,
        "low": 95.0,
        "close": 105.0,
        "volume": 12.5,
    }
    fields.update(changes)
    return Bar(**fields)


def test_instrument_key_names_venue_and_symbol() -> None:
    assert BTC.key == "binance:BTC/USDT"


def test_a_symbol_may_contain_a_colon() -> None:
    perp = Instrument(
        symbol="BTC/USDT:USDT", venue="binance", asset_class="crypto", quote_currency="USDT"
    )
    assert perp.key == "binance:BTC/USDT:USDT"


def test_a_venue_may_not_contain_a_colon_so_keys_stay_unique() -> None:
    # Allowed, "a:b" + "c" and "a" + "b:c" would both have the key "a:b:c".
    with pytest.raises(InvalidValueError, match="venue 'a:b' must not contain ':'"):
        Instrument(symbol="c", venue="a:b", asset_class="crypto", quote_currency="USDT")


def test_instrument_fields_must_not_be_empty() -> None:
    with pytest.raises(InvalidValueError, match="symbol"):
        Instrument(symbol=" ", venue="binance", asset_class="crypto", quote_currency="USDT")


def test_a_bar_becomes_known_when_it_closes() -> None:
    assert make_bar().ts == NANOS_PER_HOUR


def test_messages_cannot_be_changed_after_creation() -> None:
    bar = make_bar()
    with pytest.raises(dataclasses.FrozenInstanceError):
        bar.close = 1.0  # type: ignore[misc]


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"end": Timestamp(0)}, "does not end after it starts"),
        ({"high": 104.0}, "outside"),
        ({"low": 101.0}, "outside"),
        ({"close": math.nan}, "non-finite"),
        ({"volume": math.inf}, "non-finite"),
        ({"volume": -1.0}, "negative volume"),
    ],
)
def test_impossible_bars_are_refused(changes: dict[str, Any], reason: str) -> None:
    with pytest.raises(InvalidValueError, match=reason):
        make_bar(**changes)


def make_point(**changes: Any) -> Point:
    fields: dict[str, Any] = {
        "instrument": BTC,
        "event_time": Timestamp(1_000),
        "received_at": Timestamp(1_250),
        "values": {"price": 101.5, "size": 2},
    }
    fields.update(changes)
    return Point(**fields)


def test_a_point_is_known_when_it_is_received_not_when_it_happened() -> None:
    point = make_point()
    assert (point.ts, point.event_time) == (1_250, 1_000)


def test_a_source_clock_ahead_of_ours_is_allowed() -> None:
    assert make_point(event_time=Timestamp(2_000)).ts == 1_250


def test_point_values_are_frozen_floats() -> None:
    values = {"price": 101.5, "size": 2}
    point = make_point(values=values)
    values["price"] = 0.0
    assert dict(point.values) == {"price": 101.5, "size": 2.0}
    assert isinstance(point.values["size"], float)
    with pytest.raises(TypeError):
        point.values["price"] = 1.0  # type: ignore[index]


def test_equal_points_are_equal_and_hash_equal() -> None:
    assert make_point() == make_point()
    assert len({make_point(), make_point(), make_point(received_at=Timestamp(2_000))}) == 2


@pytest.mark.parametrize(
    ("values", "reason"),
    [
        ({}, "has no values"),
        ({" ": 1.0}, "without a name"),
        ({1: 1.0}, "without a name"),
        ({"price": 10**400}, "too large"),
        ({"price": "101"}, "not a number"),
        ({"flag": True}, "not a number"),
        ({"price": math.nan}, "not finite"),
        ({"price": math.inf}, "not finite"),
    ],
)
def test_impossible_points_are_refused(values: dict[str, Any], reason: str) -> None:
    with pytest.raises(InvalidValueError, match=reason):
        make_point(values=values)


def test_a_bar_received_later_is_known_when_received() -> None:
    bar = make_bar(received_at=Timestamp(NANOS_PER_HOUR + 5))
    assert bar.ts == NANOS_PER_HOUR + 5


def test_a_bar_cannot_be_received_before_it_closes() -> None:
    with pytest.raises(InvalidValueError, match="before it closed"):
        make_bar(received_at=Timestamp(NANOS_PER_HOUR - 1))


def test_point_values_accept_any_real_number_type() -> None:
    point = make_point(values={"price": Fraction(203, 2), "size": 2})
    assert dict(point.values) == {"price": 101.5, "size": 2.0}


def test_point_values_must_be_a_mapping() -> None:
    with pytest.raises(InvalidValueError, match="must be a mapping"):
        make_point(values=[("price", 1.0)])


def test_points_survive_pickle_deepcopy_and_asdict() -> None:
    point = make_point()
    assert pickle.loads(pickle.dumps(point)) == point
    assert copy.deepcopy(point) == point
    assert dict(dataclasses.asdict(point)["values"]) == {"price": 101.5, "size": 2.0}
