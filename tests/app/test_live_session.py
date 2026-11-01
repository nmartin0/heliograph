# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""A live session from its configuration: journalled as it goes, and stopped cleanly."""

import asyncio
import json
import shutil
import sqlite3
from collections.abc import AsyncIterator, Callable, Sequence
from pathlib import Path
from typing import ClassVar

import pytest

from heliograph.app.answers import inbox_for, order, submit
from heliograph.app.config import ConfigError, parse_config
from heliograph.app.run import JOURNALLED, replay_backtest, run_backtest, run_live_session
from heliograph.core.answers import SignalAnswer, SignalExpired
from heliograph.core.errors import HeliographError, InvalidValueError
from heliograph.core.graph import IndicatorValues
from heliograph.core.halt import Cleared, Halted
from heliograph.core.messages import Bar, Message, Point, RunEnded
from heliograph.core.pipeline import Signal, SignalRefused
from heliograph.core.sinks import Delivered, Delivery, SinkUnavailableError
from heliograph.core.sources import (
    Capabilities,
    Health,
    HistoryRequest,
    SourceUnavailableError,
    Subscription,
    Throttle,
)
from heliograph.core.time import NANOS_PER_HOUR, Timestamp, from_iso
from heliograph.plugins.manifest import Manifest, NoParams, Params, Plugin, param
from heliograph.plugins.notify_sinks import Webhook
from heliograph.plugins.registry import Registry
from heliograph.plugins.sqlite_journal import raw_rows, read_entries, read_header
from tests.support.simulated import SimulatedTime

NOW = from_iso("2026-10-21T00:00:00Z")
LIVE = """
mode = "live"

[mounts.btc]
source = "pulse"
kind = "trades"

[mounts.btc.instrument]
symbol = "BTC/USD"
venue = "kraken"
asset_class = "crypto"
quote_currency = "USD"

[[streams]]
mount = "btc"
bars = "1h"
"""


class Pulse(Plugin[NoParams]):
    """A live source that sends a trade, lets an hour pass on the test's clock, and repeats."""

    manifest = Manifest(kind="data_sources", name="pulse", version="1", summary="Live trades")
    time: ClassVar[SimulatedTime]
    steps: ClassVar[int] = 3
    prices: ClassVar[Sequence[float] | None] = None  # default: 100, 101, 102...
    then: ClassVar[str] = "end"  # "end", "wait" for a stop, or "fail"
    closed: ClassVar[int] = 0
    gate: ClassVar["asyncio.Queue[None] | None"] = None  # each trade waits for a permit
    start_at: ClassVar[int] = 0  # the trade to begin with (a resumed session carries on)

    @property
    def capabilities(self) -> Capabilities:
        return Capabilities(live=frozenset({"trades"}))

    def bind(self, throttle: Throttle) -> None:
        pass

    async def history(self, request: HistoryRequest) -> AsyncIterator[Message]:
        return
        yield

    async def subscribe(self, subscriptions: Sequence[Subscription]) -> AsyncIterator[Message]:
        instrument = subscriptions[0].instrument
        for step in range(Pulse.start_at, self.steps):
            if Pulse.gate is not None:
                await Pulse.gate.get()
            await Pulse.time.advance(10 * 60 if step == 0 else 60 * 60)
            at = Pulse.time.clock.now()
            yield Point(
                instrument=instrument,
                event_time=at,
                received_at=at,
                values={"price": self._price(step), "size": 1.0},
            )
            for _ in range(5):  # let the session take it before the clock moves on
                await asyncio.sleep(0)
        if Pulse.then == "wait":
            await asyncio.Event().wait()
        if Pulse.then == "fail":
            raise SourceUnavailableError("the exchange went away")

    def _price(self, step: int) -> float:
        return 100.0 + step if Pulse.prices is None else Pulse.prices[step]

    async def health_check(self) -> Health:
        return Health(ok=True)

    async def close(self) -> None:
        Pulse.closed += 1


@pytest.fixture
def time() -> SimulatedTime:
    simulated = SimulatedTime(start=NOW)
    Pulse.time, Pulse.steps, Pulse.then, Pulse.closed, Pulse.prices = simulated, 3, "end", 0, None
    Pulse.gate = None
    Pulse.start_at = 0
    return simulated


