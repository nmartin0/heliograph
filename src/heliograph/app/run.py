# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Running a backtest from its configuration, journalled, and replaying it.

``run_backtest`` assembles a run from a loaded configuration: the mounts
are built and checked (app/mounts.py), each mount's history becomes a feed
through the bridge (app/bridge.py), the streams' bars are built from the
trades (core/bars.py), the indicators, forecasters and gates are computed
from the bars through the graph (core/graph.py), each strategy decides on
them at the closes of its price stream and signals (core/pipeline.py), and
the engine replays it all in time order onto a bus that journals every
message to a new SQLite file before delivering it.

The journal's header says what produced it, so a run can be reproduced and
questioned later:

- ``config_text`` and ``config_digest``: the configuration's exact text
  and its SHA-256 (the text, not a JSON form: TOML's date-times would not
  read back the same);
- ``mounts``: each mount's source, plug-in version and validated
  parameters, defaults included, as JSON;
- ``snapshots``: the ids of the store snapshots read, as a JSON list;
- ``components``: each named component's kind, plug-in, version, validated
  parameters with their defaults, and inputs, as JSON;
- ``strategies``: each strategy's configuration, as JSON;
- ``start`` and ``end``: the interval the mounts asked for, and ``until``:
  where the run stopped (``end`` plus the longest live delay and grace, so
  the last trades arrive and the last bars close), in nanoseconds;
- ``heliograph``: the version that ran it.

``replay_backtest`` rebuilds a complete run from its journal alone: the
configuration from its recorded text, the inputs from its entries, the
components from the registry, refused unless each has the version and the
parameters the run recorded. A replay's entries are the original's, byte
for byte (tests/app/test_run.py).

