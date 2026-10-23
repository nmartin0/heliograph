# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Bars built from finer bars, on the real engine and bus."""

from collections import defaultdict
from collections.abc import Sequence

import pytest
from hypothesis import given
from hypothesis import strategies as st

from heliograph.core.alarms import AlarmFired
from heliograph.core.bars import BarsFromBars
from heliograph.core.errors import InvalidValueError
from heliograph.core.messages import Bar, Instrument, Message
from heliograph.core.streams import BarSpec, StreamSpec
from heliograph.core.time import NANOS_PER_MINUTE, Timestamp
from tests.support.world import BTC, ETH, world

MIN = NANOS_PER_MINUTE


def part(
    minute: int,
    o: float,
    h: float,
    low: float,
    c: float,
    v: float = 1.0,
    *,
    size: int = 15,
    delay: int = 0,
    instrument: Instrument = BTC,
) -> Bar:
    """A finer bar of ``size`` minutes starting at ``minute``, known ``delay`` after its end."""
    end = (minute + size) * MIN
    return Bar(
        instrument=instrument,
        start=Timestamp(minute * MIN),
        end=Timestamp(end),
        open=o,
        high=h,
        low=low,
        close=c,
        volume=v,
        received_at=Timestamp(end + delay * MIN) if delay else None,
    )


