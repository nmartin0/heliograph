# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Run configuration: which plug-ins to use, with which parameters.

A run is described by a TOML file::

    [[components]]
    kind = "filters"
    plugin = "moving_average"
    params = { period = 20 }

The file is validated as a whole and fingerprinted, so every run and signal
can record exactly which configuration produced it.
"""

import hashlib
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from types import MappingProxyType
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_serializer,
    field_validator,
)

from heliograph.core.errors import HeliographError


class ConfigError(HeliographError):
    """A configuration could not be read, parsed or validated."""


def _freeze(value: Any) -> Any:
    """A read-only copy: mappings become mapping proxies, lists become tuples."""
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _naive_times(value: Any, path: str) -> list[str]:
    """Where a value holds a date-time or time of day without a UTC offset.

    TOML's local date-times and local times name no instant, which the
    project's time rule refuses; local dates (whole days) are fine.
    """
    if isinstance(value, Mapping):
        return [p for key, item in value.items() for p in _naive_times(item, f"{path}.{key}")]
    if isinstance(value, list | tuple):
        return [p for i, item in enumerate(value) for p in _naive_times(item, f"{path}[{i}]")]
    if isinstance(value, datetime | time) and value.utcoffset() is None:
        return [f"{path} = {value.isoformat()}"]
    return []


def _thaw(value: Any) -> Any:
    """A plain copy of a frozen value: mapping proxies become dicts, tuples lists."""
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _hash_key(value: Any) -> Any:
    """A hashable stand-in for a frozen value, equal exactly when the values are."""
    if isinstance(value, Mapping):
        return tuple(sorted((key, _hash_key(item)) for key, item in value.items()))
    if isinstance(value, tuple):
        return tuple(_hash_key(item) for item in value)
    return value


class ComponentConfig(BaseModel):
    """One plug-in to build: its kind, its name, and its raw parameters.

    The parameters are frozen all the way down, so a loaded configuration
    always matches the digest recorded for it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str
    plugin: str
    params: Mapping[str, Any] = Field(default_factory=dict)

    @field_validator("params", mode="after")
    @classmethod
    def _freeze_params(cls, params: Mapping[str, Any]) -> Mapping[str, Any]:
        naive = _naive_times(params, "params")
        if naive:
            raise ValueError(
                "times need a UTC offset (Z or +hh:mm); local times name no instant: "
                + ", ".join(naive)
            )
        frozen: Mapping[str, Any] = _freeze(params)
        return frozen

    @field_serializer("params")
    def _serialize_params(self, params: Mapping[str, Any]) -> dict[str, Any]:
        thawed: dict[str, Any] = _thaw(params)
        return thawed

    def __hash__(self) -> int:
        return hash((self.kind, self.plugin, _hash_key(self.params)))


class RunConfig(BaseModel):
    """Everything needed to assemble one run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    components: tuple[ComponentConfig, ...] = ()


@dataclass(frozen=True, slots=True)
class LoadedConfig:
    """A validated configuration and the SHA-256 of the exact text it came from."""

    config: RunConfig
    digest: str


def parse_config(text: str) -> LoadedConfig:
    """Parse and validate TOML configuration text.

    Raises:
        ConfigError: if the text is not valid TOML or not a valid RunConfig.
    """
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"not valid TOML: {exc}") from exc
    try:
        config = RunConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"invalid run configuration:\n{exc}") from exc
    return LoadedConfig(config, hashlib.sha256(text.encode("utf-8")).hexdigest())


def load_config(path: Path) -> LoadedConfig:
    """Read and validate a TOML configuration file.

    Raises:
        ConfigError: if the file cannot be read or is invalid; the path is named.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    try:
        return parse_config(text)
    except ConfigError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