@pytest.fixture
def registry() -> Registry:
    registry = Registry()
    for kind in ("forecasters", "combiners", "sizers"):
        registry.discover(kind)
    registry.register(Pulse)
    return registry


def session(text: str, registry: Registry, journal: Path, time: SimulatedTime):  # type: ignore[no-untyped-def]
    return run_live_session(parse_config(text), registry, journal, time.clock, time.advance)


def test_a_live_session_journals_what_it_read_and_ends_marked_complete(
    registry: Registry, tmp_path: Path, time: SimulatedTime
) -> None:
    journal = tmp_path / "live.sqlite"
    summary = asyncio.run(session(LIVE, registry, journal, time))
    assert summary.delivered == 3
    header = read_header(journal)
    assert header["status"] == "complete"
    assert header["kind"] == "live"
    assert "until" not in header
    entries = list(read_entries(journal, JOURNALLED))
    assert isinstance(entries[-1].message, RunEnded)
    points = [e.message for e in entries if e.origin == "input"]
    assert [p.values["price"] for p in points if isinstance(p, Point)] == [100.0, 101.0, 102.0]
    # bars close by alarms as time passes (the timekeeper moves this test's clock too)
    closed = [e.message for e in entries if isinstance(e.message, Bar)]
    assert len(closed) == 2
    assert closed[0].end == Timestamp(NOW + NANOS_PER_HOUR)
    assert Pulse.closed == 1


def test_a_stopped_session_leaves_a_complete_journal_and_closed_sources(
    registry: Registry, tmp_path: Path, time: SimulatedTime
) -> None:
    Pulse.then = "wait"
    journal = tmp_path / "live.sqlite"

    async def main() -> None:
        task = asyncio.create_task(session(LIVE, registry, journal, time))
        while not journal.exists() or len(list(read_entries(journal, JOURNALLED))) < 3:
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(asyncio.wait_for(main(), timeout=2))  # a journal not written as it goes hangs
    assert read_header(journal)["status"] == "complete"
    assert isinstance(list(read_entries(journal, JOURNALLED))[-1].message, RunEnded)
    assert Pulse.closed == 1


def test_a_failed_session_is_marked_failed_and_its_sources_closed(
    registry: Registry, tmp_path: Path, time: SimulatedTime
) -> None:
    Pulse.then = "fail"
    journal = tmp_path / "live.sqlite"
    with pytest.raises(SourceUnavailableError, match="went away"):
        asyncio.run(session(LIVE, registry, journal, time))
    assert read_header(journal)["status"].startswith("failed: SourceUnavailableError")
    assert Pulse.closed == 1


def test_a_session_does_not_overwrite_a_journal(
    registry: Registry, tmp_path: Path, time: SimulatedTime
) -> None:
    journal = tmp_path / "live.sqlite"
    journal.write_text("")
    with pytest.raises(InvalidValueError, match="a run writes a new journal"):
        asyncio.run(session(LIVE, registry, journal, time))


def test_a_backtest_configuration_is_not_a_live_session(
    registry: Registry, tmp_path: Path, time: SimulatedTime
) -> None:
    with pytest.raises(ConfigError, match="is a backtest; run_live_session runs live ones"):
        asyncio.run(
            session(
                LIVE.replace('mode = "live"', 'mode = "backtest"').replace(
                    'kind = "trades"',
                    'kind = "trades"\nstart = 2026-10-01T00:00:00Z\nend = 2026-10-02T00:00:00Z',
                ),
                registry,
                tmp_path / "j",
                time,
            )
        )


def test_a_live_configuration_is_not_a_backtest(
    registry: Registry, tmp_path: Path, time: SimulatedTime
) -> None:
    with pytest.raises(ConfigError, match="is live; run_backtest runs backtests"):
        run_backtest(parse_config(LIVE), registry, tmp_path / "j", time.clock, time.advance)


# -- signals reach the sinks ---------------------------------------------------------------

