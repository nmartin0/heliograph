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

``run_live_session`` runs a live configuration through the same assembly on the
wall clock (decision D44); its journal's kind is ``live``, it has no ``end`` or
``until``, and a stop ends it complete.

Components of any other kind than the graph's and the strategies' are
refused rather than run without them.
"""

import asyncio
import json
import sys
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Any, Protocol, cast

from heliograph.app.bridge import HistoryFeed, SourceLoop, run_live
from heliograph.app.composition import build_components, build_sinks
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
from heliograph.app.dispatch import Dispatcher, Outcome
from heliograph.app.limits import Sleep
from heliograph.app.mounts import Mount, build_mounts
from heliograph.core.alarms import AlarmFired
from heliograph.core.bars import BarsFromBars, TimeBars
from heliograph.core.broker import Fill, FillModel, SimulatedBroker
from heliograph.core.bus import MessageBus
from heliograph.core.clock import Clock, SimulatedClock
from heliograph.core.engine import BacktestEngine, LiveEngine, Sequencer
from heliograph.core.errors import HeliographError, InvalidValueError
from heliograph.core.graph import BarField, Graph, Indicator, IndicatorValues
from heliograph.core.journal import runs
from heliograph.core.messages import Bar, Message, Point, RunEnded, Settled
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
from heliograph.core.sinks import Delivered, SignalSink
from heliograph.core.sources import DataSource, Subscription
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

BARS_RULES = "2"
"""The rules bars were built by, recorded in a run's header: 2 since D35 (bars from
trades wait for their source's delay; carried bars stop at their mount's end)."""

JOURNALLED = (
    Point,
    Bar,
    AlarmFired,
    Settled,
    RunEnded,
    IndicatorValues,
    Signal,
    SignalRefused,
    Fill,
    Delivered,
)
"""Every message type a run journals."""


class RunStoppedError(HeliographError):
    """A run was asked to stop before it finished; its journal records it as failed."""


class Stop(Protocol):
    """What a run is told to stop by: anything that says whether it is set."""

    def is_set(self) -> bool: ...


class StopFlag:
    """A flag a signal handler may set: one plain attribute and no lock, so setting
    it can never wait (``threading.Event.set`` takes a lock, which a second signal
    arriving inside the first would wait on for ever)."""

    __slots__ = ("_set",)

    def __init__(self) -> None:
        self._set = False

    def set(self) -> None:
        self._set = True

    def is_set(self) -> bool:
        return self._set


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
    *,
    stop: Stop | None = None,
    progress: Callable[[Timestamp], None] | None = None,
) -> RunSummary:
    """Run the backtest ``loaded`` describes, journalling it to a new file.

    ``progress`` is told each instant of the run as it settles. Setting
    ``stop`` (from another thread) ends the run at its next input:
    the journal is closed as failed, saying it was stopped, and
    ``RunStoppedError`` is raised.

    ``clock`` and ``sleep`` are the real time, as for ``build_mounts``. The
    run itself is on a simulated clock, from the earliest mount's start to
    the latest's end plus the longest live delay and grace, so the last
    trades become known and the last bars close before it stops.

    Raises:
        ConfigError: if the configuration is live, has components of a kind
            a run does not run, has no mounts, a component cannot be built
            or is not what its kind needs, or a mount cannot be served.
        InvalidValueError: if ``journal`` already exists.
        RunStoppedError: if ``stop`` was set before the run finished.
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
    delays = {m.name: m.source.capabilities.live_delay for m in mounts}
    misaligned = config.misaligned(delays)
    if misaligned:
        raise ConfigError("; ".join(misaligned))
    until = Timestamp(end + late + _longest_wait(config))
    header = _header(loaded, mounts, components, start, end, until)
    sources: dict[int, DataSource] = {id(m.source): m.source for m in mounts}
    with SourceLoop() as loop:
        try:
            with _assembled(
                config,
                journal,
                header,
                components,
                clock=SimulatedClock(start),
                make=BacktestEngine,
                progress=progress,
            ) as engine:
                feeds: list[HistoryFeed] = []
                try:
                    for mount, wanted in zip(mounts, requests, strict=True):
                        feeds.append(HistoryFeed(loop, mount.source, wanted))
                    inputs = feeds if stop is None else [_until_stopped(f, stop) for f in feeds]
                    delivered = engine.run(*inputs, until=until)
                    empty = [
                        m.name
                        for m, f in zip(mounts, feeds, strict=True)
                        if not f.delivered and not config.mounts[m.name].allow_empty
                    ]
                    if empty:
                        # A source that sent nothing for a whole span is almost always
                        # wrong (a time unit, a symbol, a span outside the file), and a
                        # run on nothing would look like a quiet market.
                        raise InvalidValueError(
                            f"{', '.join(f'mounts.{n}' for n in empty)} delivered nothing "
                            "between start and end; check the data, its time unit and span, or set "
                            "allow_empty = true on a mount whose quiet span is expected"
                        )
                finally:
                    for feed in feeds:
                        feed.close()
        finally:
            _close_all(loop, sources.values())
    return RunSummary(journal, delivered, header)


