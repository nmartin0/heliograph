# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The dispatcher gives every signal to every sink, each retried on its own."""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field

import pytest

from heliograph.app.dispatch import LONGEST_WAIT, Dispatcher, Outcome
from heliograph.core.errors import InvalidValueError
from heliograph.core.pipeline import Signal
from heliograph.core.sinks import Delivery, SinkUnavailableError
from heliograph.core.sources import Health
from heliograph.core.time import Timestamp
from tests.support.world import BTC

type Step = Delivery | Exception


@dataclass
class Sink:
    """A sink that plays its steps in order, one per ask (the last repeats)."""

    steps: Sequence[Step] = (Delivery(True),)
    asked: list[str] = field(default_factory=list)
    closed: int = 0
    health: Health | Exception = field(default_factory=lambda: Health(ok=True))
    close_fails: bool = False

    async def emit(self, signal: Signal) -> Delivery:
        self.asked.append(signal.id)
        step = self.steps[min(len(self.asked), len(self.steps)) - 1]
        if isinstance(step, Exception):
            raise step
        return step

    async def health_check(self) -> Health:
        if isinstance(self.health, Exception):
            raise self.health
        return self.health

    async def close(self) -> None:
        self.closed += 1
        if self.close_fails:
            raise RuntimeError("would not close")


def signal(number: int = 1) -> Signal:
    return Signal(
        ts=Timestamp(number),
        id=f"s-{number}",
        strategy="s",
        instrument=BTC,
        side="long",
        target=1.0,
        held=0.0,
        price=100.0,
        forecast=5.0,
    )


class Waits:
    def __init__(self) -> None:
        self.seconds: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.seconds.append(seconds)


def dispatch(
    sinks: dict[str, Sink], *, waits: Waits | None = None, **settings: float
) -> tuple[Outcome, ...]:
    waits = waits or Waits()
    dispatcher = Dispatcher(sinks, sleep=waits, **settings)  # type: ignore[arg-type]
    return asyncio.run(dispatcher.dispatch(signal()))


def test_a_signal_goes_to_every_sink_and_each_outcome_is_reported_in_name_order() -> None:
    sinks = {"b": Sink(), "a": Sink((Delivery(True, "sent"),))}
    outcomes = dispatch(sinks)
    assert outcomes == (Outcome("a", "s-1", True, 1, "sent"), Outcome("b", "s-1", True, 1, ""))
    assert sinks["a"].asked == sinks["b"].asked == ["s-1"]


def test_an_unavailable_sink_is_tried_again_with_a_doubling_wait_until_it_delivers() -> None:
    waits = Waits()
    down = SinkUnavailableError("503")
    sinks = {"ntfy": Sink((down, down, Delivery(True)))}
    (outcome,) = dispatch(sinks, waits=waits, retries=3, backoff=2.0)
    assert outcome == Outcome("ntfy", "s-1", True, 3, "")
    assert waits.seconds == [2.0, 4.0]


def test_a_sink_that_stays_unavailable_is_given_up_on_after_the_retries() -> None:
    sinks = {"ntfy": Sink((SinkUnavailableError("503"),))}
    (outcome,) = dispatch(sinks, retries=2, backoff=0)
    assert outcome == Outcome("ntfy", "s-1", False, 3, "unavailable: 503")


def test_the_wait_between_tries_is_capped() -> None:
    waits = Waits()
    sinks = {"x": Sink((SinkUnavailableError("down"),))}
    dispatch(sinks, waits=waits, retries=4, backoff=40.0)
    assert waits.seconds == [40.0, LONGEST_WAIT, LONGEST_WAIT, LONGEST_WAIT]


def test_a_refusal_for_good_is_not_retried() -> None:
    waits = Waits()
    sinks = {"hook": Sink((InvalidValueError("bad url"),))}
    (outcome,) = dispatch(sinks, waits=waits)
    assert outcome == Outcome("hook", "s-1", False, 1, "refused: bad url")
    assert waits.seconds == []


def test_a_sink_that_declines_is_not_retried_and_says_why() -> None:
    sinks = {"quiet": Sink((Delivery(False, "muted"),))}
    (outcome,) = dispatch(sinks)
    assert outcome == Outcome("quiet", "s-1", False, 1, "muted")
    assert sinks["quiet"].asked == ["s-1"]


def test_one_sinks_failure_does_not_stop_the_others() -> None:
    sinks = {
        "a": Sink((RuntimeError("boom"),)),
        "b": Sink((SinkUnavailableError("down"),)),
        "c": Sink(),
    }
    a, b, c = dispatch(sinks, retries=1, backoff=0)
    assert (a.ok, a.detail) == (False, "RuntimeError: boom")
    assert (b.ok, b.attempts) == (False, 2)
    assert c.ok


def test_a_signal_a_sink_accepted_is_not_given_to_it_again() -> None:
    sinks = {"a": Sink(), "b": Sink((Delivery(False, "no"), Delivery(True)))}
    dispatcher = Dispatcher(sinks, sleep=Waits())

    async def twice() -> tuple[tuple[Outcome, ...], tuple[Outcome, ...]]:
        return await dispatcher.dispatch(signal()), await dispatcher.dispatch(signal())

    first, second = asyncio.run(twice())
    assert second[0] == Outcome("a", "s-1", True, 0, "already delivered")
    assert second[1].ok  # b declined before, so it was asked again
    assert second[1].attempts == 1
    assert sinks["a"].asked == ["s-1"]
    assert first[1].ok is False


def test_negative_retries_or_backoff_are_refused() -> None:
    with pytest.raises(InvalidValueError, match="cannot be negative"):
        Dispatcher({}, retries=-1)
    with pytest.raises(InvalidValueError, match="cannot be negative"):
        Dispatcher({}, backoff=-1.0)


def test_health_is_each_sinks_own_report_and_a_sink_that_cannot_answer_is_not_ok() -> None:
    sinks = {
        "a": Sink(health=Health(ok=True)),
        "b": Sink(health=Health(ok=False, detail="no route")),
        "c": Sink(health=RuntimeError("broken")),
    }
    report = asyncio.run(Dispatcher(sinks).health())
    assert report == {
        "a": Health(ok=True),
        "b": Health(ok=False, detail="no route"),
        "c": Health(ok=False, detail="RuntimeError: broken"),
    }


def test_closing_closes_every_sink_and_raises_the_first_failure_after() -> None:
    sinks = {"a": Sink(close_fails=True), "b": Sink(), "c": Sink(close_fails=True)}
    with pytest.raises(RuntimeError, match="would not close"):
        asyncio.run(Dispatcher(sinks).close())
    assert [s.closed for s in sinks.values()] == [1, 1, 1]


def test_closing_without_failures_is_quiet() -> None:
    sinks = {"a": Sink()}
    asyncio.run(Dispatcher(sinks).close())
    assert sinks["a"].closed == 1