STRATEGY = (
    LIVE
    + """
[[components]]
kind = "forecasters"
plugin = "ewmac"
name = "fast"
inputs = ["btc@1h.close"]
params = { fast = 2, slow = 6, scalar = 1.0 }

[[components]]
kind = "combiners"
plugin = "weighted"
name = "only"
params = { weights = { fast = 1.0 }, multiplier = 1.0 }

[[components]]
kind = "sizers"
plugin = "vol_target"
name = "vol"
params = { capital = 10000.0, target = 0.25, bars_per_year = 8760.0, vol_span = 4, buffer = 0.0 }

[[strategies]]
name = "trend"
price = "btc@1h.close"
forecasters = ["fast"]
combiner = "only"
sizer = "vol"
"""
)
SINKS = """
[sinks.phone]
plugin = "recorder"
params = { name = "phone" }

[sinks.log]
plugin = "recorder"
params = { name = "log", fail_first = 2 }
"""
UP_THEN_DOWN = [100.0 + 3 * i for i in range(14)] + [142.0 - 4 * i for i in range(14)]


class RecorderParams(Params):
    name: str = param("sink", description="What it calls itself in the record")
    fail_first: int = param(0, description="Asks it is unavailable for first", ge=0)
    hang: bool = param(False, description="Never answers")


class Recorder(Plugin[RecorderParams]):
    """A sink that keeps what it is given, and is down for its first few asks."""

    manifest = Manifest(
        kind="sinks", name="recorder", version="1", summary="Keeps signals", params=RecorderParams
    )
    got: ClassVar[list[tuple[str, str]]] = []
    cancelled: ClassVar[int] = 0
    turns: ClassVar[int] = 3

    def __init__(self, params: RecorderParams) -> None:
        super().__init__(params)
        self.asks = 0

    async def emit(self, signal: Signal) -> Delivery:
        if self.params.hang:
            try:
                await asyncio.Event().wait()
            finally:
                Recorder.cancelled += 1
        for _ in range(Recorder.turns):  # a delivery takes a few turns of the loop
            await asyncio.sleep(0)
        self.asks += 1
        if self.asks <= self.params.fail_first:
            raise SinkUnavailableError("down for now")
        Recorder.got.append((self.params.name, signal.id))
        return Delivery(True, "kept")

    async def health_check(self) -> Health:
        return Health(ok=True)

    async def close(self) -> None:
        pass


@pytest.fixture
def with_sinks(registry: Registry) -> Registry:
    registry.register(Recorder)
    Recorder.got.clear()
    Recorder.cancelled = 0
    Recorder.turns = 3
    return registry


def strategy_session(
    sinks: str, registry: Registry, tmp_path: Path, time: SimulatedTime, grace: float = 10.0
) -> Path:
    Pulse.prices = UP_THEN_DOWN
    Pulse.steps = len(UP_THEN_DOWN)
    journal = tmp_path / "live.sqlite"
    text = STRATEGY + sinks
    run = run_live_session(
        parse_config(text), registry, journal, time.clock, time.advance, grace=grace
    )
    asyncio.run(run)
    return journal


def deliveries(journal: Path) -> list[Delivered]:
    return [
        e.message for e in read_entries(journal, JOURNALLED) if isinstance(e.message, Delivered)
    ]


def signals(journal: Path) -> list[Signal]:
    return [e.message for e in read_entries(journal, JOURNALLED) if isinstance(e.message, Signal)]


def test_each_signal_reaches_every_sink_and_every_outcome_is_journalled(
    with_sinks: Registry, tmp_path: Path, time: SimulatedTime
) -> None:
    journal = strategy_session(SINKS, with_sinks, tmp_path, time)
    made = signals(journal)
    assert len(made) >= 2  # up, then down
    ids = [s.id for s in made]
    assert sorted(i for n, i in Recorder.got if n == "phone") == sorted(ids)
    assert sorted(i for n, i in Recorder.got if n == "log") == sorted(ids)
    first = [d for d in deliveries(journal) if d.signal == ids[0]]
    # journalled as each sink finishes: phone at once, log after its two failures
    assert [(d.sink, d.ok, d.attempts) for d in first] == [("phone", True, 1), ("log", True, 3)]
    assert len(deliveries(journal)) == 2 * len(ids)


def test_the_header_describes_the_sinks_as_the_run_built_them(
    with_sinks: Registry, tmp_path: Path, time: SimulatedTime
) -> None:
    journal = strategy_session(SINKS, with_sinks, tmp_path, time)
    described = json.loads(read_header(journal)["sinks"])
    assert described["log"] == {
        "plugin": "recorder",
        "version": "1",
        "params": {"name": "log", "fail_first": 2, "hang": False},
    }
    assert set(described) == {"log", "phone"}


