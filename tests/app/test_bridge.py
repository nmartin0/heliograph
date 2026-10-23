# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

import asyncio
from collections.abc import AsyncIterator, Iterator, Sequence

import pytest

from heliograph.app.bridge import HistoryFeed, SourceLoop, run_live
from heliograph.core.bars import TimeBars
from heliograph.core.bus import MessageBus
from heliograph.core.clock import WallClock
from heliograph.core.errors import InvalidValueError, OutOfOrderError
from heliograph.core.messages import Bar, Instrument, Message, Point, Settled
from heliograph.core.sources import (
    Capabilities,
    HistoryRequest,
    SourceUnavailableError,
    Subscription,
)
from heliograph.core.streams import BarSpec, StreamSpec
from heliograph.core.time import NANOS_PER_HOUR, NANOS_PER_MINUTE, Timestamp
from tests.support.simulated import SimulatedTime
from tests.support.sources import TRADES, ScriptedSource, trade
from tests.support.world import BTC, ETH, world

MIN, HOUR = NANOS_PER_MINUTE, NANOS_PER_HOUR
WHOLE_DAY = HistoryRequest(
    instrument=BTC, kind="trades", start=Timestamp(0), end=Timestamp(24 * HOUR)
)


def flat_bar(start: int, end: int, instrument: Instrument = BTC) -> Bar:
    return Bar(
        instrument=instrument,
        start=Timestamp(start),
        end=Timestamp(end),
        open=1,
        high=1,
        low=1,
        close=1,
        volume=0,
    )


@pytest.fixture
def loop() -> Iterator[SourceLoop]:
    with SourceLoop() as source_loop:
        yield source_loop


# -- history ---------------------------------------------------------------------


def test_history_is_stamped_with_the_sources_declared_delay(loop: SourceLoop) -> None:
    delayed = Capabilities(history=frozenset({"trades"}), live_delay=15 * 10**9)
    source = ScriptedSource(history_messages=[trade(1), trade(2)], caps=delayed)
    with HistoryFeed(loop, source, WHOLE_DAY) as feed:
        stamped = list(feed)
    assert [m.ts for m in stamped] == [MIN + 15 * 10**9, 2 * MIN + 15 * 10**9]
    assert [m.event_time for m in stamped if isinstance(m, Point)] == [MIN, 2 * MIN]


def test_the_engine_reads_a_history_feed_lazily_and_closes_it_when_it_stops(
    loop: SourceLoop,
) -> None:
    source = ScriptedSource(history_messages=[trade(m) for m in range(1, 100)])
    w = world()
    with HistoryFeed(loop, source, WHOLE_DAY) as feed:
        w.engine.run(feed, until=Timestamp(5 * MIN))
        assert source.yielded <= 6  # five delivered, at most one read ahead
    assert source.streams_closed == 1


def test_a_history_feed_builds_the_same_bars_as_the_points_it_carries(loop: SourceLoop) -> None:
    """End to end: source, bridge, engine and time bars agree with the points fed directly."""
    raw = [trade(m, 100 + m % 13) for m in range(0, 240, 7)]

    def bars(feed: Iterator[Message] | list[Point]) -> list[Bar]:
        w = world()
        TimeBars(StreamSpec(instrument=BTC, bars=BarSpec.of("1h")), w.bus, w.engine.scheduler)
        seen: list[Bar] = []
        w.bus.subscribe(Bar, seen.append)
        w.engine.run(feed, until=Timestamp(5 * HOUR))
        return seen

    with HistoryFeed(loop, ScriptedSource(history_messages=raw), WHOLE_DAY) as feed:
        through_the_bridge = bars(feed)
    assert through_the_bridge == bars(raw)
    assert len(through_the_bridge) == 4


@pytest.mark.parametrize(
    ("messages", "error", "reason"),
    [
        ([trade(5), trade(4)], OutOfOrderError, "went back in time"),
        ([trade(24 * 60)], InvalidValueError, "outside"),
        ([Settled(Timestamp(0))], InvalidValueError, "not a Point or Bar"),
        (
            [
                Point(
                    instrument=ETH,
                    event_time=Timestamp(0),
                    received_at=Timestamp(0),
                    values={"price": 1.0},
                )
            ],
            InvalidValueError,
            "the source sent kraken:ETH/USD",
        ),
    ],
)
def test_history_a_source_should_not_have_sent_is_refused(
    loop: SourceLoop, messages: list[Message], error: type[Exception], reason: str
) -> None:
    source = ScriptedSource(history_messages=messages)
    with HistoryFeed(loop, source, WHOLE_DAY) as feed, pytest.raises(error, match=reason):
        list(feed)


