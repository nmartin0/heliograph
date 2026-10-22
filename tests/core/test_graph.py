# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The indicator graph on the real engine, bus and bar builders."""

from collections import deque
from collections.abc import Sequence
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from heliograph.core.alarms import AlarmFired
from heliograph.core.bars import TimeBars
from heliograph.core.errors import InvalidValueError
from heliograph.core.graph import Graph, IndicatorValues
from heliograph.core.messages import Bar, Point, RunEnded, Settled
from heliograph.core.streams import BarSpec, StreamSpec
from heliograph.core.time import NANOS_PER_HOUR, Timestamp
from heliograph.plugins.sqlite_journal import SqliteJournal, SqliteJournalParams, read_entries
from tests.support.world import BTC, ETH, World, points, world

HOUR = NANOS_PER_HOUR
ENGINE = (AlarmFired, Settled, RunEnded)


class Mean:
    """The mean of the last ``n`` samples of one input."""

    arity = 1

    def __init__(self, n: int) -> None:
        self.window: deque[float] = deque(maxlen=n)

    def update(self, inputs: Sequence[float | None]) -> float | None:
        (value,) = inputs
        assert value is not None
        self.window.append(value)
        return (
            sum(self.window) / len(self.window) if len(self.window) == self.window.maxlen else None
        )


class Difference:
    """Its first input less its second, once both have a sample."""

    arity = 2

    def __init__(self) -> None:
        self.calls = 0

    def update(self, inputs: Sequence[float | None]) -> float | None:
        self.calls += 1
        first, second = inputs
        return None if first is None or second is None else first - second


class Silent:
    """Never produces a sample."""

    arity = 1

    def update(self, inputs: Sequence[float | None]) -> float | None:
        return None


def hourly(w: World, *sizes: str) -> None:
    for size in sizes:
        TimeBars(StreamSpec(instrument=BTC, bars=BarSpec.of(size)), w.bus, w.engine.scheduler)


def values(seen: Sequence[object]) -> list[IndicatorValues]:
    return [m for m in seen if isinstance(m, IndicatorValues)]