def hourly(
    parts: Sequence[Message], until: int, *, grace: int = 0, finer: int = 15, delay: int = 0
) -> tuple[list[tuple[int, Bar]], list[int], BarsFromBars]:
    """The 1h bars built from ``parts`` (with the minute each was published), and alarms fired."""
    w = world()
    spec = StreamSpec(instrument=BTC, bars=BarSpec.of("1h", grace=grace * MIN))
    builder = BarsFromBars(spec, finer * MIN, w.bus, w.engine.scheduler, delay * MIN)
    built: list[tuple[int, Bar]] = []
    w.bus.subscribe(
        Bar,
        lambda bar: (
            built.append((w.clock.now() // MIN, bar)) if bar.end - bar.start > finer * MIN else None
        ),
    )
    alarms: list[int] = []
    w.bus.subscribe(AlarmFired, lambda alarm: alarms.append(alarm.ts // MIN))
    w.engine.run(parts, until=Timestamp(until * MIN))
    return built, alarms, builder


def ohlcv(bar: Bar) -> tuple[float, float, float, float, float]:
    return (bar.open, bar.high, bar.low, bar.close, bar.volume)


def test_a_bar_is_its_parts_put_together_published_with_its_last_part() -> None:
    parts = [
        part(0, 10, 12, 9, 11, 1.5),
        part(15, 11, 15, 11, 14, 2.0),
        part(30, 14, 14, 8, 9, 0.5),
        part(45, 9, 10, 9, 10, 1.0),
    ]
    built, alarms, _ = hourly(parts, until=120)
    ((minute, bar),) = built
    assert minute == 60
    assert (bar.start, bar.end, bar.received_at) == (0, 60 * MIN, None)
    assert ohlcv(bar) == (10, 15, 8, 10, 5.0)
    assert alarms == []  # closed by its last part: the fallback alarm was taken back


def test_a_bar_missing_its_last_part_is_published_one_part_after_its_end() -> None:
    built, alarms, _ = hourly([part(0, 10, 12, 9, 11), part(15, 11, 13, 10, 12)], until=120)
    ((minute, bar),) = built
    assert minute == 75
    assert bar.received_at == 75 * MIN
    assert ohlcv(bar) == (10, 13, 9, 12, 2.0)
    assert alarms == [75]


def test_an_hour_with_no_parts_has_no_bar() -> None:
    parts = [part(45, 1, 1, 1, 1), part(120, 2, 2, 2, 2), part(165, 3, 3, 3, 3)]
    built, _, _ = hourly(parts, until=240)
    assert [(minute, bar.start // MIN, bar.close) for minute, bar in built] == [
        (60, 0, 1),
        (180, 120, 3),
    ]


def test_the_parts_are_put_in_order_by_start_whatever_order_they_arrive_in() -> None:
    parts = [
        part(15, 20, 21, 19, 20, delay=10),
        part(0, 10, 11, 9, 10, delay=30),
        part(45, 5, 6, 4, 6),
    ]
    # The 0-15 part arrives at 45, after the 15-30 one (at 40) and before 45-60 (at 60).
    built, _, _ = hourly(sorted(parts, key=lambda p: p.ts), until=120)
    ((_, bar),) = built
    assert (bar.open, bar.close) == (10, 6)


def test_a_part_of_a_bar_already_published_is_late_and_changes_nothing() -> None:
    # The 15-30 part is known at 65: after its bar was published (60), before its close (75).
    parts = [part(0, 10, 10, 10, 10), part(45, 11, 11, 11, 11), part(15, 99, 99, 0, 99, delay=35)]
    built, _, builder = hourly(sorted(parts, key=lambda p: p.ts), until=180)
    ((_, bar),) = built
    assert ohlcv(bar) == (10, 11, 10, 11, 2.0)
    assert builder.late_bars == 1


def test_a_part_arriving_at_its_bars_close_is_late() -> None:
    """The fallback close of hour 0 is 75; a part known then is late, alarms first."""
    built, _, builder = hourly([part(0, 1, 1, 1, 1), part(30, 2, 2, 2, 2, delay=30)], until=180)
    assert [(minute, bar.close) for minute, bar in built] == [(75, 1)]
    assert builder.late_bars == 1


def test_a_first_part_arriving_at_or_after_its_bars_close_opens_no_bar() -> None:
    built, _, builder = hourly(
        [part(0, 1, 1, 1, 1, delay=60), part(60, 2, 2, 2, 2, delay=60)], until=240
    )
    assert built == []
    assert builder.late_bars == 2


def test_grace_waits_longer_for_parts_known_late() -> None:
    parts = [part(0, 1, 1, 1, 1), part(15, 2, 2, 2, 2, delay=25)]  # known at 55
    on_time, _, _ = hourly(parts, until=180)
    assert [ohlcv(bar)[3] for _, bar in on_time] == [2]  # before the close at 75 anyway
    slow = [part(0, 1, 1, 1, 1), part(15, 2, 2, 2, 2, delay=70)]  # known at 100
    without, _, b1 = hourly(slow, until=180)
    with_grace, _, b2 = hourly(slow, until=180, grace=30)
    assert [(m, bar.close) for m, bar in without] == [(75, 1)]
    assert [(m, bar.close) for m, bar in with_grace] == [(105, 2)]
    assert (b1.late_bars, b2.late_bars) == (1, 0)


def test_bars_are_published_in_order_and_one_per_instant() -> None:
    """Hour 0 lacks its last part and would wait to 125 (grace 50); hour 1 completes at
    120. Hour 0 is closed then, and hour 1 at its own close, 60 + 60 + 15 + 50 = 185:
    the graph takes one bar of a stream per instant (D27)."""
    parts = [part(0, 1, 1, 1, 1)] + [part(m, 2, 2, 2, 2) for m in (60, 75, 90, 105)]
    built, _, _ = hourly(parts, until=240, grace=50)
    assert [(minute, bar.start // MIN, bar.close) for minute, bar in built] == [
        (120, 0, 1),
        (185, 60, 2),
    ]


def test_a_source_known_late_moves_the_close_by_its_delay() -> None:
    """Every part known 15 minutes after its end: each hour still closes on its last part."""
    parts = [part(m, m, m, m, m, delay=15) for m in (0, 15, 30, 45, 60, 75)]
    built, _, builder = hourly(parts, until=240, delay=15)
    assert [(minute, bar.close) for minute, bar in built] == [(75, 45), (150, 75)]
    assert builder.late_bars == 0


def test_a_part_off_its_grid_or_repeated_is_refused() -> None:
    with pytest.raises(InvalidValueError, match="not on its own grid"):
        hourly([part(7, 1, 1, 1, 1)], until=120)
    with pytest.raises(InvalidValueError, match="two bars starting at"):
        hourly([part(0, 1, 1, 1, 1), part(0, 2, 2, 2, 2)], until=120)


def test_other_instruments_and_sizes_are_not_parts() -> None:
    parts = [
        part(0, 1, 1, 1, 1),
        part(0, 7, 7, 7, 7, instrument=ETH),
        part(0, 8, 8, 8, 8, size=5),
        part(45, 2, 2, 2, 2),
    ]
    built, _, _ = hourly(sorted(parts, key=lambda p: p.ts), until=120)
    assert [ohlcv(bar) for _, bar in built] == [(1, 2, 1, 2, 2.0)]


@pytest.mark.parametrize(
    ("bars", "finer", "reason"),
    [
        (BarSpec.of("1h"), 60, "cannot be built from bars"),
        (BarSpec.of("1h"), 7, "cannot be built from bars"),
        (BarSpec.of("1h", anchor=Timestamp(5 * MIN)), 15, "cannot be built from bars"),
        (BarSpec.of("1h", gaps="carry"), 15, "skip gaps"),
    ],
)
def test_bars_that_are_not_whole_groups_of_the_finer_cannot_be_built(
    bars: BarSpec, finer: int, reason: str
) -> None:
    w = world()
    with pytest.raises(InvalidValueError, match=reason):
        BarsFromBars(StreamSpec(instrument=BTC, bars=bars), finer * MIN, w.bus, w.engine.scheduler)


@given(
    st.lists(
        st.tuples(st.integers(0, 47), st.floats(1, 100), st.floats(0, 5)),
        unique_by=lambda t: t[0],
        max_size=40,
    )
)
def test_bars_equal_their_parts_grouped_by_hour(raw: list[tuple[int, float, float]]) -> None:
    """Parts on time, any subset of twelve hours of quarters: each hour that has one has a bar."""
    parts = [part(q * 15, p, p + 1, p - 1, p, v) for q, p, v in sorted(raw)]
    built, _, builder = hourly(parts, until=13 * 60)
    groups: defaultdict[int, list[Bar]] = defaultdict(list)
    for p in parts:
        groups[p.start // (60 * MIN)].append(p)
    assert [bar.start // (60 * MIN) for _, bar in built] == sorted(groups)
    for (minute, bar), (hour, group) in zip(built, sorted(groups.items()), strict=True):
        last = group[-1].end == (hour + 1) * 60 * MIN
        assert minute == (hour + 1) * 60 + (0 if last else 15)
        assert ohlcv(bar)[:4] == (
            group[0].open,
            max(p.high for p in group),
            min(p.low for p in group),
            group[-1].close,
        )
        assert bar.volume == pytest.approx(sum(p.volume for p in group))
    assert builder.late_bars == 0