def test_vendor_bars_are_read_as_history_and_known_at_their_end_plus_the_delay(
    loop: SourceLoop,
) -> None:
    delayed = Capabilities(history=frozenset({"bars"}), bar_sizes=frozenset({MIN}), live_delay=7)
    source = ScriptedSource(
        history_messages=[flat_bar(0, MIN), flat_bar(MIN, 2 * MIN)], caps=delayed
    )
    request = HistoryRequest(
        instrument=BTC, kind="bars", start=Timestamp(0), end=Timestamp(HOUR), bar_size=MIN
    )
    with HistoryFeed(loop, source, request) as feed:
        got = list(feed)
    assert [m.ts for m in got] == [MIN + 7, 2 * MIN + 7]
    assert [m.start for m in got if isinstance(m, Bar)] == [0, MIN]


@pytest.mark.parametrize(
    ("sent", "reason"),
    [
        (trade(1), "asked for kraken:BTC/USD 1m bars, the source sent kraken:BTC/USD trades"),
        (flat_bar(0, 2 * MIN), "the source sent kraken:BTC/USD 2m bars"),
    ],
)
def test_history_of_another_kind_or_bar_size_is_refused(
    loop: SourceLoop, sent: Message, reason: str
) -> None:
    """A bar declared smaller than it is would be known before it closed."""
    source = ScriptedSource(history_messages=[sent])
    request = HistoryRequest(
        instrument=BTC, kind="bars", start=Timestamp(0), end=Timestamp(HOUR), bar_size=MIN
    )
    with HistoryFeed(loop, source, request) as feed, pytest.raises(InvalidValueError, match=reason):
        list(feed)


def test_a_source_that_cannot_deliver_stops_the_run_with_its_own_error(loop: SourceLoop) -> None:
    source = ScriptedSource(
        history_messages=[trade(1), trade(2)],
        fail_after=1,
        failure=SourceUnavailableError("rate limited", retry_after=10**9),
    )
    w = world()
    with (
        HistoryFeed(loop, source, WHOLE_DAY) as feed,
        pytest.raises(SourceUnavailableError, match="rate limited") as caught,
    ):
        w.engine.run(feed)
    assert caught.value.retry_after == 10**9
    assert source.streams_closed == 1


# -- live --------------------------------------------------------------------------


def counting_clock(start: int = 1_000) -> WallClock:
    """A wall clock whose system time moves one nanosecond per reading."""
    readings = iter(range(start, start + 1_000_000))
    return WallClock(source=lambda: next(readings))


def live(*feeds: tuple[ScriptedSource, list[Subscription]]) -> tuple[list[Message], WallClock]:
    clock = counting_clock()
    bus = MessageBus(clock)
    published: list[Message] = []
    bus.subscribe(Point, published.append)
    asyncio.run(run_live(list(feeds), clock, bus.publish))
    return published, clock


SUBSCRIBED = [Subscription(instrument=BTC, kind="trades")]


def test_live_data_from_several_sources_is_published_in_arrival_order_stamped_on_arrival() -> None:
    """Forced interleaving: A sends, B sends twice, then A again (events, not timing)."""
    a_may_go_on, b_done = asyncio.Event(), asyncio.Event()

    async def let_a_go() -> None:
        a_may_go_on.set()
        b_done.set()

    a = ScriptedSource(live=[trade(1, 1), a_may_go_on.wait, trade(4, 4)])
    b = ScriptedSource(live=[trade(2, 2), trade(3, 3), let_a_go])
    published, _ = live((a, SUBSCRIBED), (b, SUBSCRIBED))
    assert [p.values["price"] for p in published if isinstance(p, Point)] == [1, 2, 3, 4]
    stamps = [m.ts for m in published]
    assert stamps == sorted(stamps)
    assert len(set(stamps)) == len(stamps)
    assert a.streams_closed == b.streams_closed == 1


def test_live_data_is_stamped_by_the_runs_clock_not_the_venues() -> None:
    venue_says_much_later = trade(10_000_000)
    published, clock = live((ScriptedSource(live=[venue_says_much_later]), SUBSCRIBED))
    (point,) = published
    assert point.ts < clock.now() < venue_says_much_later.ts


def test_the_first_source_to_fail_stops_the_others_with_its_own_error() -> None:
    held = asyncio.Event()  # never set: B would wait for ever
    a = ScriptedSource(live=[trade(1)], fail_after=0, failure=SourceUnavailableError("gone"))
    b = ScriptedSource(live=[held.wait, trade(2)])
    with pytest.raises(SourceUnavailableError, match="gone"):
        live((a, SUBSCRIBED), (b, SUBSCRIBED))
    assert b.streams_closed == 1


