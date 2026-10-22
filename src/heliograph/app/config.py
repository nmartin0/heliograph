# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Run configuration: which plug-ins to use, with which parameters.

A run is described by a TOML file::

    mode = "backtest"

    [[components]]
    kind = "filters"
    plugin = "moving_average"
    params = { period = 20 }

    [mounts.btc]
    source = "file"
    params = { path = "data/btc.csv" }
    kind = "trades"
    start = 2026-01-01T00:00:00Z
    end = 2026-02-01T00:00:00Z

    [mounts.btc.instrument]
    symbol = "BTC/USD"
    venue = "kraken"
    asset_class = "crypto"
    quote_currency = "USD"

    [[streams]]
    mount = "btc"
    bars = "1h"

A mount binds a name to a data source and says what to take from it. A
stream builds bars of one size from a mount's trades (core/bars.py), with
an optional ``grace``, ``gaps`` ("skip" or "carry"), ``price`` and
``volume`` (``volume = ""`` for trades without a size). A stream's mount
must be the only one on its instrument, since bars take every trade of
their instrument. What can be checked from the text alone is checked here;
whether the named source can serve each mount is checked at composition
(app/mounts.py).

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
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_serializer,
    field_validator,
    model_validator,
)

from heliograph.core.errors import HeliographError, InvalidValueError
from heliograph.core.messages import Instrument
from heliograph.core.streams import BarSpec
from heliograph.core.time import Timestamp, from_datetime, parse_span


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
        return _frozen_params(params)

    @field_serializer("params")
    def _serialize_params(self, params: Mapping[str, Any]) -> dict[str, Any]:
        thawed: dict[str, Any] = _thaw(params)
        return thawed

    def __hash__(self) -> int:
        return hash((self.kind, self.plugin, _hash_key(self.params)))


def _frozen_params(params: Mapping[str, Any]) -> Mapping[str, Any]:
    """Plug-in parameters, deep-frozen, with every time naming an instant."""
    naive = _naive_times(params, "params")
    if naive:
        raise ValueError(
            "times need a UTC offset (Z or +hh:mm); local times name no instant: "
            + ", ".join(naive)
        )
    frozen: Mapping[str, Any] = _freeze(params)
    return frozen


class InstrumentConfig(BaseModel):
    """An instrument as written in configuration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    venue: str
    asset_class: str
    quote_currency: str

    @model_validator(mode="after")
    def _valid_instrument(self) -> "InstrumentConfig":
        try:
            self.instrument()
        except InvalidValueError as exc:
            raise ValueError(str(exc)) from exc
        return self

    def instrument(self) -> Instrument:
        return Instrument(
            symbol=self.symbol,
            venue=self.venue,
            asset_class=self.asset_class,
            quote_currency=self.quote_currency,
        )


_INT64 = 2**63


class MountConfig(BaseModel):
    """One mount: a data source, its parameters, and what to take from it.

    ``bar_size`` (such as ``"1h"``) is given for bars and only for bars;
    ``start`` and ``end`` bound a backtest's history. They are TOML
    date-times (ISO 8601 text in JSON) with a UTC offset, never numbers,
    whose unit would be a guess, and lie within the years 1678 to 2261,
    which nanosecond timestamps can hold.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str
    params: Mapping[str, Any] = Field(default_factory=dict)
    instrument: InstrumentConfig
    kind: Literal["trades", "bars"]
    bar_size: str | None = None
    start: Annotated[datetime | None, Field(strict=True)] = None
    end: Annotated[datetime | None, Field(strict=True)] = None

    @field_validator("params", mode="after")
    @classmethod
    def _freeze_params(cls, params: Mapping[str, Any]) -> Mapping[str, Any]:
        return _frozen_params(params)

    @field_serializer("params")
    def _serialize_params(self, params: Mapping[str, Any]) -> dict[str, Any]:
        thawed: dict[str, Any] = _thaw(params)
        return thawed

    @field_validator("start", "end", mode="after")
    @classmethod
    def _an_instant(cls, moment: datetime | None) -> datetime | None:
        if moment is None:
            return None
        if moment.utcoffset() is None:
            raise ValueError(f"{moment.isoformat()} needs a UTC offset (Z or +hh:mm)")
        if not -_INT64 <= from_datetime(moment) < _INT64:
            raise ValueError(f"{moment.isoformat()} is outside what a timestamp can hold")
        return moment

    @model_validator(mode="after")
    def _consistent(self) -> "MountConfig":
        if (self.kind == "bars") != (self.bar_size is not None):
            raise ValueError("bar_size is given for bars, and only for bars")
        if self.bar_size is not None:
            try:
                parse_span(self.bar_size)
            except InvalidValueError as exc:
                raise ValueError(str(exc)) from exc
        if self.start is not None and self.end is not None and self.end <= self.start:
            raise ValueError("end must be after start")
        return self

    @property
    def bar_size_ns(self) -> int | None:
        return None if self.bar_size is None else parse_span(self.bar_size)

    @property
    def start_ts(self) -> Timestamp | None:
        return None if self.start is None else from_datetime(self.start)

    @property
    def end_ts(self) -> Timestamp | None:
        return None if self.end is None else from_datetime(self.end)

    def __hash__(self) -> int:
        fields = (self.source, self.instrument, self.kind, self.bar_size, self.start, self.end)
        return hash((fields, _hash_key(self.params)))


