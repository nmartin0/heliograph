# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The indicator graph: indicators recomputed once per instant, in dependency order.

After Jane Street's Incremental (decision D11), adapted to indicators. A graph
has two kinds of node:

- an **input** takes one field (open, high, low, close or volume) of the bars
  of one instrument and size, as they close;
- an **indicator** takes the samples of earlier nodes and produces its own,
  through ``Indicator.update``.

Nothing is computed when a bar arrives. When the engine says an instant has
settled (``Settled``), every bar closing then has been delivered, whatever the
order of subscriptions, and the graph *stabilises*: each node with a changed
input is updated once, in dependency order (an input before what reads it;
nodes are only added after what they read, so the order they were added in
is that order). So a 1h indicator reading a 4h one sees the 4h value ending
at the same instant, never an older one and never a newer one, when the two
streams close at the same time after their end; a run refuses indicators
reading streams that do not (``RunConfig.misaligned``, D35).

Indicators are series, not formulas: an average of 20 closes needs every
close, even one equal to the last. So a node passes a change on whenever it
produces a sample, equal or not; the cutoff is a node producing none (still
warming up, or waiting for a second input), which leaves what reads it
untouched. Nodes are only ever added before they are read, so the graph has
no cycles by construction.

After stabilising, the graph publishes one ``IndicatorValues`` for the
instant: every indicator's latest sample, and which changed. Then it calls
its observers (``observe``) with the instant and every node that took a
sample then, bar inputs included, so a strategy acts once per instant on
values that are all of that instant (core/pipeline.py).
"""

import heapq
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol, get_args

from heliograph.core.bus import MessageBus
from heliograph.core.errors import InvalidValueError
from heliograph.core.messages import Bar, FrozenValues, Instrument, Settled
from heliograph.core.time import Timestamp, format_span

type BarField = Literal["open", "high", "low", "close", "volume"]
BAR_FIELDS: tuple[str, ...] = get_args(BarField.__value__)


class Indicator(Protocol):
    """Something that turns the samples of its inputs into samples of its own."""

    @property
    def arity(self) -> int:
        """How many inputs it reads."""
        ...

    def update(self, inputs: Sequence[float | None]) -> float | None:
        """A new sample, given each input's latest (None: none yet); None for no sample.

        Called once per instant at which any input produced a sample.
        """
        ...


@dataclass(frozen=True, slots=True)
class IndicatorValues:
    """Every indicator's latest sample at ``ts``, and the names of those that changed then."""

    ts: Timestamp
    values: Mapping[str, float]
    changed: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", FrozenValues(self.values))
        object.__setattr__(self, "changed", tuple(self.changed))


@dataclass(slots=True)
class _Node:
    name: str
    order: int  # nodes are added after their inputs, so this order is dependency order
    indicator: Indicator | None  # None for an input
    inputs: tuple["_Node", ...]
    readers: list["_Node"] = field(default_factory=list)
    value: float | None = None
    pending: float | None = None  # an input's sample, waiting for the instant to settle


