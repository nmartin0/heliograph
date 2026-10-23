# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The strategy pipeline: forecasts combined, sized, exited and checked into signals.

After Carver (decision D8, docs/design.md "Strategy pipeline"). A strategy
is assembled from stages:

- **forecasters** and **gates** are nodes of the indicator graph
  (core/graph.py): each reads indicator values and gives one sample per
  instant, journalled with the indicators. A forecast is on Carver's scale
  (an average absolute value of 10); the pipeline caps it at ±20. A gate
  passes while its sample is above 0 and vetoes otherwise: a veto makes the
  combined forecast 0, so the strategy goes flat. It never votes or dilutes.
- a **combiner** turns the strategy's forecasts into one, capped again at ±20;
- a **sizer** turns the combined forecast into a target position, in units
  of the instrument, given the price and the position held;
- **exits** may override that target (a stop, a time limit), in order;
- **risk gates** check the result independently and may refuse it. They
  guard against adding risk: a proposal that only reduces it (to flat, or
  to a smaller position on the same side) is never put to them, so a stop
  or a veto always leaves.

A strategy decides once per close of its price stream, after the graph has
stabilised for that instant, so every value it reads is the latest known
then (a forecaster on a slower stream gives its last sample). Until each of
its forecasters and gates has a sample, and its sizer can size, it decides
nothing. A veto takes it flat whatever the sizer's buffer. When the target
differs from the position held (the sizer's no-trade buffer keeps small
changes from trading), the strategy publishes a ``Signal``; if a risk gate
refuses it, a ``SignalRefused`` instead, and the position held stays as it
was, unless the refused signal was a reversal: then the side held is one
the strategy no longer wants, and it is signalled flat instead (a
``Signal`` with ``instead_of``, which only takes risk off). A gate may
also doubt the data (``Suspicious``, a price jump that looks like a bad
print): that is asked at every close before the sizer and exits see it,
and a doubted close is not acted on at all (journalled as a
``SignalRefused`` with ``doubted`` set). The graph's nodes have seen the
price already: a forecaster or an indicator an exit reads (an ATR) may
carry the print for a while. While the sizer
cannot size, the exits and vetoes still act on the position held, and a
signal that only takes risk off still leaves. With a
simulated broker (core/broker.py, D31), the position held is what has been
filled, which the broker tells it (``hold``); without one, it is the last
target signalled.
"""

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Literal, Protocol, runtime_checkable

from heliograph.core.bus import MessageBus
from heliograph.core.errors import InvalidValueError
from heliograph.core.graph import Graph
from heliograph.core.messages import FrozenValues, Instrument
from heliograph.core.time import Timestamp

CAP = 20.0
"""The largest forecast, either way, after each forecaster and after combining."""

SCHEMA = 2
"""The version of the ``Signal`` record; raised whenever its fields change."""


def capped(forecast: float) -> float:
    """``forecast`` held to ±CAP."""
    return max(-CAP, min(CAP, forecast))


# -- the stages a strategy is built from (forecasters and gates are graph nodes) --------


class Combiner(Protocol):
    def combine(self, forecasts: Mapping[str, float]) -> float:
        """One forecast from the strategy's forecasts, by forecaster name."""
        ...


class Sizer(Protocol):
    def target(self, forecast: float | None, price: float, held: float) -> float | None:
        """The position to hold, in units, or None while it cannot size yet.

        Called at every close of the strategy's price stream, ``forecast``
        None while the forecasts warm up, so a sizer can follow the price.
        Returning ``held`` means no trade.
        """
        ...


class Exit(Protocol):
    def adjust(
        self, target: float | None, held: float, price: float, values: Sequence[float | None]
    ) -> float | None:
        """``target``, or the position an exit rule requires instead.

        Called at every close of the strategy's price stream; ``values`` are
        the latest samples of the graph nodes the exit reads, in order.
        """
        ...


@dataclass(frozen=True, slots=True, kw_only=True)
class Proposal:
    """What a strategy is about to signal, as risk gates see it.

    ``previous`` is the close before ``price`` on the strategy's price
    stream, or None at its first close.
    """

    ts: Timestamp
    strategy: str
    instrument: Instrument
    price: float
    previous: float | None
    held: float
    target: float


class RiskGate(Protocol):
    def check(self, proposal: Proposal) -> str | None:
        """Why ``proposal`` must not leave, or None to let it.

        Called once for each proposal that adds risk, in order; a gate that
        refuses stops the gates after it from seeing that proposal.
        """
        ...

    def sent(self, proposal: Proposal) -> None:
        """``proposal`` left as a signal (every gate let it, or it reduced risk)."""
        ...


