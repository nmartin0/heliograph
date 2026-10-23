# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The plug-in registry: every available plug-in, found through entry points.

A package offers plug-ins by declaring them in its ``pyproject.toml``::

    [project.entry-points."heliograph.filters"]
    moving_average = "mypackage.filters:MovingAverage"

The group is ``heliograph.<kind>`` and the entry-point name must equal the
plug-in's manifest name. Heliograph's own plug-ins are registered the same way,
so built-in and third-party plug-ins are indistinguishable.
"""

import importlib.metadata
from collections.abc import Callable, Iterable
from typing import Any, Protocol

from heliograph.plugins.errors import PluginError
from heliograph.plugins.manifest import Manifest, Plugin

GROUP_PREFIX = "heliograph."

type PluginClass = type[Plugin[Any]]


class UnknownPluginError(PluginError, LookupError):
    """No plug-in of that kind and name is registered."""


class DuplicatePluginError(PluginError):
    """Two plug-ins of the same kind claimed the same name."""


class PluginLoadError(PluginError):
    """An entry point could not be loaded or did not name a valid plug-in."""


class EntryPointLike(Protocol):
    """The part of ``importlib.metadata.EntryPoint`` the registry uses."""

    @property
    def name(self) -> str: ...

    def load(self) -> Any: ...


type EntryPointSource = Callable[[str], Iterable[EntryPointLike]]


def installed_entry_points(group: str) -> Iterable[EntryPointLike]:
    """The entry points of the installed distributions in ``group``."""
    return importlib.metadata.entry_points(group=group)


def installed_kinds() -> tuple[str, ...]:
    """Every kind some installed distribution declares plug-ins of, sorted."""
    groups = importlib.metadata.entry_points().groups
    return tuple(sorted(g.removeprefix(GROUP_PREFIX) for g in groups if g.startswith(GROUP_PREFIX)))


class Registry:
    """All plug-ins known to this process, by kind and name."""

    @classmethod
    def installed(
        cls,
        kinds: Iterable[str] | None = None,
        *,
        entry_points: EntryPointSource = installed_entry_points,
    ) -> "Registry":
        """A registry of the installed plug-ins of ``kinds`` (None: of every kind).

        Name only the kinds a run uses, so a broken plug-in of another kind
        does not stop it.

        Raises:
            PluginLoadError, DuplicatePluginError: as ``discover`` does.
        """
        registry = cls()
        for kind in sorted(set(installed_kinds() if kinds is None else kinds)):
            registry.discover(kind, entry_points=entry_points)
        return registry

    __slots__ = ("_plugins",)

    def __init__(self) -> None:
        self._plugins: dict[str, dict[str, PluginClass]] = {}

    def register(self, plugin: PluginClass) -> None:
        """Add a plug-in class.

        Raises:
            PluginLoadError: if ``plugin`` is not a Plugin subclass with a manifest.
            DuplicatePluginError: if its kind and name are already taken.
        """
        manifest = _manifest_of(plugin)
        self._check_free(manifest, plugin, self._plugins.get(manifest.kind, {}))
        self._plugins.setdefault(manifest.kind, {})[manifest.name] = plugin

    def discover(
        self, kind: str, *, entry_points: EntryPointSource = installed_entry_points
    ) -> None:
        """Register every plug-in declared in the ``heliograph.<kind>`` entry-point group.

        Every entry point is loaded and checked before any is registered, so a
        bad entry point leaves the registry unchanged.

        Raises:
            PluginLoadError: if an entry point fails to load, is not a plug-in,
                or disagrees with its manifest about kind or name.
            DuplicatePluginError: if two plug-ins claim the same name.
        """
        group = GROUP_PREFIX + kind
        staged: dict[str, PluginClass] = dict(self._plugins.get(kind, {}))
        for entry_point in entry_points(group):
            label = f"entry point {entry_point.name!r} in {group!r}"
            try:
                loaded = entry_point.load()
            except Exception as exc:
                raise PluginLoadError(f"{label} failed to load: {exc}") from exc
            manifest = _manifest_of(loaded, label)
            if (manifest.kind, manifest.name) != (kind, entry_point.name):
                raise PluginLoadError(
                    f"{label} names a plug-in whose manifest says {manifest.kind}/{manifest.name}"
                )
            self._check_free(manifest, loaded, staged)
            staged[manifest.name] = loaded
        if staged:
            self._plugins[kind] = staged

    @staticmethod
    def _check_free(manifest: Manifest, plugin: PluginClass, taken: dict[str, PluginClass]) -> None:
        existing = taken.get(manifest.name)
        if existing is not None and existing is not plugin:
            raise DuplicatePluginError(
                f"{manifest.kind}/{manifest.name} is provided by both "
                f"{_qualified_name(existing)} and {_qualified_name(plugin)}"
            )

    def get(self, kind: str, name: str) -> PluginClass:
        """The plug-in class registered as ``kind``/``name``.

        Raises:
            UnknownPluginError: if there is none; the message lists the valid names.
        """
        try:
            return self._plugins[kind][name]
        except KeyError:
            raise UnknownPluginError(_unknown_message(kind, name, self)) from None

    def kinds(self) -> tuple[str, ...]:
        """Every kind with at least one plug-in, sorted."""
        return tuple(sorted(kind for kind, names in self._plugins.items() if names))

    def names(self, kind: str) -> tuple[str, ...]:
        """The names of the plug-ins of ``kind``, sorted."""
        return tuple(sorted(self._plugins.get(kind, {})))

    def manifests(self, kind: str) -> tuple[Manifest, ...]:
        """The manifests of the plug-ins of ``kind``, sorted by name."""
        return tuple(self.get(kind, name).manifest for name in self.names(kind))


def _manifest_of(candidate: object, label: str | None = None) -> Manifest:
    what = label or repr(candidate)
    if not (isinstance(candidate, type) and issubclass(candidate, Plugin)):
        raise PluginLoadError(f"{what} is not a Plugin subclass")
    # Its own manifest only: a subclass must not quietly claim its parent's name.
    manifest = vars(candidate).get("manifest")
    if not isinstance(manifest, Manifest):
        raise PluginLoadError(f"{what} declares no Manifest of its own")
    return manifest


def _qualified_name(plugin: PluginClass) -> str:
    return f"{plugin.__module__}.{plugin.__qualname__}"


def _unknown_message(kind: str, name: str, registry: Registry) -> str:
    if kind not in registry.kinds():
        valid = ", ".join(registry.kinds()) or "none registered"
        return f"no plug-ins of kind {kind!r}; known kinds: {valid}"
    return f"no {kind} plug-in named {name!r}; valid names: {', '.join(registry.names(kind))}"
