# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The same inputs give the same journal in separate processes: every entry row
identical byte for byte, and the same header.

Replaying inside one process cannot see output that depends on the process:
its hash seed (the iteration order of sets of strings) or its time zone. Each
process here is a fresh interpreter, because only a new process gets a new
hash seed (docs/testing.md, "Determinism across processes").
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from heliograph.plugins.sqlite_journal import raw_rows, read_header

ROOT = Path(__file__).resolve().parents[2]

# Behind UTC with a half-hour offset and daylight saving; ahead of UTC by
# 5:45 with none; UTC itself.
PROCESSES = [("0", "UTC"), ("1", "America/St_Johns"), ("2", "Asia/Kathmandu")]


def run(path: Path, hash_seed: str, zone: str, *flags: str) -> list[tuple[Any, ...]]:
    """Run the session in a fresh interpreter; return its entry rows as stored."""
    env = {**os.environ, "PYTHONHASHSEED": hash_seed, "TZ": zone}
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
