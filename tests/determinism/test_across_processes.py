# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The same inputs give the same journal in separate processes: every entry row
identical byte for byte, and the same header.

Replaying inside one process cannot see output that depends on the process:
its hash seed (the iteration order of sets of strings) or its time zone. Each
process here is a fresh interpreter, because only a new process gets a new
hash seed (docs/testing.md, "Determinism across processes").
"""

import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from heliograph.core.clock import SimulatedClock
from heliograph.core.messages import Point
from heliograph.core.sources import HistoryRequest
from heliograph.core.time import NANOS_PER_MINUTE, Timestamp, from_iso
from heliograph.plugins.sqlite_journal import raw_rows, read_header
from heliograph.plugins.store import Store
from tests.support.sources import ScriptedSource, trade
from tests.support.world import BTC, ETH

ROOT = Path(__file__).resolve().parents[2]

# Behind UTC with a half-hour offset and daylight saving; ahead of UTC by
# 5:45 with none; UTC itself.
PROCESSES = [("0", "UTC"), ("1", "America/St_Johns"), ("2", "Asia/Kathmandu")]


def process_env(hash_seed: str, zone: str) -> dict[str, str]:
    """The environment of a fresh interpreter with its own hash seed and time zone."""
    return {**os.environ, "PYTHONHASHSEED": hash_seed, "TZ": zone}


def run(path: Path, hash_seed: str, zone: str, *flags: str) -> list[tuple[Any, ...]]:
    """Run the session in a fresh interpreter; return its entry rows as stored."""
    env = process_env(hash_seed, zone)
    subprocess.run(
        [sys.executable, "-m", "tests.determinism.session", str(path), *flags],
        cwd=ROOT,
        env=env,
        check=True,
        timeout=120,
    )
    return raw_rows(path)


def test_the_same_session_writes_the_same_journal_in_every_process(tmp_path: Path) -> None:
    journals = [run(tmp_path / f"run{seed}.sqlite", seed, zone) for seed, zone in PROCESSES]
    assert len(journals[0]) > 300  # the session did something: trades, bars, marks
    assert journals[1] == journals[0]
    assert journals[2] == journals[0]
    headers = [read_header(tmp_path / f"run{seed}.sqlite") for seed, _ in PROCESSES]
    assert headers[1] == headers[0] == headers[2]


def test_the_comparison_sees_output_that_follows_the_hash_seed(tmp_path: Path) -> None:
    """Without this, a harness that failed to vary the seed would pass for ever."""
    journals = [
        run(tmp_path / f"set{seed}.sqlite", seed, "UTC", "--set-order") for seed, _ in PROCESSES
    ]
    assert len({tuple(journal) for journal in journals}) > 1


def test_the_comparison_sees_output_that_follows_the_time_zone(tmp_path: Path) -> None:
    """The same, for the time zone: one hash seed, three zones, three journals."""
    journals = [
        run(tmp_path / f"zone{i}.sqlite", "0", zone, "--local-time")
        for i, (_, zone) in enumerate(PROCESSES)
    ]
    assert len({tuple(journal) for journal in journals}) == len(PROCESSES)


def test_phase_1_gate_snapshots_published_in_separate_processes_are_identical(
    tmp_path: Path,
) -> None:
    """The same raw files, published by fresh interpreters with other hash seeds and
    time zones, give the same snapshot id and the same bytes in every file."""
    source = tmp_path / "source"
    store = Store(source)
    asyncio.run(_download_examples(store))
    published = []
    for seed, zone in PROCESSES:
        root = tmp_path / f"run{seed}"
        shutil.copytree(source / "raw", root / "raw")
        env = process_env(seed, zone)
        result = subprocess.run(
            [sys.executable, "-m", "tests.determinism.publish", str(root)],
            cwd=ROOT,
            env=env,
            check=True,
            timeout=120,
            capture_output=True,
            text=True,
        )
        folder = root / "published" / result.stdout.strip()
        published.append({p.relative_to(folder): p.read_bytes() for p in folder.rglob("*.*")})
    assert len(published[0]) == 5  # a manifest, and data and quarantine for two datasets
    assert published[1] == published[0]
    assert published[2] == published[0]


async def _download_examples(store: Store) -> None:
    """Two datasets with something superseded and something quarantined."""
    clock = SimulatedClock(from_iso("2026-10-21T12:00:00Z"))
    trades = [trade(m, 100 + m) for m in (3, 1, 2)] + [trade(1, -1)]
    first = ScriptedSource(history_messages=trades)
    request = HistoryRequest(
        instrument=BTC, kind="trades", start=Timestamp(0), end=Timestamp(60 * NANOS_PER_MINUTE)
    )
    await store.download("feed", {"exchange": "kraken"}, first, request, clock)
    clock.advance_to(Timestamp(clock.now() + 1))
    correction = ScriptedSource(history_messages=[trade(2, 222)])
    narrow = HistoryRequest(
        instrument=BTC,
        kind="trades",
        start=Timestamp(2 * NANOS_PER_MINUTE),
        end=Timestamp(3 * NANOS_PER_MINUTE),
    )
    await store.download("feed", {"exchange": "kraken"}, correction, narrow, clock)
    eth = HistoryRequest(
        instrument=ETH, kind="trades", start=Timestamp(0), end=Timestamp(60 * NANOS_PER_MINUTE)
    )
    eth_trades = [
        Point(
            instrument=ETH, event_time=Timestamp(m), received_at=Timestamp(m), values={"price": 1.5}
        )
        for m in (5, 9)
    ]
    await store.download("feed", {}, ScriptedSource(history_messages=eth_trades), eth, clock)