LIVE_BARS = Capabilities(live=frozenset({"bars"}), bar_sizes=frozenset({MIN}))
BARS_SUBSCRIBED = [Subscription(instrument=BTC, kind="bars", bar_size=MIN)]


def live_bars(time: SimulatedTime, *bars: Bar) -> list[Message]:
    """Bars sent live on simulated time, so a wait moves the clock and never blocks."""
    bus = MessageBus(time.clock)
    published: list[Message] = []
    bus.subscribe(Bar, published.append)
    source = ScriptedSource(live=list(bars), caps=LIVE_BARS)
    feeds = [(source, BARS_SUBSCRIBED)]
    asyncio.run(run_live(feeds, time.clock, bus.publish, sleep=time.advance))
    return published


def test_a_live_bar_arriving_long_before_it_closed_is_refused_as_partial() -> None:
    """Our clock is 2 s behind the bar's end: beyond the 1 s skew allowed."""
    time = SimulatedTime(start=MIN - 2 * 10**9)
    with pytest.raises(InvalidValueError, match="a partial bar"):
        live_bars(time, flat_bar(0, MIN))
    assert time.slept == []


def test_a_live_bar_from_a_venue_clock_slightly_ahead_is_held_until_it_closes() -> None:
    """The venue's clock is 5 ms ahead: the bar waits 5 ms and is published at its end."""
    time = SimulatedTime(start=MIN - 5_000_000)
    published = live_bars(time, flat_bar(0, MIN))
    assert published == [flat_bar(0, MIN)]  # known at its end: no separate received time
    assert time.slept == [0.005]


@pytest.mark.parametrize(
    ("sent", "reason"),
    [
        (trade(1), "a source sent kraken:BTC/USD trades unasked"),
        (flat_bar(0, 2 * MIN), "a source sent kraken:BTC/USD 2m bars unasked"),
        (flat_bar(0, MIN, ETH), "a source sent kraken:ETH/USD 1m bars unasked"),
    ],
)
def test_live_data_a_source_was_not_subscribed_to_is_refused(sent: Message, reason: str) -> None:
    source = ScriptedSource(live=[sent], caps=LIVE_BARS)
    with pytest.raises(InvalidValueError, match=reason):
        live((source, BARS_SUBSCRIBED))


class HeldStream:
    """A live stream that is not a generator: nothing closes it but ``aclose``."""

    def __init__(self) -> None:
        self.closed = False

    def __aiter__(self) -> "HeldStream":
        return self

    async def __anext__(self) -> Message:
        await asyncio.Event().wait()  # never: only cancellation ends it
        raise AssertionError("unreachable")

    async def aclose(self) -> None:
        self.closed = True


def test_a_stream_cancelled_because_another_source_failed_is_closed() -> None:
    held = HeldStream()

    class Held(ScriptedSource):
        def subscribe(self, subscriptions: Sequence[Subscription]) -> AsyncIterator[Message]:
            return held

    failing = ScriptedSource(live=[trade(1)], fail_after=0, failure=SourceUnavailableError("gone"))
    with pytest.raises(SourceUnavailableError, match="gone"):
        live((failing, SUBSCRIBED), (Held(), SUBSCRIBED))
    assert held.closed


def test_the_fake_source_is_a_data_source() -> None:
    """Checked by mypy: the scripted source satisfies the port's protocol."""
    from heliograph.core.sources import DataSource

    source: DataSource = ScriptedSource()
    assert source.capabilities is TRADES


def test_a_bar_ending_after_the_request_does_is_refused(loop: SourceLoop) -> None:
    """A source must send only bars whole within the request: the 1:00-2:00 bar is not
    within one ending at 1:30."""
    caps = Capabilities(history=frozenset({"bars"}), bar_sizes=frozenset({HOUR}))
    source = ScriptedSource(
        history_messages=[flat_bar(0, HOUR), flat_bar(HOUR, 2 * HOUR)], caps=caps
    )
    request = HistoryRequest(
        instrument=BTC, kind="bars", start=Timestamp(0), end=Timestamp(90 * MIN), bar_size=HOUR
    )
    with HistoryFeed(loop, source, request) as feed:
        with pytest.raises(InvalidValueError, match="a bar must be whole within it"):
            list(feed)
        assert feed.delivered == 1


def test_a_feed_counts_what_its_source_delivered(loop: SourceLoop) -> None:
    source = ScriptedSource(history_messages=[trade(1), trade(2), trade(3)])
    with HistoryFeed(loop, source, WHOLE_DAY) as feed:
        list(feed)
        assert feed.delivered == 3
