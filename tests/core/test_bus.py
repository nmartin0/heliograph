from dataclasses import dataclass

import pytest

from heliograph.core.bus import MessageBus
from heliograph.core.clock import SimulatedClock
from heliograph.core.errors import InvalidTypeError, LookAheadError
from heliograph.core.messages import Message
from heliograph.core.time import Timestamp


@dataclass(frozen=True, slots=True)
class Ping:
    ts: Timestamp
    label: str = ""


@dataclass(frozen=True, slots=True)
class Pong:
    ts: Timestamp
    label: str = ""


@pytest.fixture
def clock() -> SimulatedClock:
    return SimulatedClock(Timestamp(100))


@pytest.fixture
def bus(clock: SimulatedClock) -> MessageBus:
    return MessageBus(clock)


def test_a_subscriber_receives_only_its_message_type(bus: MessageBus) -> None:
    pings: list[Ping] = []
    bus.subscribe(Ping, pings.append)
    bus.publish(Ping(Timestamp(100), "a"))
    bus.publish(Pong(Timestamp(100), "b"))
    assert pings == [Ping(Timestamp(100), "a")]


def test_subscribers_are_called_in_subscription_order(bus: MessageBus) -> None:
    calls: list[str] = []
    bus.subscribe(Ping, lambda _: calls.append("first"))
    bus.subscribe(Ping, lambda _: calls.append("second"))
    bus.publish(Ping(Timestamp(100)))
    assert calls == ["first", "second"]


def test_messages_published_by_handlers_are_delivered_breadth_first(bus: MessageBus) -> None:
    order: list[str] = []

    def answer(ping: Ping) -> None:
        order.append(f"ping {ping.label}")
        bus.publish(Pong(ping.ts, ping.label))

    bus.subscribe(Ping, answer)
    bus.subscribe(Ping, lambda ping: order.append(f"log {ping.label}"))
    bus.subscribe(Pong, lambda pong: order.append(f"pong {pong.label}"))
    bus.publish(Ping(Timestamp(100), "x"))
    assert order == ["ping x", "log x", "pong x"]


def test_a_message_from_the_future_is_refused(bus: MessageBus) -> None:
    received: list[Ping] = []
    bus.subscribe(Ping, received.append)
    with pytest.raises(LookAheadError, match="stamped 101 published at 100"):
        bus.publish(Ping(Timestamp(101)))
    assert received == []


def test_a_handler_cannot_publish_into_the_future(bus: MessageBus) -> None:
    bus.subscribe(Ping, lambda ping: bus.publish(Pong(Timestamp(ping.ts + 1))))
    with pytest.raises(LookAheadError):
        bus.publish(Ping(Timestamp(100)))


def test_unsubscribed_handlers_receive_nothing_more(bus: MessageBus) -> None:
    received: list[Ping] = []
    subscription = bus.subscribe(Ping, received.append)
    bus.publish(Ping(Timestamp(100), "before"))
    bus.unsubscribe(subscription)
    bus.publish(Ping(Timestamp(100), "after"))
    assert [p.label for p in received] == ["before"]


def test_a_failing_handler_abandons_the_message_and_the_queue(bus: MessageBus) -> None:
    received: list[str] = []

    def queue_then_explode(ping: Ping) -> None:
        bus.publish(Pong(ping.ts, "queued"))
        raise RuntimeError("boom")

    bus.subscribe(Ping, lambda ping: bus.publish(Pong(ping.ts, "earlier")))
    failing = bus.subscribe(Ping, queue_then_explode)
    bus.subscribe(Ping, lambda _: received.append("later subscriber"))
    bus.subscribe(Pong, lambda pong: received.append(pong.label))
    with pytest.raises(RuntimeError, match="boom"):
        bus.publish(Ping(Timestamp(100)))
    assert received == []
    assert not bus.dispatching

    bus.unsubscribe(failing)
    bus.publish(Pong(Timestamp(100), "afterwards"))
    assert received == ["afterwards"]


def test_a_subscriber_added_during_delivery_receives_only_later_messages(
    bus: MessageBus,
) -> None:
    late: list[str] = []

    def add_subscriber(ping: Ping) -> None:
        if ping.label == "first":
            bus.subscribe(Ping, lambda p: late.append(p.label))

    bus.subscribe(Ping, add_subscriber)
    bus.publish(Ping(Timestamp(100), "first"))
    bus.publish(Ping(Timestamp(100), "second"))
    assert late == ["second"]


def test_a_subscriber_removed_during_delivery_receives_nothing_more(bus: MessageBus) -> None:
    received: list[str] = []
    victim = bus.subscribe(Pong, lambda pong: received.append(pong.label))

    def remove_victim(ping: Ping) -> None:
        bus.publish(Pong(ping.ts, "queued before removal"))
        bus.unsubscribe(victim)

    bus.subscribe(Ping, remove_victim)
    bus.publish(Ping(Timestamp(100)))
    assert received == []
    assert not victim.active


def test_dispatching_is_true_only_inside_handlers(bus: MessageBus) -> None:
    observed: list[bool] = []
    bus.subscribe(Ping, lambda _: observed.append(bus.dispatching))
    assert not bus.dispatching
    bus.publish(Ping(Timestamp(100)))
    assert observed == [True]
    assert not bus.dispatching


def test_a_type_isinstance_cannot_check_is_refused_at_subscription(bus: MessageBus) -> None:
    with pytest.raises(InvalidTypeError, match="cannot subscribe to"):
        bus.subscribe(Message, lambda _: None)  # type: ignore[type-abstract]


def test_the_bus_reports_its_clock_without_exposing_it(
    bus: MessageBus, clock: SimulatedClock
) -> None:
    assert bus.runs_on(clock)
    assert not bus.runs_on(SimulatedClock(Timestamp(100)))
    assert not hasattr(bus, "clock")
