# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The strategy pipeline on the real engine, bus, bars and graph, with stand-in stages."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from heliograph.app.run import JOURNALLED
from heliograph.core.alarms import AlarmFired
from heliograph.core.bars import TimeBars
from heliograph.core.errors import InvalidValueError
from heliograph.core.graph import Graph
from heliograph.core.messages import Bar, Message, Point, RunEnded, Settled
from heliograph.core.pipeline import (
    CAP,
    Proposal,
    Signal,
    SignalRefused,
    Strategy,
    StrategySpec,
    signal_id,
)
from heliograph.core.streams import BarSpec, StreamSpec
from heliograph.core.time import NANOS_PER_HOUR, Timestamp
from heliograph.plugins.sqlite_journal import SqliteJournal, SqliteJournalParams, read_entries
from tests.support.world import BTC, World, points, world

HOUR = NANOS_PER_HOUR


@dataclass
class Fixed:
    """A graph node giving ``samples`` in turn, one per update, then the last forever."""

    samples: Sequence[float | None]
    arity: int = 1
    seen: int = 0

    def update(self, inputs: Sequence[float | None]) -> float | None:
        sample = self.samples[min(self.seen, len(self.samples) - 1)]
        self.seen += 1
        return sample


class Sum:
    def combine(self, forecasts: Mapping[str, float]) -> float:
        return sum(forecasts.values())


@dataclass
class Units:
    """A sizer: the forecast as units, once ``warm`` closes have been seen."""

    warm: int = 0
    seen: int = 0
    keep: Callable[[float, float], bool] = lambda wanted, held: False
    prices: list[float] = field(default_factory=list)

    def target(self, forecast: float | None, price: float, held: float) -> float | None:
        self.seen += 1
        self.prices.append(price)
        if forecast is None or self.seen <= self.warm:
            return None
        return held if self.keep(forecast, held) else forecast


@dataclass
class FlatBelow:
    """An exit: flat when the price is below ``level``."""

    level: float

    def adjust(
        self, target: float | None, held: float, price: float, values: Sequence[float | None]
    ) -> float | None:
        return 0.0 if target is not None and price < self.level else target


@dataclass
class Refuse:
    """A risk gate refusing targets above ``limit``; it records what it saw and what was sent."""

    limit: float
    seen: list[Proposal] = field(default_factory=list)
    sent_: list[Proposal] = field(default_factory=list)

    def check(self, proposal: Proposal) -> str | None:
        self.seen.append(proposal)
        return f"{proposal.target} is over {self.limit}" if proposal.target > self.limit else None

    def sent(self, proposal: Proposal) -> None:
        self.sent_.append(proposal)


def hourly_closes(prices: Sequence[float]) -> list[Point]:
    """One trade per hour, at minute 30, so the 1h bar closing at hour h+1 closes at prices[h]."""
    return points([(60 * h + 30, int(p)) for h, p in enumerate(prices)])


def setup(
    forecasts: Mapping[str, Sequence[float | None]],
    gates: Mapping[str, Sequence[float | None]] | None = None,
    **stages: object,
) -> tuple[World, Graph, Strategy, list[Message]]:
    w = world()
    TimeBars(StreamSpec(instrument=BTC, bars=BarSpec.of("1h")), w.bus, w.engine.scheduler)
    graph = Graph(w.bus)
    graph.bar_input("price", BTC, HOUR, "close")
    for name, samples in {**forecasts, **(gates or {})}.items():
        graph.add(name, Fixed(samples), ["price"])
    spec = StrategySpec(
        name="s",
        instrument=BTC,
        price="price",
        forecasters=tuple(forecasts),
        gates=tuple(gates or {}),
        combiner=stages.pop("combiner", Sum()),  # type: ignore[arg-type]
        sizer=stages.pop("sizer", Units()),  # type: ignore[arg-type]
        **stages,  # type: ignore[arg-type]
    )
    strategy = Strategy(spec, graph, w.bus)
    return w, graph, strategy, w.record(Signal, SignalRefused)