def test_an_indicator_takes_every_close_as_a_sample_even_one_equal_to_the_last() -> None:
    """Closes 100, 100, 100, 106: the mean of three needs all four bars."""
    w = world()
    hourly(w, "1h")
    graph = Graph(w.bus)
    graph.bar_input("close", BTC, HOUR, "close")
    graph.add("mean3", Mean(3), ["close"])
    seen = w.record(IndicatorValues)
    w.engine.run(points([(30, 100), (90, 100), (150, 100), (210, 106)]), until=Timestamp(4 * HOUR))
    out = values(seen)
    assert [(v.ts // HOUR, v.values["mean3"]) for v in out] == [(3, 100.0), (4, 102.0)]
    assert all(v.changed == ("mean3",) for v in out)


def test_a_1h_indicator_reading_a_4h_value_sees_it_at_its_end_and_holds_it_until_the_next() -> None:
    """At 04:00 both bars close: the difference uses the 4h close of that same instant."""
    w = world()
    hourly(w, "1h", "4h")
    graph = Graph(w.bus)
    graph.bar_input("close_1h", BTC, HOUR, "close")
    graph.bar_input("close_4h", BTC, 4 * HOUR, "close")
    difference = Difference()
    graph.add("diff", difference, ["close_1h", "close_4h"])
    seen = w.record(IndicatorValues)
    trades = [(m, 100 + m // 60) for m in range(30, 8 * 60, 60)]  # one trade an hour, rising
    w.engine.run(points(trades), until=Timestamp(8 * HOUR))
    out = {v.ts // HOUR: v.values["diff"] for v in values(seen)}
    # 1h closes are 100..107; the 4h close is 103 at 04:00 and 107 at 08:00.
    assert out == {4: 0.0, 5: 1.0, 6: 2.0, 7: 3.0, 8: 0.0}
    assert difference.calls == 8  # once per instant, though two inputs changed at 04:00


def test_the_graph_stabilises_after_every_bar_of_the_instant_whatever_was_subscribed_first() -> (
    None
):
    """The graph subscribed before the bar builders: the result is the same."""
    out = []
    for graph_first in (True, False):
        w = world()
        # The 4h builder subscribed before the 1h one, or after: at 04:00 the bars then
        # arrive in either order, and an eager graph would see the 4h close late.
        if graph_first:
            graph = Graph(w.bus)
            hourly(w, "4h", "1h")
        else:
            hourly(w, "1h", "4h")
            graph = Graph(w.bus)
        graph.bar_input("close_1h", BTC, HOUR, "close")
        graph.bar_input("close_4h", BTC, 4 * HOUR, "close")
        graph.add("diff", Difference(), ["close_1h", "close_4h"])
        seen = w.record(IndicatorValues)
        w.engine.run(
            points([(m, 100 + m // 60) for m in range(30, 480, 60)]), until=Timestamp(8 * HOUR)
        )
        out.append([(v.ts, dict(v.values)) for v in values(seen)])
    assert out[0] == out[1]


def test_an_indicator_without_a_sample_leaves_what_reads_it_untouched() -> None:
    w = world()
    hourly(w, "1h")
    graph = Graph(w.bus)
    graph.bar_input("close", BTC, HOUR, "close")
    graph.add("silent", Silent(), ["close"])
    reader = Mean(1)
    graph.add("after", reader, ["silent"])
    seen = w.record(IndicatorValues)
    w.engine.run(points([(30, 100), (90, 101)]), until=Timestamp(2 * HOUR))
    assert values(seen) == []
    assert len(reader.window) == 0
    assert graph.value("silent") is None


def test_values_hold_every_indicators_latest_sample_and_changed_names_only_the_new() -> None:
    w = world()
    hourly(w, "1h", "4h")
    graph = Graph(w.bus)
    graph.bar_input("close_1h", BTC, HOUR, "close")
    graph.bar_input("close_4h", BTC, 4 * HOUR, "close")
    graph.add("fast", Mean(1), ["close_1h"])
    graph.add("slow", Mean(1), ["close_4h"])
    seen = w.record(IndicatorValues)
    w.engine.run(
        points([(m, 100 + m // 60) for m in range(30, 300, 60)]), until=Timestamp(5 * HOUR)
    )
    by_hour = {v.ts // HOUR: v for v in values(seen)}
    assert by_hour[4].changed == ("fast", "slow")
    assert by_hour[5].changed == ("fast",)
    assert dict(by_hour[5].values) == {"fast": 104.0, "slow": 103.0}


def test_bars_of_another_instrument_or_size_do_not_feed_an_input() -> None:
    w = world()
    for size in ("1h", "2h"):
        TimeBars(StreamSpec(instrument=BTC, bars=BarSpec.of(size)), w.bus, w.engine.scheduler)
    graph = Graph(w.bus)
    graph.bar_input("eth", ETH, HOUR, "close")
    graph.bar_input("btc_2h", BTC, 2 * HOUR, "close")
    graph.add("eth_mean", Mean(1), ["eth"])
    graph.add("btc_mean", Mean(1), ["btc_2h"])
    seen = w.record(IndicatorValues)
    w.engine.run(points([(30, 100), (90, 102)]), until=Timestamp(2 * HOUR))
    (only,) = values(seen)
    assert dict(only.values) == {"btc_mean": 102.0}


def test_a_graph_that_cannot_be_right_is_refused_as_it_is_built() -> None:
    graph = Graph(world().bus)
    graph.bar_input("close", BTC, HOUR, "close")
    with pytest.raises(InvalidValueError, match="reads later, not yet defined"):
        graph.add("early", Mean(2), ["later"])
    with pytest.raises(InvalidValueError, match="reads 2 inputs, but its indicator takes 1"):
        graph.add("two", Mean(2), ["close", "close"])
    with pytest.raises(InvalidValueError, match="'close' is empty or taken"):
        graph.add("close", Mean(2), ["close"])
    with pytest.raises(InvalidValueError, match="close is already close"):
        graph.bar_input("again", BTC, HOUR, "close")
    with pytest.raises(InvalidValueError, match="no bar field 'vwap'"):
        graph.bar_input("vwap", BTC, HOUR, "vwap")  # type: ignore[arg-type]


def test_a_sample_that_is_not_a_finite_number_stops_the_run() -> None:
    class Broken:
        arity = 1

        def update(self, inputs: Sequence[float | None]) -> float | None:
            return float("nan")

    w = world()
    hourly(w, "1h")
    graph = Graph(w.bus)
    graph.bar_input("close", BTC, HOUR, "close")
    graph.add("broken", Broken(), ["close"])
    with pytest.raises(InvalidValueError, match="broken produced nan"):
        w.engine.run(points([(30, 100)]), until=Timestamp(HOUR))


@settings(max_examples=50, deadline=None)
@given(st.lists(st.tuples(st.integers(0, 600), st.integers(90, 120)), min_size=1, max_size=60))
def test_no_indicator_ever_sees_a_bar_before_it_closes(raw: list[tuple[int, int]]) -> None:
    """At every instant the mean equals the mean of the closes of bars ended by then."""
    w = world()
    hourly(w, "1h")
    graph = Graph(w.bus)
    graph.bar_input("close", BTC, HOUR, "close")
    graph.add("mean2", Mean(2), ["close"])
    seen = w.record(IndicatorValues, Bar)
    w.engine.run(points(raw), until=Timestamp(11 * HOUR))
    for v in values(seen):
        closed = [m.close for m in seen if isinstance(m, Bar) and m.end <= v.ts]
        assert v.values["mean2"] == sum(closed[-2:]) / 2


def test_indicator_values_are_journalled_and_read_back(tmp_path: Path) -> None:
    path = tmp_path / "run.sqlite"
    with SqliteJournal(SqliteJournalParams(path=str(path))) as journal:
        w = world(journal=journal)
        hourly(w, "1h")
        graph = Graph(w.bus)
        graph.bar_input("close", BTC, HOUR, "close")
        graph.add("mean1", Mean(1), ["close"])
        w.engine.run(points([(30, 100), (90, 101)]), until=Timestamp(2 * HOUR))
    entries = list(read_entries(path, (Point, Bar, IndicatorValues, *ENGINE)))
    got = [e.message for e in entries if isinstance(e.message, IndicatorValues)]
    assert [(v.ts // HOUR, dict(v.values), v.changed) for v in got] == [
        (1, {"mean1": 100.0}, ("mean1",)),
        (2, {"mean1": 101.0}, ("mean1",)),
    ]
    origins = [e.origin for e in entries if isinstance(e.message, IndicatorValues)]
    assert origins == ["derived", "derived"]


def test_two_bars_of_one_input_known_at_one_instant_are_refused_not_one_dropped() -> None:
    """A vendor's late bar and a fresh one, both known at 02:00."""
    w = world()
    graph = Graph(w.bus)
    graph.bar_input("close", BTC, HOUR, "close")
    graph.add("mean2", Mean(2), ["close"])

    def bar(start: int, close: float, received: int | None = None) -> Bar:
        known = None if received is None else Timestamp(received * HOUR)
        return Bar(
            instrument=BTC,
            start=Timestamp(start * HOUR),
            end=Timestamp((start + 1) * HOUR),
            open=close,
            high=close,
            low=close,
            close=close,
            volume=1,
            received_at=known,
        )

    with pytest.raises(InvalidValueError, match="two 1h bars of kraken:BTC/USD known at"):
        w.engine.run([bar(0, 100, received=2), bar(1, 110)], until=Timestamp(2 * HOUR))


def test_observers_hear_once_per_instant_which_nodes_took_a_sample_inputs_included() -> None:
    w = world()
    hourly(w, "1h")
    graph = Graph(w.bus)
    graph.bar_input("close", BTC, HOUR, "close")
    graph.add("mean2", Mean(2), ["close"])
    heard: list[tuple[str, int, frozenset[str], float | None]] = []
    graph.observe(
        lambda ts, changed: heard.append(("first", ts // HOUR, changed, graph.value("mean2")))
    )
    graph.observe(lambda ts, changed: heard.append(("second", ts // HOUR, changed, None)))
    w.engine.run(points([(30, 100), (90, 102)]), until=Timestamp(2 * HOUR))
    assert heard == [
        ("first", 1, frozenset({"close"}), None),
        ("second", 1, frozenset({"close"}), None),
        ("first", 2, frozenset({"close", "mean2"}), 101.0),
        ("second", 2, frozenset({"close", "mean2"}), None),
    ]
    assert graph.has("mean2")
    assert not graph.has("nothing")
