# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The engine process, started and spoken to as a person or a front end would.

Imports nothing from ``heliograph`` (tests/meta/test_suite_rules.py): what it
knows of the engine is the ``heliograph`` command, its stderr and its HTTP
API, as any other client's would be.
"""

import calendar
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

COMMAND = Path(sys.executable).with_name("heliograph")
"""The installed ``heliograph`` command, beside the interpreter running the tests."""

STARTING = 30.0
"""Seconds an engine has to say it is listening; generous for a slow computer."""


class Engine:
    """One ``heliograph serve`` process: its log events and its API."""

    def __init__(
        self,
        *args: str,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
    ) -> None:
        self.process = subprocess.Popen(
            [str(COMMAND), "serve", *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, **(env or {})},
            cwd=cwd,
        )
        self.lines: list[str] = []
        self._seen = threading.Condition()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()
        self.port: int | None = None

    def _read(self) -> None:
        assert self.process.stderr is not None
        for line in self.process.stderr:
            with self._seen:
                self.lines.append(line.rstrip("\n"))
                self._seen.notify_all()
        with self._seen:
            self._seen.notify_all()

    def events(self) -> list[dict[str, Any]]:
        """Every log line so far, read as JSON (one that is not fails the test)."""
        with self._seen:
            return [json.loads(line) for line in self.lines]

    def wait_for(self, event: str, timeout: float = STARTING) -> dict[str, Any]:
        """The first log event named ``event``, waiting for it if need be."""
        deadline = time.monotonic() + timeout
        with self._seen:
            while True:
                for line in self.lines:
                    parsed = json.loads(line)
                    if parsed.get("event") == event:
                        return dict(parsed)
                left = deadline - time.monotonic()
                if left <= 0 or (self.process.poll() is not None and not self._reader.is_alive()):
                    raise AssertionError(
                        f"no {event!r} event; stderr was:\n" + "\n".join(self.lines)
                    )
                self._seen.wait(min(left, 0.5))

    def started(self) -> "Engine":
        self.port = int(self.wait_for("listening")["port"])
        return self

    def request(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        """The status and JSON body of ``method path``."""
        data = (
            None
            if body is None
            else (body if isinstance(body, bytes) else json.dumps(body).encode())
        )
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=data, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def get(self, path: str) -> Any:
        status, body = self.request("GET", path)
        assert status == 200, (status, body)
        return body

    def until(self, path: str, done: Any, timeout: float = 120.0) -> Any:
        """``GET path`` until ``done(body)``, and that body.

        Never sleeps: every change of a backtest's status is logged, so after each
        look it waits for the engine's next log line.
        """
        deadline = time.monotonic() + timeout
        while True:
            with self._seen:
                seen = len(self.lines)
            body = self.get(path)
            if done(body):
                return body
            left = deadline - time.monotonic()
            assert left > 0, f"{path} still {body} after {timeout} s"
            self._next_line(seen, left)

    def _next_line(self, seen: int, timeout: float) -> None:
        """Wait until the engine has logged more than ``seen`` lines, or stopped."""
        with self._seen:
            self._seen.wait_for(
                lambda: len(self.lines) > seen or not self._reader.is_alive(), timeout
            )

    def polling(self, done: Any, timeout: float = 60.0) -> None:
        """Ask for the engine's health until ``done()``: for what the engine does not log
        (its requests pace the asking)."""
        deadline = time.monotonic() + timeout
        while not done():
            assert time.monotonic() < deadline, f"not done after {timeout} s"
            self.get("/api/health")

    def stop(self, number: int = signal.SIGTERM, timeout: float = 30.0) -> int:
        """Send ``number`` and wait for the process to exit; its exit status."""
        self.process.send_signal(number)
        return self.wait(timeout)

    def wait(self, timeout: float = 30.0) -> int:
        code = self.process.wait(timeout)
        self._reader.join(timeout)
        for pipe in (self.process.stdout, self.process.stderr):
            if pipe is not None:
                pipe.close()
        return code

    def close(self) -> None:
        """Stop the process if it still runs: asked first, so it can save what it
        must (coverage's data among it), then killed if it does not stop."""
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGTERM)
            try:
                self.process.wait(30)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.wait()


JANUARY_2026 = calendar.timegm((2026, 1, 1, 0, 0, 0))
"""2026-01-01T00:00:00Z, in seconds."""


def trades_csv(path: Path, count: int) -> Path:
    """``count`` trades a minute apart from 2026-01-01, prices wandering, as a CSV."""
    first = JANUARY_2026
    rows = ["time,price,size\n"]
    price = 40_000.0
    for k in range(count):
        price *= 1.0 + (0.002 if (k * 7919) % 13 < 7 else -0.002)
        rows.append(f"{first + 60 * k},{price:.2f},1\n")
    path.write_text("".join(rows))
    return path


def trend_config(data: Path, start: str, end: str) -> str:
    """A small trend-following backtest of the trades in ``data``."""
    return f"""
mode = "backtest"

[mounts.btc]
source = "file"
params = {{ path = {str(data)!r}, time_unit = "s" }}
kind = "trades"
start = {start}
end = {end}

[mounts.btc.instrument]
symbol = "BTC/USD"
venue = "kraken"
asset_class = "crypto"
quote_currency = "USD"

[[streams]]
mount = "btc"
bars = "1h"

[[components]]
kind = "forecasters"
plugin = "ewmac"
name = "fast"
inputs = ["btc@1h.close"]
params = {{ fast = 4, slow = 16 }}

[[components]]
kind = "fills"
plugin = "next_open"
name = "costs"
params = {{ spread = 0.001, commission = 0.001 }}

[[components]]
kind = "sizers"
plugin = "vol_target"
name = "vol"
params = {{ capital = 10000.0, target = 0.25, bars_per_year = 8760.0 }}

[[strategies]]
name = "trend"
price = "btc@1h.close"
forecasters = ["fast"]
sizer = "vol"
fills = "costs"
"""
