# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The engine's process, from outside (decision D18): started with the
``heliograph serve`` command, spoken to over HTTP, stopped with a signal.

These tests import nothing from ``heliograph``; they know only what any
client knows.
"""

import calendar
import contextlib
import os
import re
import signal
import socket
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.support.engine import Engine, trades_csv, trend_config

ROOT = Path(__file__).resolve().parents[2]
DEADLINE = 10.0
"""Seconds from a stop signal to the engine's exit, as the engine promises."""

ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,9})?Z$")


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    started = Engine(
        "--data", str(tmp_path / "data"), "--configs", str(ROOT / "examples"), "--port", "0",
        cwd=ROOT,
    ).started()  # fmt: skip
    yield started
    started.close()


def written(journal: Path) -> bool:
    """Whether ``journal`` has its header, read as any SQLite reader would."""
    if not journal.is_file():
        return False
    try:
        with contextlib.closing(sqlite3.connect(f"{journal.as_uri()}?mode=ro", uri=True)) as db:
            return bool(db.execute("SELECT value FROM header WHERE key = 'status'").fetchall())
    except sqlite3.Error:
        return False


def finished(body: dict[str, Any]) -> bool:
    return body["status"] not in ("queued", "running")


# -- health and logs ---------------------------------------------------------------


def test_the_engine_listens_on_this_computer_only_and_says_where(engine: Engine) -> None:
    listening = engine.wait_for("listening")
    assert listening["host"] == "127.0.0.1"
    assert engine.port is not None
    assert listening["port"] == engine.port > 0
    assert listening["pid"] == engine.process.pid


def test_health_says_the_engine_is_up_and_what_it_runs(engine: Engine) -> None:
    health = engine.get("/api/health")
    assert health["status"] == "ok"
    assert re.match(r"^\d+\.\d+", health["version"])
    assert ISO.match(health["time"])
    assert health["running"] is None


def test_every_line_the_engine_logs_is_one_json_event(engine: Engine) -> None:
    engine.get("/api/health")
    assert engine.stop() == 0
    events = engine.events()  # a line that is not JSON fails here
    assert [e["event"] for e in events][:1] == ["listening"]
    assert {"stopping", "stopped"} <= {e["event"] for e in events}
    assert all(ISO.match(e["time"]) and e["level"] in ("info", "warning") for e in events)


# -- plug-ins and configurations ---------------------------------------------------


def test_the_installed_plugins_are_listed_by_kind_and_name(engine: Engine) -> None:
    plugins = engine.get("/api/plugins")["plugins"]
    assert len(plugins) >= 10
    found = {(p["kind"], p["name"]) for p in plugins}
    assert {("forecasters", "ewmac"), ("data_sources", "file"), ("sizers", "vol_target")} <= found
    assert all(p["version"] and p["summary"] for p in plugins)


def test_a_plugins_parameters_come_as_a_json_schema(engine: Engine) -> None:
    ewmac = engine.get("/api/plugins/forecasters/ewmac")
    assert ewmac["name"] == "ewmac"
    schema = ewmac["params"]
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert {"fast", "slow"} <= set(schema["properties"])
    assert schema["properties"]["fast"]["type"] == "integer"


@pytest.mark.parametrize(
    ("path", "says"),
    [
        ("/api/plugins/nonsense/ewmac", "no plug-ins of kind 'nonsense'"),
        ("/api/plugins/forecasters/nonsense", "no forecasters plug-in named 'nonsense'"),
        ("/api/configs/nonsense", "no configuration named 'nonsense'"),
        ("/api/configs/..%2Fpyproject", ""),  # decoded before routing: matches nothing
        ("/api/backtests/nonsense", "no backtest 'nonsense'"),
        ("/api/backtests/nonsense/report", "no backtest 'nonsense'"),
        ("/api/nonsense", "nothing at /api/nonsense"),
        ("/api/backtests/a%20b", "no backtest 'a b'"),
        ("/api/configs/a%20b", "no configuration named 'a b'"),
    ],
)
def test_what_is_not_there_is_a_404_saying_so(engine: Engine, path: str, says: str) -> None:
    status, body = engine.request("GET", path)
    assert status == 404
    assert says in body["error"]


def test_a_method_a_path_does_not_take_is_a_405(engine: Engine) -> None:
    status, body = engine.request("DELETE", "/api/health")
    assert (status, body) == (405, {"error": "DELETE is not allowed at /api/health"})


