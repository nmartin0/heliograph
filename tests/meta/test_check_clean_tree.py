# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The clean-tree check sees ignored leftovers as well as changes, but not tool caches."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_clean_tree.sh"


def git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    shutil.copy(ROOT / ".gitignore", tmp_path / ".gitignore")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "tracked.py").write_text("x = 1\n")
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "start")
    return tmp_path


def clean(repo: Path) -> bool:
    result = subprocess.run(["bash", str(SCRIPT), str(repo)], capture_output=True, check=False)
    return result.returncode == 0


def test_a_fresh_tree_with_tool_caches_is_clean(repo: Path) -> None:
    for cache in (".pytest_cache", ".hypothesis", "pkg/__pycache__", ".venv"):
        (repo / cache).mkdir(parents=True)
        (repo / cache / "x").write_text("")
    (repo / ".coverage").write_text("")
    assert clean(repo)


@pytest.mark.parametrize(
    "leftover", ["journal.sqlite", "data/raw.parquet", ".controls-in-progress.json", "new.py"]
)
def test_a_file_left_behind_is_caught_even_when_ignored(repo: Path, leftover: str) -> None:
    (repo / leftover).parent.mkdir(parents=True, exist_ok=True)
    (repo / leftover).write_text("")
    assert not clean(repo)


def test_a_changed_tracked_file_is_caught(repo: Path) -> None:
    (repo / "pkg" / "tracked.py").write_text("x = 2\n")
    assert not clean(repo)
