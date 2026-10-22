# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The mutation wrapper's own logic; running mutmut itself is too slow for every check."""

import tomllib

from scripts import mutation_test

RESULTS = """\
    heliograph.core.streams.x_label__mutmut_1: killed
    heliograph.core.streams.x_label__mutmut_2: survived
    heliograph.core.streams.x_label__mutmut_3: timeout
    heliograph.core.streams.x_label__mutmut_4: survived
    heliograph.core.streams.x_label__mutmut_5: no tests
    heliograph.core.streams.x_label__mutmut_6: not checked
"""

DIFF = """\
--- src/heliograph/core/streams.py
+++ src/heliograph/core/streams.py
@@ -1,3 +1,3 @@
     context
-    if self.gaps not in ("skip", "carry"):
+    if self.gaps not in ("skip", "CARRY"):
"""


def test_every_mutant_not_killed_is_listed_with_its_status() -> None:
    assert mutation_test.unkilled(RESULTS) == [
        ("heliograph.core.streams.x_label__mutmut_2", "survived"),
        ("heliograph.core.streams.x_label__mutmut_3", "timeout"),
        ("heliograph.core.streams.x_label__mutmut_4", "survived"),
        ("heliograph.core.streams.x_label__mutmut_5", "no tests"),
        ("heliograph.core.streams.x_label__mutmut_6", "not checked"),
    ]


def test_a_diff_is_shown_as_its_changed_lines_only() -> None:
    assert mutation_test.changed_lines(DIFF) == [
        '-    if self.gaps not in ("skip", "carry"):',
        '+    if self.gaps not in ("skip", "CARRY"):',
    ]


def test_structural_tests_are_refused_and_behavioural_ones_are_not() -> None:
    chosen = ["tests/core/test_bars.py", "tests/core/test_core_imports.py", "tests/meta/x.py"]
    assert mutation_test.structural(chosen) == chosen[1:]


def test_the_settings_mutate_one_module_against_the_chosen_tests() -> None:
    table = tomllib.loads(mutation_test.settings("src/m.py", ["tests/a.py", "tests/b.py"]))
    mutmut = table["tool"]["mutmut"]
    assert mutmut["only_mutate"] == ["src/m.py"]
    assert mutmut["pytest_add_cli_args_test_selection"] == ["tests/a.py", "tests/b.py"]
    assert "no:randomly" in mutmut["pytest_add_cli_args"]
