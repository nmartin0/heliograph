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
``volume`` (``volume = ""`` for trades without a size). On a mount of bars
(``kind = "bars"``, ``bar_size = "1m"``), a stream of the same size is those
bars as they come, and a stream of a whole multiple of it is built from
them (an exchange's 1m candles into 1h bars, D33); either way without
``price``, ``volume`` or carried gaps, and only the second takes a grace. A stream's mount
must be the only one on its instrument, since bars take every trade of
their instrument. What can be checked from the text alone is checked here;
whether the named source can serve each mount is checked at composition
(app/mounts.py).

Indicators, forecasters and gates are nodes of the indicator graph: each
has a ``name`` and reads ``inputs``, streams' fields or nodes defined
before it. Combiners, sizers, exits and risk gates are a strategy's
stages: each has a ``name``, and an exit may read graph nodes or streams'
fields. A strategy assembles them (core/pipeline.py)::

    [[strategies]]
    name = "trend"
    price = "btc@1h.close"          # decides at each close of this stream
    forecasters = ["fast", "slow"]
    gates = ["calm"]                 # optional
    combiner = "blend"               # optional with one forecaster
    sizer = "vol"
    exits = ["stop"]                 # optional
    risk_gates = ["limits"]          # optional
    fills = "costs"                  # optional: a fill model, so signals are filled

Each stage belongs to one strategy, and each must be used by one.

The file is validated as a whole and fingerprinted, so every run and signal
can record exactly which configuration produced it.
"""

import hashlib
import re
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
from heliograph.core.time import Timestamp, format_span, from_datetime, parse_span


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


GRAPH_KINDS = frozenset({"indicators", "forecasters", "gates"})
"""Kinds whose plug-ins are nodes of the indicator graph."""
STAGE_KINDS = frozenset({"combiners", "sizers", "exits", "risk_gates", "fills"})


def inputs_rule(kind: str) -> str:
    """What a component of ``kind`` says of its ``inputs`` in a configuration: ``required``
    (a graph node reads something), ``optional`` (an exit may read nodes) or ``none``
    (a stage, a source and the rest read none)."""
    if kind in GRAPH_KINDS:
        return "required"
    return "optional" if kind == "exits" else "none"


"""Kinds whose plug-ins are a strategy's stages."""


class ComponentConfig(BaseModel):
    """One plug-in to build: its kind, its plug-in name, its raw parameters, and for a
    graph node or a stage its own ``name``, and the ``inputs`` it reads.

    An input is a stream's bar field, written ``<mount>@<size>.<field>`` (such
    as ``btc@1h.close``), or the name of a graph node defined earlier; an
    exit reads graph nodes only. The parameters are frozen all the way down,
    so a loaded configuration always matches the digest recorded for it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str
    plugin: str
    params: Mapping[str, Any] = Field(default_factory=dict)
    name: str | None = None
    inputs: tuple[str, ...] = ()

    @field_validator("params", mode="after")
    @classmethod
    def _freeze_params(cls, params: Mapping[str, Any]) -> Mapping[str, Any]:
        return _frozen_params(params)

    @field_serializer("params")
    def _serialize_params(self, params: Mapping[str, Any]) -> dict[str, Any]:
        thawed: dict[str, Any] = _thaw(params)
        return thawed

    def __hash__(self) -> int:
        return hash((self.kind, self.plugin, _hash_key(self.params), self.name, self.inputs))


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


class SinkConfig(BaseModel):
    """One sink: the plug-in that tells a person, and its parameters."""

    model_config = ConfigDict(frozen=True, extra="forbid")

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
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
"""A mount's or an indicator's name: letters, digits, underscores and hyphens."""
_REF = re.compile(r"(?P<mount>[A-Za-z_][A-Za-z0-9_-]*)@(?P<size>[0-9a-z]+)\.(?P<field>[a-z]+)")
_BAR_FIELDS = frozenset({"open", "high", "low", "close", "volume"})


class MountConfig(BaseModel):
    """One mount: a data source, its parameters, and what to take from it.

    ``bar_size`` (such as ``"1h"``) is given for bars and only for bars;
    ``start`` and ``end`` bound a backtest's history. They are TOML
    date-times (ISO 8601 text in JSON) with a UTC offset, never numbers,
    whose unit would be a guess, and lie within the years 1678 to 2261,
    which nanosecond timestamps can hold. A mount that delivers nothing
    between them fails the run unless ``allow_empty`` is set: a span with no
    data is far more often a mistake (a time unit, a symbol) than a market
    that did not trade.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str
    params: Mapping[str, Any] = Field(default_factory=dict)
    instrument: InstrumentConfig
    kind: Literal["trades", "bars"]
    bar_size: str | None = None
    start: Annotated[datetime | None, Field(strict=True)] = None
    end: Annotated[datetime | None, Field(strict=True)] = None
    allow_empty: bool = False

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
    """Bars of one size, built from one mount's trades or from its finer bars."""

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

    def built_from(self, mount: "MountConfig") -> int | None:
        """The size of the finer bars these are built from, or None.

        None for a stream on trades (built by ``TimeBars``) or one that is its
        mount's bars as they come (the same size); otherwise the mount's bar
        size (built by ``BarsFromBars``).
        """
        finer = mount.bar_size_ns
        return finer if finer is not None and finer != self.bar_spec().size else None

    def on_bars(self, mount: "MountConfig") -> list[str]:
        """What is wrong with this stream on ``mount``, a mount of bars (D33)."""
        finer = mount.bar_size_ns
        assert finer is not None  # a mount of bars has a size
        spec = self.bar_spec()
        problems = []
        if spec.size % finer:
            problems.append(
                f"{self.bars} bars cannot be built from mounts.{self.mount}'s "
                f"{mount.bar_size} bars (not a whole number of them)"
            )
        elif spec.size == finer and (self.grace is not None or self.gaps != "skip"):
            problems.append(
                f"{self.bars} bars are mounts.{self.mount}'s own bars, as they come: "
                "they take no grace or gaps"
            )
        if self.gaps != "skip" and spec.size != finer:
            problems.append("bars built from bars skip gaps; carrying them is not offered yet")
        if spec.size > finer and spec.grace >= spec.size - finer:
            problems.append(
                f"grace must be shorter than the bar less one of mounts.{self.mount}'s bars "
                f"({format_span(spec.size - finer)}), or bars would wait on each other"
            )
        if (self.price, self.volume) != ("price", "size"):
            problems.append(
                "price and volume name a trade's values; bars built from bars use the bars' own"
            )
        return problems

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


STRATEGY_ROLES: tuple[tuple[str, str, bool], ...] = (
    ("forecasters", "forecasters", True),
    ("gates", "gates", True),
    ("combiner", "combiners", False),
    ("sizer", "sizers", False),
    ("exits", "exits", True),
    ("risk_gates", "risk_gates", True),
    ("fills", "fills", False),
)
"""What a strategy takes, in the order a pipeline reads: for each field of ``StrategyConfig``
that names components, the kind of plug-in it must be, and whether it takes a list of them."""


class StrategyConfig(BaseModel):
    """One strategy: the stream whose closes it decides at, and its stages by name."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    price: str
    forecasters: tuple[str, ...]
    gates: tuple[str, ...] = ()
    combiner: str | None = None
    sizer: str
    exits: tuple[str, ...] = ()
    risk_gates: tuple[str, ...] = ()
    fills: str | None = None

    def stages(self) -> dict[str, tuple[str, ...]]:
        """The names this strategy uses, by the kind each must be."""
        return {
            "forecasters": self.forecasters,
            "gates": self.gates,
            "combiners": () if self.combiner is None else (self.combiner,),
            "sizers": (self.sizer,),
            "exits": self.exits,
            "risk_gates": self.risk_gates,
            "fills": () if self.fills is None else (self.fills,),
        }


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
    strategies: tuple[StrategyConfig, ...] = ()
    sinks: Mapping[str, SinkConfig] = Field(default_factory=dict)

    @field_validator("sinks", mode="after")
    @classmethod
    def _freeze_sinks(cls, sinks: Mapping[str, SinkConfig]) -> Mapping[str, SinkConfig]:
        bad = [name for name in sinks if not _NAME.fullmatch(name)]
        if bad:
            raise ValueError(
                f"sink names {bad} must be letters, digits, underscores and hyphens, "
                "starting with a letter or underscore"
            )
        return MappingProxyType(dict(sinks))

    @field_serializer("sinks")
    def _serialize_sinks(self, sinks: Mapping[str, SinkConfig]) -> dict[str, Any]:
        return {name: sink.model_dump() for name, sink in sinks.items()}

    @model_validator(mode="after")
    def _sinks_are_live(self) -> "RunConfig":
        if self.sinks and self.mode != "live":
            raise ValueError("sinks tell a person about live signals; a backtest has none")
        return self

    @field_validator("mounts", mode="after")
    @classmethod
    def _freeze_mounts(cls, mounts: Mapping[str, MountConfig]) -> Mapping[str, MountConfig]:
        bad = [name for name in mounts if not _NAME.fullmatch(name)]
        if bad:
            raise ValueError(
                f"mount names {bad} must be letters, digits, underscores and hyphens, "
                "starting with a letter or underscore"
            )
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
            if mount.kind == "bars":
                problems += [f"{where}: {p}" for p in stream.on_bars(mount)]
            # Only bars built here can be partial; a mount's own bars come whole.
            cut = stream.bar_spec()
            built = mount.kind == "trades" or stream.built_from(mount) is not None
            for edge, moment in (("start", mount.start_ts), ("end", mount.end_ts)):
                if built and moment is not None and cut.start_of(moment) != moment:
                    problems.append(
                        f"{where}: mounts.{stream.mount}'s {edge} falls inside a {stream.bars} "
                        f"bar, which would be built from part of its data; put the {edge} "
                        "on a whole bar"
                    )
            key = mount.instrument.instrument().key
            sharing = by_instrument[key]
            if len(sharing) > 1:
                problems.append(
                    f"{where}: mounts {', '.join(sharing)} all deliver {key}; "
                    "a stream needs its mount to be the only one"
                )
            # Bars say their instrument and size, not their grace or gap policy,
            # so two streams of one size on one mount could not be told apart.
            identity = (stream.mount, stream.bar_spec().label())
            if identity in seen:
                problems.append(
                    f"{where}: mounts.{stream.mount} already has {stream.bars} bars "
                    f"(streams[{seen[identity]}])"
                )
            seen.setdefault(identity, index)
        if problems:
            raise ValueError("; ".join(problems))
        return self

    @model_validator(mode="after")
    def _components_read_what_exists(self) -> "RunConfig":
        problems = []
        nodes: set[str] = set()  # the graph nodes defined so far
        named: set[str] = set()
        exits: list[tuple[str, ComponentConfig]] = []
        for index, component in enumerate(self.components):
            where = f"components[{index}] ({component.plugin})"
            kind = component.kind
            if kind not in GRAPH_KINDS and kind not in STAGE_KINDS:
                if component.name is not None or component.inputs:
                    problems.append(
                        f"{where}: only graph nodes (indicators, forecasters, gates) and "
                        "strategy stages take a name and inputs"
                    )
                continue
            noun = kind.removesuffix("s").replace("_", " ")
            if not component.name:
                problems.append(f"{where}: {_article(noun)} {noun} needs a name")
                continue
            if not _NAME.fullmatch(component.name) or component.name in named:
                problems.append(
                    f"{where}: the name {component.name!r} is taken, or not letters, digits, "
                    "underscores and hyphens"
                )
            named.add(component.name)
            if kind in STAGE_KINDS:
                if kind == "exits":
                    exits.append((where, component))
                elif component.inputs:
                    problems.append(f"{where}: a {noun} reads no inputs; exits may")
                continue
            if not component.inputs:
                problems.append(f"{where}: {_article(noun)} {noun} needs inputs")
            for read in component.inputs:
                if read not in nodes and self.stream_field(read) is None:
                    problems.append(
                        f"{where}: reads {read!r}, which is neither an indicator, forecaster or "
                        "gate defined before it nor a stream's field (<mount>@<size>.<field>)"
                    )
            nodes.add(component.name)
        for where, component in exits:
            unknown = [
                read
                for read in component.inputs
                if read not in nodes and self.stream_field(read) is None
            ]
            if unknown:
                problems.append(
                    f"{where}: reads {', '.join(map(repr, unknown))}; an exit reads indicators, "
                    "forecasters, gates or streams' fields"
                )
        problems += self._strategies_assemble()
        problems += self.misaligned(None)
        if problems:
            raise ValueError("; ".join(problems))
        return self

    def _strategies_assemble(self) -> list[str]:
        problems = []
        kinds = {c.name: c.kind for c in self.components if c.name is not None}
        owner: dict[str, str] = {}
        strategy_names: set[str] = set()
        for index, strategy in enumerate(self.strategies):
            where = f"strategies[{index}] ({strategy.name})"
            if not _NAME.fullmatch(strategy.name) or strategy.name in strategy_names:
                problems.append(
                    f"{where}: the name is taken, or not letters, digits, underscores and hyphens"
                )
            strategy_names.add(strategy.name)
            found = self.stream_field(strategy.price)
            if found is None:
                problems.append(
                    f"{where}: price {strategy.price!r} is not a stream's field "
                    "(<mount>@<size>.<field>)"
                )
            elif found[2] == "volume":
                problems.append(f"{where}: price {strategy.price!r} is a volume, not a price")
            if not strategy.forecasters:
                problems.append(f"{where}: a strategy needs at least one forecaster")
            if strategy.combiner is None and len(strategy.forecasters) > 1:
                problems.append(f"{where}: {len(strategy.forecasters)} forecasters need a combiner")
            for kind, names in strategy.stages().items():
                for name in names:
                    if kinds.get(name) != kind:
                        problems.append(f"{where}: {name!r} is not a component of kind {kind}")
                        continue
                    if kind in STAGE_KINDS:
                        if name in owner:
                            problems.append(
                                f"{where}: {name!r} is already strategy {owner[name]}'s; "
                                "a stage keeps state, so it serves one strategy"
                            )
                        owner.setdefault(name, strategy.name)
        for index, component in enumerate(self.components):
            if component.kind in STAGE_KINDS and component.name not in owner:
                problems.append(
                    f"components[{index}] ({component.name}): no strategy uses this "
                    f"{component.kind.removesuffix('s').replace('_', ' ')}"
                )
        return problems

    def misaligned(self, delays: Mapping[str, int] | None) -> list[str]:
        """Graph nodes and strategies reading streams whose bars close at different times.

        A stream's bar is known at its end plus its mount's ``delays`` (how
        long after the fact the source's data arrives; 0 if not given) plus
        its grace, for bars built from trades; bars of a mount of bars,
        as they come or built from them, close on their last part, with no
        grace. A node or strategy reading streams that close at different
        offsets would read one of them a bar late (D27, D33). With ``delays``
        None (the sources' are not known yet), only streams of one mount are
        compared, since their delay is the same; a run compares the rest.
        """
        by_name = {c.name: c for c in self.components if c.name is not None}
        found_streams: dict[str, set[tuple[str, str]]] = {}

        def streams_of(ref: str) -> set[tuple[str, str]]:
            field_ = self.stream_field(ref)
            if field_ is not None:
                mount, stream, _ = field_
                return {(mount, stream.bars)}
            if ref in found_streams:
                return found_streams[ref]
            component = by_name.get(ref)
            reads: set[tuple[str, str]] = set()
            if component is not None and component.kind in GRAPH_KINDS:
                found_streams[ref] = reads  # a cycle cannot recurse forever
                for read in component.inputs:
                    reads |= streams_of(read)
            return reads

        def offset(key: tuple[str, str]) -> int:
            mount_name, bars = key
            mount = self.mounts[mount_name]
            stream = next(s for s in self.streams if s.mount == mount_name and s.bars == bars)
            grace = stream.bar_spec().grace if mount.kind == "trades" else 0
            return (delays or {}).get(mount_name, 0) + grace

        readers: list[tuple[str, set[tuple[str, str]]]] = []
        for index, component in enumerate(self.components):
            if component.kind in GRAPH_KINDS and component.name is not None:
                where = f"components[{index}] ({component.plugin})"
                readers.append((where, set().union(*map(streams_of, component.inputs))))
        for index, strategy in enumerate(self.strategies):
            reads = set(streams_of(strategy.price))
            for name in (*strategy.forecasters, *strategy.gates):
                reads |= streams_of(name)
            for exit_name in strategy.exits:
                exit_ = by_name.get(exit_name)
                for read in exit_.inputs if exit_ is not None else ():
                    reads |= streams_of(read)
            readers.append((f"strategies[{index}] ({strategy.name})", reads))
        problems = []
        for where, reads in readers:
            offsets = {
                f"{mount}@{bars}": offset((mount, bars))
                for mount, bars in sorted(reads)
                if mount in self.mounts
            }
            if delays is None:
                by_mount: dict[str, set[int]] = {}
                for name, value in offsets.items():
                    by_mount.setdefault(name.split("@")[0], set()).add(value)
                if all(len(values) == 1 for values in by_mount.values()):
                    continue
            if len(set(offsets.values())) > 1:
                detail = ", ".join(
                    f"{k} {format_span(v) if v else '0s'}" for k, v in offsets.items()
                )
                problems.append(
                    f"{where}: reads streams whose bars close at different times after their "
                    f"end ({detail}), so it would read one of them a bar late"
                )
        return problems

    def stream_field(self, ref: str) -> tuple[str, StreamConfig, str] | None:
        """The mount, stream and bar field ``ref`` names (``btc@1h.close``), or None."""
        match = _REF.fullmatch(ref)
        if match is None or match["field"] not in _BAR_FIELDS:
            return None
        try:
            size = parse_span(match["size"])
        except InvalidValueError:
            return None
        for stream in self.streams:
            if stream.mount == match["mount"] and stream.bar_spec().size == size:
                return match["mount"], stream, match["field"]
        return None

    def input_name(self, ref: str) -> str:
        """The one name of what ``ref`` reads: ``btc@60m.close`` is ``btc@1h.close``."""
        found = self.stream_field(ref)
        if found is None:
            return ref
        mount, stream, bar_field = found
        return f"{mount}@{stream.bar_spec().label()}.{bar_field}"

    def __hash__(self) -> int:
        mounts = tuple(sorted(self.mounts.items()))
        return hash((self.mode, self.components, mounts, self.streams, self.strategies))


def _article(noun: str) -> str:
    return "an" if noun[0] in "aeiou" else "a"


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


def _problems(exc: ValidationError) -> str:
    """Each problem ``exc`` found, one to a line, as ``where: what`` (the library's own
    text also dumps the input and links to its documentation, which hides the point)."""
    lines = []
    for found in exc.errors(include_url=False, include_input=False, include_context=False):
        where = ".".join(str(part) for part in found["loc"]) or "(the configuration)"
        lines.append(f"  {where}: {str(found['msg']).removeprefix('Value error, ')}")
    return "\n".join(lines)


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
        raise ConfigError(f"invalid run configuration:\n{_problems(exc)}") from exc
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
