"""The core imports only the standard library and itself.

import-linter enforces the direction between Heliograph's own layers; this
test enforces that no third-party package ever reaches the core, whatever
dependencies the project adds later.
"""

import ast
import sys
from pathlib import Path

import heliograph.core

CORE = Path(heliograph.core.__file__).parent


def package_of(path: Path) -> list[str]:
    """The dotted package a core file belongs to, as a list of names."""
    return ["heliograph", *path.parent.relative_to(CORE.parent).parts]


def imported_modules(source: str, package: list[str]) -> set[str]:
    """Every module some source imports, with relative imports made absolute."""
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                names.add(node.module or "")
                continue
            base = package[: len(package) - (node.level - 1)]
            names.add(".".join([*base, *([node.module] if node.module else [])]))
    return names


def test_the_core_imports_only_the_standard_library_and_itself() -> None:
    offenders = {
        f"{path.relative_to(CORE.parent)}: {module}"
        for path in sorted(CORE.rglob("*.py"))
        for module in imported_modules(path.read_text(encoding="utf-8"), package_of(path))
        if module.split(".")[0] not in sys.stdlib_module_names
        and not (module == "heliograph.core" or module.startswith("heliograph.core."))
    }
    assert not offenders, f"the core must use the standard library only: {sorted(offenders)}"


def test_the_check_sees_every_core_module() -> None:
    assert {"bus.py", "clock.py", "engine.py", "errors.py", "messages.py", "time.py"} <= {
        path.name for path in CORE.rglob("*.py")
    }


def test_relative_imports_are_resolved_before_checking() -> None:
    source = "from . import bus\nfrom .clock import Clock\nfrom .. import plugins\n"
    assert imported_modules(source, ["heliograph", "core"]) == {
        "heliograph.core",
        "heliograph.core.clock",
        "heliograph",
    }
