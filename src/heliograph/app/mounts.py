# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Building the mount table: data sources, checked against what each mount asks.

``build_mounts`` turns ``[mounts]`` into sources ready to use. It finds
every problem before using anything and reports them all at once:

- the source plug-in exists, as a ``data_sources`` plug-in, and its
  parameters are valid;
- the source can serve the mount: the data kind, for history or live, the
  bar size, the lookback, and the terms (a live mount on a source whose
  terms forbid live use is refused);
- a backtest's history has already happened: it ends no later than now.

Mounts naming the same source with the same parameters share one instance,
so they share its rate limit: two mounts on one exchange are one client.
Each instance is then bound to a ``RateThrottle`` made from its declared
limits. Source constructors must not connect to anything; nothing is
contacted here, though every source that resolves is constructed so that
its capabilities can be checked.

The clock here is the real time, never a backtest's simulated clock: "now"
decides whether a backtest's history has happened yet, and the throttle
keeps to an API's limits in real seconds. Its ``sleep`` must agree with it.
"""

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from heliograph.app.config import ConfigError, MountConfig, RunConfig
from heliograph.app.limits import RateThrottle, Sleep
from heliograph.core.clock import Clock
from heliograph.core.errors import InvalidValueError
from heliograph.core.sources import DataSource, HistoryRequest, Subscription
from heliograph.core.time import Timestamp
from heliograph.plugins.manifest import Params
from heliograph.plugins.registry import PluginClass, Registry, UnknownPluginError

KIND = "data_sources"
"""The plug-in kind of data sources (decision D16)."""

PORT = ("capabilities", "bind", "history", "subscribe", "health_check", "close")


@dataclass(frozen=True, slots=True)
class Mount:
    """One mount, ready to use: its source, and what to ask it for.

    A backtest mount has a ``request``; a live one a ``subscription``.
    """

    name: str
    source: DataSource
    request: HistoryRequest | None = None
    subscription: Subscription | None = None


def _is_data_source(built: object) -> list[str]:
    """The parts of the DataSource port ``built`` lacks."""
    return [attribute for attribute in PORT if not hasattr(built, attribute)]


def _check(
    name: str, where: str, mount: MountConfig, source: DataSource, live: bool, now: Timestamp
) -> tuple[Mount, list[str]]:
    """The mount, and every reason its source cannot serve it."""
    instrument = mount.instrument.instrument()
    capabilities = source.capabilities
    size = mount.bar_size_ns
    if live:
        problems = capabilities.problems(mount.kind, live=True, bar_size=size)
        found = [f"{where}: the source {problem}" for problem in problems]
        subscription = Subscription(instrument=instrument, kind=mount.kind, bar_size=size)
        return Mount(name, source, subscription=subscription), found
    # RunConfig requires start and end in a backtest.
    start, end = mount.start_ts, mount.end_ts
    assert start is not None
    assert end is not None
    problems = capabilities.problems(mount.kind, live=False, bar_size=size, reach=now - start)
    found = [f"{where}: the source {problem}" for problem in problems]
    if end > now:  # ending exactly now is fine: [start, end) excludes now
        found.append(f"{where}: a backtest cannot end after now")
    request = HistoryRequest(
        instrument=instrument, kind=mount.kind, start=start, end=end, bar_size=size
    )
    return Mount(name, source, request=request), found


def build_mounts(
    config: RunConfig, registry: Registry, clock: Clock, sleep: Sleep
) -> tuple[Mount, ...]:
    """Every mount of ``config``, with its source built, checked and throttled.

    ``clock`` is the real time and ``sleep`` waits in it: ``WallClock()`` and
    ``asyncio.sleep`` in use, a simulated clock and a sleep that advances it
    in tests (see the module docstring).

    Raises:
        ConfigError: listing each mount that names an unknown source, has
            invalid parameters, or asks for what its source cannot serve.
    """
    live = config.mode == "live"
    problems: list[str] = []
    resolved: list[tuple[str, MountConfig, PluginClass, Params]] = []
    for name, mount in config.mounts.items():
        where = f"mounts.{name} ({mount.source})"
        try:
            plugin = registry.get(KIND, mount.source)
            params = plugin.manifest.params.model_validate(mount.params)
        except UnknownPluginError as exc:
            problems.append(f"{where}: {exc}")
        except ValidationError as exc:
            problems.append(f"{where}: invalid params:\n{exc}")
        else:
            resolved.append((name, mount, plugin, params))

    instances: dict[tuple[str, str], Any] = {}
    mounts: list[Mount] = []
    now = clock.now()
    for name, mount, plugin, params in resolved:
        where = f"mounts.{name} ({mount.source})"
        identity = (mount.source, params.model_dump_json())
        if identity not in instances:
            instances[identity] = plugin(params)
        built = instances[identity]
        try:
            if missing := _is_data_source(built):
                problems.append(f"{where}: not a data source; it lacks {', '.join(missing)}")
                continue
            ready, found = _check(name, where, mount, built, live, now)
        except InvalidValueError as exc:  # reading a source's capabilities may fail
            problems.append(f"{where}: its capabilities are invalid: {exc}")
            continue
        problems += found
        mounts.append(ready)
    if problems:
        raise ConfigError("\n".join(problems))

    for source in instances.values():
        source.bind(RateThrottle(source.capabilities, clock, sleep))
    return tuple(mounts)
