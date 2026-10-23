# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

import asyncio
from collections.abc import AsyncIterator, Sequence
from typing import ClassVar

import pytest

from heliograph.app.config import ConfigError, RunConfig, parse_config
from heliograph.app.limits import RateThrottle
from heliograph.app.mounts import Mount, build_mounts
from heliograph.core.messages import Message
from heliograph.core.sources import (
    Capabilities,
    Health,
    HistoryRequest,
    RateLimit,
    Subscription,
    Throttle,
)
from heliograph.core.time import NANOS_PER_DAY, NANOS_PER_HOUR, NANOS_PER_MINUTE, from_iso
from heliograph.plugins.manifest import Manifest, NoParams, Params, Plugin, param
from heliograph.plugins.registry import Registry
from tests.support.simulated import SimulatedTime
from tests.support.world import BTC

NOW_ISO = "2026-10-21T12:00:00Z"
NOW = from_iso(NOW_ISO)
OCTOBER_1_ISO = "2026-10-01T00:00:00Z"


class FeedParams(Params):
    exchange: str = param("kraken", description="Venue")
    live: bool = param(True, description="Offers live trades")
    no_live_trading: bool = param(False, description="Terms forbid live use")
    lookback_days: int | None = param(None, description="History reaches this far back")


class Feed(Plugin[FeedParams]):
    """A data source whose capabilities follow its parameters."""

    manifest = Manifest(
        kind="data_sources", name="feed", version="1", summary="Test feed", params=FeedParams
    )
    built: ClassVar[list["Feed"]] = []

    def __init__(self, params: FeedParams) -> None:
        super().__init__(params)
        self.throttle: Throttle | None = None
        Feed.built.append(self)

    @property
    def capabilities(self) -> Capabilities:
        p = self.params
        return Capabilities(
            history=frozenset({"trades", "bars"}),
            live=frozenset({"trades"} if p.live else set()),
            bar_sizes=frozenset({NANOS_PER_MINUTE, NANOS_PER_HOUR}),
            lookback=None if p.lookback_days is None else p.lookback_days * NANOS_PER_DAY,
            rate_limit=RateLimit(calls=10, period=10**9),
            terms=frozenset({"no_live_trading"} if p.no_live_trading else set()),
        )

    def bind(self, throttle: Throttle) -> None:
        self.throttle = throttle

    async def history(self, request: HistoryRequest) -> AsyncIterator[Message]:
        return
        yield

    async def subscribe(self, subscriptions: Sequence[Subscription]) -> AsyncIterator[Message]:
        return
        yield

    async def health_check(self) -> Health:
        return Health(ok=True)

    async def close(self) -> None:
        pass


class Malformed(Feed):
    """A data source whose capabilities raise when read."""

    manifest = Manifest(
        kind="data_sources", name="malformed", version="1", summary="Bad caps", params=FeedParams
    )

    @property
    def capabilities(self) -> Capabilities:
        return Capabilities(concurrency=0)


class Broken(Plugin[NoParams]):
    """Registered as a data source, but implements none of the port."""

    manifest = Manifest(kind="data_sources", name="broken", version="1", summary="Not a source")


@pytest.fixture
def registry() -> Registry:
    Feed.built.clear()
    registry = Registry()
    registry.register(Feed)
    registry.register(Broken)
    registry.register(Malformed)
    return registry


def mounts(registry: Registry, text: str) -> list[Mount]:
    time = SimulatedTime(start=NOW)
    return list(build_mounts(parse_config(text).config, registry, time.clock, time.advance))


INSTRUMENT = """
[mounts.{name}.instrument]
symbol = "BTC/USD"
venue = "kraken"
asset_class = "crypto"
quote_currency = "USD"
"""

OCTOBER_1 = "start = 2026-10-01T00:00:00Z\nend = 2026-10-02T00:00:00Z"
INTO_NOVEMBER = "start = 2026-10-01T00:00:00Z\nend = 2026-11-01T00:00:00Z"
SINCE_JANUARY = "start = 2026-01-01T00:00:00Z\nend = 2026-10-02T00:00:00Z"
BACKWARDS = "start = 2026-10-02T00:00:00Z\nend = 2026-10-01T00:00:00Z"
NAIVE_START = "start = 2026-10-01T00:00:00\nend = 2026-10-02T00:00:00Z"
FEED, TRADES = 'source = "feed"', 'kind = "trades"'


