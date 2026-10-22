# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The engine and its alarms against a slow reference, after any sequence of operations.

Hypothesis chooses the sequence: set, move or cancel alarms, one-off or
repeating; have a handler set an "echo" alarm, possibly for the same instant,
whenever another fires; and run over one or two feeds of ticks to an end time. The reference
keeps alarms in a plain dictionary and merges by sorting; after each run the
messages the engine delivered must equal the reference's, one for one, and
the pending alarms must agree. This is the state-machine form NautilusTrader
and Hypothesis's own documentation use for stateful components
(docs/testing.md).
"""

from dataclasses import dataclass

from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from heliograph.core.alarms import AlarmFired, AlarmQueue
from heliograph.core.bus import MessageBus
from heliograph.core.clock import SimulatedClock
from heliograph.core.engine import BacktestEngine
from heliograph.core.messages import Message, RunEnded, Settled
from heliograph.core.time import Timestamp
from tests.support.world import Tick

KEYS = st.sampled_from(["a", "b", "c", "d"])


@dataclass
class Expected:
    """One alarm as the reference keeps it."""

    ts: int
    order: int
    period: int | None


def label(message: Message) -> str:
    if isinstance(message, AlarmFired):
        return f"alarm {message.key} at {message.ts}"
    return f"{type(message).__name__} at {message.ts}"


class EngineAgainstReference(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.clock = SimulatedClock(Timestamp(0))
        bus = MessageBus(self.clock)
        self.queue = AlarmQueue(self.clock)
        self.engine = BacktestEngine(self.clock, bus, self.queue)
        self.seen: list[str] = []
        for kind in (Tick, AlarmFired, Settled, RunEnded):
            bus.subscribe(kind, lambda m: self.seen.append(label(m)))
        bus.subscribe(AlarmFired, self._echo)
        self.alarms: dict[str, Expected] = {}
        self.order = 0
        self.echo_delay: int | None = None

    def _echo(self, alarm: AlarmFired) -> None:
        """The handler under test: when an alarm other than the echo fires, set the echo."""
        if self.echo_delay is not None and alarm.key != "echo":
            self.engine.scheduler.at(Timestamp(alarm.ts + self.echo_delay), "echo")

    def _next_order(self) -> int:
        self.order += 1
        return self.order

    @rule(key=KEYS, ahead=st.integers(1, 30))
    def set_alarm(self, key: str, ahead: int) -> None:
        ts = self.clock.now() + ahead
        self.engine.scheduler.at(Timestamp(ts), key)
        self.alarms[key] = Expected(ts, self._next_order(), None)

    @rule(key=KEYS, ahead=st.integers(1, 30), period=st.integers(1, 12))
    def set_repeating_alarm(self, key: str, ahead: int, period: int) -> None:
        ts = self.clock.now() + ahead
        self.engine.scheduler.every(Timestamp(ts), period, key)
        self.alarms[key] = Expected(ts, self._next_order(), period)

    @rule(delay=st.one_of(st.none(), st.integers(0, 5)))
    def echo_after(self, delay: int | None) -> None:
        self.echo_delay = delay

    @rule(key=KEYS)
    def cancel_alarm(self, key: str) -> None:
        self.engine.scheduler.cancel(key)
        self.alarms.pop(key, None)

    @rule(
        offsets=st.lists(st.tuples(st.integers(1, 40), st.booleans()), max_size=12),
        length=st.integers(0, 45),
    )
    def run(self, offsets: list[tuple[int, bool]], length: int) -> None:
        """Ticks at ``now + offset``, each in the first or second feed."""
        now = self.clock.now()
        until = now + max([length, *(offset for offset, _ in offsets)])
        feeds = [
            [Tick(Timestamp(now + o)) for o in sorted(o for o, second in offsets if second is s)]
            for s in (False, True)
        ]
        ticks = sorted(now + offset for offset, _ in offsets)
        self.seen.clear()
        self.engine.run(*feeds, until=Timestamp(until))
        assert self.seen == self._expected(ticks, until)
        assert self.clock.now() == until

    def _expected(self, ticks: list[int], until: int) -> list[str]:
        """What the engine should deliver, computed the slow way."""
        out: list[str] = []
        busy: int | None = None
        while True:
            due = [(a.ts, a.order, key) for key, a in self.alarms.items() if a.ts <= until]
            alarm = min(due, default=None)
            tick = ticks[0] if ticks else None
            if alarm is None and tick is None:
                break
            at = min(t for t in (alarm[0] if alarm else None, tick) if t is not None)
            if busy is not None and at > busy:
                out.append(f"Settled at {busy}")
            busy = at
            if alarm is not None and alarm[0] == at:  # alarms first at an instant
                _, _, key = alarm
                out.append(f"alarm {key} at {at}")
                expected = self.alarms[key]
                if expected.period is None:
                    del self.alarms[key]
                else:
                    expected.ts += expected.period
                if self.echo_delay is not None and key != "echo":
                    self.alarms["echo"] = Expected(at + self.echo_delay, self._next_order(), None)
            else:
                ticks.pop(0)
                out.append(f"Tick at {at}")
        if busy is not None:
            out.append(f"Settled at {busy}")
        out.append(f"RunEnded at {until}")
        return out

    @invariant()
    def the_same_alarms_are_pending(self) -> None:
        assert len(self.queue) == len(self.alarms)
        assert self.queue.next_due() == min((a.ts for a in self.alarms.values()), default=None)


EngineAgainstReference.TestCase.settings = settings(max_examples=150, stateful_step_count=30)
test_the_engine_matches_its_reference = EngineAgainstReference.TestCase
