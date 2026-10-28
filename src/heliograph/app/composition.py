# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The composition root: the one place that turns configuration into objects."""

from typing import Any

from pydantic import ValidationError

from heliograph.app.config import ConfigError, RunConfig
from heliograph.core.errors import InvalidValueError
from heliograph.plugins.manifest import Params, Plugin
from heliograph.plugins.registry import PluginClass, Registry, UnknownPluginError


def build_components(config: RunConfig, registry: Registry) -> tuple[Plugin[Any], ...]:
    """Build every component of ``config``, in order.

    All components are resolved and validated before any is constructed, and
    every problem is reported at once. A bad configuration therefore builds
    nothing.

    Raises:
        ConfigError: listing each component that names an unknown plug-in or
            has invalid parameters, by position.
    """
    resolved: list[tuple[PluginClass, Params]] = []
    problems: list[str] = []
    for index, component in enumerate(config.components):
        where = f"components[{index}] ({component.kind}/{component.plugin})"
        try:
            plugin = registry.get(component.kind, component.plugin)
            params = plugin.manifest.params.model_validate(component.params)
        except UnknownPluginError as exc:
            problems.append(f"{where}: {exc}")
        except ValidationError as exc:
            problems.append(f"{where}: invalid params:\n{exc}")
        else:
            resolved.append((plugin, params))
    if problems:
        raise ConfigError("\n".join(problems))
    return tuple(plugin(params) for plugin, params in resolved)


def build_sinks(config: RunConfig, registry: Registry) -> dict[str, Plugin[Any]]:
    """Build every sink of ``config``, by name.

    All are resolved and validated before any is constructed, and every
    problem is reported at once.

    Raises:
        ConfigError: listing each sink that names an unknown plug-in, has
            invalid parameters, or cannot be built (a missing secret).
    """
    problems: list[str] = []
    resolved: list[tuple[str, PluginClass, Params]] = []
    for name, sink in config.sinks.items():
        where = f"sinks.{name} ({sink.plugin})"
        try:
            plugin = registry.get("sinks", sink.plugin)
            params = plugin.manifest.params.model_validate(sink.params)
        except UnknownPluginError as exc:
            problems.append(f"{where}: {exc}")
        except ValidationError as exc:
            problems.append(f"{where}: invalid params:\n{exc}")
        else:
            resolved.append((name, plugin, params))
    built: dict[str, Plugin[Any]] = {}
    for name, plugin, params in resolved:
        try:
            built[name] = plugin(params)
        except InvalidValueError as exc:
            problems.append(f"sinks.{name} ({config.sinks[name].plugin}): {exc}")
    if problems:
        raise ConfigError("\n".join(problems))
    return built
