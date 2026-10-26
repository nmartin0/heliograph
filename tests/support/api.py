# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The engine's own API app, in this process, over ASGI: its real handlers on a real
run, with no port and no second process (tests/process starts the real process)."""

import asyncio
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from heliograph.app.config import load_config
from heliograph.app.run import run_backtest
from heliograph.app.server import Backtests, Settings, make_app
from heliograph.core.time import from_iso
from heliograph.gui.client import Engine
from heliograph.plugins.registry import Registry
from tests.support.simulated import SimulatedTime

ROOT = Path(__file__).resolve().parents[2]
RUN = "trend-20261023T000000Z"
"""The id of the finished run the engine has, a run of examples/trend.toml."""


def engine_holding_a_run(folder: Path) -> Callable[[], Engine]:
    """A function giving a client of an engine that holds one finished run in ``folder``
    (call it from inside the test's event loop)."""
    runs = folder / "runs"
    runs.mkdir()
    time = SimulatedTime(start=from_iso("2026-10-23T00:00:00Z"))
    with pytest.MonkeyPatch.context() as patch:
        patch.chdir(ROOT)
        loaded = load_config(ROOT / "examples" / "trend.toml")
        run_backtest(loaded, Registry.installed(), runs / f"{RUN}.sqlite", time.clock, time.advance)
    backtests = Backtests(runs, time.clock)
    app = make_app(Settings(data=folder, configs=ROOT / "examples"), time.clock, backtests)

    def engine() -> Engine:
        http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://engine")
        return Engine("http://engine", http)

    # The first answer about plug-ins imports them all, which takes longer than a
    # simulated page may (3 s): do it now, once.
    asyncio.run(_warmed(engine()))
    return engine


async def _warmed(engine: Engine) -> None:
    await engine.plugins()
    await engine.close()
