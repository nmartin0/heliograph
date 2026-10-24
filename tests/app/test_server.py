# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The engine process's parts that no client can make happen: a backtest that
crashes with a bug, one that will not stop. Everything a client can see is
tested from outside, in tests/process."""

import json
import logging
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from heliograph.app import server
from heliograph.app.config import parse_config
from heliograph.app.server import Backtests, _JsonLines
from heliograph.core.clock import SimulatedClock
from heliograph.core.errors import HeliographError
from heliograph.core.time import Timestamp

CONFIG = """
mode = "backtest"

[mounts.btc]
source = "file"
params = { path = "nowhere.csv" }
kind = "trades"
start = 2026-01-01T00:00:00Z
end = 2026-01-02T00:00:00Z

[mounts.btc.instrument]
symbol = "BTC/USD"
venue = "kraken"
asset_class = "crypto"
quote_currency = "USD"
"""

CLOCK = SimulatedClock(Timestamp(1_792_454_400_000_000_000))


def test_a_backtest_that_crashes_is_logged_and_the_worker_goes_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    second_ran = threading.Event()

    def run(loaded: Any, registry: Any, journal: Path, *args: Any, **kwargs: Any) -> Any:
        if journal.stem.startswith("a-"):
            raise RuntimeError("a bug")
        second_ran.set()
        raise HeliographError("a named problem")

    monkeypatch.setattr(server, "run_backtest", run)
    monkeypatch.setattr(server, "_registry", lambda loaded: None)
    backtests = Backtests(tmp_path / "runs", CLOCK)
    backtests.start()
    first = backtests.submit("a", parse_config(CONFIG))
    second = backtests.submit("b", parse_config(CONFIG))
    assert second_ran.wait(10), "the worker stopped at the bug"
    backtests.stop()
    assert backtests.wait(10.0)
    assert backtests.describe(first.id) == {"id": first.id, "status": "failed: RuntimeError: a bug"}
    assert backtests.describe(second.id) == {"id": second.id, "status": "failed: a named problem"}
    crashed = [r for r in caplog.records if r.getMessage() == "backtest crashed"]
    assert [r.exc_info is not None for r in crashed] == [True]


def test_a_backtest_that_will_not_stop_in_time_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = threading.Event()
    started = threading.Event()

    def run(*args: Any, **kwargs: Any) -> Any:
        started.set()
        release.wait(10)  # ignores its stop
        raise HeliographError("let go")

    monkeypatch.setattr(server, "run_backtest", run)
    monkeypatch.setattr(server, "_registry", lambda loaded: None)
    backtests = Backtests(tmp_path / "runs", CLOCK)
    backtests.start()
    backtests.submit("slow", parse_config(CONFIG))
    assert started.wait(10)
    backtests.stop()
    assert backtests.wait(0.1) is False
    release.set()
    assert backtests.wait(10.0) is True


def test_a_logged_error_carries_its_traceback_in_its_json() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        record = logging.LogRecord(
            "heliograph.server", logging.ERROR, __file__, 1, "crashed", None, sys.exc_info()
        )
    record.fields = {"id": "x"}
    line = json.loads(_JsonLines(CLOCK).format(record))
    assert line["event"] == "crashed"
    assert line["id"] == "x"
    assert line["level"] == "error"
    assert "ValueError: boom" in line["traceback"]
    assert line["time"] == "2026-10-20T00:00:00Z"


@pytest.mark.parametrize(
    ("value", "json_value"),
    [(1.5, 1.5), (float("nan"), None), (float("inf"), None), (-float("inf"), None), (3, 3)],
)
def test_a_number_json_cannot_hold_is_null(value: Any, json_value: Any) -> None:
    assert server._finite(value) == json_value


def test_threads_still_running_at_the_deadline_are_reported() -> None:
    release = threading.Event()
    busy = threading.Thread(target=release.wait, args=(10,))  # not a daemon
    busy.start()
    try:
        assert server._threads_done(0.1) is False
    finally:
        release.set()
    assert server._threads_done(10.0) is True


def test_a_message_of_many_lines_is_named_by_its_first() -> None:
    record = logging.LogRecord(
        "uvicorn.error",
        logging.ERROR,
        __file__,
        1,
        "Traceback (most recent call last):\n  x",
        None,
        None,
    )
    line = json.loads(_JsonLines(CLOCK).format(record))
    assert (line["event"], line["detail"]) == ("Traceback (most recent call last):", "  x")
