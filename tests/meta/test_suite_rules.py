# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Rules about the tests themselves, checked from their source.

Each rule is one that has let a wrong test pass somewhere (docs/testing.md,
"What fails and what skips"; Elysium's sim/tests/test_suite_hygiene.py). Each
is a function over parsed source with three tests: the repository obeys it;
it catches planted offences and passes their legitimate twins; and, because a
rule over an empty set passes for ever, it found something to check. Each has
a declared control in tests/controls.toml.
"""

import ast
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests"
CHECK = ROOT / "scripts" / "check.sh"
COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def _test_files() -> list[Path]:
    return sorted(TESTS.rglob("test_*.py"))


def _parse(source: str) -> ast.Module:
    return ast.parse(source)


def dotted(node: ast.expr) -> str:
    """``pytest.mark.skipif`` for an attribute chain, ``""`` for anything else."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        inner = dotted(node.value)
        return f"{inner}.{node.attr}" if inner else ""
    return ""


def _outside_comprehensions(node: ast.AST) -> Iterator[ast.AST]:
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack += [c for c in ast.iter_child_nodes(current) if not isinstance(c, COMPREHENSIONS)]


def _functions(tree: ast.AST) -> Iterator[ast.FunctionDef]:
    return (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))


# -- 1. all() over a collection never shown to be non-empty ---------------------


def _collections_of(call: ast.Call) -> set[str]:
    """The names an ``all(...)`` call iterates over."""
    if not call.args:
        return set()
    argument = call.args[0]
    if isinstance(argument, COMPREHENSIONS):
        sources: list[ast.expr] = [g.iter for g in argument.generators]
    else:
        sources = [argument]
    return {n.id for source in sources for n in ast.walk(source) if isinstance(n, ast.Name)}


def _guard_names(node: ast.AST) -> set[str]:
    """Names an assertion depends on for its size: outside comprehensions, or the
    collection a comprehension iterates (`[b.ts for b in rows] == [...]`)."""
    names = set()
    for part in _outside_comprehensions(node):
        if isinstance(part, ast.Name):
            names.add(part.id)
        for child in ast.iter_child_nodes(part):
            if isinstance(child, COMPREHENSIONS):
                for generator in child.generators:
                    names |= {n.id for n in ast.walk(generator.iter) if isinstance(n, ast.Name)}
    return names


def _iterates_a_literal(call: ast.Call) -> bool:
    argument = call.args[0] if call.args else None
    if isinstance(argument, COMPREHENSIONS):
        argument = argument.generators[0].iter
    return isinstance(argument, ast.Tuple | ast.List | ast.Set) and bool(argument.elts)


EXEMPT = re.compile(r"# empty is fine: \S")
"""Marks an all() that is right to pass on nothing; a reason must follow the colon."""


def unguarded_alls(source: str) -> list[str]:
    """``assert all(...)`` in a test with no other assertion about the same collection.

    all() over nothing is True. A guard is another assertion depending on the
    same collection's size (`assert rows`, `len(rows) == 3`, `rows[0]`,
    `rows == [...]`, `x in rows`, `[r.ts for r in rows] == [...]`); `assert not
    rows` is no guard. A non-empty literal needs none, and a line marked
    ``# empty is fine: <reason>`` is exempt.
    """
    lines = source.splitlines()
    found = []
    for function in _functions(ast.parse(source)):
        asserts = [n for n in ast.walk(function) if isinstance(n, ast.Assert)]
        for node in asserts:
            test = node.test
            if not (isinstance(test, ast.Call) and dotted(test.func) == "all"):
                continue
            if _iterates_a_literal(test) or EXEMPT.search(lines[node.lineno - 1]):
                continue
            wanted = _collections_of(test)
            guards = [
                a
                for a in asserts
                if a is not node
                and not (isinstance(a.test, ast.UnaryOp) and isinstance(a.test.op, ast.Not))
                and wanted & _guard_names(a.test)
            ]
            if not guards:
                found.append(f"{function.name}:{node.lineno}")
    return found


