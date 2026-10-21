import dataclasses
import math
from typing import Any

import pytest

from heliograph.core.messages import Bar, Instrument
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


def test_instrument_fields_must_not_be_empty() -> None:
    with pytest.raises(ValueError, match="symbol"):
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
    with pytest.raises(ValueError, match=reason):
        make_bar(**changes)
