# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The Halted state: refuses to add risk until the owner clears it."""

import pytest

from heliograph.core.bus import MessageBus
from heliograph.core.clock import SimulatedClock
from heliograph.core.engine import LiveEngine
from heliograph.core.errors import InvalidValueError
from heliograph.core.halt import Cleared, Halt, Halted, RateWatch, SilenceWatch
from heliograph.core.messages import Instrument, Point
from heliograph.core.pipeline import Proposal, Signal
from heliograph.core.time import NANOS_PER_HOUR, Timestamp
from tests.support.world import BTC, ETH

NOW = Timestamp(1_000)


def proposal() -> Proposal:
    return Proposal(
        ts=NOW, strategy="trend", instrument=BTC, price=100.0, previous=None, held=0.0, target=1.0
    )


def gate() -> tuple[Halt, MessageBus]:
    bus = MessageBus(SimulatedClock(NOW))
    return Halt(bus), bus


def test_nothing_is_refused_until_something_halts() -> None:
    halt, _ = gate()
    assert halt.halted is None
    assert halt.check(proposal()) is None


def test_a_halt_refuses_with_who_halted_and_why() -> None:
    halt, bus = gate()
    bus.publish(Halted(ts=NOW, reason="exchange maintenance"))
    assert halt.check(proposal()) == "halted by owner: exchange maintenance"
    bus.publish(Halted(ts=NOW, reason="no price for 5m", by="stale:btc"), origin="derived")
    # the first halt stands; a watch tripping later does not rewrite why
    assert halt.check(proposal()) == "halted by owner: exchange maintenance"


def test_a_watchs_halt_is_named_for_the_watch() -> None:
    halt, bus = gate()
    bus.publish(Halted(ts=NOW, reason="no price for 5m", by="stale:btc"), origin="derived")
    assert halt.check(proposal()) == "halted by stale:btc: no price for 5m"


def test_only_a_clear_ends_a_halt_and_clearing_when_not_halted_is_harmless() -> None:
    halt, bus = gate()
    bus.publish(Cleared(ts=NOW))
    assert halt.check(proposal()) is None
    bus.publish(Halted(ts=NOW, reason="x"))
    bus.publish(Halted(ts=NOW, reason="y"))
    assert halt.check(proposal()) is not None
    bus.publish(Cleared(ts=NOW))
    assert halt.check(proposal()) is None


HOUR = NANOS_PER_HOUR


def point(at: int, instrument: Instrument = BTC) -> Point:
    return Point(
        instrument=instrument,
        event_time=Timestamp(at),
        received_at=Timestamp(at),
        values={"price": 100.0},
    )


class Live:
    """A live engine whose clock is moved by hand, with the halts it publishes."""

    def __init__(self) -> None:
        self.clock = SimulatedClock(Timestamp(0))
        self.bus = MessageBus(self.clock)
        self.engine = LiveEngine(self.clock, self.bus)
        self.halts: list[Halted] = []
        self.bus.subscribe(Halted, self.halts.append)

    def at(self, ts: int) -> None:
        self.clock.advance_to(Timestamp(ts))


def test_no_price_for_the_span_halts_at_the_deadline_naming_the_watch() -> None:
    live = Live()
    SilenceWatch("trend", BTC, 2 * HOUR, live.bus, live.engine.scheduler, live.clock.now)
    live.at(1 * HOUR)
    live.engine.receive(point(1 * HOUR))
    live.at(2 * HOUR)
    live.engine.tick()
    assert live.halts == []
    live.at(5 * HOUR)  # found far later: the halt is as of the deadline, 3 h
    live.engine.tick()
    assert live.halts == [
        Halted(ts=Timestamp(3 * HOUR), reason="no price of BTC/USD for 2h", by="silence:trend")
    ]