def test_no_test_asserts_about_every_item_of_nothing() -> None:
    files = _test_files()
    users = [p for p in files if "all(" in p.read_text()]
    offending = [f"{p.name}:{o}" for p in files for o in unguarded_alls(p.read_text())]
    assert len(users) >= 3, "few tests use all(); the rule checks little"
    assert offending == []


def test_the_emptiness_rule_needs_a_guard_on_the_same_collection() -> None:
    def offences(body: str) -> list[str]:
        return unguarded_alls("def test_x():\n" + body)

    assert offences("    rows = f()\n    assert all(r > 0 for r in rows)\n")
    assert offences("    rows = f()\n    assert x == 1\n    assert all(r > 0 for r in rows)\n")
    assert offences("    rows = f()\n    assert not rows\n    assert all(rows)\n")
    assert offences("    assert all(r[0] for r in f())\n")
    assert not offences("    rows = f()\n    assert len(rows) == 3\n    assert all(rows)\n")
    assert not offences("    rows = f()\n    assert rows[0]\n    assert all(r for r in rows)\n")
    assert not offences("    rows = f()\n    assert 4 in rows\n    assert all(r for r in rows)\n")
    assert not offences(
        "    rows = f()\n    assert [r.a for r in rows] == [1]\n    assert all(rows)\n"
    )
    assert not offences("    assert all(n > 0 for n in (1, 2))\n")
    assert not offences("    assert all(r for r in f())  # empty is fine: none is right\n")
    assert offences("    assert all(r for r in f())  # empty is fine\n")
    assert offences("    assert all(r for r in f())  # empty is fine: \n")


# -- 2. shared builders come from tests/support, never a conftest ---------------


def conftest_imports(tree: ast.AST) -> list[int]:
    """Lines importing a conftest module, in any spelling."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""] + [alias.name for alias in node.names]
        else:
            continue
        if any(name.split(".")[-1] == "conftest" for name in names):
            found.append(node.lineno)
    return found


def test_no_test_imports_from_a_conftest() -> None:
    """Two conftests on the path shadow each other; shared code lives in tests/support."""
    files = sorted(TESTS.rglob("*.py"))
    offending = [f"{p.name}:{n}" for p in files for n in conftest_imports(_parse(p.read_text()))]
    assert len(files) > 20
    assert offending == []


def test_the_conftest_rule_catches_every_spelling() -> None:
    for source in (
        "from conftest import x",
        "import conftest",
        "from tests.conftest import x",
        "from tests import conftest",
        "from . import conftest",
        "import tests.conftest as c",
    ):
        assert conftest_imports(_parse(source)) == [1], source
    assert conftest_imports(_parse("from tests.support.world import BTC")) == []


# -- 3. skips name their reason; a missing dependency is a failure ------------

SKIPS = {"pytest.skip", "pytest.mark.skip", "pytest.mark.skipif"}


def bad_skips(tree: ast.AST) -> list[str]:
    """importorskip anywhere, and skips without a reason, called or bare."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            target = dotted(node.func)
            if target == "pytest.importorskip":
                found.append(f"{node.lineno} importorskip")
            elif target in SKIPS:
                given = {k.arg for k in node.keywords}
                if "reason" not in given and not (target == "pytest.skip" and node.args):
                    found.append(f"{node.lineno} {target} without a reason")
        elif isinstance(node, ast.FunctionDef | ast.ClassDef):
            # A bare `@pytest.mark.skip` is an attribute, not a call.
            found += [
                f"{d.lineno} bare {dotted(d)}" for d in node.decorator_list if dotted(d) in SKIPS
            ]
    return found


def test_every_skip_gives_a_reason_and_nothing_skips_for_a_missing_import() -> None:
    """importorskip turned Elysium's missing database into a silent skip for weeks."""
    files = _test_files()
    offending = [f"{p.name}:{o}" for p in files for o in bad_skips(_parse(p.read_text()))]
    assert len(files) > 15
    assert offending == []