def test_a_session_without_sinks_records_none(
    with_sinks: Registry, tmp_path: Path, time: SimulatedTime
) -> None:
    journal = strategy_session("", with_sinks, tmp_path, time)
    assert "sinks" not in read_header(journal)
    assert deliveries(journal) == []
    assert Recorder.got == []


def test_deliveries_still_under_way_when_the_session_ends_are_recorded_as_not_delivered(
    with_sinks: Registry, tmp_path: Path, time: SimulatedTime
) -> None:
    hung = (
        SINKS
        + """
[sinks.stuck]
plugin = "recorder"
params = { name = "stuck", hang = true }
"""
    )
    journal = strategy_session(hung, with_sinks, tmp_path, time, grace=0.05)
    ids = [s.id for s in signals(journal)]
    undelivered = [d for d in deliveries(journal) if not d.ok]
    assert {d.sink for d in undelivered} == {"stuck"}  # the others' news did not wait for it
    assert {d.signal for d in undelivered} == set(ids)
    assert {d.sink for d in deliveries(journal) if d.ok} == {"log", "phone"}
    assert all(d.attempts == 0 for d in undelivered)
    assert all("session ended first" in d.detail for d in undelivered)
    assert Recorder.cancelled == len(ids)  # every stuck delivery was stopped, none left running
    # nothing is recorded twice for one signal and sink
    pairs = [(d.signal, d.sink) for d in deliveries(journal)]
    assert len(pairs) == len(set(pairs))
    assert {s for s, _ in pairs} <= set(ids)


