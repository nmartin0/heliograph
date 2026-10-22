# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

from collections import defaultdict
from typing import Any

import pytest
from hypothesis import example, given
from hypothesis import strategies as st
from tests.support.world import BTC, ETH, world

from heliograph.core.alarms import AlarmFired
from heliograph.core.bars import TimeBars
from heliograph.core.bus import MessageBus
from heliograph.core.clock import SimulatedClock
from heliograph.core.engine import BacktestEngine
from heliograph.core.errors import InvalidValueError
from heliograph.core.messages import Bar, Point, Settled
from heliograph.core.streams import BarSpec, StreamSpec
from heliograph.core.time import NANOS_PER_HOUR, NANOS_PER_MINUTE, Timestamp

MIN = NANOS_PER_MINUTE
HOUR = NANOS_PER_HOUR


def point(minute: float, price: float, size: float = 1.0, *, delay: float = 0, **kw: Any) -> Point:
    happened = Timestamp(int(minute * MIN))
    return Point(
        instrument=kw.get("instrument", BTC),
        event_time=happened,
        received_at=Timestamp(happened + int(delay * MIN)),
        values=kw.get("values", {"price": price, "size": size}),
    )


def build(*specs: BarSpec) -> tuple[SimulatedClock, MessageBus, BacktestEngine, list[TimeBars]]:
    w = world()
    builders = [
        TimeBars(StreamSpec(instrument=BTC, bars=s), w.bus, w.engine.scheduler) for s in specs
    ]
    return w.clock, w.bus, w.engine, builders


def collect(bus: MessageBus, clock: SimulatedClock) -> list[tuple[int, Bar]]:
    """Bars with the clock reading at which each was delivered."""
    seen: list[tuple[int, Bar]] = []
    bus.subscribe(Bar, lambda bar: seen.append((clock.now(), bar)))
    return seen


def test_a_bar_closes_at_its_end_with_open_high_low_close_and_volume() -> None:
    clock, bus, engine, _ = build(BarSpec.of("1h"))
    seen = collect(bus, clock)
    engine.run([point(5, 100, 2), point(20, 110), point(40, 95, 3), point(59, 105)])
    assert seen == []  # nothing has reached 01:00 yet
    engine.run(until=Timestamp(HOUR))
    (at, bar), *_ = seen
    assert at == bar.ts == bar.end == HOUR
    assert (bar.start, bar.open, bar.high, bar.low, bar.close, bar.volume) == (
        0,
        100,
        110,
        95,
        105,
        7,
    )


def test_a_bar_closes_on_time_even_if_no_point_follows() -> None:
    clock, bus, engine, _ = build(BarSpec.of("1h"))
    seen = collect(bus, clock)
    engine.run([point(10, 100)], until=Timestamp(5 * HOUR))
    assert [(at, bar.end) for at, bar in seen] == [(HOUR, HOUR)]