Components of any other kind than the graph's and the strategies' are
refused rather than run without them.
"""

import json
import sys
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Any, cast

from heliograph.app.bridge import HistoryFeed, SourceLoop
from heliograph.app.composition import build_components
from heliograph.app.config import (
    GRAPH_KINDS,
    STAGE_KINDS,
    ComponentConfig,
    ConfigError,
    LoadedConfig,
    RunConfig,
    StrategyConfig,
    parse_config,
)
from heliograph.app.limits import Sleep
from heliograph.app.mounts import Mount, build_mounts
from heliograph.core.alarms import AlarmFired
from heliograph.core.bars import TimeBars
from heliograph.core.bus import MessageBus
from heliograph.core.clock import Clock, SimulatedClock
from heliograph.core.engine import BacktestEngine
from heliograph.core.errors import InvalidValueError
from heliograph.core.graph import BarField, Graph, Indicator, IndicatorValues
from heliograph.core.journal import runs
from heliograph.core.messages import Bar, Point, RunEnded, Settled
from heliograph.core.pipeline import (
    Combiner,
    Exit,
    RiskGate,
    Signal,
    SignalRefused,
    Sizer,
    Strategy,
    StrategySpec,
)
from heliograph.core.sources import DataSource
from heliograph.core.streams import StreamSpec
from heliograph.core.time import Timestamp
from heliograph.plugins.manifest import Plugin
from heliograph.plugins.registry import Registry
from heliograph.plugins.sqlite_journal import (
    RESERVED_HEADER,
    SqliteJournal,
    SqliteJournalParams,
    read_entries,
    read_header,
)

JOURNALLED = (Point, Bar, AlarmFired, Settled, RunEnded, IndicatorValues, Signal, SignalRefused)
"""Every message type a backtest run journals."""


@dataclass(frozen=True, slots=True)
class RunSummary:
    """What a run did: where its journal is, how many input messages it delivered."""

    journal: Path
    delivered: int
    header: Mapping[str, str]


def run_backtest(
    loaded: LoadedConfig,
    registry: Registry,
    journal: Path,
    clock: Clock,
    sleep: Sleep,
) -> RunSummary:
    """Run the backtest ``loaded`` describes, journalling it to a new file.

    ``clock`` and ``sleep`` are the real time, as for ``build_mounts``. The
    run itself is on a simulated clock, from the earliest mount's start to
    the latest's end plus the longest live delay and grace, so the last
    trades become known and the last bars close before it stops.

    Raises:
        ConfigError: if the configuration is live, has components of a kind
            a run does not run, has no mounts, a component cannot be built
            or is not what its kind needs, or a mount cannot be served.
        InvalidValueError: if ``journal`` already exists.
        Whatever a source raised; the journal then records the failure.
    """
    config = loaded.config
    _runnable(config)
    if journal.exists():
        raise InvalidValueError(f"{journal} exists; a run writes a new journal")
    components = _components(config, registry)
    mounts = build_mounts(config, registry, clock, sleep)
    requests = [m.request for m in mounts if m.request is not None]
    start = Timestamp(min(r.start for r in requests))
    end = Timestamp(max(r.end for r in requests))
    late = max(m.source.capabilities.live_delay for m in mounts)
    grace = max((stream.bar_spec().grace for stream in config.streams), default=0)
    until = Timestamp(end + late + grace)
    header = _header(loaded, mounts, components, start, end, until)
    sources: dict[int, DataSource] = {id(m.source): m.source for m in mounts}
    with SourceLoop() as loop:
        try:
            with _assembled(config, journal, header, components) as engine:
                feeds: list[HistoryFeed] = []
                try:
                    for mount, wanted in zip(mounts, requests, strict=True):
                        feeds.append(HistoryFeed(loop, mount.source, wanted))
                    delivered = engine.run(*feeds, until=until)
                finally:
                    for feed in feeds:
                        feed.close()
        finally:
            _close_all(loop, sources.values())
    return RunSummary(journal, delivered, header)


type Built = list[tuple[ComponentConfig, Plugin[Any]]]

_NEEDS = {
    "combiners": ("combine", "a combiner (it has no combine)"),
    "sizers": ("target", "a sizer (it has no target)"),
    "exits": ("adjust", "an exit (it has no adjust)"),
    "risk_gates": ("check", "a risk gate (it has no check)"),
    "risk_gates/sent": ("sent", "a risk gate (it has no sent)"),
}


def _components(config: RunConfig, registry: Registry) -> Built:
    """Every component with its plug-in built, checked to be what its kind needs.

    Raises:
        ConfigError: listing every component that names an unknown plug-in,
            has invalid parameters, is not an indicator (graph kinds) or the
            stage its kind is, is given another number of inputs than it
            reads, or (a combiner) has weights for other forecasters than
            its strategy's.
    """
    built = build_components(config, registry)
    problems = []
    for index, (component, plugin) in enumerate(zip(config.components, built, strict=True)):
        where = f"components[{index}] ({component.name})"
        arity = getattr(plugin, "arity", None)
        if component.kind in GRAPH_KINDS:
            if not callable(getattr(plugin, "update", None)) or not isinstance(arity, int):
                problems.append(f"{where}: {component.plugin} is not an indicator")
                continue
        elif component.kind in STAGE_KINDS:
            needs = [_NEEDS[component.kind]]
            if component.kind == "risk_gates":
                needs.append(_NEEDS["risk_gates/sent"])
            lacking = [
                noun for method, noun in needs if not callable(getattr(plugin, method, None))
            ]
            if lacking:
                problems.append(f"{where}: {component.plugin} is not {lacking[0]}")
                continue
            if component.kind != "exits":
                continue
            arity = arity if isinstance(arity, int) else 0
        if len(component.inputs) != arity:
            problems.append(
                f"{where}: reads {len(component.inputs)} inputs; {component.plugin} takes {arity}"
            )
    by_name = {c.name: p for c, p in zip(config.components, built, strict=True)}
    for strategy in config.strategies:
        combiner = by_name.get(strategy.combiner) if strategy.combiner else None
        expects = getattr(combiner, "expects", None)
        wanted = expects() if callable(expects) else None
        if wanted is not None and wanted != set(strategy.forecasters):
            problems.append(
                f"strategy {strategy.name}: combiner {strategy.combiner} has weights for "
                f"{sorted(wanted)}, but the strategy's forecasters are "
                f"{sorted(strategy.forecasters)}"
            )
    if problems:
        raise ConfigError("\n".join(problems))
    return list(zip(config.components, built, strict=True))


class _Only:
    """The combiner of a strategy with one forecaster: its forecast, as it is."""

    def combine(self, forecasts: Mapping[str, float]) -> float:
        (forecast,) = forecasts.values()
        return forecast


def _runnable(config: RunConfig) -> None:
    """Raise unless ``config`` is a backtest this module can run."""
    if config.mode != "backtest":
        raise ConfigError("this configuration is live; run_backtest runs backtests")
    later = sorted({c.kind for c in config.components if c.kind not in GRAPH_KINDS | STAGE_KINDS})
    if later:
        raise ConfigError(
            f"components of kind {', '.join(later)} are not run: a run runs indicators, "
            "forecasters and gates, and its strategies' combiners, sizers, exits and risk gates"
        )
    if not config.mounts:
        raise ConfigError("a backtest needs at least one mount")


def _close_all(loop: SourceLoop, sources: Iterable[DataSource]) -> None:
    """Close every source; a failure to close one does not leave the others open.

    The first failure is raised once all are closed, unless the run is already
    raising, whose error matters more.
    """
    failures: list[BaseException] = []
    for source in sources:
        try:
            loop.run(source.close())
        except Exception as exc:
            failures.append(exc)
    if failures and sys.exc_info()[1] is None:
        raise failures[0]


REQUIRED = ("kind", "config_text", "config_digest", "start", "until", "heliograph")
"""The header keys a run's journal must have to be replayed."""


