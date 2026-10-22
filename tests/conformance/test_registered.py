# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Every plug-in this repository registers, through the conformance kit of its kind."""

import importlib.metadata
from typing import Any

import pytest

from heliograph.plugins.registry import GROUP_PREFIX, Registry
from heliograph.testkit import cases, report, run

# Every kind any installed distribution registers a plug-in under, so a new kind
# is in the kit the moment its first plug-in is.
KINDS = sorted(
    group.removeprefix(GROUP_PREFIX)
    for group in importlib.metadata.entry_points().groups
    if group.startswith(GROUP_PREFIX)
)


def registered() -> list[type[Any]]:
    registry = Registry()
    for kind in KINDS:
        registry.discover(kind)
    return [registry.get(kind, name) for kind in KINDS for name in registry.names(kind)]


PLUGINS = registered()


@pytest.mark.parametrize(("plugin", "check"), cases(PLUGINS))
def test_conformance(plugin: Any, check: str) -> None:
    run(plugin, check)


def test_every_installed_plugin_is_in_the_kit() -> None:
    names = {f"{p.manifest.kind}/{p.manifest.name}" for p in PLUGINS}
    assert names == {
        "combiners/weighted",
        "data_sources/ccxt",
        "data_sources/file",
        "data_sources/store",
        "exits/trailing_stop",
        "forecasters/ewmac",
        "forecasters/linear",
        "gates/threshold",
        "instrument_providers/static",
        "indicators/alma",
        "indicators/atr",
        "indicators/bbands",
        "indicators/ema",
        "indicators/hma",
        "indicators/macd",
        "indicators/rsi",
        "indicators/sma",
        "indicators/stddev",
        "indicators/zlema",
        "journals/sqlite",
        "risk_gates/limits",
        "sizers/vol_target",
    }


def test_no_check_is_softened_or_replaced() -> None:
    assert report(PLUGINS) == []
