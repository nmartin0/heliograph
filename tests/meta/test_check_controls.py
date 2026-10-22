# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The controls script, on a throwaway project, and the declared controls' drift."""

import json
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from scripts import check_controls
from scripts.check_controls import Control

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "mod.py").write_text("def double(x):\n    return x * 2\n")
    (tmp_path / "test_mod.py").write_text(
        "from mod import double\n\n"
        "def test_double():\n    assert double(2) == 4\n\n"
        "def test_type():\n    assert isinstance(double(2), int)\n"
    )
    return tmp_path


def control(*tests: str, old: str = "return x * 2", new: str = "return x * 3") -> Control:
    return Control("doubling doubles", "mod.py", old, new, tests)


def test_a_break_its_test_catches_fires_and_the_file_is_restored(project: Path) -> None:
    record = project / "record.json"
    before = (project / "mod.py").read_text()
    outcome = check_controls.check(project, record, control("test_mod.py::test_double"))
    assert outcome.failed == {"test_mod.py::test_double"}
    assert outcome.silent == ()
    assert (project / "mod.py").read_text() == before
    assert not record.exists()


def test_a_break_its_tests_do_not_check_is_silent(project: Path) -> None:
    outcome = check_controls.check(project, project / "r.json", control("test_mod.py::test_type"))
    assert outcome.silent == ("test_mod.py::test_type",)


def test_every_named_test_must_fail_not_just_one(project: Path) -> None:
    both = control("test_mod.py::test_double", "test_mod.py::test_type")
    outcome = check_controls.check(project, project / "r.json", both)
    assert outcome.silent == ("test_mod.py::test_type",)


def test_a_named_test_fails_through_its_parameters_or_the_tests_inside_it() -> None:
    output = (
        "FAILED tests/a.py::test_p[1-2] - assert 1 == 2\n"
        "FAILED tests/b.py::Machine::runTest - AssertionError\n"
        "FAILED tests/c.py::test_plain\n"
    )
    failed = check_controls.failed_tests(output)
    assert failed == {
        "tests/a.py::test_p[1-2]",
        "tests/b.py::Machine::runTest",
        "tests/c.py::test_plain",
    }
    named = (
        "tests/a.py::test_p",
        "tests/b.py::Machine",
        "tests/c.py::test_plain",
        "tests/c.py::test_pl",
    )
    outcome = check_controls.Outcome(Control("d", "p", "o", "n", named), failed)
    assert outcome.silent == ("tests/c.py::test_pl",)


def test_a_break_that_makes_its_tests_hang_is_reported_and_restored(project: Path) -> None:
    """A break that loops for ever proves nothing, and must not stall the run."""
    before = (project / "mod.py").read_text()
    forever = control("test_mod.py::test_double", new="return next(i for i in iter(int, 1) if i)")
    outcome = check_controls.check(project, project / "r.json", forever, timeout=3)
    assert outcome.hung
    assert (project / "mod.py").read_text() == before


def running(pid: int) -> bool:
    """Whether process ``pid`` still runs (a zombie, killed but not reaped, does not)."""
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    return state.stdout.strip()[:1] not in ("", "Z")


def test_a_hung_control_kills_what_its_tests_started_too(project: Path) -> None:
    """A test that starts a process and hangs leaves nothing running behind it."""
    child = (
        "import subprocess, sys, pathlib; "
        "pathlib.Path('child.pid').write_text(str(subprocess.Popen("
        "[sys.executable, '-c', 'import signal; signal.pause()']).pid)); "
        "return next(i for i in iter(int, 1) if i)"
    )
    outcome = check_controls.check(
        project, project / "r.json", control("test_mod.py::test_double", new=child), timeout=3
    )
    assert outcome.hung
    pid = int((project / "child.pid").read_text())
    assert any(not running(pid) for _ in range(50))  # SIGKILL lands within a few polls


def test_a_break_that_does_not_compile_is_refused(project: Path) -> None:
    before = (project / "mod.py").read_text()
    with pytest.raises(SystemExit, match="does not compile"):
        check_controls.check(project, project / "r.json", control("t", new="return x *"))
    assert (project / "mod.py").read_text() == before


