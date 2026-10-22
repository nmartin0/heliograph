# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The conformance kit: every plug-in of a kind meets its kind's contract.

One kit per kind, run on every plug-in by default (docs/testing.md,
"Conformance kits"). The checks are plain functions (testkit/checks.py);
this module turns plug-ins into pytest cases, so a test file needs only::

    import pytest
    from heliograph.testkit import cases, run

    @pytest.mark.parametrize(("plugin", "check"), cases([MyIndicator]))
    def test_conformance(plugin, check):
        run(plugin, check)

That is how this repository tests its own plug-ins (tests/conformance), and
how a plug-in written elsewhere runs the same kit, with this package and
pytest installed (the ``testkit`` extra).

A check held as ``todo`` is an expected failure that fails the run if it
starts passing; one held ``off`` is not run; one ``replaced`` passes when
its named test exists. ``report`` lists all three, with their reasons.

pytest is imported only by ``cases``, so the checks themselves run without
it.
"""

import ast
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from heliograph.plugins.conformance import Conformance, Reference, Setting
from heliograph.testkit.checks import (
    CheckFailedError,
    PluginClass,
    checks_for,
    conformance,
    run_check,
)

__all__ = [
    "CheckFailedError",
    "Conformance",
    "Reference",
    "Setting",
    "cases",
    "report",
    "run",
]


def _label(plugin: PluginClass) -> str:
    return f"{plugin.manifest.kind}/{plugin.manifest.name}"


def _ordered(plugins: Iterable[PluginClass]) -> list[PluginClass]:
    return sorted(set(plugins), key=lambda p: (_label(p), p.__module__, p.__qualname__))


def cases(plugins: Iterable[PluginClass]) -> list[Any]:
    """One pytest case per plug-in and check, marked as each check is held.

    ``settings``, which checks the plug-in's own Conformance, always runs; a
    plug-in whose Conformance cannot be read gets only that case, which fails.
    """
    import pytest

    out = []
    for plugin in _ordered(plugins):
        out.append(pytest.param(plugin, "settings", id=f"{_label(plugin)}:settings"))
        try:
            declared = conformance(plugin)
        except CheckFailedError:
            continue
        for name in checks_for(plugin):
            setting = declared.setting(name)
            if setting.level == "off":
                continue
            marks = []
            if setting.level == "todo":
                marks.append(
                    pytest.mark.xfail(strict=True, raises=CheckFailedError, reason=setting.reason)
                )
            out.append(pytest.param(plugin, name, id=f"{_label(plugin)}:{name}", marks=marks))
    return out


def run(plugin: PluginClass, check: str, root: Path | None = None) -> None:
    """Run one check on ``plugin``, as its Conformance holds it.

    ``root`` is where a replacing test's path is found: by default the
    nearest folder, from the working directory up, holding a pyproject.toml.

    Raises:
        CheckFailedError: saying how the plug-in fails the check, including
            anything the plug-in itself raises.
    """
    root = root or project_root()
    if check == "settings":
        _check_settings(plugin, root)
        return
    setting = conformance(plugin).setting(check)
    if setting.level == "replaced":
        _exists(setting.by, root)
        return
    run_check(checks_for(plugin)[check], plugin)


def project_root(start: Path | None = None) -> Path:
    """The nearest folder from ``start`` (the working directory) up with a pyproject.toml."""
    here = (start or Path.cwd()).resolve()
    for folder in (here, *here.parents):
        if (folder / "pyproject.toml").is_file():
            return folder
    return here


def _check_settings(plugin: PluginClass, root: Path) -> None:
    declared = conformance(plugin)
    known = checks_for(plugin)
    unknown = sorted(set(declared.checks) - set(known))
    if unknown:
        raise CheckFailedError(
            f"holds checks its kind does not have: {', '.join(unknown)} "
            f"(its checks: {', '.join(known)})"
        )
    for name in known:
        setting = declared.setting(name)
        if setting.level == "replaced":
            _exists(setting.by, root)


def _exists(by: str, root: Path) -> None:
    """Confirm the test ``path::[Class::]name[params]`` exists, so a replaced check
    is really replaced: the file is parsed, so a comment or a docstring does not count."""
    path, *names = by.split("::")
    if names:
        names[-1] = names[-1].split("[", 1)[0]
    missing = CheckFailedError(f"the test said to replace this check, {by}, does not exist")
    file = (root / path).resolve()
    if not names or not file.is_relative_to(root.resolve()) or not file.is_file():
        raise missing
    scope: list[ast.stmt] = ast.parse(file.read_text(encoding="utf-8")).body
    *classes, function = names
    for name in classes:
        found = [n for n in scope if isinstance(n, ast.ClassDef) and n.name == name]
        if not found:
            raise missing
        scope = found[0].body
    kinds = (ast.FunctionDef, ast.AsyncFunctionDef)
    if not any(isinstance(n, kinds) and n.name == function for n in scope):
        raise missing


def report(plugins: Iterable[PluginClass]) -> list[str]:
    """Every check held other than as an error, one line each, with its reason."""
    lines = []
    for plugin in _ordered(plugins):
        declared = conformance(plugin)
        for name in checks_for(plugin):
            setting = declared.setting(name)
            if setting.level == "error":
                continue
            replaced = f" by {setting.by}" if setting.level == "replaced" else ""
            lines.append(f"{_label(plugin)}: {name} {setting.level}{replaced}: {setting.reason}")
    return lines