def test_the_configurations_folder_is_offered_by_name_with_its_text(engine: Engine) -> None:
    assert engine.get("/api/configs") == {"configs": [{"name": "trend"}]}
    trend = engine.get("/api/configs/trend")
    assert trend["text"] == (ROOT / "examples" / "trend.toml").read_text()


# -- backtests ---------------------------------------------------------------------


def test_a_backtest_of_an_offered_configuration_runs_and_reports(engine: Engine) -> None:
    status, queued = engine.request("POST", "/api/backtests", {"config_name": "trend"})
    assert status == 202
    assert re.match(r"^trend-\d{8}T\d{6}Z$", queued["id"])
    done = engine.until(f"/api/backtests/{queued['id']}", finished)
    assert done["status"] == "complete"
    assert (done["start"], done["end"]) == ("2026-01-01T00:00:00Z", "2026-05-01T00:00:00Z")
    report = engine.get(f"/api/backtests/{queued['id']}/report")
    (trend,) = report["strategies"]
    assert trend["strategy"] == "trend"
    assert trend["fills"] > 0
    assert report["text"].startswith("Backtest 2026-01-01T00:00:00Z to 2026-05-01T00:00:00Z")
    assert queued["id"] in [b["id"] for b in engine.get("/api/backtests")["backtests"]]
    assert [e["event"] for e in engine.events() if e.get("id") == queued["id"]] == [
        "backtest queued",
        "backtest started",
        "backtest finished",
    ]


def test_a_backtest_of_a_configurations_text_runs_under_its_name(
    engine: Engine, tmp_path: Path
) -> None:
    data = trades_csv(tmp_path / "btc.csv", 3 * 24 * 60)
    text = trend_config(data, "2026-01-01T00:00:00Z", "2026-01-03T00:00:00Z")
    status, queued = engine.request("POST", "/api/backtests", {"config": text, "name": "mine"})
    assert status == 202
    assert queued["id"].startswith("mine-")
    assert engine.until(f"/api/backtests/{queued['id']}", finished)["status"] == "complete"


def test_two_backtests_of_one_name_get_their_own_ids(engine: Engine) -> None:
    """Within one second the second is numbered; across a second's turn, each its own."""
    ids = [
        engine.request("POST", "/api/backtests", {"config_name": "trend"})[1]["id"] for _ in "ab"
    ]
    assert ids[0] != ids[1]
    stamps = [re.sub(r"-\d+$", "", i) if i.count("-") > 1 else i for i in ids]
    if stamps[0] == stamps[1]:
        assert ids[1] == f"{ids[0]}-2"


def test_backtests_are_listed_the_latest_submitted_first(engine: Engine, tmp_path: Path) -> None:
    text = trend_config(tmp_path / "missing.csv", "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z")
    names = ["zz", "aa", *["m"] * 11]
    ids = [
        engine.request("POST", "/api/backtests", {"config": text, "name": n})[1]["id"]
        for n in names
    ]
    listed = [b["id"] for b in engine.get("/api/backtests")["backtests"]]
    assert listed == ids[::-1]


@pytest.mark.parametrize(
    ("body", "says"),
    [
        (b"not json", "the body is not JSON"),
        ({}, 'give either "config"'),
        ({"config": "", "config_name": "trend"}, 'give either "config"'),
        ({"config": 3}, '"config" must be a configuration\'s text'),
        ({"config": 'mode = "backtest"', "name": "../up"}, '"name" must be up to 100 letters'),
        ({"config": 'mode = "backtest"', "name": "a" * 101}, '"name" must be up to 100 letters'),
        ({"config_name": 3}, '"config_name" must be a configuration\'s name'),
        ({"config": "mode = 1"}, "invalid run configuration"),
        ({"config": 'mode = "live"'}, "only backtests run here yet"),
    ],
)
def test_a_backtest_that_cannot_run_is_refused_saying_why(
    engine: Engine, body: Any, says: str
) -> None:
    status, refused = engine.request("POST", "/api/backtests", body)
    assert status == 400
    assert says in refused["error"]