def replay_backtest(journal: Path, out: Path, registry: Registry) -> RunSummary:
    """Run again what ``journal`` recorded, from its header and inputs alone, into ``out``.

    The configuration is read from the text recorded and checked against its
    digest; the inputs and where each run stopped come from the entries. Only
    a complete run replays: a failed or unfinished one stopped where nothing
    recorded says, so its replay could not match it. The replay's header is
    the original's, but for the version of Heliograph that replayed it.

    Raises:
        InvalidValueError: if ``journal`` is not a complete backtest's
            journal, its configuration no longer matches its digest, a
            component's installed version or parameters are not the ones the
            run recorded, or ``out`` already exists.
        ConfigError: if a component can no longer be built (its plug-in
            is gone, or its parameters no longer valid).
    """
    written = read_header(journal)
    if written.get("kind") != "backtest":
        raise InvalidValueError(f"{journal} is not the journal of a backtest run")
    missing = [key for key in REQUIRED if key not in written]
    if missing:
        raise InvalidValueError(f"{journal}'s header lacks {', '.join(missing)}")
    if written.get("status") != "complete":
        raise InvalidValueError(
            f"{journal} records a run that did not finish ({written.get('status')}); "
            "only a complete run replays"
        )
    if out.exists():
        raise InvalidValueError(f"{out} exists; a replay writes a new journal")
    loaded = parse_config(written["config_text"])
    if loaded.digest != written["config_digest"]:
        raise InvalidValueError(f"{journal}'s configuration does not match its digest")
    _runnable(loaded.config)
    components = _components(loaded.config, registry)
    # A journal written before indicators ran records none.
    recorded = json.loads(written.get("components", "{}"))
    for component, plugin in components:
        entry = recorded.get(component.name, {})
        installed = plugin.manifest.version
        if entry.get("version") != installed:
            raise InvalidValueError(
                f"{component.name} ran {component.plugin} version {entry.get('version')}; "
                f"version {installed} is installed, and its values could differ"
            )
        params = plugin.params.model_dump(mode="json")
        if entry.get("params") != params:
            raise InvalidValueError(
                f"{component.name} ran with parameters {entry.get('params')}; they would now "
                f"be {params} (a default changed?), and its values could differ"
            )
    # The journal writes its own format, versions and status; the rest is the run's.
    header = {k: v for k, v in written.items() if k not in RESERVED_HEADER}
    header["heliograph"] = version("heliograph")
    replayed = runs(read_entries(journal, JOURNALLED))
    delivered = 0
    with _assembled(loaded.config, out, header, components) as engine:
        for inputs, until in replayed:
            delivered += engine.run(inputs, until=until)
    return RunSummary(out, delivered, header)