async def run_live_session(
    loaded: LoadedConfig,
    registry: Registry,
    journal: Path,
    clock: Clock,
    sleep: Sleep = asyncio.sleep,
    *,
    grace: float = 10.0,
) -> RunSummary:
    """Run the live session ``loaded`` describes until its sources end or it is cancelled.

    The same components as a backtest, on the real ``clock`` and
    ``LiveEngine``, journalled to a new file in small batches. Cancelling the
    task (a stop signal) ends the engine's run properly: the journal is
    marked complete, ``RunEnded`` is its last entry, the sources are closed,
    and then the cancellation is raised again. A failure marks the journal
    failed, as in a backtest.

    Each signal a strategy publishes is also given to the configuration's
    sinks (decision D46), in the background; every outcome is journalled as
    a ``Delivered`` input. When the run ends, deliveries under way get
    ``grace`` seconds to finish; those that do not are recorded as not
    delivered.

    Raises:
        ConfigError: as ``run_backtest``, and if the configuration is not live.
        InvalidValueError: if ``journal`` already exists.
        asyncio.CancelledError: after a clean stop.
        Whatever a source raised; the journal then records the failure.
    """
    config = loaded.config
    _runnable(config, "live")
    if journal.exists():
        raise InvalidValueError(f"{journal} exists; a run writes a new journal")
    components = _components(config, registry)
    mounts = build_mounts(config, registry, clock, sleep)
    misaligned = config.misaligned({m.name: m.source.capabilities.live_delay for m in mounts})
    if misaligned:
        raise ConfigError("; ".join(misaligned))
    sinks = build_sinks(config, registry)
    header = _header(loaded, mounts, components, clock.now(), None, None)
    if sinks:
        header["sinks"] = _sinks_described(config, sinks)
    feeds: list[tuple[DataSource, Sequence[Subscription]]] = []
    for mount in mounts:
        assert mount.subscription is not None  # a live mount always has one
        feeds.append((mount.source, [mount.subscription]))
    stopped = False
    delivered = 0
    dispatcher = Dispatcher(cast(Mapping[str, SignalSink], sinks), sleep=sleep)
    try:
        with _assembled(
            config, journal, header, components, clock=clock, make=LiveEngine, batch=1
        ) as engine:
            deliveries = Deliveries(engine, dispatcher, clock, grace) if sinks else None
            try:
                await run_live(
                    feeds,
                    engine,
                    clock,
                    sleep=sleep,
                    before_end=None if deliveries is None else deliveries.finish,
                )
            except asyncio.CancelledError:
                stopped = True
            finally:
                if deliveries is not None:
                    deliveries.cancel()
            delivered = engine.received
    finally:
        await _aclose_all({id(m.source): m.source for m in mounts}.values())
        await dispatcher.close()
    if stopped:
        raise asyncio.CancelledError
    return RunSummary(journal, delivered, header)


def _sinks_described(config: RunConfig, sinks: Mapping[str, Plugin[Any]]) -> str:
    """The journal header's account of the sinks: plug-in, version and validated parameters."""
    described = {
        name: {
            "plugin": config.sinks[name].plugin,
            "version": sink.manifest.version,
            "params": sink.params.model_dump(mode="json"),
        }
        for name, sink in sinks.items()
    }
    return json.dumps(described, sort_keys=True, separators=(",", ":"))