def test_the_skip_rule_catches_reasonless_skips_and_passes_reasoned_ones() -> None:
    bad = (
        "import pytest\nnp = pytest.importorskip('numpy')",
        "import pytest\n@pytest.mark.skip\ndef test_x(): pass",
        "import pytest\n@pytest.mark.skip()\ndef test_x(): pass",
        "import pytest\n@pytest.mark.skipif(True)\ndef test_x(): pass",
        "import pytest\ndef test_x():\n    pytest.skip()",
    )
    for source in bad:
        assert bad_skips(_parse(source)), source
    good = (
        "import pytest\n@pytest.mark.skip(reason='POSIX only')\ndef test_x(): pass",
        "import pytest\n@pytest.mark.skipif(True, reason='POSIX only')\ndef test_x(): pass",
        "import pytest\ndef test_x():\n    pytest.skip('POSIX only')",
    )
    for source in good:
        assert bad_skips(_parse(source)) == [], source


# -- 4. tests do not wait on the wall clock ---------------------------------------


def sleeps(tree: ast.AST) -> list[int]:
    """Lines calling anything named sleep, or importing sleep by name."""
    found = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and dotted(node.func).split(".")[-1] == "sleep"
    ]
    found += [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and any(a.name == "sleep" for a in node.names)
    ]
    return sorted(found)


def test_no_test_sleeps() -> None:
    """Time comes from a SimulatedClock; concurrency is forced, never raced."""
    files = _test_files()
    offending = [f"{p.name}:{n}" for p in files for n in sleeps(_parse(p.read_text()))]
    assert offending == []


def test_the_sleep_rule_catches_every_spelling() -> None:
    for source in (
        "import time\ntime.sleep(1)",
        "import asyncio\nasyncio.sleep(1)",
        "from time import sleep as pause",
    ):
        assert sleeps(_parse(source)), source
    assert sleeps(_parse("import time\ntime.monotonic()")) == []


# -- 5. every step of check.sh can fail it ------------------------------------------


def stray_steps(script: str) -> list[str]:
    """Lines between `run`'s definition and the ending that are not a `run` step.

    A command there could fail and still end in "All checks passed." (Elysium's
    lint.sh had a step setting a variable nothing read.)
    """
    start = script.index("\n}\n", script.index("run() {")) + 3
    body = script[start : script.index("if ((${#failed[@]}))")]
    return [
        line
        for line in (raw.strip() for raw in body.splitlines())
        if line and not line.startswith(("#", "run "))
    ]


def test_every_check_runs_through_the_function_that_records_failure() -> None:
    script = CHECK.read_text()
    assert len([line for line in script.splitlines() if line.startswith("run ")]) >= 9
    assert stray_steps(script) == []


def test_the_step_rule_catches_any_command_outside_run() -> None:
    script = 'run() {\n    x\n}\n\nrun "a" true\n{}\nif ((${#failed[@]})); then\n'
    for stray in ("lint-imports", "./scripts/x.sh", "coverage report", "FOO=1 uv run pytest"):
        assert stray_steps(script.replace("{}", stray)) == [stray]
    assert stray_steps(script.replace("{}", "# a comment")) == []


def test_one_failing_step_fails_the_script_and_is_named() -> None:
    """check.sh's own `run` function and ending, driven with one bad and one good step."""
    text = CHECK.read_text()
    function = text[text.index("run() {") : text.index("\n}\n", text.index("run() {")) + 3]
    ending = text[text.index("if ((${#failed[@]}))") :]
    script = "\n".join(
        ["set -uo pipefail", "failed=()", function, 'run "bad" false', 'run "good" true', ending]
    )
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)
    assert result.returncode == 1
    assert re.search(r"^FAILED: bad$", result.stdout, re.MULTILINE)
