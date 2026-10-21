# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Every source file we own says so.

Code copied under a permissive licence lives in third_party/ with its own
notice and is exempt (docs/licensing.md).
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HEADER = (
    re.compile(r"# SPDX-FileCopyrightText: 20\d\d(-20\d\d)? Nicholas Martin"),
    re.compile(r"# SPDX-License-Identifier: LicenseRef-Proprietary"),
)
OWNED = ("src", "tests", "scripts", "integrations")
SUFFIXES = {".py", ".pyi", ".sh"}


def is_source(path: Path) -> bool:
    if not path.is_file() or "__pycache__" in path.parts or "third_party" in path.parts:
        return False
    if path.suffix in SUFFIXES:
        return True
    # Scripts without an extension are recognised by their shebang.
    with path.open("rb") as handle:
        return handle.read(2) == b"#!"


def owned_sources() -> list[Path]:
    return sorted(
        path
        for folder in OWNED
        if (ROOT / folder).exists()
        for path in (ROOT / folder).rglob("*")
        if is_source(path)
    )


def header_of(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if lines and lines[0].startswith("#!"):
        lines = lines[1:]
    return lines[: len(HEADER)]


def test_the_check_finds_the_files_it_guards() -> None:
    names = {path.relative_to(ROOT).as_posix() for path in owned_sources()}
    assert {"src/heliograph/core/bus.py", "scripts/check.sh", "tests/test_headers.py"} <= names


@pytest.mark.parametrize(
    "path", owned_sources(), ids=lambda path: path.relative_to(ROOT).as_posix()
)
def test_every_owned_source_file_carries_the_copyright_header(path: Path) -> None:
    lines = header_of(path)
    assert len(lines) == len(HEADER), f"add the two-line SPDX header to {path.name}"
    for pattern, line in zip(HEADER, lines, strict=True):
        assert pattern.fullmatch(line), f"add the two-line SPDX header to {path.name}"


def test_scripts_are_found_by_shebang_and_third_party_code_is_exempt(tmp_path: Path) -> None:
    (tmp_path / "third_party").mkdir()
    files = {
        "run": "#!/bin/sh\n",
        "notes.txt": "plain text\n",
        "stub.pyi": "x: int\n",
        "third_party/vendored.py": "print()\n",
    }
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    found = {path.name for path in tmp_path.rglob("*") if is_source(path)}
    assert found == {"run", "stub.pyi"}


def test_a_later_year_is_accepted() -> None:
    assert HEADER[0].fullmatch("# SPDX-FileCopyrightText: 2026-2027 Nicholas Martin")
