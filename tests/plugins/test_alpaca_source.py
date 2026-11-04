# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The Alpaca source, offline: fake clients that answer as Alpaca's REST service and stream do.

The replies are real alpaca-py models built from raw data shaped as Alpaca's documentation
shows it (``t``, ``p``, ``s``, ``x``, ``i`` for a trade; ``t``, ``o``, ``h``, ``l``, ``c``, ``v``
for a bar), so our reading of them is exercised. Whether the service still answers so is for
the owner's live run (tests/live).
"""

import asyncio
import threading
from collections.abc import AsyncIterator, Coroutine, Iterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
from alpaca.common.exceptions import APIError
from alpaca.data.models import BarSet, Trade, TradeSet

from heliograph.core.errors import InvalidValueError
from heliograph.core.messages import Bar, Instrument, Message, Point
from heliograph.core.sources import (
    HistoryRequest,
    RateLimit,
    SourceUnavailableError,
    Subscription,
)
from heliograph.core.time import NANOS_PER_HOUR, NANOS_PER_MINUTE, Timestamp, from_iso
from heliograph.plugins.alpaca_source import AlpacaParams, AlpacaSource

ALP = Instrument(symbol="AAPL", venue="alpaca", asset_class="equity", quote_currency="USD")
MIN = NANOS_PER_MINUTE
OPEN = from_iso("2026-10-30T13:30:00Z")


def iso(nanos: int) -> str:
    """``nanos`` as Alpaca writes a time: to the microsecond, with a Z."""
    seconds, rest = divmod(nanos, 10**9)
    moment = datetime.fromtimestamp(seconds, tz=UTC)
    return moment.strftime("%Y-%m-%dT%H:%M:%S") + f".{rest // 1000:06d}Z"


def raw_trade(at: int, price: float = 100.0, ident: int = 1) -> dict[str, Any]:
    return {"t": iso(at), "x": "V", "p": price, "s": 10, "c": [], "i": ident, "z": "C"}


def raw_bar(at: int, close: float = 100.0) -> dict[str, Any]:
    return {
        "t": iso(at),
        "o": 99.0,
        "h": 200.0,
        "l": 98.0,
        "c": close,
        "v": 1000,
        "n": 5,
        "vw": 100,
    }


class Gate:
    """A throttle that only counts the requests let through."""

    def __init__(self) -> None:
        self.entered = 0

    async def __aenter__(self) -> None:
        self.entered += 1

    async def __aexit__(self, *_: object) -> None:
        return None


class Service:
    """A fake REST client: serves what it was given as Alpaca pages it, and fails on request."""

    def __init__(
        self, trades: list[dict[str, Any]] | None = None, bars: list[dict[str, Any]] | None = None
    ):
        self.trades = sorted(trades or [], key=lambda r: r["t"])
        self.bars = sorted(bars or [], key=lambda r: r["t"])
        self.requests: list[Any] = []
        self.failures: list[Exception] = []
        self.keys: tuple[str, str] | None = None

    def _gone(self) -> None:
        if self.failures:
            raise self.failures.pop(0)

    def _slice(self, rows: list[dict[str, Any]], request: Any) -> list[dict[str, Any]]:
        start, end = iso_of(request.start), iso_of(request.end)
        ordered = sorted(rows, key=lambda r: r["t"])
        return [r for r in ordered if start <= r["t"] <= end][: request.limit]

    def get_stock_trades(self, request: Any) -> TradeSet:
        self.requests.append(request)
        self._gone()
        return TradeSet({request.symbol_or_symbols: self._slice(self.trades, request)})

    def get_stock_bars(self, request: Any) -> BarSet:
        self.requests.append(request)
        self._gone()
        return BarSet({request.symbol_or_symbols: self._slice(self.bars, request)})

    def get_stock_latest_trade(self, request: Any) -> dict[str, Any]:
        self.requests.append(request)
        self._gone()
        return {}


def iso_of(moment: Any) -> str:
    return str(moment.strftime("%Y-%m-%dT%H:%M:%S")) + f".{moment.microsecond:06d}Z"


class Stream:
    """A fake StockDataStream: calls the handler for each scripted trade in its own thread."""

    script: ClassVar[list[Any]] = []
    hold: ClassVar[bool] = True
    made: ClassVar[list["Stream"]] = []

    def __init__(self, key: str, secret: str, feed: str) -> None:
        self.feed = feed
        self.symbols: tuple[str, ...] = ()
        self.stopped = threading.Event()
        self.handler: Any = None
        type(self).made.append(self)

    def subscribe_trades(self, handler: Any, *symbols: str) -> None:
        self.handler, self.symbols = handler, symbols

    def run(self) -> None:
        async def deliver() -> None:
            for trade in type(self).script:
                await self.handler(trade)

        asyncio.run(deliver())
        if type(self).hold:
            self.stopped.wait(timeout=10)

    def stop(self) -> None:
        self.stopped.set()


@pytest.fixture
def keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APCA_API_KEY_ID", "key-id")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "secret")


@pytest.fixture
def service(keys: None) -> Iterator[Service]:
    fake = Service()
    AlpacaSource.history_client = lambda key, secret: _with_keys(fake, key, secret)
    AlpacaSource.stream = Stream
    Stream.script, Stream.hold, Stream.made = [], True, []
    yield fake
    AlpacaSource.history_client = None
    AlpacaSource.stream = None


def _with_keys(fake: Service, key: str, secret: str) -> Service:
    fake.keys = (key, secret)
    return fake


def source(**params: Any) -> tuple[AlpacaSource, Gate]:
    found = AlpacaSource(AlpacaParams(backoff=0.0, **params))
    gate = Gate()
    found.bind(gate)
    return found, gate


async def read(found: AlpacaSource, request: HistoryRequest) -> list[Message]:
    return [m async for m in found.history(request)]


def trades_request(start: int, end: int) -> HistoryRequest:
    return HistoryRequest(instrument=ALP, kind="trades", start=Timestamp(start), end=Timestamp(end))


def run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(asyncio.wait_for(coroutine, timeout=30))


# -- what it declares ------------------------------------------------------------------


def test_it_declares_stock_history_live_trades_and_the_free_plans_rate() -> None:
    caps = AlpacaSource(AlpacaParams()).capabilities
    assert caps.history == {"trades", "bars"}
    assert caps.live == {"trades"}
    assert caps.bar_sizes == {MIN, 5 * MIN, 15 * MIN, 30 * MIN, NANOS_PER_HOUR}
    assert caps.rate_limit == RateLimit(calls=200, period=60 * 10**9)


def test_keys_are_named_by_variables_and_a_missing_one_is_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    found, _ = source()
    with pytest.raises(InvalidValueError, match="APCA_API_KEY_ID is not set"):
        run(read(found, trades_request(OPEN, OPEN + MIN)))
    assert not run(found.health_check()).ok


def test_a_source_that_was_never_bound_says_so(keys: None) -> None:
    with pytest.raises(InvalidValueError, match="before it was bound"):
        run(read(AlpacaSource(AlpacaParams()), trades_request(OPEN, OPEN + MIN)))


# -- history ---------------------------------------------------------------------------


def test_trades_are_paged_from_the_last_instant_without_a_repeat_or_a_gap(service: Service) -> None:
    # Ten trades, two of them at one instant across a page boundary (page = 4).
    times = [OPEN + i * 1000 for i in (0, 1, 2, 3, 3, 4, 5, 6, 7, 8)]
    service.trades = [raw_trade(at, 100.0 + i, ident=i) for i, at in enumerate(times)]
    found, gate = source(page=4)
    got = run(read(found, trades_request(OPEN, OPEN + MIN)))
    assert [m.event_time for m in got if isinstance(m, Point)] == times
    assert [m.values["price"] for m in got if isinstance(m, Point)] == [
        100.0 + i for i in range(10)
    ]
    assert next(m.received_at for m in got if isinstance(m, Point)) == times[0]
    assert gate.entered == len(service.requests) == 4  # the last page is a short one


def test_a_trade_at_the_end_is_not_in_the_request(service: Service) -> None:
    service.trades = [raw_trade(OPEN), raw_trade(OPEN + MIN, ident=2)]
    found, _ = source()
    got: list[Message] = run(read(found, trades_request(OPEN, OPEN + MIN)))
    assert [m.event_time for m in got if isinstance(m, Point)] == [OPEN]


def test_a_full_page_at_one_instant_cannot_be_read_past(service: Service) -> None:
    service.trades = [raw_trade(OPEN, ident=i) for i in range(3)]
    found, _ = source(page=2)
    got: list[Message] = []

    async def drain() -> None:
        async for message in found.history(trades_request(OPEN, OPEN + MIN)):
            got.append(message)

    with pytest.raises(SourceUnavailableError, match="brought nothing new"):
        run(drain())
    assert len(got) == 2


def test_bars_are_whole_ones_only_and_paged(service: Service) -> None:
    service.bars = [raw_bar(OPEN + i * 5 * MIN, close=100.0 + i) for i in range(5)]
    found, _ = source(page=2)
    request = HistoryRequest(
        instrument=ALP, kind="bars", start=OPEN, end=Timestamp(OPEN + 18 * MIN), bar_size=5 * MIN
    )
    bars = [m for m in run(read(found, request)) if isinstance(m, Bar)]
    assert [(b.start, b.end, b.close) for b in bars] == [
        (OPEN + i * 5 * MIN, OPEN + (i + 1) * 5 * MIN, 100.0 + i) for i in range(3)
    ]  # the bar starting 15 min in ends after the request does
    assert bars[0].high == 200.0


def test_raw_prices_and_the_chosen_feed_are_asked_for(service: Service) -> None:
    service.bars = [raw_bar(OPEN)]
    found, _ = source(feed="sip")
    request = HistoryRequest(
        instrument=ALP, kind="bars", start=OPEN, end=Timestamp(OPEN + MIN), bar_size=MIN
    )
    run(read(found, request))
    [asked] = service.requests
    assert str(asked.adjustment.value) == "raw"
    assert str(asked.feed.value) == "sip"
    assert service.keys == ("key-id", "secret")


def test_a_bar_size_alpaca_lacks_and_another_venue_are_refused(service: Service) -> None:
    found, _ = source()
    two = HistoryRequest(
        instrument=ALP, kind="bars", start=OPEN, end=Timestamp(OPEN + 9 * MIN), bar_size=2 * MIN
    )
    with pytest.raises(InvalidValueError, match="no 2m bars"):
        run(read(found, two))
    elsewhere = Instrument(
        symbol="BTC/USD", venue="kraken", asset_class="crypto", quote_currency="USD"
    )
    with pytest.raises(InvalidValueError, match="not on alpaca"):
        run(
            read(
                found,
                HistoryRequest(
                    instrument=elsewhere, kind="trades", start=OPEN, end=Timestamp(OPEN + MIN)
                ),
            )
        )


def refused(status: int) -> APIError:
    http = SimpleNamespace(response=SimpleNamespace(status_code=status), request=None)
    return APIError('{"code": 1, "message": "no"}', http)  # type: ignore[no-untyped-call]


def test_a_request_refused_for_now_is_tried_again_and_then_gives_up(service: Service) -> None:
    service.trades = [raw_trade(OPEN)]
    service.failures = [refused(429), OSError("connection reset"), refused(503)]
    found, gate = source(retries=3)
    assert len(run(read(found, trades_request(OPEN, OPEN + MIN)))) == 1
    assert gate.entered == 4  # each try goes through the throttle
    service.failures = [refused(500), refused(500)]
    again, _ = source(retries=1)
    with pytest.raises(SourceUnavailableError, match=r"HTTP 500.*tried 2 times"):
        run(read(again, trades_request(OPEN, OPEN + MIN)))


def test_a_refused_key_or_plan_is_never_asked_again(service: Service) -> None:
    service.failures = [refused(403)]
    found, gate = source(retries=5)
    with pytest.raises(InvalidValueError, match="HTTP 403"):
        run(read(found, trades_request(OPEN, OPEN + MIN)))
    assert gate.entered == 1


# -- live ------------------------------------------------------------------------------


def live_trade(at: int, ident: int, price: float = 100.0) -> Trade:
    return Trade("AAPL", raw_trade(at, price, ident))


async def take(found: AlpacaSource, count: int) -> list[Message]:
    """The first ``count`` live messages, then the subscription closed."""
    stream = found.subscribe([Subscription(instrument=ALP, kind="trades")])
    got: list[Message] = []
    try:
        async for message in stream:
            got.append(message)
            if len(got) == count:
                break
    finally:
        await stream.aclose()  # type: ignore[attr-defined]
    return got


def test_live_trades_come_as_points_with_repeats_dropped_and_the_stream_stopped(
    service: Service,
) -> None:
    Stream.script = [live_trade(OPEN, 1), live_trade(OPEN, 1), live_trade(OPEN + 1000, 2, 101.5)]
    found, _ = source(feed="iex")
    got = run(take(found, 2))
    assert [(m.event_time, m.values["price"]) for m in got if isinstance(m, Point)] == [
        (OPEN, 100.0),
        (OPEN + 1000, 101.5),
    ]
    assert found.dropped == {"undated": 0, "repeated": 1}
    [stream] = Stream.made
    assert stream.symbols == ("AAPL",)
    assert stream.feed == "iex"
    assert stream.stopped.is_set()  # closing the subscription stopped the stream


def test_a_trade_without_a_time_is_skipped_and_counted(service: Service) -> None:
    undated = SimpleNamespace(symbol="AAPL", timestamp=None, exchange="V", id=9, price=1.0, size=1)
    Stream.script = [undated, live_trade(OPEN, 1)]
    found, _ = source()
    assert len(run(take(found, 1))) == 1
    assert found.dropped["undated"] == 1


def test_a_stream_that_ends_is_an_error_not_silence(service: Service) -> None:
    Stream.script, Stream.hold = [live_trade(OPEN, 1)], False
    found, _ = source()
    got: list[Message] = []

    async def drain() -> None:
        async for message in found.subscribe([Subscription(instrument=ALP, kind="trades")]):
            got.append(message)

    with pytest.raises(SourceUnavailableError, match="stream ended"):
        run(drain())
    assert len(got) == 1


def test_a_refused_key_is_found_before_the_stream_starts(service: Service) -> None:
    service.failures = [refused(401)]
    found, _ = source()
    with pytest.raises(InvalidValueError, match="HTTP 401"):
        run(take(found, 1))
    assert Stream.made == []


def test_live_bars_and_another_venue_are_refused(service: Service) -> None:
    found, _ = source()

    async def first(subscription: Subscription) -> None:
        stream: AsyncIterator[Message] = found.subscribe([subscription])
        await anext(stream)

    with pytest.raises(InvalidValueError, match="live trades only"):
        run(first(Subscription(instrument=ALP, kind="bars", bar_size=MIN)))
    elsewhere = Instrument(
        symbol="BTC/USD", venue="kraken", asset_class="crypto", quote_currency="USD"
    )
    with pytest.raises(InvalidValueError, match="not on alpaca"):
        run(first(Subscription(instrument=elsewhere, kind="trades")))
