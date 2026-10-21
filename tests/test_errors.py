# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

import pytest

from heliograph.app.config import ConfigError
from heliograph.core.errors import (
    ClockError,
    HeliographError,
    InvalidTypeError,
    InvalidValueError,
    LookAheadError,
    OutOfOrderError,
    ReentrancyError,
)
from heliograph.plugins.errors import PluginDefinitionError, PluginError
from heliograph.plugins.registry import (
    DuplicatePluginError,
    PluginLoadError,
    UnknownPluginError,
)

ERRORS = [
    ClockError,
    ConfigError,
    DuplicatePluginError,
    InvalidTypeError,
    InvalidValueError,
    LookAheadError,
    OutOfOrderError,
    PluginDefinitionError,
    PluginError,
    PluginLoadError,
    ReentrancyError,
    UnknownPluginError,
]


@pytest.mark.parametrize("error", ERRORS)
def test_every_error_can_be_caught_as_a_heliograph_error(error: type[Exception]) -> None:
    assert issubclass(error, HeliographError)


@pytest.mark.parametrize(
    ("error", "builtin"),
    [
        (InvalidValueError, ValueError),
        (InvalidTypeError, TypeError),
        (PluginDefinitionError, TypeError),
        (ReentrancyError, RuntimeError),
        (UnknownPluginError, LookupError),
    ],
)
def test_errors_also_match_the_builtin_they_refine(
    error: type[Exception], builtin: type[Exception]
) -> None:
    assert issubclass(error, builtin)
