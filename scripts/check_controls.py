# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Check that every declared negative control still fires.

A control (tests/controls.toml) breaks one guarantee on purpose and names the
tests that must then fail. This applies each break, runs those tests, and
reports a control as silent unless every named test failed. A silent control
is the finding: the break is not the break it claims, or the tests no longer
check what they appear to (docs/testing.md, "Negative controls").

Elysium kept its breaks the same way after counting nine controls that had
stopped firing unnoticed; this is our own script for the same job.

The file is always restored, including on Ctrl-C. The original is written to
disk before the break, so a run that was killed is undone by the next run.

Usage:
    python scripts/check_controls.py               # every control
    python scripts/check_controls.py --only grace  # controls whose description matches
    python scripts/check_controls.py --list
"""

import argparse
import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTROLS = ROOT / "tests" / "controls.toml"
IN_PROGRESS = ROOT / ".controls-in-progress.json"


@dataclass(frozen=True)
class Control:
    describes: str
    path: str
    old: str
    new: str
    tests: tuple[str, ...]


@dataclass(frozen=True)
class Outcome:
    control: Control
    failed: frozenset[str]
    reasons: dict[str, str] = field(default_factory=dict)
    """Each failed node id's reason as pytest summarised it, such as ``AssertionError``."""

    @property
    def silent(self) -> tuple[str, ...]:
        """Named tests that did not fail."""
        return tuple(test for test in self.control.tests if not covers(test, self.failed))

    @property
    def wrong_reason(self) -> tuple[str, ...]:
        """Named tests that failed only because the broken code could not run.

        A NameError or ImportError from a break says the break was not the
        change it claims; the test may check nothing about the guarantee.
        """
        return tuple(
            test
            for test in self.control.tests
            if covers(test, self.failed)
            and all(
                self.reasons.get(f, "").startswith(WRONG_REASONS)
                for f in self.failed
                if belongs(f, test)
            )
        )

    def summary(self) -> str:
        kinds = sorted({(r.split(":")[0].split() or ["?"])[0] for r in self.reasons.values()})
        return ", ".join(kinds) or "no reason given"


WRONG_REASONS = ("NameError", "ImportError", "ModuleNotFoundError", "SyntaxError")


def covers(test: str, failed: frozenset[str]) -> bool:
    """Whether ``test`` failed: it, one of its parameters, or a test inside it.

    pytest reports a parametrized test as ``name[param]`` and a unittest-style
    class such as a Hypothesis state machine as ``name::runTest``.
    """
    return any(belongs(f, test) for f in failed)


def belongs(node: str, test: str) -> bool:
    """Whether pytest's ``node`` is ``test``, one of its parameters, or a test inside it."""
    return node == test or node.startswith((f"{test}[", f"{test}::"))


def load(path: Path) -> list[Control]:
    with path.open("rb") as handle:
        entries = tomllib.load(handle).get("control", [])
    controls = []
    for entry in entries:
        control = Control(
            describes=entry["describes"],
            path=entry["path"],
            old=entry["old"],
            new=entry["new"],
            tests=tuple(entry["tests"]),
        )
        if not control.tests:
            raise SystemExit(f"control {control.describes!r} names no tests")
        if control.old == control.new:
            raise SystemExit(f"control {control.describes!r} replaces its text with itself")
        controls.append(control)
    return controls


def restore_left_behind(root: Path, record: Path) -> list[str]:
    """Undo a break left by a run that was killed; return the files restored."""
    if not record.exists():
        return []
    try:
        parked: dict[str, str] = json.loads(record.read_text())
    except (OSError, ValueError):
        raise SystemExit(
            f"{record} is unreadable, so a break may still be applied; check `git status`"
        ) from None
    for relative, original in parked.items():
        write(root / relative, original)
    record.unlink()
    return sorted(parked)


def write(path: Path, text: str) -> None:
    """Write ``text`` and drop the file's cached bytecode.

    Python trusts a cached ``.pyc`` whose source has the same modification
    second and size. Two breaks of equal length made within one second match
    on both, and the second control then ran the first one's code and passed.
    """
    path.write_text(text)
    Path(importlib.util.cache_from_source(str(path))).unlink(missing_ok=True)


