# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Instruments written out in the run configuration, for venues with no listing service.

Each listing names the instrument (symbol, venue, asset class, quote
currency), the venue's own symbol for it when that differs, and optionally
its price and size increments. Its parameters, in TOML::

    [[listings]]
    symbol = "BTC/USD"
    venue = "kraken"
    asset_class = "crypto"
    quote_currency = "USD"
    venue_symbol = "XBTUSD"
    price_increment = 0.1

Every listing is checked when the configuration is read. An instrument
listed twice is refused, since two listings would leave its venue symbol
and increments ambiguous; so are two instruments with one venue symbol on
one venue, since the venue's data could not say which it is.
"""

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from heliograph.core.errors import InvalidValueError
from heliograph.core.messages import Instrument
from heliograph.core.sources import Listing
from heliograph.plugins.manifest import Manifest, Params, Plugin, param


class ListingParams(BaseModel):
    """One listing as written in the configuration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    venue: str
    asset_class: str
    quote_currency: str
    venue_symbol: str | None = Field(None, description="The venue's symbol, if not ``symbol``")
    price_increment: float | None = Field(None, description="Smallest price step")
    size_increment: float | None = Field(None, description="Smallest size step")

    @model_validator(mode="after")
    def _valid(self) -> "ListingParams":
        try:
            self.listing()
        except InvalidValueError as exc:
            raise ValueError(str(exc)) from exc
        return self

    def listing(self) -> Listing:
        instrument = Instrument(
            symbol=self.symbol,
            venue=self.venue,
            asset_class=self.asset_class,
            quote_currency=self.quote_currency,
        )
        return Listing(
            instrument=instrument,
            venue_symbol=self.symbol if self.venue_symbol is None else self.venue_symbol,
            price_increment=self.price_increment,
            size_increment=self.size_increment,
        )


class StaticInstrumentsParams(Params):
    listings: tuple[ListingParams, ...] = param((), description="The instruments, one each")

    @model_validator(mode="after")
    def _each_once(self) -> "StaticInstrumentsParams":
        seen: set[str] = set()
        venue_symbols: dict[tuple[str, str], str] = {}
        for entry in self.listings:
            listing = entry.listing()
            key = listing.instrument.key
            if key in seen:
                raise ValueError(f"{key} is listed twice")
            seen.add(key)
            where = (listing.instrument.venue, listing.venue_symbol)
            if where in venue_symbols:
                raise ValueError(
                    f"{key} and {venue_symbols[where]} are both {listing.venue_symbol} "
                    f"on {listing.instrument.venue}"
                )
            venue_symbols[where] = key
        return self


class StaticInstruments(Plugin[StaticInstrumentsParams]):
    """The listings given in its parameters, as they are."""

    manifest = Manifest(
        kind="instrument_providers",
        name="static",
        version="1",
        summary="Instruments written out in the configuration",
        params=StaticInstrumentsParams,
    )

    async def load(self) -> Sequence[Listing]:
        return tuple(entry.listing() for entry in self.params.listings)