def mount(name: str, *lines: str) -> str:
    return f"[mounts.{name}]\n" + "\n".join(lines) + "\n" + INSTRUMENT.format(name=name)


BACKTEST = mount("btc", FEED, TRADES, OCTOBER_1)


def test_a_backtest_mount_asks_its_source_for_that_history(registry: Registry) -> None:
    (built,) = mounts(registry, BACKTEST)
    assert built.name == "btc"
    assert built.subscription is None
    assert built.request == HistoryRequest(
        instrument=BTC,
        kind="trades",
        start=from_iso("2026-10-01T00:00:00Z"),
        end=from_iso("2026-10-02T00:00:00Z"),
    )


def test_a_live_mount_subscribes(registry: Registry) -> None:
    (built,) = mounts(registry, 'mode = "live"\n' + mount("btc", FEED, TRADES))
    assert built.request is None
    assert built.subscription == Subscription(instrument=BTC, kind="trades")


def test_every_source_is_bound_to_a_throttle_of_its_declared_limits(registry: Registry) -> None:
    """Feed declares 10 calls a second: the eleventh waits for the window to move."""
    mounts(registry, BACKTEST)
    (feed,) = Feed.built
    throttle = feed.throttle
    assert isinstance(throttle, RateThrottle)

    async def eleven() -> None:
        for _ in range(11):
            async with throttle:
                pass

    asyncio.run(eleven())
    assert throttle._sleep.__self__.slept == [1.0]  # type: ignore[attr-defined]


def test_mounts_on_the_same_source_and_parameters_share_one_instance(registry: Registry) -> None:
    """So they share its rate limit: two mounts on one exchange are one client."""
    coinbase = 'params = { exchange = "coinbase" }'
    text = (
        mount("a", FEED, TRADES, OCTOBER_1)
        + mount("b", FEED, TRADES, OCTOBER_1)
        + mount("c", FEED, coinbase, TRADES, OCTOBER_1)
    )
    built = mounts(registry, text)
    assert len(Feed.built) == 2
    assert built[0].source is built[1].source is not built[2].source


def test_unknown_sources_and_bad_parameters_are_reported_with_every_other_problem(
    registry: Registry,
) -> None:
    text = (
        mount("unknown", 'source = "nowhere"', TRADES, SINCE_JANUARY)
        + mount("bad_params", FEED, 'params = { live = "maybe" }', TRADES, SINCE_JANUARY)
        + mount("future", FEED, TRADES, INTO_NOVEMBER)
    )
    with pytest.raises(ConfigError) as caught:
        mounts(registry, text)
    message = str(caught.value)
    assert "mounts.unknown (nowhere)" in message
    assert "mounts.bad_params (feed): invalid params" in message
    assert "mounts.future (feed): a backtest cannot end after now" in message
    (future,) = Feed.built  # only the mount that resolved was built
    assert future.throttle is None


def test_a_backtest_may_end_exactly_now(registry: Registry) -> None:
    (built,) = mounts(
        registry, mount("btc", FEED, TRADES, f"start = {OCTOBER_1_ISO}", f"end = {NOW_ISO}")
    )
    assert built.request is not None
    assert built.request.end == NOW


def test_when_any_mount_has_a_problem_no_source_is_bound(registry: Registry) -> None:
    text = mount("good", FEED, TRADES, OCTOBER_1) + mount(
        "future", FEED, 'params = { exchange = "x" }', TRADES, INTO_NOVEMBER
    )
    with pytest.raises(ConfigError, match="cannot end after now"):
        mounts(registry, text)
    assert len(Feed.built) == 2
    assert all(feed.throttle is None for feed in Feed.built)


def test_a_source_whose_capabilities_cannot_be_read_is_reported_with_the_rest(
    registry: Registry,
) -> None:
    text = mount("bad", 'source = "malformed"', TRADES, OCTOBER_1) + mount(
        "future", FEED, TRADES, INTO_NOVEMBER
    )
    with pytest.raises(ConfigError) as caught:
        mounts(registry, text)
    assert (
        "mounts.bad (malformed): its capabilities are invalid: concurrency must be an int"
        in str(caught.value)
    )
    assert "mounts.future (feed)" in str(caught.value)