def test_a_test_failing_only_because_the_broken_code_cannot_run_is_the_wrong_reason(
    project: Path,
) -> None:
    """A NameError from the break proves the break ran, not that the test checks the rule."""
    (project / "test_late.py").write_text(
        "def test_type():\n    from mod import double\n    assert isinstance(double(2), int)\n"
    )
    broken = control("test_late.py::test_type", new="return undefined_name")
    outcome = check_controls.check(project, project / "r.json", broken)
    assert outcome.silent == ()
    assert outcome.wrong_reason == ("test_late.py::test_type",)
    right = check_controls.check(project, project / "r.json", control("test_mod.py::test_double"))
    assert right.wrong_reason == ()
    assert right.summary() == "assert"


def test_node_ids_with_spaces_and_their_reasons_are_read_whole() -> None:
    output = (
        "FAILED tests/a.py::test_t[07:32 = x] - Failed: DID NOT RAISE\nFAILED tests/b.py::test_u\n"
    )
    assert check_controls.failure_reasons(output) == {
        "tests/a.py::test_t[07:32 = x]": "Failed: DID NOT RAISE",
        "tests/b.py::test_u": "",
    }


def test_a_break_that_does_not_match_exactly_once_is_refused(project: Path) -> None:
    before = (project / "mod.py").read_text()
    with pytest.raises(SystemExit, match="matches 0 times"):
        check_controls.check(project, project / "r.json", control("t", old="absent"))
    with pytest.raises(SystemExit, match="matches 2 times"):
        check_controls.check(project, project / "r.json", control("t", old="x"))
    assert (project / "mod.py").read_text() == before


def test_a_break_left_by_a_killed_run_is_restored(project: Path) -> None:
    record = project / "record.json"
    original = (project / "mod.py").read_text()
    record.write_text(json.dumps({"mod.py": original}))
    (project / "mod.py").write_text("broken")
    assert check_controls.restore_left_behind(project, record) == ["mod.py"]
    assert (project / "mod.py").read_text() == original
    assert not record.exists()


def test_a_break_the_same_size_as_the_last_in_the_same_second_runs_its_own_code(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Python trusts a cached .pyc whose source has the same mtime second and size.

    Two equal-length breaks of one file within a second once made the second
    control run the first one's code. Every write here is pinned to one mtime,
    so the bytecode cache cannot tell the versions apart.
    """
    (project / "mod.py").write_text(
        "def double(x):\n    return x * 2\n\ndef half(x):\n    return x // 2\n"
    )
    (project / "test_mod.py").write_text(
        "from mod import double, half\n\n"
        "def test_double():\n    assert double(3) == 6\n\n"
        "def test_half():\n    assert half(6) == 3\n"
    )
    run_tests = check_controls.run_tests

    def pinned(root: Path, tests: Sequence[str], timeout: float = check_controls.TIMEOUT) -> str:
        os.utime(root / "mod.py", (1_700_000_000, 1_700_000_000))
        return run_tests(root, tests, timeout)

    monkeypatch.setattr(check_controls, "run_tests", pinned)
    record = project / "r.json"
    halving = Control("halving", "mod.py", "x // 2", "x // 3", ("test_mod.py::test_half",))
    doubling = Control("doubling", "mod.py", "x * 2", "x * 3", ("test_mod.py::test_double",))
    assert check_controls.check(project, record, halving).silent == ()
    assert check_controls.check(project, record, doubling).silent == ()


# -- the declared controls, without running them -------------------------------


def test_every_declared_break_still_matches_its_file_exactly_once() -> None:
    """Code moved under a control orphans it; caught here, on every check."""
    controls = check_controls.load(check_controls.CONTROLS)
    assert len(controls) >= 20
    stale = [c.describes for c in controls if (ROOT / c.path).read_text().count(c.old) != 1]
    assert stale == []


def test_every_test_a_control_names_exists() -> None:
    named = sorted({t for c in check_controls.load(check_controls.CONTROLS) for t in c.tests})
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:randomly", *named],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-2000:]
