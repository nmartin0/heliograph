# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The ``heliograph`` command, run as a person runs it."""

import sqlite3
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

import pytest

from heliograph import __version__
from heliograph.app import cli
from heliograph.app.cli import main
from heliograph.core.clock import WallClock
from heliograph.core.time import from_iso
from heliograph.plugins.registry import Registry
from tests.support.plugins import Units

ROOT = Path(__file__).resolve().parents[2]
HAND = ROOT / "tests" / "scenarios" / "hand-checked.toml"
NOON = from_iso("2026-10-23T12:00:00Z")


@pytest.fixture
def with_units(monkeypatch: pytest.MonkeyPatch) -> None:
    """The installed plug-ins, and the test sizer the hand-checked scenario uses."""
    installed = Registry.installed

    def plus_units(kinds: Iterable[str] | None = None) -> Registry:
        registry = installed(kinds)
        registry.register(Units)
        return registry

    monkeypatch.setattr(Registry, "installed", staticmethod(plus_units))


def hand_checked(folder: Path) -> Path:
    """The hand-checked scenario's configuration, its data path made absolute."""
    data = ROOT / "tests" / "scenarios" / "data" / "hand-checked.csv"
    text = HAND.read_text().replace('"tests/scenarios/data/hand-checked.csv"', f'"{data}"')
    path = folder / "hand.toml"
    path.write_text(text)
    return path


@pytest.mark.usefixtures("with_units")
def test_a_backtest_prints_its_report_and_journals_under_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = hand_checked(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["backtest", str(config)]) == 0
    out = capsys.readouterr().out
    assert "  profit and loss       -14.87\n" in out
    (journal,) = (tmp_path / "runs").iterdir()
    assert journal.name.startswith("hand-")
    assert journal.suffix == ".sqlite"
    assert out.endswith(f"\njournal runs/{journal.name} (6 inputs delivered)\n")


@pytest.mark.usefixtures("with_units")
def test_a_journal_is_reported_and_replayed_the_same(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    journal = tmp_path / "run.sqlite"
    assert main(["backtest", str(hand_checked(tmp_path)), "--journal", str(journal)]) == 0
    printed = capsys.readouterr().out
    assert main(["report", str(journal)]) == 0
    assert printed.startswith(capsys.readouterr().out)
    assert main(["replay", str(journal)]) == 0
    replay = tmp_path / "run-replay.sqlite"
    assert capsys.readouterr().out.startswith(f"replay {replay}: the same ")
    assert replay.is_file()


def test_a_problem_heliograph_can_name_is_one_line_and_status_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["backtest", str(tmp_path / "missing.toml")]) == 2
    assert main(["report", str(tmp_path / "missing.sqlite")]) == 2
    errors = capsys.readouterr().err.splitlines()
    assert errors[0].startswith("heliograph: cannot read ")
    assert errors[1] == f"heliograph: no journal at {tmp_path / 'missing.sqlite'}"


def test_the_command_is_installed() -> None:
    command = Path(sys.executable).parent / "heliograph"
    done = subprocess.run(
        [command, "--version"], capture_output=True, text=True, check=True, timeout=60
    )
    assert done.stdout == f"heliograph {__version__}\n"


@pytest.mark.usefixtures("with_units")
def test_a_replay_that_differs_says_where(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    journal = tmp_path / "run.sqlite"
    assert main(["backtest", str(hand_checked(tmp_path)), "--journal", str(journal)]) == 0
    with sqlite3.connect(journal) as db:  # a signal the run did not make
        (seq,) = db.execute("SELECT min(seq) FROM entries WHERE type LIKE '%Signal'").fetchone()
        db.execute("UPDATE entries SET payload = payload || ' ' WHERE seq = ?", (seq,))
        (count,) = db.execute("SELECT count(*) FROM entries").fetchone()
    db.close()
    capsys.readouterr()
    assert main(["replay", str(journal), "--journal", str(tmp_path / "again.sqlite")]) == 1
    assert capsys.readouterr().out == (
        f"replay {tmp_path / 'again.sqlite'}: differs from entry {seq} "
        f"({count} entries recorded, {count} replayed)\n"
    )


@pytest.mark.usefixtures("with_units")
def test_two_runs_in_the_same_second_get_journals_of_their_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "WallClock", lambda: WallClock(lambda: NOON))
    config = hand_checked(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["backtest", str(config)]) == 0
    assert main(["backtest", str(config)]) == 0
    assert main(["replay", "runs/hand-20261023T120000Z.sqlite"]) == 0
    assert main(["replay", "runs/hand-20261023T120000Z.sqlite"]) == 0
    assert sorted(p.name for p in (tmp_path / "runs").iterdir()) == [
        "hand-20261023T120000Z-2.sqlite",
        "hand-20261023T120000Z-replay-2.sqlite",
        "hand-20261023T120000Z-replay.sqlite",
        "hand-20261023T120000Z.sqlite",
    ]


def test_a_run_that_failed_names_its_journal_and_its_report_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The example from another folder: its data path is relative, so not found."""
    monkeypatch.chdir(tmp_path)
    assert main(["backtest", str(ROOT / "examples" / "trend.toml")]) == 2
    (journal,) = (tmp_path / "runs").iterdir()
    error = capsys.readouterr().err
    assert "examples/data/btc-synthetic.csv" in error
    assert error.endswith(f"(the journal runs/{journal.name} records it)\n")
    assert main(["report", str(journal)]) == 1
    assert "NOT COMPLETE (failed: " in capsys.readouterr().out


def test_a_run_loads_only_the_kinds_its_configuration_uses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[set[str]] = []
    installed = Registry.installed

    def recording(kinds: Iterable[str] | None = None) -> Registry:
        asked.append(set(kinds or ()))
        registry = installed(kinds)
        registry.register(Units)
        return registry

    monkeypatch.setattr(Registry, "installed", staticmethod(recording))
    journal = tmp_path / "run.sqlite"
    assert main(["backtest", str(hand_checked(tmp_path)), "--journal", str(journal)]) == 0
    assert main(["replay", str(journal)]) == 0
    assert asked == [{"data_sources", "forecasters", "sizers", "fills"}] * 2


def test_a_file_that_is_not_a_journal_is_one_line_and_status_2(
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = ROOT / "examples" / "trend.toml"
    assert main(["report", str(config)]) == 2
    assert main(["replay", str(config)]) == 2
    assert capsys.readouterr().err.splitlines() == [f"heliograph: {config} is not a journal"] * 2
