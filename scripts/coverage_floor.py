# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Fail if any source file's branch coverage is below the floor.

A total hides a weak file (Elysium's 96% total hid a 63% file), so the floor
applies to each file. It reads the data `coverage run` left behind and the
floor from `[tool.heliograph.coverage]` in pyproject.toml. Coverage says what
ran, not what was checked: mutation testing answers the second question
(docs/testing.md).

Usage: coverage run -m pytest && python scripts/coverage_floor.py
"""

import json
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


def floor(pyproject: Path) -> float:
    with pyproject.open("rb") as handle:
        return float(tomllib.load(handle)["tool"]["heliograph"]["coverage"]["floor"])


def below(report: dict[str, Any], minimum: float) -> list[str]:
    """Each file under ``minimum`` percent, as ``path: percent``, worst first."""
    found = [
        (data["summary"]["percent_covered"], path)
        for path, data in report["files"].items()
        if data["summary"]["percent_covered"] < minimum
    ]
    return [f"{path}: {percent:.2f}%" for percent, path in sorted(found)]


def main() -> int:
    minimum = floor(ROOT / "pyproject.toml")
    result = subprocess.run(
        [sys.executable, "-m", "coverage", "json", "-q", "-o", "-"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(result.stderr.strip() or "coverage json failed", file=sys.stderr)
        return 1
    report = json.loads(result.stdout)
    files = report["files"]
    if not files:
        print("coverage recorded no source files; did the tests run under coverage?")
        return 1
    weak = below(report, minimum)
    for line in weak:
        print(f"below {minimum:g}%: {line}")
    total = report["totals"]["percent_covered"]
    print(f"{len(files)} files, {total:.1f}% of lines and branches; floor {minimum:g}% per file")
    return 1 if weak else 0


if __name__ == "__main__":
    sys.exit(main())