def test_the_deadline_counts_from_when_the_price_was_known_not_when_it_was_read() -> None:
    live = Live()
    SilenceWatch("trend", BTC, 2 * HOUR, live.bus, live.engine.scheduler, live.clock.now)
    live.at(1 * HOUR + HOUR // 2)  # the session read the 1 h price half an hour late
    live.engine.receive(point(1 * HOUR))
    live.at(4 * HOUR)
    live.engine.tick()
    assert [h.ts for h in live.halts] == [Timestamp(3 * HOUR)]


def test_each_price_puts_the_deadline_back_and_a_silence_halts_once() -> None:
    live = Live()
    SilenceWatch("trend", BTC, 2 * HOUR, live.bus, live.engine.scheduler, live.clock.now)
    for hour in (1, 2, 3):
        live.at(hour * HOUR)
        live.engine.receive(point(hour * HOUR))
        live.engine.tick()
    assert live.halts == []
    live.at(20 * HOUR)
    live.engine.tick()
    live.at(30 * HOUR)
    live.engine.tick()
    assert [h.ts for h in live.halts] == [Timestamp(5 * HOUR)]


def test_a_price_of_another_instrument_is_not_this_ones_news() -> None:
    live = Live()
    SilenceWatch("trend", BTC, 2 * HOUR, live.bus, live.engine.scheduler, live.clock.now)
    live.at(1 * HOUR)
    live.engine.receive(point(1 * HOUR))
    live.at(2 * HOUR)
    live.engine.receive(point(2 * HOUR, ETH))
    live.at(4 * HOUR)
    live.engine.tick()
    assert [h.ts for h in live.halts] == [Timestamp(3 * HOUR)]


def test_a_watch_before_any_price_does_not_halt() -> None:
    live = Live()
    SilenceWatch("trend", BTC, 2 * HOUR, live.bus, live.engine.scheduler, live.clock.now)
    live.at(100 * HOUR)
    live.engine.tick()
    assert live.halts == []


def signal(at: int, strategy: str = "trend") -> Signal:
    return Signal(
        ts=Timestamp(at),
        id=f"s{at}",
        strategy=strategy,
        instrument=BTC,
        side="long",
        target=1.0,
        held=0.0,
        price=100.0,
        forecast=5.0,
    )


def test_more_signals_than_the_count_within_the_window_halts() -> None:
    live = Live()
    RateWatch("trend", 2, 1 * HOUR, live.bus)
    for minute in (0, 10, 20):
        live.at(minute * 60 * 10**9)
        live.engine.receive(signal(minute * 60 * 10**9))
    assert [(h.by, h.ts) for h in live.halts] == [("rate:trend", Timestamp(20 * 60 * 10**9))]
    assert live.halts[0].reason == "trend signalled more than 2 times in 1h"


def test_signals_spread_out_or_of_another_strategy_do_not_halt() -> None:
    live = Live()
    RateWatch("trend", 2, 1 * HOUR, live.bus)
    for hour in (0, 1, 2, 3):  # one an hour: never more than 2 inside the window
        live.at(hour * HOUR)
        live.engine.receive(signal(hour * HOUR))
    for hour in (4, 4, 4):
        live.at(hour * HOUR)
        live.engine.receive(signal(hour * HOUR, "other"))
    assert live.halts == []


def test_a_burst_halts_once_and_the_next_burst_counts_afresh() -> None:
    live = Live()
    RateWatch("trend", 1, 1 * HOUR, live.bus)
    for second in (0, 1, 2):
        live.at(second * 10**9)
        live.engine.receive(signal(second * 10**9))
    assert len(live.halts) == 1


@pytest.mark.parametrize("span", [0, -1])
def test_a_watch_needs_a_positive_span(span: int) -> None:
    live = Live()
    with pytest.raises(InvalidValueError, match="positive"):
        SilenceWatch("trend", BTC, span, live.bus, live.engine.scheduler, live.clock.now)
    with pytest.raises(InvalidValueError, match="count of 1 or more and a span"):
        RateWatch("trend", 1, span, live.bus)


class Shift:
    """A market open for the first hour of each day, and closed for the other 23."""

    name = "SHIFT"
    DAY = 24 * HOUR

    def after_open_time(self, start: Timestamp, span: int) -> Timestamp:
        day, into = divmod(int(start), self.DAY)
        if into >= HOUR:  # closed: counting begins at the next open
            day, into = day + 1, 0
        left = span
        while left > HOUR - into:
            left -= HOUR - into
            day, into = day + 1, 0
        return Timestamp(day * self.DAY + into + left)


def test_a_closed_market_is_not_silent_and_the_halt_names_its_hours() -> None:
    live = Live()
    SilenceWatch("stocks", BTC, HOUR // 2, live.bus, live.engine.scheduler, live.clock.now, Shift())
    live.at(HOUR // 2 + HOUR // 4)
    live.engine.receive(point(HOUR // 2 + HOUR // 4))  # a quarter hour before the close
    live.at(10 * HOUR)  # the night passed with nothing
    live.engine.tick()
    assert live.halts == []
    live.at(24 * HOUR + HOUR // 10)  # 6 min into the next open: 15 min are owed
    live.engine.tick()
    assert live.halts == []
    live.at(24 * HOUR + HOUR // 2)
    live.engine.tick()
    [halt] = live.halts
    assert halt.ts == Timestamp(24 * HOUR + HOUR // 4)
    assert halt.reason == "no price of BTC/USD for 30m of SHIFT hours"