class Deliveries:
    """Gives each signal the strategies publish to the sinks, and records what became of it.

    Every signal is delivered in a task of its own, in the background, so a slow
    sink never holds up the run or the signals after it. Each outcome enters the
    engine as a ``Delivered`` input the moment its sink is done. At the end of the
    run the deliveries under way are given ``grace`` seconds; what is left is
    recorded as not delivered.
    """

    def __init__(
        self, engine: LiveEngine, dispatcher: Dispatcher, clock: Clock, grace: float
    ) -> None:
        self._engine = engine
        self._dispatcher = dispatcher
        self._clock = clock
        self._grace = grace
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._signals: dict[str, Signal] = {}
        self._recorded: set[tuple[str, str]] = set()
        engine.bus.subscribe(Signal, self._start)

    def _start(self, signal: Signal) -> None:
        self._signals[signal.id] = signal
        self._tasks[signal.id] = asyncio.create_task(self._give(signal))

    async def _give(self, signal: Signal) -> None:
        await self._dispatcher.dispatch(signal, self._record_outcome)

    def _record_outcome(self, outcome: Outcome) -> None:
        self._record(outcome.signal, outcome.sink, outcome.ok, outcome.attempts, outcome.detail)

    def _record(self, signal: str, sink: str, ok: bool, attempts: int, detail: str) -> None:
        self._engine.receive(Delivered(self._clock.now(), signal, sink, ok, attempts, detail))
        self._recorded.add((signal, sink))

    def cancel(self) -> None:
        """Stop delivering, if the run ended any way but by finishing."""
        for task in self._tasks.values():
            task.cancel()

    async def finish(self) -> None:
        """Wait for deliveries under way, for at most the grace; record the rest as undelivered."""
        if not self._tasks:
            return
        _, pending = await asyncio.wait(self._tasks.values(), timeout=self._grace)
        for task in self._tasks.values():
            if task.done() and not task.cancelled() and (error := task.exception()) is not None:
                raise error
        for task in pending:
            task.cancel()
        for signal_id, task in self._tasks.items():
            if task in pending:
                for name in self._dispatcher.names:
                    if (signal_id, name) not in self._recorded:
                        self._record(
                            signal_id, name, False, 0, "not delivered: the session ended first"
                        )


async def _aclose_all(sources: Iterable[DataSource]) -> None:
    """Close every source; the first failure is raised once all are closed, unless raising."""
    failures: list[BaseException] = []
    for source in sources:
        try:
            await source.close()
        except Exception as exc:
            failures.append(exc)
    if failures and sys.exc_info()[1] is None:
        raise failures[0]


def _until_stopped(feed: Iterable[Message], stop: Stop) -> Iterator[Message]:
    for message in feed:
        if stop.is_set():
            raise RunStoppedError("the run was stopped before it finished")
        yield message


type Built = list[tuple[ComponentConfig, Plugin[Any]]]