@runtime_checkable
class Suspicious(Protocol):
    """A risk gate that may also doubt the data itself (optional)."""

    def suspect(self, proposal: Proposal) -> str | None:
        """Why the close ``proposal`` is at cannot be trusted, or None.

        Asked at every close, before the sizer and exits see it, with a
        proposal to keep the position held: a strategy acting on a bad
        print, even to leave, would trade on a price that was never there.
        A doubted close is not acted on at all. The gate decides when to
        trust a price again; it must not doubt for ever, or exits would
        never act.
        """
        ...


def reduces_risk(held: float, target: float) -> bool:
    """Whether going from ``held`` to ``target`` only takes risk off."""
    return held != 0 and (target == 0 or (target * held > 0 and abs(target) <= abs(held)))


# -- the records it publishes ------------------------------------------------------------

type Side = Literal["long", "short", "flat"]


def side_of(target: float) -> Side:
    return "long" if target > 0 else "short" if target < 0 else "flat"


def signal_id(scope: str, strategy: str, ts: int, target: float) -> str:
    """A stable id: the same decision always has the same id, so delivery is idempotent.

    ``scope`` tells runs apart (a run passes its configuration's digest), so
    two configurations' strategies of one name do not share ids.
    """
    target = target + 0.0  # -0.0 and 0.0 are one decision
    return hashlib.sha256(f"{scope}|{strategy}|{ts}|{target!r}".encode()).hexdigest()[:16]


@dataclass(frozen=True, slots=True, kw_only=True)
class Signal:
    """The product: hold ``target`` units of ``instrument``, and why.

    ``price`` is the close the decision was made on; ``held`` the position
    before it. ``forecast`` is the combined forecast after gates, and
    ``forecasts`` each forecaster's, capped; ``vetoed_by`` names the gates
    that vetoed and ``exit`` the exit that set the target, if any.
    ``instead_of`` is set when a risk gate refused a reversal: it is the
    target refused, and this signal goes flat instead (schema 2).
    """

    ts: Timestamp
    id: str
    strategy: str
    instrument: Instrument
    side: Side
    target: float
    held: float
    price: float
    forecast: float
    forecasts: Mapping[str, float] = field(default_factory=dict)
    vetoed_by: tuple[str, ...] = ()
    exit: str = ""
    instead_of: float | None = None
    schema: int = SCHEMA

    def __post_init__(self) -> None:
        object.__setattr__(self, "forecasts", FrozenValues(self.forecasts))
        object.__setattr__(self, "vetoed_by", tuple(self.vetoed_by))
        for name in ("target", "held", "price", "forecast"):
            if not math.isfinite(getattr(self, name)):
                raise InvalidValueError(f"Signal.{name} must be finite")


@dataclass(frozen=True, slots=True, kw_only=True)
class SignalRefused:
    """A signal a risk gate kept from leaving.

    The position held stays as it was, unless the signal refused was a
    reversal: then a flat ``Signal`` follows at once (its ``instead_of`` is
    this ``target``). A close whose price was doubted (``Suspicious``) is
    refused with ``doubted`` set and the position held as its ``target``:
    nothing was decided on it.
    """

    ts: Timestamp
    id: str
    strategy: str
    instrument: Instrument
    target: float
    held: float
    price: float
    exit: str
    refused_by: str
    reason: str
    doubted: bool = False
    schema: int = SCHEMA


# -- one strategy -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class StrategySpec:
    """What a strategy is made of: its graph nodes by name, and its stage objects."""

    name: str
    instrument: Instrument
    price: str
    forecasters: tuple[str, ...]
    gates: tuple[str, ...] = ()
    scope: str = ""
    filled: bool = False
    """True when a broker fills the signals and reports the position (``Strategy.hold``)."""
    combiner: Combiner
    sizer: Sizer
    exits: tuple[tuple[str, Exit, tuple[str, ...]], ...] = ()
    risk_gates: tuple[tuple[str, RiskGate], ...] = ()


