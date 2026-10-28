# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The ``heliograph`` command: run a backtest, report on it, replay it (decision D32).

    heliograph backtest examples/trend.toml          # run, journal, print the report
    heliograph live examples/trend-live.toml         # run on live data until stopped
    heliograph report runs/trend-20261023T101500Z.sqlite
    heliograph replay runs/trend-20261023T101500Z.sqlite
    heliograph serve --data ~/heliograph-data --configs examples
    heliograph gui --engine http://127.0.0.1:8765

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

``live`` runs a live configuration, journalling to ``runs/<name>-live-<UTC time>.sqlite``
as it goes, until its sources end or it is stopped (SIGTERM, or Ctrl-C): the
engine marks the end, the journal is closed complete, and the exit status is 0.

``serve`` runs the engine's own process and its JSON API (D18; see
app/server.py) until it is stopped (SIGTERM, or Ctrl-C), then exits 0, or
1 if a backtest had not stopped by the deadline.
"""

import argparse
import asyncio
import os
import signal
import sys
from collections.abc import Sequence
from pathlib import Path

from heliograph import __version__
from heliograph.app.config import LoadedConfig, RunConfig, load_config, parse_config
from heliograph.app.mounts import KIND as SOURCES
from heliograph.app.report import report
from heliograph.app.run import replay_backtest, run_backtest, run_live_session
from heliograph.app.server import DEFAULT_PORT, serve, settings_from
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

    live = commands.add_parser("live", help="run a live configuration until it is stopped")
    live.add_argument("config", type=Path, help="the session's configuration (TOML, mode live)")
    live.add_argument(
        "--journal", type=Path, help="where to journal it (default: runs/<name>-live-<time>.sqlite)"
    )
    live.set_defaults(command=_live)

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

    serve = commands.add_parser("serve", help="run the engine's process and its JSON API")
    serve.add_argument("--data", help="the folder for its runs (or HELIOGRAPH_DATA)")
    serve.add_argument(
        "--configs", help="a folder of run configurations to offer (or HELIOGRAPH_CONFIGS)"
    )
    serve.add_argument("--host", help="the address to listen on (default 127.0.0.1)")
    serve.add_argument(
        "--port", type=int, help=f"the port (default {DEFAULT_PORT}; 0: any free port)"
    )
    serve.set_defaults(command=_serve)

    window = commands.add_parser(
        "gui", help="serve the window: a web page that speaks to a running engine"
    )
    window.add_argument(
        "--engine",
        default=None,
        help="the engine's address (or HELIOGRAPH_ENGINE; default http://127.0.0.1:8765)",
    )
    window.add_argument("--host", help="the address to listen on (default 127.0.0.1)")
    window.add_argument("--port", type=int, help="the port (default 8766)")
    window.set_defaults(command=_gui)
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


def _live(args: argparse.Namespace) -> int:
    loaded = load_config(args.config)
    clock = WallClock()
    journal: Path = args.journal or _free(
        Path("runs") / f"{args.config.stem}-live-{to_datetime(clock.now()):%Y%m%dT%H%M%SZ}.sqlite"
    )
    journal.parent.mkdir(parents=True, exist_ok=True)
    try:
        asyncio.run(_session(loaded, journal, clock))
    except HeliographError as exc:
        if journal.is_file():
            raise HeliographError(f"{exc} (the journal {journal} records it)") from exc
        raise
    return 0


async def _session(loaded: LoadedConfig, journal: Path, clock: WallClock) -> None:
    """Run the session; SIGTERM and SIGINT cancel it, which ends it cleanly.

    (asyncio.run already cancels on SIGINT; naming both keeps the behaviour in one place.)
    """
    task = asyncio.current_task()
    assert task is not None
    loop = asyncio.get_running_loop()
    for stop in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(stop, task.cancel)
    print(f"live session; journal {journal}", flush=True)
    try:
        summary = await run_live_session(
            loaded, _registry(loaded.config), journal, clock, asyncio.sleep
        )
    except asyncio.CancelledError:
        print(f"stopped; journal {journal}", flush=True)
        return
    print(f"ended; journal {summary.journal} ({summary.delivered} inputs delivered)")


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


def _serve(args: argparse.Namespace) -> int:
    status = serve(settings_from(args.data, args.configs, args.host, args.port))
    if status:
        # A backtest still running, perhaps blocked in a thread Python would wait
        # for: leave now, as the deadline promised, its journal read as interrupted.
        sys.stderr.flush()
        os._exit(status)
    return status


def _gui(args: argparse.Namespace) -> int:
    from heliograph.gui.app import serve as serve_window  # NiceGUI loads only when asked for
    from heliograph.gui.client import DEFAULT_ENGINE

    engine = args.engine or os.environ.get("HELIOGRAPH_ENGINE") or DEFAULT_ENGINE
    host = args.host or "127.0.0.1"
    if host not in ("127.0.0.1", "::1", "localhost"):
        print(
            f"heliograph: the window has no sign-in; anyone who can reach {host} can read "
            "every run the engine holds",
            file=sys.stderr,
        )
    serve_window(engine, host, args.port or 8766)
    return 0


def _free(path: Path) -> Path:
    """``path``, or the first of ``<stem>-2``, ``<stem>-3``... beside it that is free."""
    candidate, number = path, 1
    while candidate.exists():
        number += 1
        candidate = path.with_name(f"{path.stem}-{number}{path.suffix}")
    return candidate


def _registry(config: RunConfig) -> Registry:
    """The installed plug-ins of the kinds ``config`` uses."""
    kinds = {SOURCES, *(c.kind for c in config.components)}
    return Registry.installed(kinds | {"sinks"} if config.sinks else kinds)


if __name__ == "__main__":
    sys.exit(main())
