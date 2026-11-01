# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""A person's answer to a signal: took it, or skipped it (decision D50).

Heliograph never trades; it tells a person what to trade. What the person then
did is news from outside the run, like a price: it is stamped when it was
learned, journalled as an input and replayed, so a live day with its answers
can be reproduced.

``quantity`` is what the person actually traded, in units, positive for a buy;
without it they are taken to have traded what the signal asked. ``price`` is
what they paid or received, kept for the comparison with the signal's paper
result and not used to decide anything.

``Followed`` makes the strategy's position held follow what the person did
(decision D51). Until a signal is answered the person is taken to have followed
it, as in a backtest; once an answer says they skipped it, or its time to answer
runs out (``SignalExpired``), it never happened for them and the position held
goes back to what they hold.
"""

import math
from collections import deque
from dataclasses import dataclass
from typing import Literal, Protocol

from heliograph.core.alarms import AlarmFired, Scheduler
from heliograph.core.bus import MessageBus
from heliograph.core.errors import InvalidValueError
from heliograph.core.pipeline import Signal
from heliograph.core.time import Timestamp

type Verdict = Literal["took", "skipped"]

VERDICTS: tuple[Verdict, ...] = ("took", "skipped")


@dataclass(frozen=True, slots=True, kw_only=True)
class SignalAnswer:
    """What the person did about the signal ``signal``, as of ``ts``.

    A later answer to the same signal replaces an earlier one: a person may
    change their mind, or correct a slip.
    """

    ts: Timestamp
    signal: str
    verdict: Verdict
    price: float | None = None
    quantity: float | None = None

    def __post_init__(self) -> None:
        if not self.signal:
            raise InvalidValueError("an answer needs the id of the signal it answers")
        if self.verdict not in VERDICTS:
            raise InvalidValueError(f"an answer is {' or '.join(VERDICTS)}, not {self.verdict!r}")
        if self.verdict == "skipped" and (self.price is not None or self.quantity is not None):
            raise InvalidValueError("a skipped signal has no price or quantity")
        if self.price is not None and not (math.isfinite(self.price) and self.price > 0):
            raise InvalidValueError(f"the price must be positive, not {self.price}")
        if self.quantity is not None and not (math.isfinite(self.quantity) and self.quantity != 0):
            raise InvalidValueError(
                f"the quantity must be a number other than 0, not {self.quantity}"
            )


@dataclass(frozen=True, slots=True)
class SignalExpired:
    """A signal nobody answered in time; for the position held it was skipped.

    Derived from the clock (an alarm), so a replay produces it again.
    """

    ts: Timestamp
    signal: str


class Holder(Protocol):
    """What is told the position a person holds: a strategy."""

    def hold(self, position: float) -> None: ...


@dataclass(slots=True)
class _Signalled:
    target: float
    applied: bool  # the paper trade has happened (or the strategy takes it as made)
    verdict: Literal["took", "skipped", "expired"] | None = None
    quantity: float | None = None

    def effective(self) -> bool:
        """Whether the person holds this signal's result."""
        return self.verdict == "took" or (self.verdict is None and self.applied)


class Followed:
    """Tells a strategy the position its person holds, from its signals and their answers.

    The position is worked out in the order the signals were made, from the
    start (flat): a signal the person holds moves the position to its target, or,
    if they said what they traded, by that quantity; one they skipped, or let
    expire, changes nothing. A signal nobody has answered counts as held once
    it has happened: at once when the strategy takes its target as made
    (``filled`` false), else when the broker is done with it (``applied``).
    One the person says they took counts at once. A later answer replaces an
    earlier one.

    A signal not answered within ``within`` nanoseconds of being made expires;
    one alarm, under this strategy's own key, is kept for the earliest.
    """

    def __init__(
        self,
        strategy: str,
        holder: Holder,
        bus: MessageBus,
        scheduler: Scheduler,
        *,
        filled: bool,
        within: int,
    ) -> None:
        if within <= 0:
            raise InvalidValueError(f"the time to answer must be positive, not {within}")
        self._strategy = strategy
        self._holder = holder
        self._bus = bus
        self._scheduler = scheduler
        self._filled = filled
        self._within = within
        self._signals: dict[str, _Signalled] = {}
        self._waiting: deque[tuple[Timestamp, str]] = deque()
        self._position = 0.0
        self._key = f"answers:{strategy}"
        scheduler.claim(self._key)
        bus.subscribe(Signal, self._on_signal)
        bus.subscribe(SignalAnswer, self._on_answer)
        bus.subscribe(AlarmFired, self._on_alarm)

    @property
    def position(self) -> float:
        """The position the person holds."""
        return self._position

    def applied(self, signal: str) -> None:
        """The paper trade for ``signal`` has happened (a filled strategy's broker says so)."""
        found = self._signals.get(signal)
        if found is not None:
            found.applied = True
            self._follow()

    def _on_signal(self, signal: Signal) -> None:
        if signal.strategy != self._strategy:
            return
        self._signals[signal.id] = _Signalled(signal.target, applied=not self._filled)
        self._waiting.append((Timestamp(signal.ts + self._within), signal.id))
        self._scheduler.at(self._waiting[0][0], self._key)
        self._follow()

    def _on_answer(self, answer: SignalAnswer) -> None:
        found = self._signals.get(answer.signal)
        if found is None:
            return
        found.verdict = answer.verdict
        found.quantity = answer.quantity
        self._follow()

    def _on_alarm(self, alarm: AlarmFired) -> None:
        if alarm.key != self._key:
            return
        while self._waiting and self._waiting[0][0] <= alarm.ts:
            _, identity = self._waiting.popleft()
            found = self._signals[identity]
            if found.verdict is None:
                found.verdict = "expired"
                self._bus.publish(SignalExpired(alarm.ts, identity))
        if self._waiting:
            self._scheduler.at(self._waiting[0][0], self._key)
        self._follow()

    def _follow(self) -> None:
        position = 0.0
        for found in self._signals.values():
            if found.effective():
                position = found.target if found.quantity is None else position + found.quantity
        if position != self._position:
            self._position = position
            self._holder.hold(position)
