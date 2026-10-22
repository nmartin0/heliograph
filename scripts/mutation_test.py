# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Mutation-test one module against the tests that cover it, with mutmut.

    python scripts/mutation_test.py src/heliograph/core/bars.py tests/core/test_bars.py

mutmut changes the module in every small way it can (``<`` for ``<=``, a
constant for another, a value for None) and reruns the named tests on each
change. A change no test notices is a survivor. A survivor is a question,
not a verdict: some changes mean the same thing either way. Read each, then
write the test or decide it does not matter (docs/testing.md). Declared
controls (tests/controls.toml) hold the breaks someone chose; this looks for
the ones nobody thought of.

It runs in a scratch copy of the repository, because mutmut writes its
`mutants/` folder and reads its settings where it runs. Nothing in the
working tree changes. Slow by nature, so it is not part of check.sh.

Structural tests are refused: mutmut injects an import of its own into every
mutated module, which the core-purity test rightly rejects, so including it
would kill every mutant for the wrong reason.
"""

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIPPED = {
    ".git",
    ".venv",
    "mutants",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    ".hypothesis",
    "htmlcov",
}
STRUCTURAL = ("tests/core/test_core_imports.py", "tests/meta/")
"""Tests that read the source rather than run it; mutmut's own edits fail them."""


def settings(module: str, tests: Sequence[str]) -> str:
    """The [tool.mutmut] table appended to the copy's pyproject.toml."""
    selected = ", ".join(f'"{test}"' for test in tests)
    return f"""

[tool.mutmut]
source_paths = ["src/"]
only_mutate = ["{module}"]
also_copy = ["tests/", "scripts/"]
pytest_add_cli_args_test_selection = [{selected}]
pytest_add_cli_args = ["-p", "no:randomly", "-p", "no:cacheprovider", "-q", "-x"]
"""


def structural(tests: Sequence[str]) -> list[str]:
    return [test for test in tests if test.startswith(STRUCTURAL)]


def unkilled(results: str) -> list[tuple[str, str]]:
    """Every mutant mutmut's ``results`` lists that no test killed, with its status.

    Survived is the usual one, but "no tests", "timeout", "suspicious" and "not
    checked" are no better: in each, nothing showed the change was noticed.
    """
    found = []
    for line in results.splitlines():
        name, _, status = line.strip().rpartition(": ")
        if name and status and status != "killed":
            found.append((name, status))
    return found


def changed_lines(diff: str) -> list[str]:
    return [
        line for line in diff.splitlines() if line[:1] in "+-" and line[:3] not in ("+++", "---")
    ]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("module", help="file to mutate, e.g. src/heliograph/core/bars.py")
    parser.add_argument("tests", nargs="+", help="test files or node ids that cover it")
    parser.add_argument("--keep", action="store_true", help="keep the scratch copy")
    args = parser.parse_args(argv)

    for path in (args.module, *(t.split("::")[0] for t in args.tests)):
        if not (ROOT / path).is_file():
            parser.error(f"{path} is not a file in this repository")
    if refused := structural(args.tests):
        parser.error(f"structural tests reject mutmut's own imports: {', '.join(refused)}")

    scratch = Path(tempfile.mkdtemp(prefix="heliograph-mutation-"))
    try:
        shutil.copytree(
            ROOT, scratch, dirs_exist_ok=True, ignore=lambda _, names: SKIPPED & set(names)
        )
        with (scratch / "pyproject.toml").open("a") as pyproject:
            pyproject.write(settings(args.module, args.tests))
        mutmut = [sys.executable, "-m", "mutmut"]
        run = subprocess.run(
            [*mutmut, "run"], cwd=scratch, capture_output=True, text=True, check=False
        )
        if run.returncode != 0:
            print(run.stdout[-3000:], run.stderr[-3000:], sep="\n", file=sys.stderr)
            return run.returncode
        results = subprocess.run(
            [*mutmut, "results"], cwd=scratch, capture_output=True, text=True, check=False
        ).stdout
        # The last progress line holds the totals, e.g. "236/236  🎉 218 ... 🙁 18".
        totals = re.findall(r"(\d+)/(\d+)\s+\S+ (\d+) .*?🙁 (\d+)", run.stdout)
        if totals:
            _, total, killed, survived = totals[-1]
            print(f"{args.module}: {total} mutants, {killed} killed, {survived} survived")
        left = unkilled(results)
        if not left:
            print("Every mutant was killed: each change was noticed by a test.")
            return 0
        print(f"\n{len(left)} mutant(s) not killed, each a change no selected test noticed:\n")
        for name, status in left:
            shown = subprocess.run(
                [*mutmut, "show", name], cwd=scratch, capture_output=True, text=True, check=False
            ).stdout
            print(f"  {name}  ({status})")
            for line in changed_lines(shown):
                print(f"      {line}")
        return 0
    finally:
        if args.keep:
            print(f"\nscratch copy kept at {scratch}")
        else:
            shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
