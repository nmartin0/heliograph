# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Running a backtest from its configuration, journalled, and replaying it.

``run_backtest`` assembles a run from a loaded configuration: the mounts
are built and checked (app/mounts.py), each mount's history becomes a feed
through the bridge (app/bridge.py), the streams' bars are built from the
trades (core/bars.py), the indicators are computed from the bars through
the graph (core/graph.py), and the engine replays it all in time order
onto a bus that journals every message to a new SQLite file before
delivering it.

The journal's header says what produced it, so a run can be reproduced and
questioned later:

- ``config_text`` and ``config_digest``: the configuration's exact text
  and its SHA-256 (the text, not a JSON form: TOML's date-times would not
  read back the same);
- ``mounts``: each mount's source, plug-in version and validated
  parameters, defaults included, as JSON;
- ``snapshots``: the ids of the store snapshots read, as a JSON list;
- ``components``: each indicator's plug-in, version, validated parameters
  with their defaults, and inputs, as JSON;
- ``start`` and ``end``: the interval the mounts asked for, and ``until``:
  where the run stopped (``end`` plus the longest live delay and grace, so
  the last trades arrive and the last bars close), in nanoseconds;
- ``heliograph``: the version that ran it.

``replay_backtest`` rebuilds a complete run from its journal alone: the
configuration from its recorded text, the inputs from its entries, the
indicators from the registry, refused unless each has the version and the
parameters the run recorded. A replay's entries are the original's, byte
for byte (tests/app/test_run.py).

Of the components, only indicators run yet: the pipeline that runs the
other kinds comes in Phase 2, so a configuration with any of them is
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
    ComponentConfig,
    ConfigError,
    LoadedConfig,
    RunConfig,
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

JOURNALLED = (Point, Bar, AlarmFired, Settled, RunEnded, IndicatorValues)
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
        ConfigError: if the configuration is live, has components other
            than indicators, has no mounts, an indicator cannot be built or
            read its inputs, or a mount cannot be served.
        InvalidValueError: if ``journal`` already exists.
        Whatever a source raised; the journal then records the failure.
    """
    config = loaded.config
    _runnable(config)
    if journal.exists():
        raise InvalidValueError(f"{journal} exists; a run writes a new journal")
    indicators = _indicators(config, registry)
    mounts = build_mounts(config, registry, clock, sleep)
    requests = [m.request for m in mounts if m.request is not None]
    start = Timestamp(min(r.start for r in requests))
    end = Timestamp(max(r.end for r in requests))
    late = max(m.source.capabilities.live_delay for m in mounts)
    grace = max((stream.bar_spec().grace for stream in config.streams), default=0)
    until = Timestamp(end + late + grace)
    header = _header(loaded, mounts, indicators, start, end, until)
    sources: dict[int, DataSource] = {id(m.source): m.source for m in mounts}
    with SourceLoop() as loop:
        try:
            with _assembled(config, journal, header, indicators) as engine:
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


def _indicators(config: RunConfig, registry: Registry) -> list[tuple[ComponentConfig, Indicator]]:
    """Each indicator component with its plug-in built, checked to read its inputs.

    Raises:
        ConfigError: listing every component that names an unknown plug-in,
            has invalid parameters, is not an indicator, or is given another
            number of inputs than it reads.
    """
    built = build_components(config, registry)
    problems = []
    for index, (component, plugin) in enumerate(zip(config.components, built, strict=True)):
        where = f"components[{index}] ({component.name})"
        arity = getattr(plugin, "arity", None)
        if not callable(getattr(plugin, "update", None)) or not isinstance(arity, int):
            problems.append(f"{where}: {component.plugin} is not an indicator")
        elif len(component.inputs) != arity:
            problems.append(
                f"{where}: reads {len(component.inputs)} inputs; {component.plugin} takes {arity}"
            )
    if problems:
        raise ConfigError("\n".join(problems))
    return [(c, cast(Indicator, p)) for c, p in zip(config.components, built, strict=True)]


def _runnable(config: RunConfig) -> None:
    """Raise unless ``config`` is a backtest this module can run."""
    if config.mode != "backtest":
        raise ConfigError("this configuration is live; run_backtest runs backtests")
    later = sorted({c.kind for c in config.components if c.kind != "indicators"})
    if later:
        raise ConfigError(
            f"components of kind {', '.join(later)} are not run yet (the pipeline that runs "
            "them comes in Phase 2); only indicators are"
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
            journal, its configuration no longer matches its digest, an
            indicator's installed version or parameters are not the ones the
            run recorded, or ``out`` already exists.
        ConfigError: if an indicator can no longer be built (its plug-in
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
    indicators = _indicators(loaded.config, registry)
    # A journal written before indicators ran records none.
    recorded = json.loads(written.get("components", "{}"))
    for component, indicator in indicators:
        plugin = cast(Plugin[Any], indicator)
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
    with _assembled(loaded.config, out, header, indicators) as engine:
        for inputs, until in replayed:
            delivered += engine.run(inputs, until=until)
    return RunSummary(out, delivered, header)


@contextmanager
def _assembled(
    config: RunConfig,
    journal: Path,
    header: Mapping[str, str],
    indicators: Sequence[tuple[ComponentConfig, Indicator]],
) -> Iterator[BacktestEngine]:
    """An engine on a journalled bus: the streams build bars, the graph its indicators."""
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
        if indicators:
            graph = Graph(bus)
            # A stream field may be written two ways (btc@1h.close, btc@60m.close):
            # the graph knows it by one name.
            fields = {
                config.input_name(read): read
                for component, _ in indicators
                for read in component.inputs
                if config.stream_field(read) is not None
            }
            for name, read in sorted(fields.items()):
                found = config.stream_field(read)
                assert found is not None
                mount, stream, bar_field = found
                instrument = config.mounts[mount].instrument.instrument()
                size = stream.bar_spec().size
                graph.bar_input(name, instrument, size, cast(BarField, bar_field))
            for component, indicator in indicators:
                assert component.name is not None  # RunConfig requires it of indicators
                reads = [config.input_name(read) for read in component.inputs]
                graph.add(component.name, indicator, reads)
        yield engine


def _header(
    loaded: LoadedConfig,
    mounts: Sequence[Mount],
    indicators: Sequence[tuple[ComponentConfig, Indicator]],
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
    for component, indicator in indicators:
        plugin = cast(Plugin[Any], indicator)
        components[str(component.name)] = {
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
        "start": str(start),
        "end": str(end),
        "until": str(until),
        "heliograph": version("heliograph"),
    }
