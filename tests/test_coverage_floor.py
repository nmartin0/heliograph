# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

from pathlib import Path
from typing import Any

from scripts import coverage_floor


def report(**percents: float) -> dict[str, Any]:
    return {"files": {name: {"summary": {"percent_covered": p}} for name, p in percents.items()}}


def test_files_under_the_floor_are_listed_worst_first() -> None:
    found = coverage_floor.below(report(good=100.0, weak=80.0, worse=61.5), 90.0)
    assert found == ["worse: 61.50%", "weak: 80.00%"]


def test_a_file_exactly_at_the_floor_passes() -> None:
    assert coverage_floor.below(report(edge=90.0, under=89.99), 90.0) == ["under: 89.99%"]


def test_the_floor_is_read_from_the_project_settings() -> None:
    root = Path(__file__).resolve().parent.parent
    assert coverage_floor.floor(root / "pyproject.toml") == 90.0
