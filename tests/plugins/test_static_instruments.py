# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

import asyncio
import tomllib
from typing import Any

import pytest
from pydantic import ValidationError

from heliograph.core.sources import Listing
from heliograph.plugins.static_instruments import StaticInstruments, StaticInstrumentsParams
from tests.support.world import BTC

KRAKEN = """
[[listings]]
symbol = "BTC/USD"
venue = "kraken"
asset_class = "crypto"
quote_currency = "USD"
venue_symbol = "XBTUSD"
price_increment = 0.1

[[listings]]
symbol = "ETH/USD"
venue = "kraken"
asset_class = "crypto"
quote_currency = "USD"
"""


def load(params: dict[str, Any]) -> tuple[Listing, ...]:
    provider = StaticInstruments(StaticInstrumentsParams.model_validate(params))
    return tuple(asyncio.run(provider.load()))


def test_the_listings_in_its_parameters_are_loaded_as_written() -> None:
    btc, eth = load(tomllib.loads(KRAKEN))
    assert btc == Listing(instrument=BTC, venue_symbol="XBTUSD", price_increment=0.1)
    assert eth.instrument.key == "kraken:ETH/USD"
    assert eth.venue_symbol == "ETH/USD"  # the venue's symbol defaults to ours
    assert eth.price_increment is None


def test_no_listings_is_no_instruments() -> None:
    assert load({}) == ()


def test_an_instrument_listed_twice_is_refused() -> None:
    params = tomllib.loads(KRAKEN)
    params["listings"].append(dict(params["listings"][0], venue_symbol="BTCUSD"))
    with pytest.raises(ValidationError, match="kraken:BTC/USD is listed twice"):
        StaticInstrumentsParams.model_validate(params)


def test_two_instruments_with_one_venue_symbol_are_refused() -> None:
    params = tomllib.loads(KRAKEN)
    params["listings"][1]["venue_symbol"] = "XBTUSD"
    with pytest.raises(ValidationError, match="kraken:ETH/USD and kraken:BTC/USD are both XBTUSD"):
        StaticInstrumentsParams.model_validate(params)
    params["listings"][1]["venue"] = "bitstamp"  # the same symbol on another venue is fine
    assert len(load(params)) == 2


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"venue": "kra:ken"}, "must not contain ':'"),
        ({"symbol": " "}, "symbol must not be empty"),
        ({"venue_symbol": ""}, "venue symbol must not be empty"),
        ({"price_increment": 0}, "price_increment must be positive"),
        ({"size_increment": -1}, "size_increment must be positive"),
        ({"lot": 1}, "Extra inputs are not permitted"),
    ],
)
def test_a_listing_that_cannot_be_right_is_refused_when_read(
    change: dict[str, Any], reason: str
) -> None:
    params = tomllib.loads(KRAKEN)
    params["listings"][0].update(change)
    with pytest.raises(ValidationError, match=reason):
        StaticInstrumentsParams.model_validate(params)