class Graph:
    """Indicators over bar fields, stabilised on ``Settled``; see the module docstring."""

    __slots__ = ("_bus", "_dirty", "_inputs", "_nodes", "_observers", "_queued")

    def __init__(self, bus: MessageBus) -> None:
        self._bus = bus
        self._nodes: dict[str, _Node] = {}
        self._inputs: dict[tuple[str, int, str], _Node] = {}
        self._dirty: list[tuple[int, str]] = []
        self._queued: set[str] = set()
        self._observers: list[Callable[[Timestamp, frozenset[str]], None]] = []
        bus.subscribe(Bar, self._on_bar)
        bus.subscribe(Settled, self._on_settled)

    def bar_input(self, name: str, instrument: Instrument, size: int, bar_field: BarField) -> None:
        """A node taking ``bar_field`` of each ``size`` bar of ``instrument`` as it closes.

        Raises:
            InvalidValueError: if the name is taken, the field unknown, or the
                same bars and field already have an input.
        """
        if bar_field not in BAR_FIELDS:
            raise InvalidValueError(f"{name}: no bar field {bar_field!r}; fields: {BAR_FIELDS}")
        key = (instrument.key, size, bar_field)
        if key in self._inputs:
            existing = self._inputs[key].name
            raise InvalidValueError(
                f"{name}: {instrument.key} {format_span(size)} {bar_field} is already {existing}"
            )
        node = self._new(name, None, ())
        self._inputs[key] = node

    def add(self, name: str, indicator: Indicator, inputs: Sequence[str]) -> None:
        """An indicator node reading the nodes named ``inputs``, which must exist.

        Raises:
            InvalidValueError: if the name is taken, an input is not yet a node,
                or the indicator reads another number of inputs.
        """
        missing = [i for i in inputs if i not in self._nodes]
        if missing:
            raise InvalidValueError(f"{name}: reads {', '.join(missing)}, not yet defined")
        if len(inputs) != indicator.arity:
            raise InvalidValueError(
                f"{name}: reads {len(inputs)} inputs, but its indicator takes {indicator.arity}"
            )
        self._new(name, indicator, tuple(self._nodes[i] for i in inputs))

    def _new(self, name: str, indicator: Indicator | None, inputs: tuple[_Node, ...]) -> _Node:
        if not name or name in self._nodes:
            raise InvalidValueError(f"node name {name!r} is empty or taken")
        node = _Node(name, len(self._nodes), indicator, inputs)
        for read in inputs:
            read.readers.append(node)
        self._nodes[name] = node
        return node

    def value(self, name: str) -> float | None:
        """The latest sample of the node ``name`` (None before its first)."""
        return self._nodes[name].value

    def has(self, name: str) -> bool:
        """Whether a node is named ``name``."""
        return name in self._nodes

    def observe(self, observer: Callable[[Timestamp, frozenset[str]], None]) -> None:
        """Call ``observer(ts, changed)`` after each stabilisation, in the order added.

        ``changed`` names every node that took a sample at ``ts``, inputs
        included; ``value`` then reads any node's latest sample.
        """
        self._observers.append(observer)

    def _on_bar(self, bar: Bar) -> None:
        size = bar.end - bar.start
        for bar_field in BAR_FIELDS:
            node = self._inputs.get((bar.instrument.key, size, bar_field))
            if node is not None:
                if node.pending is not None:
                    # Two bars of one input known at one instant (a vendor's late bar
                    # and a fresh one): keeping one would silently drop the other.
                    raise InvalidValueError(
                        f"{node.name}: two {format_span(size)} bars of {bar.instrument.key} "
                        f"known at {bar.ts}; an input takes one bar an instant"
                    )
                node.pending = float(getattr(bar, bar_field))
                self._queue(node)

    def _queue(self, node: _Node) -> None:
        if node.name not in self._queued:
            self._queued.add(node.name)
            heapq.heappush(self._dirty, (node.order, node.name))

    def _on_settled(self, settled: Settled) -> None:
        if not self._dirty:
            return
        produced: list[str] = []
        sampled: set[str] = set()
        while self._dirty:
            _, name = heapq.heappop(self._dirty)
            self._queued.discard(name)
            node = self._nodes[name]
            if node.indicator is None:
                sample, node.pending = node.pending, None
            else:
                sample = node.indicator.update([i.value for i in node.inputs])
            if sample is None:
                continue  # no sample: nothing reading it changes (the cutoff)
            if isinstance(sample, bool) or not math.isfinite(sample):
                raise InvalidValueError(f"{name} produced {sample!r} at {settled.ts}, not a number")
            node.value = float(sample)
            sampled.add(name)
            if node.indicator is not None:
                produced.append(name)
            for reader in node.readers:
                self._queue(reader)
        if produced:
            values = {
                n.name: n.value
                for n in self._nodes.values()
                if n.indicator is not None and n.value is not None
            }
            self._bus.publish(IndicatorValues(settled.ts, values, tuple(sorted(produced))))
        if sampled:
            changed = frozenset(sampled)
            for observer in self._observers:
                observer(settled.ts, changed)