class Strategy:
    """One strategy, deciding at each close of its price stream; see the module docstring."""

    __slots__ = ("_bus", "_graph", "_held", "_previous", "_spec")

    def __init__(self, spec: StrategySpec, graph: Graph, bus: MessageBus) -> None:
        missing = [
            name
            for name in (
                spec.price,
                *spec.forecasters,
                *spec.gates,
                *(read for _, _, reads in spec.exits for read in reads),
            )
            if not graph.has(name)
        ]
        if missing:
            raise InvalidValueError(
                f"strategy {spec.name} reads {', '.join(missing)}, not in the graph"
            )
        if not spec.forecasters:
            raise InvalidValueError(f"strategy {spec.name} needs at least one forecaster")
        self._spec = spec
        self._graph = graph
        self._bus = bus
        self._held = 0.0
        self._previous: float | None = None
        graph.observe(self._decide)

    @property
    def held(self) -> float:
        """The position held: what has been filled, or else the last target signalled."""
        return self._held

    def _refuse(
        self,
        proposal: Proposal,
        identity: str,
        exit_name: str,
        gate: str,
        reason: str,
        *,
        doubted: bool = False,
    ) -> None:
        self._bus.publish(
            SignalRefused(
                ts=proposal.ts,
                id=identity,
                strategy=proposal.strategy,
                instrument=proposal.instrument,
                target=proposal.target,
                held=proposal.held,
                price=proposal.price,
                exit=exit_name,
                refused_by=gate,
                reason=reason,
                doubted=doubted,
            )
        )

    def hold(self, position: float) -> None:
        """The position a broker has filled; the strategy decides from it."""
        self._held = position

    def _decide(self, ts: Timestamp, changed: frozenset[str]) -> None:
        spec = self._spec
        if spec.price not in changed:
            return  # decisions are made at the closes of the price stream only
        value = self._graph.value
        price = value(spec.price)
        assert price is not None  # it just took a sample
        forecasts = {name: value(name) for name in spec.forecasters}
        gates = {name: value(name) for name in spec.gates}
        warm = all(v is not None for v in (*forecasts.values(), *gates.values()))
        combined: float | None = None
        breakdown: dict[str, float] = {}
        vetoed: tuple[str, ...] = ()
        if warm:
            breakdown = {name: capped(f) for name, f in forecasts.items() if f is not None}
            vetoed = tuple(name for name, g in gates.items() if g is not None and g <= 0)
            combined = 0.0 if vetoed else spec.combiner.combine(breakdown)
            if not math.isfinite(combined):
                raise InvalidValueError(
                    f"strategy {spec.name} combined its forecasts to {combined!r} at {ts}"
                )
            combined = capped(combined)
        previous, self._previous = self._previous, price
        if spec.risk_gates:
            # Before the sizer and exits see this close: a price in doubt is not acted
            # on, not even to leave. (Graph nodes have seen it already.)
            probe = Proposal(
                ts=ts,
                strategy=spec.name,
                instrument=spec.instrument,
                price=price,
                previous=previous,
                held=self._held,
                target=self._held,
            )
            for name, gate in spec.risk_gates:
                doubt = gate.suspect(probe) if isinstance(gate, Suspicious) else None
                if doubt is not None:
                    if combined is not None:
                        identity = signal_id(spec.scope, spec.name, ts, self._held)
                        self._refuse(probe, identity, "", name, doubt, doubted=True)
                    return
        # The sizer sees every close not in doubt, so it can follow the price while the
        # forecasts warm up; nothing is decided until every forecaster and gate has one.
        target = spec.sizer.target(combined, price, self._held)
        if combined is None:
            return
        sized = target is not None
        if vetoed:
            target = 0.0  # a veto takes the strategy flat, whatever the sizer's buffer
        elif target is None:
            target = self._held  # the sizer cannot size: exits still act on what is held
        exit_name = ""
        for name, rule, reads in spec.exits:
            adjusted = rule.adjust(target, self._held, price, [value(read) for read in reads])
            if adjusted is not None and adjusted != target:
                exit_name, target = name, adjusted
        if target == self._held:
            return
        if not sized and not vetoed and not reduces_risk(self._held, target):
            return  # unsized, only taking risk off may leave
        target += 0.0  # -0.0 is flat
        if not math.isfinite(target):
            raise InvalidValueError(f"strategy {spec.name} sized a target of {target!r} at {ts}")
        identity = signal_id(spec.scope, spec.name, ts, target)
        proposal = Proposal(
            ts=ts,
            strategy=spec.name,
            instrument=spec.instrument,
            price=price,
            previous=previous,
            held=self._held,
            target=target,
        )
        instead_of: float | None = None
        gates_ = () if reduces_risk(self._held, target) else spec.risk_gates
        for name, gate in gates_:
            reason = gate.check(proposal)
            if reason is not None:
                self._refuse(proposal, identity, exit_name, name, reason)
                if target * self._held >= 0:
                    return  # adding to the side held, or entering: the position held stays
                # A reversal refused: the side held is the one the strategy no longer
                # wants, so it goes flat, which only takes risk off, rather than stay.
                instead_of, target = target, 0.0
                identity = signal_id(spec.scope, spec.name, ts, target)
                proposal = replace(proposal, target=target)
                break
        for _, gate in spec.risk_gates:
            gate.sent(proposal)
        self._bus.publish(
            Signal(
                ts=ts,
                id=identity,
                strategy=spec.name,
                instrument=spec.instrument,
                side=side_of(target),
                target=target,
                held=self._held,
                price=price,
                forecast=combined,
                forecasts=breakdown,
                vetoed_by=vetoed,
                exit=exit_name,
                instead_of=instead_of,
            )
        )
        if not spec.filled:
            self._held = target
