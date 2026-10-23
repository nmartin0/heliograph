# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The ``heliograph`` command: run a backtest, report on it, replay it (decision D32).

    heliograph backtest examples/trend.toml          # run, journal, print the report
    heliograph report runs/trend-20261023T101500Z.sqlite
    heliograph replay runs/trend-20261023T101500Z.sqlite

A backtest's journal goes to ``runs/<config name>-<UTC time>.sqlite`` in the
current folder (``-2``, ``-3``... added if that is taken) unless
``--journal`` names one; a run never overwrites a journal. Relative paths in
a configuration, such as a data file's, are relative to the current folder,
not the configuration's. The installed plug-ins of the kinds a
configuration uses are available.

Exit status: 0 for a complete run, report or identical replay; 1 for a
report of a run that did not complete, or a replay that differs; 2 for a
problem Heliograph can name (a configuration that does not validate, a
file that is not a backtest's journal, a source that failed), printed as
one message. Anything else is a bug and shows its traceback.

This is a front end for one person at a terminal. The engine's own process
and its JSON API (D18) come with the graphical workspace, in Phase 3.
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

from heliograph import __version__
from heliograph.app.config import RunConfig, load_config, parse_config
from heliograph.app.mounts import KIND as SOURCES
from heliograph.app.report import report
from heliograph.app.run import replay_backtest, run_backtest
from heliograph.core.clock import WallClock
from heliograph.core.errors import HeliographError
from heliograph.core.time import to_datetime
from heliograph.plugins.registry import Registry
from heliograph.plugins.sqlite_journal import raw_rows, read_header

DIFFERS = 1
FAILED = 2


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command ``argv`` (``sys.argv[1:]`` if None) names; its exit status."""
    args = _parser().parse_args(argv)
    try:
        return int(args.command(args))
    except HeliographError as exc:
        print(f"heliograph: {exc}", file=sys.stderr)
        return FAILED


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="heliograph", description="Backtest trading strategies and report on the runs."
    )
    parser.add_argument("--version", action="version", version=f"heliograph {__version__}")
    commands = parser.add_subparsers(required=True, metavar="command")

    backtest = commands.add_parser("backtest", help="run a backtest and print its report")
    backtest.add_argument("config", type=Path, help="the run's configuration (TOML)")
    backtest.add_argument(
        "--journal", type=Path, help="where to journal it (default: runs/<name>-<time>.sqlite)"
    )
    backtest.set_defaults(command=_backtest)

    report_ = commands.add_parser("report", help="print the report of a backtest's journal")
    report_.add_argument("journal", type=Path)
    report_.set_defaults(command=_report)

    replay = commands.add_parser(
        "replay", help="run a backtest again from its journal and say whether it is the same"
    )
    replay.add_argument("journal", type=Path)
    replay.add_argument(
        "--journal", dest="out", type=Path, help="where to journal the replay (default: beside it)"
    )
    replay.set_defaults(command=_replay)
    return parser


def _backtest(args: argparse.Namespace) -> int:
    loaded = load_config(args.config)
    clock = WallClock()
    journal: Path = args.journal or _free(
        Path("runs") / f"{args.config.stem}-{to_datetime(clock.now()):%Y%m%dT%H%M%SZ}.sqlite"
    )
    journal.parent.mkdir(parents=True, exist_ok=True)
    try:
        summary = run_backtest(loaded, _registry(loaded.config), journal, clock, asyncio.sleep)
    except HeliographError as exc:
        if journal.is_file():
            raise HeliographError(f"{exc} (the journal {journal} records it)") from exc
        raise
    print(report(summary.journal), end="")
    print(f"\njournal {summary.journal} ({summary.delivered} inputs delivered)")
    return 0


def _report(args: argparse.Namespace) -> int:
    print(report(args.journal), end="")
    return 0 if read_header(args.journal).get("status") == "complete" else DIFFERS


def _replay(args: argparse.Namespace) -> int:
    original: Path = args.journal
    out: Path = args.out or _free(original.with_name(f"{original.stem}-replay{original.suffix}"))
    text = read_header(original).get("config_text")
    # Not a backtest's journal: replay_backtest says so, with no plug-ins needed.
    registry = _registry(parse_config(text).config) if text else Registry()
    replayed = replay_backtest(original, out, registry)
    before, after = raw_rows(original), raw_rows(replayed.journal)
    if before == after:
        print(f"replay {out}: the same {len(after)} entries, byte for byte")
        return 0
    # Entries are numbered from 1 (their seq); past the shorter, the first one missing.
    differs = next(
        (i for i, (a, b) in enumerate(zip(before, after, strict=False)) if a != b),
        min(len(before), len(after)),
    )
    print(
        f"replay {out}: differs from entry {differs + 1} "
        f"({len(before)} entries recorded, {len(after)} replayed)"
    )
    return DIFFERS


def _free(path: Path) -> Path:
    """``path``, or the first of ``<stem>-2``, ``<stem>-3``... beside it that is free."""
    candidate, number = path, 1
    while candidate.exists():
        number += 1
        candidate = path.with_name(f"{path.stem}-{number}{path.suffix}")
    return candidate


def _registry(config: RunConfig) -> Registry:
    """The installed plug-ins of the kinds ``config`` uses."""
    return Registry.installed({SOURCES, *(c.kind for c in config.components)})


if __name__ == "__main__":
    sys.exit(main())