class StreamConfig(BaseModel):
    """Bars of one size built from one mount's trades."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mount: str
    bars: str
    grace: str | None = None
    gaps: Literal["skip", "carry"] = "skip"
    price: str = "price"
    volume: str | None = "size"

    @field_validator("volume")
    @classmethod
    def _no_volume(cls, volume: str | None) -> str | None:
        """TOML has no null: ``volume = ""`` says the trades carry no size."""
        return None if volume == "" else volume

    @model_validator(mode="after")
    def _a_bar_spec(self) -> "StreamConfig":
        try:
            self.bar_spec()
        except InvalidValueError as exc:
            raise ValueError(str(exc)) from exc
        return self

    def bar_spec(self) -> BarSpec:
        """How the bars are cut.

        Raises:
            InvalidValueError: for a size or grace that is not a span, or a
                grace not shorter than the bar.
        """
        grace = 0 if self.grace is None else parse_span(self.grace)
        return BarSpec(
            size=parse_span(self.bars),
            grace=grace,
            gaps=self.gaps,
            price=self.price,
            volume=self.volume,
        )


class RunConfig(BaseModel):
    """Everything needed to assemble one run.

    In a backtest every mount needs ``start`` and ``end``; live, none may
    have them, since live data starts now and runs until stopped.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: Literal["backtest", "live"] = "backtest"
    components: tuple[ComponentConfig, ...] = ()
    mounts: Mapping[str, MountConfig] = Field(default_factory=dict)
    streams: tuple[StreamConfig, ...] = ()

    @field_validator("mounts", mode="after")
    @classmethod
    def _freeze_mounts(cls, mounts: Mapping[str, MountConfig]) -> Mapping[str, MountConfig]:
        return MappingProxyType(dict(mounts))

    @field_serializer("mounts")
    def _serialize_mounts(self, mounts: Mapping[str, MountConfig]) -> dict[str, Any]:
        return {name: mount.model_dump() for name, mount in mounts.items()}

    @model_validator(mode="after")
    def _mounts_fit_the_mode(self) -> "RunConfig":
        problems = []
        for name, mount in self.mounts.items():
            bounded = (mount.start is not None, mount.end is not None)
            if self.mode == "backtest" and bounded != (True, True):
                problems.append(f"mounts.{name}: a backtest needs start and end")
            if self.mode == "live" and any(bounded):
                problems.append(f"mounts.{name}: live data has no start or end")
        if problems:
            raise ValueError("; ".join(problems))
        return self

    @model_validator(mode="after")
    def _streams_read_trades(self) -> "RunConfig":
        problems = []
        seen: dict[tuple[str, str], int] = {}
        # Bars are built from every trade of an instrument on the bus, whichever
        # mount it came from, so a stream needs its mount to be the only one
        # delivering that instrument.
        by_instrument: dict[str, list[str]] = {}
        for name, spec in self.mounts.items():
            by_instrument.setdefault(spec.instrument.instrument().key, []).append(name)
        for index, stream in enumerate(self.streams):
            where = f"streams[{index}]"
            mount = self.mounts.get(stream.mount)
            if mount is None:
                problems.append(f"{where}: no mount named {stream.mount!r}")
                continue
            if mount.kind != "trades":
                problems.append(
                    f"{where}: mounts.{stream.mount} delivers bars; streams build bars from trades"
                )
            key = mount.instrument.instrument().key
            sharing = by_instrument[key]
            if len(sharing) > 1:
                problems.append(
                    f"{where}: mounts {', '.join(sharing)} all deliver {key}; "
                    "a stream needs its mount to be the only one"
                )
            identity = (stream.mount, stream.bar_spec().identity())
            if identity in seen:
                problems.append(f"{where}: the same stream as streams[{seen[identity]}]")
            seen.setdefault(identity, index)
        if problems:
            raise ValueError("; ".join(problems))
        return self

    def __hash__(self) -> int:
        mounts = tuple(sorted(self.mounts.items()))
        return hash((self.mode, self.components, mounts, self.streams))


@dataclass(frozen=True, slots=True)
class LoadedConfig:
    """A validated configuration, the exact text it came from, and that text's SHA-256.

    The text is kept because it is the only exact form: TOML has date-times,
    dates and times that JSON does not, so a configuration written out as
    JSON may not read back the same.
    """

    config: RunConfig
    digest: str
    text: str


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
    return LoadedConfig(config, hashlib.sha256(text.encode("utf-8")).hexdigest(), text)


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