@pytest.mark.parametrize("name", ["nonsense", "../pyproject", "examples/trend"])
def test_an_unknown_configuration_is_a_404(engine: Engine, name: str) -> None:
    status, body = engine.request("POST", "/api/backtests", {"config_name": name})
    assert (status, body) == (404, {"error": f"no configuration named {name!r}"})


def test_a_configuration_that_is_not_utf8_is_refused_saying_so(tmp_path: Path) -> None:
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "latin.toml").write_bytes(b'mode = "backtest" # caf\xe9\n')
    engine = Engine("--data", str(tmp_path / "data"), "--configs", str(configs), "--port", "0")
    try:
        engine.started()
        for method, path, body in (
            ("GET", "/api/configs/latin", None),
            ("POST", "/api/backtests", {"config_name": "latin"}),
        ):
            assert engine.request(method, path, body) == (
                400,
                {"error": "configuration 'latin' is not UTF-8 text"},
            )
    finally:
        engine.close()


def test_a_backtest_whose_data_cannot_be_read_fails_and_the_engine_goes_on(
    engine: Engine, tmp_path: Path
) -> None:
    text = trend_config(tmp_path / "missing.csv", "2026-01-01T00:00:00Z", "2026-01-03T00:00:00Z")
    queued = engine.request("POST", "/api/backtests", {"config": text})[1]
    failed = engine.until(f"/api/backtests/{queued['id']}", finished)
    assert failed["status"].startswith("failed: ")
    assert "missing.csv" in failed["status"]
    assert engine.get("/api/health")["status"] == "ok"
    report = engine.get(f"/api/backtests/{queued['id']}/report")  # as far as it went
    assert report["status"] == failed["status"]
    assert f"NOT COMPLETE ({failed['status']})" in report["text"]


# -- stopping --------------------------------------------------------------------------


@pytest.mark.parametrize("number", [signal.SIGTERM, signal.SIGINT])
def test_an_idle_engine_stops_on_a_signal_and_exits_0(engine: Engine, number: int) -> None:
    assert engine.stop(number) == 0
    assert engine.events()[-1]["event"] == "stopped"


@pytest.mark.parametrize("number", [signal.SIGTERM, signal.SIGINT])
def test_a_running_backtest_is_stopped_its_journal_closed_and_the_queue_dropped(
    tmp_path: Path, number: int
) -> None:
    """A long backtest, a second queued behind it: the signal stops the first at its
    next input, drops the second, and the engine exits 0 well within its deadline.
    Started again on the same data, the engine reports the first as stopped; the
    second never ran."""
    data_dir = tmp_path / "data"
    data = trades_csv(tmp_path / "btc.csv", 300_000)
    text = trend_config(data, "2026-01-01T00:00:00Z", "2026-07-01T00:00:00Z")
    engine = Engine("--data", str(data_dir), "--port", "0").started()
    try:
        long = engine.request("POST", "/api/backtests", {"config": text, "name": "long"})[1]
        queued = engine.request("POST", "/api/backtests", {"config": text, "name": "next"})[1]
        engine.until("/api/health", lambda h: h["running"] == long["id"])
        status, body = engine.request("GET", f"/api/backtests/{queued['id']}/report")
        assert (status, body) == (409, {"error": f"backtest {queued['id']} has no report (queued)"})
        asked = time.monotonic()
        assert engine.stop(number) == 0
        assert time.monotonic() - asked < DEADLINE
    finally:
        engine.close()
    dropped = [e for e in engine.events() if e["event"] == "backtests dropped"]
    assert [e["ids"] for e in dropped] == [[queued["id"]]]

    again = Engine("--data", str(data_dir), "--port", "0").started()
    try:
        stopped = again.get(f"/api/backtests/{long['id']}")
        assert (
            stopped["status"] == "failed: RunStoppedError: the run was stopped before it finished"
        )
        assert [b["id"] for b in again.get("/api/backtests")["backtests"]] == [long["id"]]
    finally:
        again.close()


# -- how it is started ---------------------------------------------------------------


def test_the_settings_may_come_from_the_environment(tmp_path: Path) -> None:
    engine = Engine(
        env={"HELIOGRAPH_DATA": str(tmp_path / "data"), "HELIOGRAPH_PORT": "0"}
    ).started()
    try:
        assert engine.get("/api/health")["status"] == "ok"
        assert engine.get("/api/configs") == {"configs": []}
        assert engine.wait_for("listening")["data"] == str(tmp_path / "data")
    finally:
        engine.close()


