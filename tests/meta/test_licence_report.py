# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The licence policy as scripts/licence_report.py applies it."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


def load_script() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "scripts" / "licence_report.py"
    spec = importlib.util.spec_from_file_location("licence_report", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


report = load_script()


@pytest.mark.parametrize(
    ("expression", "kind"),
    [
        ("MIT", "permissive"),
        ("BSD-3-Clause", "permissive"),
        ("MIT OR Apache-2.0", "permissive"),
        ("MIT AND BSD-3-Clause", "permissive"),
        ("LGPL-3.0-or-later", "copyleft"),
        ("MPL-2.0", "copyleft"),
        ("GPL-3.0-only OR MIT", "permissive"),
        ("MIT AND GPL-2.0-only", "copyleft"),
        ("AGPL-3.0-only", "denied"),
        ("BUSL-1.1", "denied"),
        ("Apache-2.0 WITH Commons-Clause", "denied"),
        ("SSPL-1.0", "denied"),
        ("LicenseRef-Proprietary", "denied"),
        ("UNKNOWN", "unknown"),
        ("Some custom terms", "unknown"),
        ("GPL-2.0-only AND (MIT OR Apache-2.0)", "copyleft"),
        ("MPL-2.0 AND (MIT OR Apache-2.0)", "copyleft"),
        ("(MIT OR Apache-2.0) AND BSD-3-Clause", "permissive"),
        ("mit or apache-2.0", "unknown"),
        ("MIT or Apache-2.0", "permissive"),
        ("GPL-2.0-only WITH Classpath-exception-2.0", "copyleft"),
        ("MIT OR AGPL-3.0-only", "permissive"),
        ("Business Source License 1.1", "denied"),
        ("Apache 2.0 with Commons Clause", "denied"),
        ("BSL-1.0", "permissive"),
        ("(MIT", "unknown"),
    ],
)
def test_licences_are_classified_by_the_policy(expression: str, kind: str) -> None:
    assert report.classify(expression) == kind


def dependency(name: str, licence: str) -> object:
    return report.Dependency(name, "1.0", licence, report.classify(licence), "")


def test_denied_and_unknown_licences_fail_unless_excepted() -> None:
    deps = [
        dependency("ok", "MIT"),
        dependency("gpl", "GPL-3.0-only"),
        dependency("bad", "AGPL-3.0"),
    ]
    deps.append(dependency("odd", "UNKNOWN"))
    found = report.problems(deps, {})
    assert [line.split(" ")[0] for line in found] == ["bad", "odd"]
    assert report.problems(deps, {"bad": "runs as a separate tool", "odd": "checked by hand"}) == []


def test_the_table_marks_copyleft_dependencies() -> None:
    table = report.table([dependency("lib", "LGPL-3.0-only")])
    assert "| lib | 1.0 | LGPL-3.0-only | copyleft |  |" in table


def test_cells_stay_on_one_line_and_escape_pipes() -> None:
    assert report.cell("MIT | Apache") == "MIT \\| Apache"
    assert report.cell("Line one\nLine two") == "see package metadata"
    assert report.cell("x" * 61) == "see package metadata"


LOCK = {
    "package": [
        {
            "name": "heliograph",
            "version": "0.1",
            "source": {"editable": "."},
            "dependencies": [{"name": "Alpha_Lib"}, {"name": "beta", "extra": ["fast"]}],
            "dev-dependencies": {"dev": [{"name": "pytest"}]},
        },
        {
            "name": "alpha-lib",
            "version": "1.0",
            "dependencies": [{"name": "winonly", "marker": "sys_platform == 'win32'"}],
        },
        {
            "name": "beta",
            "version": "2.0",
            "dependencies": [{"name": "alpha-lib"}],
            "optional-dependencies": {"fast": [{"name": "speedup"}], "slow": [{"name": "unused"}]},
        },
        {"name": "winonly", "version": "3.0"},
        {"name": "speedup", "version": "4.0"},
        {"name": "unused", "version": "5.0"},
        {"name": "pytest", "version": "9.0"},
    ]
}


def test_the_runtime_set_follows_extras_and_every_platform_but_not_dev_groups() -> None:
    assert report.runtime_packages(LOCK) == [
        ("alpha-lib", "1.0"),
        ("beta", "2.0"),
        ("speedup", "4.0"),
        ("winonly", "3.0"),
    ]


def test_a_package_not_installed_here_needs_an_override() -> None:
    deps, found = report.collect([("winonly", "3.0")], {})
    assert "not installed on this platform" in found[0]
    deps, found = report.collect([("winonly", "3.0")], {"overrides": {"WinOnly": "MIT"}})
    assert (found, deps[0].kind) == ([], "permissive")


def test_settings_for_packages_no_longer_used_are_reported() -> None:
    deps = [dependency("pydantic", "MIT")]
    settings = {"exceptions": {"gone-lib": "old reason"}, "overrides": {"Pydantic": "MIT"}}
    assert report.stale_settings(deps, settings) == [
        "[tool.heliograph.licences.exceptions] names gone-lib, which is not a runtime dependency"
    ]
