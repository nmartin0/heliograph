# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The window's tests speak to the engine's own API app (tests/support/api.py)."""

from collections.abc import Callable

import pytest

from heliograph.gui.client import Engine
from tests.support.api import engine_holding_a_run


@pytest.fixture(scope="session")
def engine_at(tmp_path_factory: pytest.TempPathFactory) -> Callable[[], Engine]:
    return engine_holding_a_run(tmp_path_factory.mktemp("engine"))