@pytest.mark.parametrize(
    ("args", "env", "says"),
    [
        ((), {}, "give a data folder: --data, or HELIOGRAPH_DATA"),
        (("--data", "d"), {"HELIOGRAPH_PORT": "eighty"}, "HELIOGRAPH_PORT must be a port number"),
        (("--data", "d", "--configs", "nowhere"), {}, "configurations folder nowhere"),
    ],
)
def test_an_engine_that_cannot_start_says_why_and_exits_2(
    tmp_path: Path, args: tuple[str, ...], env: dict[str, str], says: str
) -> None:
    clean = {"HELIOGRAPH_DATA": "", "HELIOGRAPH_PORT": "0", **env}
    engine = Engine(*args, env=clean, cwd=tmp_path)
    assert engine.wait() == 2
    assert says in "\n".join(engine.lines)


def test_an_engine_whose_port_is_taken_says_so_and_exits_2(tmp_path: Path) -> None:
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]
        engine = Engine("--data", str(tmp_path), "--port", str(port))
        assert engine.wait() == 2
    assert f"cannot listen on 127.0.0.1:{port}" in "\n".join(engine.lines)


def test_a_backtest_refused_before_its_journal_is_written_says_why(engine: Engine) -> None:
    """Kraken has no 2-minute candles: the mount is refused before the run begins."""
    file = 'source = "file"\nparams = { path = "examples/data/btc-synthetic.csv" }\nkind = "trades"'
    ccxt = 'source = "ccxt"\nparams = { exchange = "kraken" }\nkind = "bars"\nbar_size = "2m"'
    text = (ROOT / "examples" / "trend.toml").read_text()
    assert file in text
    text = text.replace(file, ccxt)
    queued = engine.request("POST", "/api/backtests", {"config": text})[1]
    failed = engine.until(f"/api/backtests/{queued['id']}", finished)
    assert failed["status"].startswith("failed: ")
    assert "2m" in failed["status"]
    status, body = engine.request("GET", f"/api/backtests/{queued['id']}/report")
    assert status == 409
    assert f"backtest {queued['id']} has no report (failed: " in body["error"]


