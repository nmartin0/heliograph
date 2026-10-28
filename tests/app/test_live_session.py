# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""A live session from its configuration: journalled as it goes, and stopped cleanly."""

import asyncio
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import ClassVar

import pytest

from heliograph.app.config import ConfigError, parse_config
from heliograph.app.run import JOURNALLED, run_backtest, run_live_session
from heliograph.core.errors import InvalidValueError
from heliograph.core.messages import Bar, Message, Point, RunEnded
from heliograph.core.sources import (
    Capabilities,
    Health,
    HistoryRequest,
    SourceUnavailableError,
    Subscription,
    Throttle,
)
from heliograph.core.time import NANOS_PER_HOUR, Timestamp, from_iso
from heliograph.plugins.manifest import Manifest, NoParams, Plugin
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
                values={"price": 100.0 + step, "size": 1.0},
            )
            for _ in range(5):  # let the session take it before the clock moves on
                await asyncio.sleep(0)
        if Pulse.then == "wait":
            await asyncio.Event().wait()
        if Pulse.then == "fail":
            raise SourceUnavailableError("the exchange went away")

    async def health_check(self) -> Health:
        return Health(ok=True)

    async def close(self) -> None:
        Pulse.closed += 1


@pytest.fixture
def time() -> SimulatedTime:
    simulated = SimulatedTime(start=NOW)
    Pulse.time, Pulse.steps, Pulse.then, Pulse.closed = simulated, 3, "end", 0
    return simulated


@pytest.fixture
def registry() -> Registry:
    registry = Registry()
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
