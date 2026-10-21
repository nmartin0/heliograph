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
from pathlib import Path
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

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
        frozen: Mapping[str, Any] = _freeze(params)
        return frozen


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