def test_every_mount_a_source_cannot_serve_is_reported_at_once_and_nothing_is_bound(
    registry: Registry,
) -> None:
    text = (
        mount("hourly", FEED, 'kind = "bars"', 'bar_size = "4h"', SINCE_JANUARY)
        + mount("old", FEED, "params = { lookback_days = 30 }", TRADES, SINCE_JANUARY)
        + mount("future", FEED, TRADES, INTO_NOVEMBER)
        + mount("broken", 'source = "broken"', TRADES, SINCE_JANUARY)
    )
    with pytest.raises(ConfigError) as caught:
        mounts(registry, text)
    message = str(caught.value)
    assert "mounts.hourly (feed): the source offers no 4h bars (sizes: 1m, 1h)" in message
    assert "mounts.old (feed): the source reaches back 4w2d, not 41w" in message
    assert "mounts.future (feed): a backtest cannot end after now" in message
    assert "mounts.broken (broken): not a data source; it lacks capabilities" in message
    assert len(Feed.built) == 2
    assert all(feed.throttle is None for feed in Feed.built)


def test_a_live_mount_on_a_source_whose_terms_forbid_live_use_is_refused(
    registry: Registry,
) -> None:
    text = 'mode = "live"\n' + mount("btc", FEED, "params = { no_live_trading = true }", TRADES)
    with pytest.raises(ConfigError, match="its terms forbid live use"):
        mounts(registry, text)


@pytest.mark.parametrize(
    ("lines", "reason"),
    [
        (['kind = "bars"', OCTOBER_1], "bar_size is given for bars"),
        ([TRADES, 'bar_size = "1h"', OCTOBER_1], "bar_size is given for bars"),
        (['kind = "bars"', 'bar_size = "1 hour"', OCTOBER_1], "is not a span"),
        ([TRADES, NAIVE_START], "UTC offset"),
        ([TRADES, BACKWARDS], "end must be after start"),
        ([TRADES], "a backtest needs start and end"),
        (['kind = "ticks"', OCTOBER_1], "kind"),
        ([TRADES, "start = 1735689600", "end = 2026-10-02T00:00:00Z"], "valid datetime"),
        ([TRADES, 'start = "1735689600"', "end = 2026-10-02T00:00:00Z"], "valid datetime"),
        ([TRADES, "start = 1600-01-01T00:00:00Z", "end = 2026-10-02T00:00:00Z"], "can hold"),
        ([TRADES, "start = 2026-10-01T00:00:00Z", "end = 2300-01-01T00:00:00Z"], "can hold"),
    ],
)
def test_a_mount_that_cannot_be_right_is_refused_when_read(lines: list[str], reason: str) -> None:
    with pytest.raises(ConfigError, match=reason):
        parse_config(mount("btc", FEED, *lines))


def test_live_data_has_no_start_or_end() -> None:
    with pytest.raises(ConfigError, match="live data has no start or end"):
        parse_config('mode = "live"\n' + BACKTEST)


def test_an_instrument_that_cannot_be_right_is_refused_when_read() -> None:
    with pytest.raises(ConfigError, match="must not contain ':'"):
        parse_config(BACKTEST.replace('venue = "kraken"', 'venue = "kra:ken"'))


def test_a_config_with_mounts_round_trips_and_hashes_by_value() -> None:
    config = parse_config(BACKTEST).config
    assert RunConfig.model_validate_json(config.model_dump_json()) == config
    assert hash(config) == hash(parse_config(BACKTEST).config)
    assert config.mounts["btc"].instrument.instrument() == BTC


def test_mounts_on_one_exchange_share_one_throttle_whatever_their_params() -> None:
    """Kraken's limit is Kraken's: a mount with pages of 500 and one of 1000 are two
    clients, and must still keep one rate between them."""
    registry = Registry()
    registry.discover("data_sources")
    text = f"""
[mounts.btc]
source = "ccxt"
params = {{ exchange = "kraken", page = 1000 }}
kind = "bars"
bar_size = "1h"
{SINCE_JANUARY}
[mounts.btc.instrument]
symbol = "BTC/USD"
venue = "kraken"
asset_class = "crypto"
quote_currency = "USD"

[mounts.eth]
source = "ccxt"
params = {{ exchange = "kraken", page = 500 }}
kind = "bars"
bar_size = "1h"
{SINCE_JANUARY}
[mounts.eth.instrument]
symbol = "ETH/USD"
venue = "kraken"
asset_class = "crypto"
quote_currency = "USD"
"""
    time = SimulatedTime(start=NOW)
    btc, eth = build_mounts(parse_config(text).config, registry, time.clock, time.advance)
    assert btc.source is not eth.source
    assert btc.source._throttle is eth.source._throttle  # type: ignore[attr-defined]