def test_empty_intervals_are_skipped_or_carried() -> None:
    for gaps, expected in (("skip", [1, 3]), ("carry", [1, 2, 3])):
        clock, bus, engine, _ = build(BarSpec.of("1h", gaps=gaps))
        seen = collect(bus, clock)
        engine.run([point(10, 100), point(130, 120)], until=Timestamp(3 * HOUR))
        assert [bar.end // HOUR for _, bar in seen] == expected
    flat = seen[1][1]
    assert (flat.open, flat.high, flat.low, flat.close, flat.volume) == (100, 100, 100, 100, 0)


def test_a_point_after_its_bar_closed_is_counted_late_and_changes_nothing() -> None:
    clock, bus, engine, (builder,) = build(BarSpec.of("1h"))
    seen = collect(bus, clock)
    engine.run([point(10, 100), point(59, 999, delay=2), point(70, 101)], until=Timestamp(2 * HOUR))
    assert [bar.high for _, bar in seen] == [100, 101]
    assert builder.late_points == 1


def test_grace_waits_for_points_that_happened_before_the_end() -> None:
    clock, bus, engine, (builder,) = build(BarSpec.of("1h", grace=2 * MIN))
    seen = collect(bus, clock)
    engine.run([point(10, 100), point(59, 120, delay=1.5)], until=Timestamp(2 * HOUR))
    at, bar = seen[0]
    assert (bar.high, bar.end, bar.ts, at) == (120, HOUR, HOUR + 2 * MIN, HOUR + 2 * MIN)
    assert builder.late_points == 0


def test_other_instruments_are_ignored() -> None:
    clock, bus, engine, _ = build(BarSpec.of("1h"))
    seen = collect(bus, clock)
    engine.run([point(10, 100), point(20, 5000, instrument=ETH)], until=Timestamp(HOUR))
    assert [bar.high for _, bar in seen] == [100]


def test_a_point_without_the_price_value_is_refused() -> None:
    _, _, engine, _ = build(BarSpec.of("1h"))
    with pytest.raises(InvalidValueError, match="needs 'price'"):
        engine.run([point(1, 0, values={"bid": 1.0, "size": 1.0})])


def test_a_stream_without_bars_cannot_be_built() -> None:
    clock = SimulatedClock(Timestamp(0))
    bus = MessageBus(clock)
    with pytest.raises(InvalidValueError, match="no bar spec"):
        TimeBars(StreamSpec(instrument=BTC), bus, BacktestEngine(clock, bus).scheduler)


@given(
    st.lists(
        st.tuples(st.integers(0, 600), st.integers(1, 1000), st.integers(0, 50)),
        min_size=1,
        max_size=60,
    )
)
def test_bars_equal_the_points_grouped_by_interval(raw: list[tuple[int, int, int]]) -> None:
    """Built one point at a time, bars match a plain grouping of all the points."""
    raw.sort()
    clock, bus, engine, _ = build(BarSpec.of("1h"))
    seen = collect(bus, clock)
    engine.run([point(m, p, s) for m, p, s in raw], until=Timestamp(11 * HOUR))
    groups: dict[int, list[tuple[int, int, int]]] = defaultdict(list)
    for minute, price, size in raw:
        groups[minute // 60].append((minute, price, size))
    expected = [
        (
            h,
            g[0][1],
            max(p for _, p, _ in g),
            min(p for _, p, _ in g),
            g[-1][1],
            sum(s for *_, s in g),
        )
        for h, g in sorted(groups.items())
    ]
    actual = [
        (bar.start // HOUR, bar.open, bar.high, bar.low, bar.close, bar.volume) for _, bar in seen
    ]
    assert actual == expected
    assert all(at == bar.ts == bar.end for at, bar in seen)


def test_phase_1_gate_a_4h_bar_is_visible_to_the_1h_consumer_at_its_end_never_before() -> None:
    """A 4h value ending at T is visible to the 1h bar ending at T: not earlier, not later."""
    one, four = BarSpec.of("1h"), BarSpec.of("4h")
    clock, bus, engine, _ = build(one, four)
    delivered = collect(bus, clock)
    snapshots: list[tuple[int, int | None, int | None]] = []
    latest: dict[int, Bar] = {}

    def remember(bar: Bar) -> None:
        latest[bar.end - bar.start] = bar

    def at_settled(settled: Settled) -> None:
        hour, four_hours = latest.get(HOUR), latest.get(4 * HOUR)
        snapshots.append(
            (settled.ts, hour.end if hour else None, four_hours.end if four_hours else None)
        )

    bus.subscribe(Bar, remember)
    bus.subscribe(Settled, at_settled)
    engine.run([point(m, 100 + m % 7) for m in range(0, 12 * 60, 10)], until=Timestamp(12 * HOUR))

    # Never earlier: every bar is delivered exactly when it becomes known.
    assert all(at == bar.ts for at, bar in delivered)
    # Not later: when the 1h bar ending at a 4h boundary T is settled, so is the 4h bar.
    by_time = {ts: (hour, four) for ts, hour, four in snapshots}
    for boundary in (4, 8, 12):
        t = boundary * HOUR
        assert by_time[t] == (t, t)
    # And before T, the 4h bar ending at T had not been seen.
    assert all(four is None or four <= ts for ts, _, four in snapshots)
    assert by_time[7 * HOUR] == (7 * HOUR, 4 * HOUR)


def test_a_point_out_of_order_still_closes_its_own_bar_on_time() -> None:
    clock, bus, engine, (builder,) = build(BarSpec.of("1m", grace=10 * 10**9))
    seen = collect(bus, clock)
    second = 1 / 60
    engine.run(
        [point(61 * second, 100, delay=1 * second), point(59 * second, 90, delay=4 * second)],
        until=Timestamp(5 * MIN),
    )
    assert [(bar.start, bar.close) for _, bar in seen] == [(0, 90), (MIN, 100)]
    assert builder.late_points == 0


def test_a_source_clock_ahead_does_not_hold_back_the_bars_before_it() -> None:
    clock, bus, engine, (builder,) = build(BarSpec.of("1m"))
    seen = collect(bus, clock)
    ahead = point(60, 500, delay=-60)  # happened "at 01:00", received at 00:00
    current = [point(m + 0.5, 100 + m) for m in range(1, 10)]
    engine.run([ahead, *current], until=Timestamp(2 * HOUR))
    assert [bar.start // MIN for _, bar in seen] == [*range(1, 10), 60]
    assert builder.late_points == 0
    assert all(at == bar.ts for at, bar in seen)


def test_a_point_received_exactly_at_its_close_is_late_first_or_not() -> None:
    for prior in ([], [point(10, 100)]):
        _, _, engine, (builder,) = build(BarSpec.of("1h"))
        engine.run([*prior, point(59, 999, delay=1)], until=Timestamp(2 * HOUR))
        assert builder.late_points == 1


def test_two_builders_for_one_stream_are_refused() -> None:
    clock = SimulatedClock(Timestamp(0))
    bus = MessageBus(clock)
    engine = BacktestEngine(clock, bus)
    stream = StreamSpec(instrument=BTC, bars=BarSpec.of("1h"))
    TimeBars(stream, bus, engine.scheduler)
    with pytest.raises(InvalidValueError, match="already claimed"):
        TimeBars(stream, bus, engine.scheduler)


def test_streams_differing_only_in_grace_each_get_their_bars() -> None:
    clock, bus, engine, _ = build(BarSpec.of("1m"), BarSpec.of("1m", grace=5 * 10**9))
    seen = collect(bus, clock)
    engine.run([point(m + 0.5, 100) for m in range(4)], until=Timestamp(10 * MIN))
    assert sorted(bar.end // MIN for _, bar in seen) == [1, 1, 2, 2, 3, 3, 4, 4]


def test_skipping_gaps_schedules_nothing_once_the_data_ends() -> None:
    clock = SimulatedClock(Timestamp(0))
    bus = MessageBus(clock)
    engine = BacktestEngine(clock, bus)
    TimeBars(StreamSpec(instrument=BTC, bars=BarSpec.of("1m")), bus, engine.scheduler)
    alarms: list[int] = []
    bus.subscribe(AlarmFired, lambda alarm: alarms.append(alarm.ts))
    engine.run([point(0.5, 100)], until=Timestamp(24 * HOUR))
    assert alarms == [MIN]


@given(
    st.lists(
        st.tuples(st.integers(0, 300), st.integers(1, 100), st.integers(-30, 40)),
        max_size=50,
    ),
    st.sampled_from(["1m", "5m", "1h"]),
    st.integers(0, 50),
)
@example(raw=[(7, 100, 1), (5, 90, 25)], size="1m", grace_s=20)  # out of order, as reviewed
@example(raw=[(7, 500, -30), (5, 100, -5)], size="1m", grace_s=0)  # a source clock ahead
def test_bars_match_an_oracle_under_delays_and_grace(
    raw: list[tuple[int, int, int]], size: str, grace_s: int
) -> None:
    """Points arrive out of order; bars must equal the accepted points, grouped."""
    spec = BarSpec.of(size, grace=min(grace_s * 10**9, BarSpec.of(size).size - 1))
    feed = sorted(
        (point(1 + m / 6, p, delay=d / 60) for m, p, d in raw),
        key=lambda pt: (pt.ts, pt.event_time),
    )
    clock, bus, engine, (builder,) = build(spec)
    seen = collect(bus, clock)
    engine.run(feed, until=Timestamp(10 * HOUR))

    accepted: dict[int, list[Point]] = defaultdict(list)
    late = 0
    for pt in feed:
        start = spec.start_of(pt.event_time)
        closes = start + spec.size + spec.grace
        closed_up_to = max(
            (s + spec.size for s in accepted if s + spec.size + spec.grace <= pt.ts), default=None
        )
        if closes <= pt.ts or (closed_up_to is not None and start < closed_up_to):
            late += 1
        else:
            accepted[start].append(pt)
    expected = [
        (s, g[0].values["price"], max(p.values["price"] for p in g), g[-1].values["price"])
        for s, g in sorted(accepted.items())
    ]
    assert [(b.start, b.open, b.high, b.close) for _, b in seen] == expected
    assert builder.late_points == late
    assert all(at == bar.ts and bar.ts >= bar.end for at, bar in seen)