def test_a_sink_that_names_no_plug_in_or_cannot_be_built_is_reported_with_the_others(
    with_sinks: Registry, tmp_path: Path, time: SimulatedTime, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = """
[sinks.nowhere]
plugin = "no_such_sink"

[sinks.bad]
plugin = "recorder"
params = { fail_first = -1 }
"""
    with pytest.raises(ConfigError) as caught:
        strategy_session(bad, with_sinks, tmp_path, time)
    message = str(caught.value)
    assert "sinks.nowhere (no_such_sink)" in message
    assert "sinks.bad (recorder): invalid params" in message
    assert not (tmp_path / "live.sqlite").exists()


def test_a_sink_whose_secret_is_missing_stops_the_session_before_it_starts(
    registry: Registry, tmp_path: Path, time: SimulatedTime, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry.register(Webhook)
    monkeypatch.delenv("NO_SUCH_TOKEN", raising=False)
    hook = """
[sinks.hook]
plugin = "webhook"
params = { url = "https://h.example/x", token_env = "NO_SUCH_TOKEN" }
"""
    with pytest.raises(
        ConfigError, match=r"sinks\.hook \(webhook\): token_env names NO_SUCH_TOKEN"
    ):
        strategy_session(hook, registry, tmp_path, time)
    assert not (tmp_path / "live.sqlite").exists()


def test_sinks_belong_to_live_configurations() -> None:
    text = STRATEGY.replace('mode = "live"', 'mode = "backtest"') + SINKS
    with pytest.raises(ConfigError, match="a backtest has none"):
        parse_config(text)


# -- a person's answers reach the journal --------------------------------------------------


def test_an_answer_left_in_the_inbox_while_the_session_runs_is_journalled(
    registry: Registry, tmp_path: Path, time: SimulatedTime
) -> None:
    Pulse.prices, Pulse.steps, Pulse.then = UP_THEN_DOWN, len(UP_THEN_DOWN), "wait"
    journal = tmp_path / "live.sqlite"

    async def look() -> None:
        await asyncio.sleep(0)

    def seen(kind: type) -> list[Message]:
        return [e.message for e in read_entries(journal, JOURNALLED) if isinstance(e.message, kind)]

    async def main() -> None:
        task = asyncio.create_task(
            run_live_session(
                parse_config(STRATEGY),
                registry,
                journal,
                time.clock,
                time.advance,
                answers_sleep=lambda _: look(),
            )
        )
        while not journal.exists() or not seen(Signal):
            await asyncio.sleep(0)
        (made, *_) = seen(Signal)
        assert isinstance(made, Signal)
        submit(inbox_for(journal), time.clock, made.id, "took", price=123.0, quantity=0.5)
        while not seen(SignalAnswer):
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        (answer, *_) = seen(SignalAnswer)
        assert answer == SignalAnswer(
            ts=answer.ts, signal=made.id, verdict="took", price=123.0, quantity=0.5
        )
        assert answer.ts >= made.ts

    asyncio.run(asyncio.wait_for(main(), timeout=10))
    inputs = [e for e in read_entries(journal, JOURNALLED) if isinstance(e.message, SignalAnswer)]
    assert [e.origin for e in inputs] == ["input"]
    assert read_header(journal)["status"] == "complete"


def test_the_strategy_holds_what_the_person_did_not_what_the_signal_asked(
    registry: Registry, tmp_path: Path, time: SimulatedTime
) -> None:
    """Unanswered or taken, the next signal starts from the target; skipped, from flat."""
    held: dict[str, float] = {}
    for verdict in (None, "took", "skipped"):
        first, second = asked_then_next_signal(registry, tmp_path / str(verdict), verdict, "6h")
        assert first.target != 0
        held[str(verdict)] = second.held
    assert held == {"None": first.target, "took": first.target, "skipped": 0.0}


def test_a_signal_nobody_answers_in_time_is_not_held_afterwards(
    registry: Registry, tmp_path: Path, time: SimulatedTime
) -> None:
    first, second = asked_then_next_signal(registry, tmp_path, None, "30m")
    assert first.target != 0
    assert second.held == 0.0
    expired = [
        e
        for e in read_entries(tmp_path / "live.sqlite", JOURNALLED)
        if isinstance(e.message, SignalExpired)
    ]
    assert {e.origin for e in expired} == {"derived"}
    assert next(e.message for e in expired) == SignalExpired(expired[0].message.ts, first.id)


def test_by_default_a_signal_has_one_bar_to_be_answered(
    registry: Registry, tmp_path: Path, time: SimulatedTime
) -> None:
    first, second = asked_then_next_signal(registry, tmp_path, None, None)
    assert first.target != 0
    assert second.held == 0.0  # the next signal came hours later; the first had expired


def asked_then_next_signal(
    registry: Registry, folder: Path, verdict: str | None, within: str | None
) -> tuple[Signal, Signal]:
    """Run a session one trade at a time, answer its first signal, and return it and the next."""
    folder.mkdir(parents=True, exist_ok=True)
    time = SimulatedTime(start=NOW)  # each session has a clock of its own
    Pulse.time, Pulse.closed = time, 0
    Pulse.prices, Pulse.steps, Pulse.then = UP_THEN_DOWN, len(UP_THEN_DOWN), "wait"
    Pulse.gate = asyncio.Queue()
    journal = folder / "live.sqlite"
    text = STRATEGY
    if within is not None:
        text = text.replace('sizer = "vol"\n', f'sizer = "vol"\nanswer_within = "{within}"\n')

    def signals() -> list[Signal]:
        return [
            e.message for e in read_entries(journal, JOURNALLED) if isinstance(e.message, Signal)
        ]

    def points() -> int:
        return sum(1 for e in read_entries(journal, JOURNALLED) if isinstance(e.message, Point))

    async def look() -> None:
        await asyncio.sleep(0)

    async def idle(_: float) -> None:
        # The trades move this session's clock; waiting for an alarm must not, or the clock
        # would run ahead to the expiry before the next trade could be read.
        await asyncio.sleep(0)

    async def until_signals(count: int) -> None:
        while len(signals()) < count:
            assert Pulse.gate is not None
            taken = points()
            Pulse.gate.put_nowait(None)
            while points() == taken:  # the session has read the trade
                await asyncio.sleep(0)
            for _ in range(100):  # and what it set off (bars closing, the strategy deciding)
                await asyncio.sleep(0)

    async def main() -> tuple[Signal, Signal]:
        task = asyncio.create_task(
            run_live_session(
                parse_config(text),
                registry,
                journal,
                time.clock,
                idle,
                answers_sleep=lambda _: look(),
            )
        )
        while not journal.exists():
            await asyncio.sleep(0)
        await until_signals(1)
        first = signals()[0]
        if verdict is not None:
            submit(inbox_for(journal), time.clock, first.id, verdict)  # type: ignore[arg-type]
            while not any(
                isinstance(e.message, SignalAnswer) for e in read_entries(journal, JOURNALLED)
            ):
                await asyncio.sleep(0)
        await until_signals(2)
        second = signals()[1]
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return first, second

    return asyncio.run(asyncio.wait_for(main(), timeout=30))


@pytest.mark.parametrize(("verdict", "extra"), [("skipped", SignalAnswer), (None, SignalExpired)])
def test_a_live_day_with_its_answers_replays_to_the_same_entries(
    registry: Registry,
    tmp_path: Path,
    time: SimulatedTime,
    verdict: str | None,
    extra: type,
) -> None:
    asked_then_next_signal(registry, tmp_path, verdict, "30m")
    journal = tmp_path / "live.sqlite"
    replayed = replay_backtest(journal, tmp_path / "again.sqlite", registry)
    assert raw_rows(replayed.journal) == raw_rows(journal)
    kinds = {type(e.message) for e in read_entries(replayed.journal, JOURNALLED)}
    assert {Signal, extra} <= kinds


# -- a session carries on from an earlier one's journal ------------------------------------


def play(
    registry: Registry,
    journal: Path,
    *,
    start: Timestamp,
    signals_wanted: int,
    start_at: int = 0,
    resume: Path | None = None,
    config: str = STRATEGY,
    sinks: str = "",
) -> Timestamp:
    """Run a session one trade at a time until it has made ``signals_wanted``, then stop it.

    Returns where its clock stood when it stopped.
    """
    time = SimulatedTime(start=start)
    Pulse.time, Pulse.closed = time, 0
    Pulse.prices, Pulse.steps, Pulse.then = UP_THEN_DOWN, len(UP_THEN_DOWN), "wait"
    Pulse.gate, Pulse.start_at = asyncio.Queue(), start_at
    text = config.replace('sizer = "vol"\n', 'sizer = "vol"\nanswer_within = "6h"\n') + sinks

    def kinds(kind: type) -> int:
        return sum(1 for e in read_entries(journal, JOURNALLED) if isinstance(e.message, kind))

    async def idle(_: float) -> None:
        await asyncio.sleep(0)

    async def main() -> None:
        task = asyncio.create_task(
            run_live_session(
                parse_config(text),
                registry,
                journal,
                time.clock,
                idle,
                answers_sleep=idle,
                resume=resume,
            )
        )
        while not journal.exists():
            await asyncio.sleep(0)
        while kinds(Signal) < signals_wanted:
            assert Pulse.gate is not None
            taken = kinds(Point)
            Pulse.gate.put_nowait(None)
            while kinds(Point) == taken:
                await asyncio.sleep(0)
            for _ in range(100):
                await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(asyncio.wait_for(main(), timeout=30))
    return time.clock.now()


def history(journal: Path) -> list[Message]:
    """What the strategy lived through, without the engine's marks (when they fell differs)."""
    keep = (Point, Bar, IndicatorValues, Signal, SignalRefused, SignalAnswer, SignalExpired)
    return [e.message for e in read_entries(journal, JOURNALLED) if isinstance(e.message, keep)]


def test_a_resumed_session_lives_on_as_if_it_had_not_stopped(
    registry: Registry, tmp_path: Path, time: SimulatedTime
) -> None:
    straight = tmp_path / "straight.sqlite"
    play(registry, straight, start=NOW, signals_wanted=2)
    first = tmp_path / "first.sqlite"
    stopped = play(registry, first, start=NOW, signals_wanted=1)
    trades = sum(1 for m in history(first) if isinstance(m, Point))
    second = tmp_path / "second.sqlite"
    play(registry, second, start=stopped, signals_wanted=2, start_at=trades, resume=first)
    # The warm-up, the position held and the clock all carried over: the second session's
    # second signal is the straight session's, which a cold start could not make so soon.
    assert history(second) == history(straight)
    made = [m for m in history(second) if isinstance(m, Signal)]
    assert made[1].held == made[0].target
    assert read_header(second)["status"] == "complete"
    assert json.loads(read_header(second)["resumes"])["journal"] == "first.sqlite"
    assert read_header(second)["start"] == read_header(first)["start"]
    # and the whole of it, old history and new, replays
    replayed = replay_backtest(second, tmp_path / "again.sqlite", registry)
    assert raw_rows(replayed.journal) == raw_rows(second)


def test_what_the_old_session_made_is_not_sent_to_the_sinks_again(
    with_sinks: Registry, tmp_path: Path, time: SimulatedTime
) -> None:
    sinks = '[sinks.phone]\nplugin = "recorder"\nparams = { name = "phone" }\n'
    first = tmp_path / "first.sqlite"
    stopped = play(with_sinks, first, start=NOW, signals_wanted=1, sinks=sinks)
    (old,) = [m.id for m in history(first) if isinstance(m, Signal)]
    assert Recorder.got == [("phone", old)]
    trades = sum(1 for m in history(first) if isinstance(m, Point))
    second = tmp_path / "second.sqlite"
    play(
        with_sinks,
        second,
        start=stopped,
        signals_wanted=2,
        start_at=trades,
        resume=first,
        sinks=sinks,
    )
    ids = [m.id for m in history(second) if isinstance(m, Signal)]
    assert ids[0] == old
    assert Recorder.got == [("phone", old), ("phone", ids[1])]  # the second only, once


def test_a_session_resumes_only_a_live_journal_of_its_own_configuration(
    registry: Registry, tmp_path: Path, time: SimulatedTime
) -> None:
    first = tmp_path / "first.sqlite"
    stopped = play(registry, first, start=NOW, signals_wanted=1)
    again = SimulatedTime(start=stopped)

    def resuming(text: str, journal: Path, resume: Path) -> None:
        async def main() -> None:
            await run_live_session(
                parse_config(text),
                registry,
                journal,
                again.clock,
                again.advance,
                resume=resume,
            )

        asyncio.run(asyncio.wait_for(main(), timeout=5))  # a session that was let start hangs

    other = STRATEGY.replace("fast = 2", "fast = 3")
    with pytest.raises(InvalidValueError, match="ran another configuration"):
        resuming(other, tmp_path / "other.sqlite", first)
    backtest = tmp_path / "backtest.sqlite"
    shutil.copy(first, backtest)
    db = sqlite3.connect(backtest)
    with db:
        db.execute("UPDATE header SET value = 'backtest' WHERE key = 'kind'")
    db.close()
    with pytest.raises(InvalidValueError, match="not the journal of a live session"):
        resuming(STRATEGY, tmp_path / "x.sqlite", backtest)
    assert not (tmp_path / "other.sqlite").exists()


def test_an_answer_left_for_a_session_that_is_resumed_reaches_the_new_one(
    registry: Registry, tmp_path: Path, time: SimulatedTime
) -> None:
    first = tmp_path / "first.sqlite"
    stopped = play(registry, first, start=NOW, signals_wanted=1)
    trades = sum(1 for m in history(first) if isinstance(m, Point))
    (made,) = [m for m in history(first) if isinstance(m, Signal)]
    # the person answers after the session stopped, before it was resumed
    submit(inbox_for(first), time.clock, made.id, "skipped")
    second = tmp_path / "second.sqlite"
    play(registry, second, start=stopped, signals_wanted=2, start_at=trades, resume=first)
    answered = [m for m in history(second) if isinstance(m, SignalAnswer)]
    assert [(a.signal, a.verdict) for a in answered] == [(made.id, "skipped")]
    with pytest.raises(HeliographError, match=r"resumed; use second\.sqlite instead"):
        submit(inbox_for(first), time.clock, made.id, "took")


def test_a_session_waits_for_deliveries_still_under_way_when_it_ends(
    with_sinks: Registry, tmp_path: Path, time: SimulatedTime
) -> None:
    """Deliveries taking far more turns of the loop than the end of the run has to spare."""
    slow = """
[sinks.slow]
plugin = "recorder"
params = { name = "slow" }
"""
    Recorder.turns = 200
    journal = strategy_session(slow, with_sinks, tmp_path, time)
    made = signals(journal)
    assert [d.ok for d in deliveries(journal)] == [True] * len(made)
    assert sorted(i for _, i in Recorder.got) == sorted(s.id for s in made)


def halted_day(
    registry: Registry, folder: Path, halt_after: int, until_signals: int | None = None
) -> list[Message]:
    """A gated session; the owner halts it once ``halt_after`` signals are out, then clears it.

    The first signal, if any, is answered as taken (so there is a position to leave). The halt
    lasts until a refusal, or until ``until_signals`` signals are out if that is given.

    Returns the halt-related and decision messages in the order journalled.
    """
    folder.mkdir(parents=True, exist_ok=True)
    time = SimulatedTime(start=NOW)
    Pulse.time, Pulse.closed = time, 0
    Pulse.prices, Pulse.steps, Pulse.then = UP_THEN_DOWN, len(UP_THEN_DOWN), "wait"
    Pulse.gate = asyncio.Queue()
    journal = folder / "live.sqlite"

    def seen(*kinds: type) -> list[Message]:
        return [
            e.message for e in read_entries(journal, JOURNALLED) if isinstance(e.message, kinds)
        ]

    async def idle(_: float) -> None:
        await asyncio.sleep(0)

    async def until(done: Callable[[], bool]) -> None:
        while not done():
            assert Pulse.gate is not None
            taken = len(seen(Point))
            Pulse.gate.put_nowait(None)
            while len(seen(Point)) == taken:
                await asyncio.sleep(0)
            for _ in range(100):
                await asyncio.sleep(0)

    async def main() -> None:
        task = asyncio.create_task(
            run_live_session(
                parse_config(STRATEGY), registry, journal, time.clock, idle, answers_sleep=idle
            )
        )
        while not journal.exists():
            await asyncio.sleep(0)
        await until(lambda: len(seen(Signal)) >= halt_after)
        if halt_after:
            (first, *_) = seen(Signal)
            assert isinstance(first, Signal)
            submit(inbox_for(journal), time.clock, first.id, "took")
            while not seen(SignalAnswer):
                await asyncio.sleep(0)
        order(inbox_for(journal), time.clock, "halt", "maintenance")
        while not seen(Halted):
            await asyncio.sleep(0)
        if until_signals is None:
            await until(lambda: len(seen(SignalRefused)) >= 1)
        else:
            await until(lambda: len(seen(Signal)) >= until_signals)
        order(inbox_for(journal), time.clock, "clear")
        while not seen(Cleared):
            await asyncio.sleep(0)
        await until(lambda: len(seen(Signal)) >= max(halt_after, until_signals or 0) + 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(asyncio.wait_for(main(), timeout=60))
    return seen(Halted, Cleared, Signal, SignalRefused, SignalAnswer)


def test_a_halted_session_refuses_entries_until_the_owner_clears_it(
    registry: Registry, tmp_path: Path
) -> None:
    made = halted_day(registry, tmp_path, halt_after=0)
    kinds = [type(m).__name__ for m in made]
    assert kinds[:3] == ["Halted", "SignalRefused", "Cleared"]
    halted, refused = made[0], made[1]
    assert isinstance(halted, Halted)
    assert (halted.by, halted.reason) == ("owner", "maintenance")
    assert isinstance(refused, SignalRefused)
    assert refused.refused_by == "halt"
    assert refused.reason == "halted by owner: maintenance"
    assert refused.held == 0.0
    assert refused.target != 0
    assert kinds[3:] == ["Signal"]  # cleared: the same entry now leaves
    entry = made[3]
    assert isinstance(entry, Signal)
    assert entry.held == 0.0
    assert entry.target > 0


def test_a_halted_session_still_sends_the_signal_that_takes_risk_off(
    registry: Registry, tmp_path: Path
) -> None:
    made = halted_day(registry, tmp_path, halt_after=1, until_signals=2)
    entered = next(m for m in made if isinstance(m, Signal))
    halt = next(i for i, m in enumerate(made) if isinstance(m, Halted))
    clear = next(i for i, m in enumerate(made) if isinstance(m, Cleared))
    during = made[halt:clear]
    refused = [m for m in during if isinstance(m, SignalRefused)]
    leaving = [m for m in during if isinstance(m, Signal)]
    # the price rose while halted: additions were kept in; then it fell: the cut left
    assert [r.refused_by for r in refused] == ["halt"] * len(refused)
    assert [r.target > r.held for r in refused] == [True] * len(refused)
    assert len(refused) >= 1
    assert [(s.held, s.target < s.held) for s in leaving] == [(entered.target, True)]


def test_the_owners_halt_and_clear_are_journalled_inputs_and_the_day_replays(
    registry: Registry, tmp_path: Path
) -> None:
    halted_day(registry, tmp_path, halt_after=1)
    journal = tmp_path / "live.sqlite"
    origins = {
        type(e.message).__name__: e.origin
        for e in read_entries(journal, JOURNALLED)
        if isinstance(e.message, Halted | Cleared)
    }
    assert origins == {"Halted": "input", "Cleared": "input"}
    replayed = replay_backtest(journal, tmp_path / "again.sqlite", registry)
    assert raw_rows(replayed.journal) == raw_rows(journal)