def apply(root: Path, record: Path, control: Control) -> str:
    """Break the file and return its original text, recorded on disk first."""
    path = root / control.path
    original = path.read_text()
    count = original.count(control.old)
    if count != 1:
        raise SystemExit(
            f"{control.path}: the break for {control.describes!r} matches {count} times, not "
            f"once; a break that matches nothing runs the unbroken code and proves nothing"
        )
    broken = original.replace(control.old, control.new)
    if path.suffix == ".py":
        try:
            compile(broken, str(path), "exec")
        except SyntaxError as error:
            raise SystemExit(
                f"{control.path}: the break for {control.describes!r} does not compile "
                f"({error.msg}); tests failing on that prove nothing about the guarantee"
            ) from None
    record.write_text(json.dumps({control.path: original}))
    write(path, broken)
    return original


FAILED_LINE = re.compile(r"^FAILED (.+?)(?: - (.*))?$", re.MULTILINE)
"""A failure in pytest's summary. Node ids may hold spaces (parameter ids), so
the id runs to pytest's " - " separator or to the end of the line."""


def failed_tests(output: str) -> frozenset[str]:
    """Node ids pytest reported as failed, from its ``-rf`` summary."""
    return frozenset(node for node, _ in FAILED_LINE.findall(output))


def failure_reasons(output: str) -> dict[str, str]:
    """Each failed node id and the reason pytest gave, e.g. ``AssertionError: ...``."""
    return {node: reason.strip() for node, reason in FAILED_LINE.findall(output)}


def run_tests(root: Path, tests: Sequence[str]) -> str:
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-rf",
        "--no-header",
        "-p",
        "no:randomly",
        "-p",
        "no:cacheprovider",
        *tests,
    ]
    # Wide, so pytest does not cut its summary lines (and their reasons) to 80 columns.
    env = {**os.environ, "COLUMNS": "1000"}
    result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, check=False)
    return result.stdout + result.stderr


def check(root: Path, record: Path, control: Control) -> Outcome:
    original = apply(root, record, control)
    try:
        output = run_tests(root, control.tests)
    finally:
        write(root / control.path, original)
        record.unlink(missing_ok=True)
    return Outcome(control, failed_tests(output), failure_reasons(output))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check that the negative controls fire.")
    parser.add_argument("--only", help="run controls whose description contains this text")
    parser.add_argument("--list", action="store_true", help="list the controls and stop")
    args = parser.parse_args(argv)

    for path in restore_left_behind(ROOT, IN_PROGRESS):
        print(f"restored {path}, left broken by a run that was killed")

    controls = [
        c for c in load(CONTROLS) if not args.only or args.only.lower() in c.describes.lower()
    ]
    if not controls:
        print(f"no control matches {args.only!r}", file=sys.stderr)
        return 1
    if args.list:
        for control in controls:
            print(f"{control.describes}  ({control.path}, {len(control.tests)} tests)")
        return 0

    # A terminated run (a cancelled CI job) restores the file like Ctrl-C does.
    signal.signal(signal.SIGTERM, lambda number, _: sys.exit(128 + number))
    bad = []
    for control in controls:
        outcome = check(ROOT, IN_PROGRESS, control)
        if outcome.silent:
            bad.append(outcome)
            print(f"  SILENT  {control.describes}")
            for test in outcome.silent:
                print(f"          still passes: {test}")
        elif outcome.wrong_reason:
            bad.append(outcome)
            print(f"  WRONG   {control.describes}")
            for test in outcome.wrong_reason:
                print(f"          fails only because the code cannot run: {test}")
        else:
            n = len(control.tests)
            print(f"  fires   {control.describes} -> {n} fail ({outcome.summary()})")
    print()
    if bad:
        print(
            f"{len(bad)} of {len(controls)} controls did not fire for the reason they claim: "
            f"the break is not the break it claims, or the tests do not check what they "
            f"appear to."
        )
        return 1
    print(f"All {len(controls)} controls fire.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
