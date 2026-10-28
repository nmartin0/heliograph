# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""``heliograph live``: runs until stopped, and a stop signal leaves a whole journal."""

import asyncio
import os
import signal
from collections.abc import AsyncIterator, Iterable, Sequence
from pathlib import Path
from typing import ClassVar

import pytest

from heliograph.app.cli import main
from heliograph.app.run import JOURNALLED
from heliograph.core.messages import Message, Point, RunEnded
from heliograph.core.sources import (
    Capabilities,
    Health,
    HistoryRequest,
    Subscription,
    Throttle,
)
from heliograph.core.time import Timestamp
from heliograph.plugins.manifest import Manifest, NoParams, Plugin
from heliograph.plugins.registry import Registry
from heliograph.plugins.sqlite_journal import read_entries, read_header

CONFIG = """
mode = "live"

[mounts.btc]
source = "stopper"
kind = "trades"

[mounts.btc.instrument]
symbol = "BTC/USD"
venue = "kraken"
asset_class = "crypto"
quote_currency = "USD"
"""


class Stopper(Plugin[NoParams]):
    """A live source that sends one trade and then stops the process, as an operator would."""

    manifest = Manifest(kind="data_sources", name="stopper", version="1", summary="Stops itself")
    sent: ClassVar[list[signal.Signals]] = []

    @property
    def capabilities(self) -> Capabilities:
        return Capabilities(live=frozenset({"trades"}))

    def bind(self, throttle: Throttle) -> None:
        pass

    async def history(self, request: HistoryRequest) -> AsyncIterator[Message]:
        return
        yield

    async def subscribe(self, subscriptions: Sequence[Subscription]) -> AsyncIterator[Message]:
        at = Timestamp(1)  # the session restamps it
        yield Point(
            instrument=subscriptions[0].instrument,
            event_time=at,
            received_at=at,
            values={"price": 1.0, "size": 1.0},
        )
        await asyncio.sleep(0)
        os.kill(os.getpid(), Stopper.sent[-1])
        await asyncio.Event().wait()

    async def health_check(self) -> Health:
        return Health(ok=True)

    async def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def with_stopper(monkeypatch: pytest.MonkeyPatch) -> None:
    installed = Registry.installed

    def plus_stopper(kinds: Iterable[str] | None = None) -> Registry:
        registry = installed(kinds)
        registry.register(Stopper)
        return registry

    monkeypatch.setattr(Registry, "installed", staticmethod(plus_stopper))


@pytest.mark.parametrize("stop", [signal.SIGTERM, signal.SIGINT])
def test_a_stop_signal_ends_a_live_session_with_a_complete_journal_and_status_0(
    stop: signal.Signals, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    Stopper.sent.append(stop)
    config = tmp_path / "session.toml"
    config.write_text(CONFIG)
    journal = tmp_path / "session.sqlite"
    assert main(["live", str(config), "--journal", str(journal)]) == 0
    assert read_header(journal)["status"] == "complete"
    entries = list(read_entries(journal, JOURNALLED))
    assert isinstance(entries[-1].message, RunEnded)
    assert any(isinstance(e.message, Point) for e in entries)
    assert f"stopped; journal {journal}" in capsys.readouterr().out


def test_a_live_session_without_a_journal_option_journals_under_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    Stopper.sent.append(signal.SIGTERM)
    config = tmp_path / "session.toml"
    config.write_text(CONFIG)
    monkeypatch.chdir(tmp_path)
    assert main(["live", str(config)]) == 0
    (made,) = (tmp_path / "runs").glob("session-live-*.sqlite")
    assert read_header(made)["kind"] == "live"


def test_a_configuration_that_is_not_live_is_refused_naming_the_problem(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "session.toml"
    config.write_text(CONFIG.replace('mode = "live"', 'mode = "live"\n[x]'))
    assert main(["live", str(config)]) == 2
    assert capsys.readouterr().err