_NEEDS = {
    "combiners": ("combine", "a combiner (it has no combine)"),
    "sizers": ("target", "a sizer (it has no target)"),
    "exits": ("adjust", "an exit (it has no adjust)"),
    "risk_gates": ("check", "a risk gate (it has no check)"),
    "risk_gates/sent": ("sent", "a risk gate (it has no sent)"),
    "fills": ("price", "a fill model (it has no price)"),
    "fills/commission": ("commission", "a fill model (it has no commission)"),
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
            if component.kind == "fills":
                needs.append(_NEEDS["fills/commission"])
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


def _runnable(config: RunConfig, mode: str = "backtest") -> None:
    """Raise unless ``config`` is a ``mode`` run this module can run."""
    if config.mode != mode:
        if mode == "backtest":
            raise ConfigError("this configuration is live; run_backtest runs backtests")
        raise ConfigError("this configuration is a backtest; run_live_session runs live ones")
    later = sorted({c.kind for c in config.components if c.kind not in GRAPH_KINDS | STAGE_KINDS})
    if later:
        raise ConfigError(
            f"components of kind {', '.join(later)} are not run: a run runs indicators, "
            "forecasters and gates, and its strategies' combiners, sizers, exits and risk gates"
        )
    if not config.mounts:
        raise ConfigError(
            f"a {'backtest' if mode == 'backtest' else 'live session'} needs at least one mount"
        )


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
    with _assembled(
        loaded.config,
        out,
        header,
        components,
        clock=SimulatedClock(Timestamp(int(header["start"]))),
        make=BacktestEngine,
    ) as engine:
        for inputs, until in replayed:
            delivered += engine.run(inputs, until=until)
    return RunSummary(out, delivered, header)


@contextmanager
def _assembled[E: Sequencer](
    config: RunConfig,
    journal: Path,
    header: Mapping[str, str],
    components: Built,
    *,
    clock: Clock,
    make: Callable[[Any, MessageBus], E],
    progress: Callable[[Timestamp], None] | None = None,
    batch: int = 1000,
) -> Iterator[E]:
    """An engine on a journalled bus: streams build bars, the graph its nodes, strategies decide.

    A backtest passes a simulated ``clock`` and ``BacktestEngine``; a live
    session its real clock and ``LiveEngine``, and a small ``batch`` so that a
    crash loses little.
    """
    params = SqliteJournalParams(path=str(journal), batch=batch)
    with SqliteJournal(params, dict(header)) as sink:
        bus = MessageBus(clock, sink)
        engine = make(clock, bus)
        if progress is not None:
            # Each instant as it settles: what the run has done, not what it has read ahead.
            bus.subscribe(Settled, lambda settled: progress(settled.ts))
        # How long after its end each mount's data is known; a journal written
        # before this was recorded had no delays on mounts of bars to record.
        mounts = json.loads(header.get("mounts", "{}"))
        delays = {name: int(m.get("live_delay", 0)) for name, m in mounts.items()}
        # A journal from before bars from trades waited for their source's delay (D35)
        # is replayed with the rule it ran under, or its bars would differ.
        rules_2 = int(header.get("bars", "1")) >= int(BARS_RULES)
        builders: dict[str, TimeBars | BarsFromBars] = {}
        for stream in config.streams:
            source = config.mounts[stream.mount]
            spec = StreamSpec(instrument=source.instrument.instrument(), bars=stream.bar_spec())
            finer = stream.built_from(source)
            delay = delays.get(stream.mount, 0)
            name = f"{stream.mount}@{stream.bar_spec().label()}"
            if source.kind == "trades":
                builders[name] = TimeBars(
                    spec,
                    bus,
                    engine.scheduler,
                    delay if rules_2 else 0,
                    ends=source.end_ts if rules_2 else None,
                )
            elif finer is not None:
                builders[name] = BarsFromBars(spec, finer, bus, engine.scheduler, delay)
            # Otherwise the stream is the mount's own bars, as they come.
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
            models = {c.name: p for c, p in components if c.kind == "fills"}
            for strategy in config.strategies:
                spec_ = _strategy_spec(config, strategy, components, header["config_digest"])
                running = Strategy(spec_, graph, bus)
                if strategy.fills is not None:
                    found = config.stream_field(strategy.price)
                    assert found is not None  # RunConfig requires it
                    broker = SimulatedBroker(
                        strategy.name,
                        spec_.instrument,
                        found[1].bar_spec().size,
                        cast(FillModel, models[strategy.fills]),
                        bus,
                    )
                    broker.on_fill(_holding(running))
        yield engine
        # What each stream left out for arriving after its bar had closed, which only
        # the end of the run knows: the report says so when it is not nothing.
        late = {
            name: b.late_points if isinstance(b, TimeBars) else b.late_bars
            for name, b in sorted(builders.items())
        }
        sink.record("late", json.dumps(late, sort_keys=True, separators=(",", ":")))


def _longest_wait(config: RunConfig) -> int:
    """How long after its end the last bar of any stream may still close."""
    waits = [0]
    for stream in config.streams:
        finer = stream.built_from(config.mounts[stream.mount])
        waits.append(stream.bar_spec().grace + (finer or 0))
    return max(waits)


def _holding(strategy: Strategy) -> Callable[[Fill], None]:
    """What tells ``strategy`` the position each fill leaves it with."""

    def hold(fill: Fill) -> None:
        strategy.hold(fill.position)

    return hold


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
        filled=strategy.fills is not None,
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
    end: Timestamp | None,
    until: Timestamp | None,
) -> dict[str, str]:
    """What the journal says about the run that wrote it.

    A live session has no ``end`` or ``until``: it runs until it is stopped.
    """
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
            "live_delay": mount.source.capabilities.live_delay,
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
    live = end is None or until is None
    return {
        "kind": "live" if live else "backtest",
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
        **({} if end is None or until is None else {"end": str(end), "until": str(until)}),
        "bars": BARS_RULES,
        "heliograph": version("heliograph"),
    }