def test_a_backtest_whose_engine_was_killed_is_reported_as_interrupted(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data = trades_csv(tmp_path / "btc.csv", 300_000)
    text = trend_config(data, "2026-01-01T00:00:00Z", "2026-07-01T00:00:00Z")
    engine = Engine("--data", str(data_dir), "--port", "0").started()
    try:
        long = engine.request("POST", "/api/backtests", {"config": text, "name": "long"})[1]
        engine.until(f"/api/backtests/{long['id']}", lambda b: b["status"] == "running")
        engine.until("/api/health", lambda h: h["running"] == long["id"])
        journal = data_dir / "runs" / f"{long['id']}.sqlite"
        engine.polling(lambda: written(journal))
        engine.stop(signal.SIGKILL)
    finally:
        engine.close()
    again = Engine("--data", str(data_dir), "--port", "0").started()
    try:
        assert again.get(f"/api/backtests/{long['id']}")["status"] == "interrupted"
    finally:
        again.close()


def test_a_file_in_the_runs_folder_that_is_not_a_journal_is_listed_as_unreadable(
    tmp_path: Path,
) -> None:
    runs = tmp_path / "data" / "runs"
    runs.mkdir(parents=True)
    (runs / "stray.sqlite").write_text("not a database")
    engine = Engine("--data", str(tmp_path / "data"), "--port", "0").started()
    try:
        assert engine.get("/api/backtests") == {
            "backtests": [{"id": "stray", "status": "unreadable: not a backtest's journal"}]
        }
        status, body = engine.request("GET", "/api/backtests/stray/report")
        assert status == 409
        assert "unreadable" in body["error"]
    finally:
        engine.close()


def test_a_backtest_that_will_not_stop_is_left_and_the_engine_exits_1_in_time(
    tmp_path: Path,
) -> None:
    """A backtest reading a pipe nothing writes to never reaches its next input:
    the engine waits out its deadline, then exits 1, the journal left running;
    started again, it reports that backtest as interrupted."""
    pipe = tmp_path / "btc.csv"
    os.mkfifo(pipe)
    text = trend_config(pipe, "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z")
    data_dir = tmp_path / "data"
    engine = Engine("--data", str(data_dir), "--port", "0").started()
    try:
        stuck = engine.request("POST", "/api/backtests", {"config": text, "name": "stuck"})[1]
        journal = data_dir / "runs" / f"{stuck['id']}.sqlite"
        engine.polling(lambda: written(journal))
        asked = time.monotonic()
        assert engine.stop(signal.SIGTERM, timeout=DEADLINE + 20) == 1
        assert DEADLINE - 1 < time.monotonic() - asked < DEADLINE + 5
    finally:
        engine.close()
    assert [e["id"] for e in engine.events() if e["event"].startswith("a backtest did not")] == [
        stuck["id"]
    ]
    again = Engine("--data", str(data_dir), "--port", "0").started()
    try:
        assert again.get(f"/api/backtests/{stuck['id']}")["status"] == "interrupted"
    finally:
        again.close()


@pytest.mark.parametrize(
    ("args", "says"),
    [
        (("--port", "70000"), "the port must be from 0 to 65535, not 70000"),
        (("--data", "a-file"), "data folder a-file is not a folder"),
    ],
)
def test_an_engine_given_a_bad_option_says_why_and_exits_2(
    tmp_path: Path, args: tuple[str, ...], says: str
) -> None:
    (tmp_path / "a-file").write_text("")
    engine = Engine("--data", "d", *args, env={"HELIOGRAPH_PORT": "0"}, cwd=tmp_path)
    assert engine.wait() == 2
    assert says in "\n".join(engine.lines)


def _instant(event: dict[str, Any]) -> float:
    """An event's time as seconds, read from its ISO text (to the microsecond)."""
    whole, _, fraction = event["time"].rstrip("Z").partition(".")
    moment = time.strptime(whole, "%Y-%m-%dT%H:%M:%S")
    return calendar.timegm(moment) + float(f"0.{fraction or 0}")


def test_a_backtest_is_told_to_stop_at_the_signal_not_after_open_requests(
    tmp_path: Path,
) -> None:
    """A request still being sent holds the server's shutdown for its grace; the
    running backtest stops at the signal all the same."""
    data = trades_csv(tmp_path / "btc.csv", 300_000)
    text = trend_config(data, "2026-01-01T00:00:00Z", "2026-07-01T00:00:00Z")
    engine = Engine("--data", str(tmp_path / "data"), "--port", "0").started()
    try:
        long = engine.request("POST", "/api/backtests", {"config": text, "name": "long"})[1]
        engine.until("/api/health", lambda h: h["running"] == long["id"])
        assert engine.port is not None
        with socket.create_connection(("127.0.0.1", engine.port)) as half:
            half.sendall(b"POST /api/backtests HTTP/1.1\r\nHost: x\r\nContent-Length: 10\r\n\r\n")
            engine.get("/api/health")  # the half-sent request has been taken in
            assert engine.stop(signal.SIGTERM) == 0
    finally:
        engine.close()
    # uvicorn logs "Shutting down" as the signal comes; open requests then have
    # their grace (three seconds) before it lets go.
    wanted = ("Shutting down", "backtest failed")
    events = {e["event"]: e for e in engine.events() if e["event"] in wanted}
    assert events["backtest failed"]["id"] == long["id"]
    assert _instant(events["backtest failed"]) - _instant(events["Shutting down"]) < 1.5


def test_a_burst_of_signals_stops_the_engine_once_and_in_time(tmp_path: Path) -> None:
    """Signals arriving inside the handling of the one before must not wait on it."""
    data = trades_csv(tmp_path / "btc.csv", 300_000)
    text = trend_config(data, "2026-01-01T00:00:00Z", "2026-07-01T00:00:00Z")
    engine = Engine("--data", str(tmp_path / "data"), "--port", "0").started()
    try:
        long = engine.request("POST", "/api/backtests", {"config": text, "name": "long"})[1]
        engine.until("/api/health", lambda h: h["running"] == long["id"])
        asked = time.monotonic()
        for _ in range(300):
            if engine.process.poll() is not None:
                break
            engine.process.send_signal(signal.SIGTERM)
        assert engine.wait(DEADLINE + 20) == 0
        assert time.monotonic() - asked < DEADLINE
    finally:
        engine.close()
    assert [e["event"] for e in engine.events()].count("stopped") == 1