def run(w: World, prices: Sequence[float]) -> None:
    w.engine.run(hourly_closes(prices), until=Timestamp(len(prices) * HOUR))


def test_nothing_is_decided_until_every_forecaster_and_gate_has_a_sample() -> None:
    """f is ready at once, h at the second close, the gate only at the third."""
    w, _, strategy, seen = setup({"f": [5.0], "h": [None, 1.0]}, {"g": [None, None, 1.0]})
    run(w, [100, 101, 102])
    assert [(s.ts // HOUR, s.target) for s in seen] == [(3, 6.0)]  # type: ignore[attr-defined]
    assert strategy.held == 6.0


def test_a_signal_says_what_to_hold_and_why() -> None:
    w, _, _, seen = setup({"a": [3.0], "b": [4.0]})
    run(w, [100])
    (signal,) = seen
    assert isinstance(signal, Signal)
    assert (signal.side, signal.target, signal.held, signal.price) == ("long", 7.0, 0.0, 100.0)
    assert signal.forecast == 7.0
    assert dict(signal.forecasts) == {"a": 3.0, "b": 4.0}
    assert signal.instrument == BTC
    assert signal.id == signal_id("", "s", signal.ts, 7.0)


def test_forecasts_are_capped_each_and_once_combined() -> None:
    w, _, _, seen = setup({"a": [50.0], "b": [-30.0], "c": [5.0]})
    run(w, [100])
    (signal,) = seen
    assert dict(signal.forecasts) == {"a": CAP, "b": -CAP, "c": 5.0}  # type: ignore[attr-defined]
    assert signal.forecast == 5.0  # type: ignore[attr-defined]
    w, _, _, seen = setup({"a": [15.0], "b": [15.0]})
    run(w, [100])
    assert seen[0].forecast == CAP  # type: ignore[attr-defined]


def test_a_gate_at_or_below_zero_vetoes_and_the_strategy_goes_flat() -> None:
    w, _, strategy, seen = setup({"f": [8.0]}, {"open": [1.0, 0.0, 1.0], "calm": [1.0]})
    run(w, [100, 100, 100])
    assert [(s.target, s.vetoed_by) for s in seen] == [  # type: ignore[attr-defined]
        (8.0, ()),
        (0.0, ("open",)),
        (8.0, ()),
    ]
    assert seen[1].side == "flat"  # type: ignore[attr-defined]
    assert [s.forecast for s in seen] == [8.0, 0.0, 8.0]  # type: ignore[attr-defined]
    assert strategy.held == 8.0


def test_no_signal_while_the_sizer_keeps_the_position_held() -> None:
    sizer = Units(keep=lambda wanted, held: abs(wanted - held) < 2)
    w, _, _, seen = setup({"f": [5.0, 6.0, 9.0]}, sizer=sizer)
    run(w, [100, 100, 100])
    assert [s.target for s in seen] == [5.0, 9.0]  # type: ignore[attr-defined]


def test_the_sizer_sees_every_close_even_while_forecasts_warm_up() -> None:
    sizer = Units()
    w, _, _, _ = setup({"f": [None, None, 1.0]}, sizer=sizer)
    run(w, [100, 101, 102])
    assert sizer.prices == [100.0, 101.0, 102.0]


def test_an_exit_overrides_the_target_and_is_named() -> None:
    w, _, _, seen = setup({"f": [5.0]}, exits=(("floor", FlatBelow(100.0), ()),))
    run(w, [101, 99])
    assert [(s.target, s.exit) for s in seen] == [(5.0, ""), (0.0, "floor")]  # type: ignore[attr-defined]


def test_a_refused_signal_is_journalled_and_the_position_held_does_not_change() -> None:
    first, second = Refuse(limit=100.0), Refuse(limit=6.0)
    gates = (("first", first), ("second", second), ("third", Refuse(limit=0.0)))
    w, _, strategy, seen = setup({"f": [7.0]}, risk_gates=gates)
    run(w, [100])
    (refused,) = seen
    assert isinstance(refused, SignalRefused)
    assert (refused.refused_by, refused.target, refused.held) == ("second", 7.0, 0.0)
    assert refused.reason == "7.0 is over 6.0"
    assert strategy.held == 0.0
    assert [p.target for p in first.seen] == [7.0]
    assert gates[2][1].seen == []  # the gates after a refusal never see it
    assert first.sent_ == []


def test_decisions_are_made_only_at_closes_of_the_price_stream() -> None:
    """A strategy on 4h closes reading a 1h forecaster: two decisions in 8 hours, not 8."""
    w = world()
    for size in ("1h", "4h"):
        TimeBars(StreamSpec(instrument=BTC, bars=BarSpec.of(size)), w.bus, w.engine.scheduler)
    graph = Graph(w.bus)
    graph.bar_input("hourly", BTC, HOUR, "close")
    graph.bar_input("price", BTC, 4 * HOUR, "close")
    graph.add("f", Fixed([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]), ["hourly"])
    sizer = Units()
    Strategy(
        StrategySpec(
            name="s", instrument=BTC, price="price", forecasters=("f",), combiner=Sum(), sizer=sizer
        ),
        graph,
        w.bus,
    )
    seen = w.record(Signal)
    run(w, [100] * 8)
    assert sizer.seen == 2
    assert [(s.ts // HOUR, s.target) for s in seen] == [(4, 4.0), (8, 8.0)]  # type: ignore[attr-defined]


def test_the_same_decision_has_the_same_id_and_runs_have_their_own() -> None:
    assert signal_id("run", "s", 5, 1.5) == signal_id("run", "s", 5, 1.5)
    assert signal_id("run", "s", 5, 0.0) == signal_id("run", "s", 5, -0.0)
    ids = {
        signal_id("run", "s", 5, 1.5),
        signal_id("run", "s", 6, 1.5),
        signal_id("run", "t", 5, 1.5),
        signal_id("other", "s", 5, 1.5),
    }
    assert len(ids) == 4


class Identity:
    """The one forecast as it is, sign of zero included (sum() would make -0.0 0.0)."""

    def combine(self, forecasts: Mapping[str, float]) -> float:
        (forecast,) = forecasts.values()
        return forecast


def test_a_flat_target_is_never_minus_zero() -> None:
    w, _, _, seen = setup({"f": [3.0, -0.0]}, combiner=Identity())
    run(w, [100, 100])
    assert [repr(s.target) for s in seen] == ["3.0", "0.0"]  # type: ignore[attr-defined]


def test_taking_risk_off_never_goes_to_the_risk_gates() -> None:
    """Long 7, then flat, then smaller, then reversed: only adding risk is checked."""
    gate = Refuse(limit=-100.0)  # refuses every proposal it is shown
    w, _, strategy, seen = setup({"f": [7.0, 0.0, 7.0]}, risk_gates=(("all", gate),))
    run(w, [100, 100, 100])
    assert [type(m).__name__ for m in seen] == ["SignalRefused", "SignalRefused"]
    assert [p.target for p in gate.seen] == [7.0, 7.0]
    gate = Refuse(limit=5.0)
    w, _, strategy, seen = setup({"f": [4.0, 2.0, 0.0, -1.0]}, risk_gates=(("cap", gate),))
    run(w, [100, 100, 100, 100])
    assert [m.target for m in seen] == [4.0, 2.0, 0.0, -1.0]  # type: ignore[attr-defined]
    assert [p.target for p in gate.seen] == [4.0, -1.0]  # 2 and 0 only took risk off
    assert [p.target for p in gate.sent_] == [4.0, 2.0, 0.0, -1.0]
    assert strategy.held == -1.0


def test_risk_gates_hear_only_of_signals_sent_and_see_the_previous_close() -> None:
    first, second = Refuse(limit=100.0), Refuse(limit=6.0)
    w, _, _, _ = setup({"f": [7.0, 5.0]}, risk_gates=(("first", first), ("second", second)))
    run(w, [100, 103])
    assert [p.target for p in first.seen] == [7.0, 5.0]
    assert [p.target for p in first.sent_] == [5.0]  # 7 was refused by the second gate
    assert [(p.price, p.previous) for p in first.seen] == [(100.0, None), (103.0, 100.0)]


def test_a_veto_takes_the_strategy_flat_whatever_the_sizers_buffer() -> None:
    # Once long, a buffer of 1 would keep 0.5 against a wanted 0: the veto overrides it.
    sizer = Units(keep=lambda wanted, held: held != 0 and abs(wanted - held) <= 1)
    w, _, strategy, seen = setup({"f": [0.5]}, {"g": [1.0, 0.0]}, sizer=sizer)
    run(w, [100, 100])
    assert [(s.target, s.vetoed_by) for s in seen] == [(0.5, ()), (0.0, ("g",))]  # type: ignore[attr-defined]
    assert strategy.held == 0.0


class Nan:
    def combine(self, forecasts: Mapping[str, float]) -> float:
        return float("nan")


def test_a_combined_forecast_that_is_not_finite_stops_the_run() -> None:
    w, _, _, _ = setup({"f": [-15.0]}, combiner=Nan())
    with pytest.raises(InvalidValueError, match="combined its forecasts to nan"):
        run(w, [100])


def test_signals_are_journalled_and_read_back_equal(tmp_path: Path) -> None:
    path = tmp_path / "run.sqlite"
    with SqliteJournal(SqliteJournalParams(path=str(path))) as journal:
        w = world(journal=journal)
        TimeBars(StreamSpec(instrument=BTC, bars=BarSpec.of("1h")), w.bus, w.engine.scheduler)
        graph = Graph(w.bus)
        graph.bar_input("price", BTC, HOUR, "close")
        graph.add("f", Fixed([3.0, -2.0]), ["price"])
        spec = StrategySpec(
            name="s",
            instrument=BTC,
            price="price",
            forecasters=("f",),
            combiner=Sum(),
            sizer=Units(),
            risk_gates=(("cap", Refuse(limit=2.5)),),
        )
        Strategy(spec, graph, w.bus)
        seen = w.record(Signal, SignalRefused)
        run(w, [100, 101])
    entries = list(read_entries(path, (*JOURNALLED, AlarmFired, Point, Bar, Settled, RunEnded)))
    back = [e.message for e in entries if isinstance(e.message, Signal | SignalRefused)]
    assert back == seen
    assert [type(m).__name__ for m in back] == ["SignalRefused", "Signal"]
    assert [e.origin for e in entries if e.message in back] == ["derived", "derived"]


def test_a_strategy_reading_what_the_graph_lacks_is_refused() -> None:
    w = world()
    graph = Graph(w.bus)
    graph.bar_input("price", BTC, HOUR, "close")
    spec = StrategySpec(
        name="s", instrument=BTC, price="price", forecasters=("f",), combiner=Sum(), sizer=Units()
    )
    with pytest.raises(InvalidValueError, match="strategy s reads f, not in the graph"):
        Strategy(spec, graph, w.bus)
    spec = StrategySpec(
        name="s", instrument=BTC, price="price", forecasters=(), combiner=Sum(), sizer=Units()
    )
    with pytest.raises(InvalidValueError, match="needs at least one forecaster"):
        Strategy(spec, graph, w.bus)


def test_a_target_that_is_not_finite_stops_the_run() -> None:
    sizer = Units()
    sizer.target = lambda forecast, price, held: float("inf")  # type: ignore[method-assign]
    w, _, _, _ = setup({"f": [1.0]}, sizer=sizer)
    with pytest.raises(InvalidValueError, match="sized a target of inf"):
        run(w, [100])


def test_a_signal_with_a_value_that_is_not_finite_is_refused_as_it_is_made() -> None:
    with pytest.raises(InvalidValueError, match=r"Signal\.price must be finite"):
        Signal(
            ts=Timestamp(0),
            id="x",
            strategy="s",
            instrument=BTC,
            side="flat",
            target=0.0,
            held=0.0,
            price=float("nan"),
            forecast=0.0,
        )