@contextmanager
def _assembled(
    config: RunConfig,
    journal: Path,
    header: Mapping[str, str],
    components: Built,
) -> Iterator[BacktestEngine]:
    """An engine on a journalled bus: streams build bars, the graph its nodes, strategies decide."""
    start = Timestamp(int(header["start"]))
    clock = SimulatedClock(start)
    params = SqliteJournalParams(path=str(journal))
    with SqliteJournal(params, dict(header)) as sink:
        bus = MessageBus(clock, sink)
        engine = BacktestEngine(clock, bus)
        for stream in config.streams:
            instrument = config.mounts[stream.mount].instrument.instrument()
            spec = StreamSpec(instrument=instrument, bars=stream.bar_spec())
            TimeBars(spec, bus, engine.scheduler)
        nodes = [(c, cast(Indicator, p)) for c, p in components if c.kind in GRAPH_KINDS]
        if nodes or config.strategies:
            graph = Graph(bus)
            # A stream field may be written two ways (btc@1h.close, btc@60m.close):
            # the graph knows it by one name.
            reads = [read for component, _ in nodes for read in component.inputs]
            reads += [strategy.price for strategy in config.strategies]
            reads += [read for c, _ in components if c.kind == "exits" for read in c.inputs]
            fields = {
                config.input_name(read): read
                for read in reads
                if config.stream_field(read) is not None
            }
            for name, read in sorted(fields.items()):
                found = config.stream_field(read)
                assert found is not None
                mount, stream, bar_field = found
                instrument = config.mounts[mount].instrument.instrument()
                size = stream.bar_spec().size
                graph.bar_input(name, instrument, size, cast(BarField, bar_field))
            for component, indicator in nodes:
                assert component.name is not None  # RunConfig requires it of graph nodes
                graph.add(
                    component.name, indicator, [config.input_name(r) for r in component.inputs]
                )
            for strategy in config.strategies:
                spec_ = _strategy_spec(config, strategy, components, header["config_digest"])
                Strategy(spec_, graph, bus)
        yield engine


def _strategy_spec(
    config: RunConfig, strategy: StrategyConfig, components: Built, scope: str
) -> StrategySpec:
    """A strategy's graph nodes and stage objects, from its configuration.

    ``scope`` (the configuration's digest) makes its signals' ids its own.
    """
    stages = {c.name: (c, p) for c, p in components if c.kind in STAGE_KINDS}
    found = config.stream_field(strategy.price)
    assert found is not None  # RunConfig requires it
    combiner = cast(Combiner, stages[strategy.combiner][1]) if strategy.combiner else _Only()
    return StrategySpec(
        name=strategy.name,
        instrument=config.mounts[found[0]].instrument.instrument(),
        price=config.input_name(strategy.price),
        forecasters=strategy.forecasters,
        gates=strategy.gates,
        scope=scope,
        combiner=combiner,
        sizer=cast(Sizer, stages[strategy.sizer][1]),
        exits=tuple(
            (
                name,
                cast(Exit, stages[name][1]),
                tuple(config.input_name(read) for read in stages[name][0].inputs),
            )
            for name in strategy.exits
        ),
        risk_gates=tuple((name, cast(RiskGate, stages[name][1])) for name in strategy.risk_gates),
    )


def _header(
    loaded: LoadedConfig,
    mounts: Sequence[Mount],
    built: "Built",
    start: Timestamp,
    end: Timestamp,
    until: Timestamp,
) -> dict[str, str]:
    """What the journal says about the run that wrote it."""
    described: dict[str, Any] = {}
    snapshots: set[str] = set()
    for mount in mounts:
        spec = loaded.config.mounts[mount.name]
        plugin = cast(Plugin[Any], mount.source)
        params = plugin.params.model_dump(mode="json")
        described[mount.name] = {
            "source": spec.source,
            "version": plugin.manifest.version,
            "params": params,
        }
        if spec.source == "store":
            snapshots.add(params["snapshot"])
    components: dict[str, Any] = {}
    for component, plugin in built:
        components[str(component.name)] = {
            "kind": component.kind,
            "plugin": component.plugin,
            "version": plugin.manifest.version,
            "params": plugin.params.model_dump(mode="json"),
            "inputs": list(component.inputs),
        }
    return {
        "kind": "backtest",
        "config_digest": loaded.digest,
        "config_text": loaded.text,
        "mounts": json.dumps(described, sort_keys=True, separators=(",", ":")),
        "snapshots": json.dumps(sorted(snapshots)),
        "components": json.dumps(components, sort_keys=True, separators=(",", ":")),
        "strategies": json.dumps(
            [s.model_dump() for s in loaded.config.strategies],
            sort_keys=True,
            separators=(",", ":"),
        ),
        "start": str(start),
        "end": str(end),
        "until": str(until),
        "heliograph": version("heliograph"),
    }
