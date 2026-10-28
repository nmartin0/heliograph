# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""A live session from its configuration: journalled as it goes, and stopped cleanly."""

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import ClassVar

import pytest

from heliograph.app.config import ConfigError, parse_config
from heliograph.app.run import JOURNALLED, run_backtest, run_live_session
from heliograph.core.errors import InvalidValueError
from heliograph.core.messages import Bar, Message, Point, RunEnded
from heliograph.core.pipeline import Signal
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
from heliograph.plugins.sqlite_journal import read_entries, read_header
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
        for step in range(self.steps):
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

    def __init__(self, params: RecorderParams) -> None:
        super().__init__(params)
        self.asks = 0

    async def emit(self, signal: Signal) -> Delivery:
        if self.params.hang:
            try:
                await asyncio.Event().wait()
            finally:
                Recorder.cancelled += 1
        for _ in range(3):  # a delivery takes a few turns of the loop
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
    return registry


def strategy_session(
    sinks: str, registry: Registry, tmp_path: Path, time: SimulatedTime, **settings: float
) -> Path:
    Pulse.prices = UP_THEN_DOWN
    Pulse.steps = len(UP_THEN_DOWN)
    journal = tmp_path / "live.sqlite"
    text = STRATEGY + sinks
    run = run_live_session(
        parse_config(text), registry, journal, time.clock, time.advance, **settings
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
