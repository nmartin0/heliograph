# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import pytest

from heliograph.plugins.manifest import Manifest, Params, Plugin, param
from heliograph.plugins.registry import (
    DuplicatePluginError,
    EntryPointLike,
    PluginLoadError,
    Registry,
    UnknownPluginError,
    installed_entry_points,
)


class WindowParams(Params):
    period: int = param(20, description="Bars in the window", ge=2)


class Sma(Plugin[WindowParams]):
    manifest = Manifest(
        kind="filters", name="sma", version="1", summary="Simple average", params=WindowParams
    )


class Ema(Plugin[WindowParams]):
    manifest = Manifest(
        kind="filters", name="ema", version="1", summary="Exponential average", params=WindowParams
    )


class Log(Plugin[Params]):
    manifest = Manifest(kind="sinks", name="log", version="1", summary="Logs signals")


class Impostor(Plugin[WindowParams]):
    manifest = Manifest(
        kind="filters", name="sma", version="2", summary="Another sma", params=WindowParams
    )


class NoManifest(Plugin[Params]):
    pass


class FasterSma(Sma):
    """Inherits Sma's manifest without declaring its own."""


@dataclass
class FakeEntryPoint:
    name: str
    target: Any

    def load(self) -> Any:
        if isinstance(self.target, Exception):
            raise self.target
        return self.target


def source(*entry_points: FakeEntryPoint) -> Any:
    def by_group(group: str) -> Iterable[EntryPointLike]:
        assert group == "heliograph.filters"
        return entry_points

    return by_group


def test_registered_plugins_are_found_by_kind_and_name() -> None:
    registry = Registry()
    for plugin in (Sma, Ema, Log):
        registry.register(plugin)
    assert registry.get("filters", "sma") is Sma
    assert registry.kinds() == ("filters", "sinks")
    assert registry.names("filters") == ("ema", "sma")
    assert [m.name for m in registry.manifests("filters")] == ["ema", "sma"]


def test_an_unknown_name_fails_loudly_and_lists_the_valid_ones() -> None:
    registry = Registry()
    registry.register(Sma)
    registry.register(Ema)
    with pytest.raises(UnknownPluginError, match="'smaa'; valid names: ema, sma"):
        registry.get("filters", "smaa")


def test_an_unknown_kind_lists_the_known_kinds() -> None:
    registry = Registry()
    registry.register(Sma)
    with pytest.raises(UnknownPluginError, match="kind 'filter'; known kinds: filters"):
        registry.get("filter", "sma")


def test_two_plugins_cannot_share_a_kind_and_name() -> None:
    registry = Registry()
    registry.register(Sma)
    registry.register(Sma)  # the same class again is harmless
    with pytest.raises(DuplicatePluginError, match="filters/sma is provided by both"):
        registry.register(Impostor)


def test_a_class_without_a_manifest_is_refused() -> None:
    with pytest.raises(PluginLoadError, match="declares no Manifest of its own"):
        Registry().register(NoManifest)


def test_a_subclass_cannot_borrow_its_parents_manifest() -> None:
    with pytest.raises(PluginLoadError, match="declares no Manifest of its own"):
        Registry().register(FasterSma)


def test_entry_points_are_discovered() -> None:
    registry = Registry()
    registry.discover(
        "filters",
        entry_points=source(FakeEntryPoint("sma", Sma), FakeEntryPoint("ema", Ema)),
    )
    assert registry.names("filters") == ("ema", "sma")


@pytest.mark.parametrize(
    ("entry_point", "reason"),
    [
        (FakeEntryPoint("sma", ImportError("no module named x")), "failed to load"),
        (FakeEntryPoint("sma", len), "is not a Plugin subclass"),
        (FakeEntryPoint("simple", Sma), "manifest says filters/sma"),
        (FakeEntryPoint("log", Log), "manifest says sinks/log"),
    ],
)
def test_bad_entry_points_fail_with_the_entry_point_named(
    entry_point: FakeEntryPoint, reason: str
) -> None:
    with pytest.raises(PluginLoadError, match=reason) as excinfo:
        Registry().discover("filters", entry_points=source(entry_point))
    assert f"entry point {entry_point.name!r} in 'heliograph.filters'" in str(excinfo.value)


def test_a_bad_entry_point_leaves_the_registry_unchanged() -> None:
    registry = Registry()
    registry.register(Ema)
    with pytest.raises(PluginLoadError):
        registry.discover(
            "filters",
            entry_points=source(FakeEntryPoint("sma", Sma), FakeEntryPoint("bad", len)),
        )
    assert registry.names("filters") == ("ema",)


def test_discovery_refuses_a_name_already_taken() -> None:
    registry = Registry()
    registry.register(Sma)
    with pytest.raises(DuplicatePluginError, match="filters/sma is provided by both"):
        registry.discover("filters", entry_points=source(FakeEntryPoint("sma", Impostor)))
    assert registry.get("filters", "sma") is Sma


def test_the_installed_distributions_can_be_queried() -> None:
    # Heliograph declares no plug-ins yet; the real lookup must still work.
    assert list(installed_entry_points("heliograph.no_such_kind")) == []
