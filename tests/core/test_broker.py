# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The simulated broker on the real engine and bus, fed signals and bars by hand."""

from collections.abc import Sequence
from dataclasses import dataclass

import pytest

from heliograph.core.broker import Fill, SimulatedBroker
from heliograph.core.errors import InvalidValueError
from heliograph.core.messages import Bar, Instrument, Message
from heliograph.core.pipeline import Signal
from heliograph.core.time import NANOS_PER_HOUR, Timestamp
from tests.support.world import BTC, ETH, world

HOUR = NANOS_PER_HOUR


@dataclass
class Plus:
    """Buys pay the open plus ``cost``, sells receive it less; commission per unit."""

    cost: float = 0.5
    per_unit: float = 0.25

    def price(self, open_: float, quantity: float) -> float:
        return open_ + self.cost if quantity > 0 else open_ - self.cost

    def commission(self, price: float, quantity: float) -> float:
        return self.per_unit * abs(quantity)


def bar(
    start: int,
    open_: float,
    close: float,
    instrument: Instrument = BTC,
    hours: int = 1,
    received: int | None = None,
    received_ns: int | None = None,
) -> Bar:
    low, high = min(open_, close), max(open_, close)
    return Bar(
        instrument=instrument,
        start=Timestamp(start * HOUR),
        end=Timestamp((start + hours) * HOUR),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=1.0,
        received_at=(
            Timestamp(received_ns)
            if received_ns is not None
            else None
            if received is None
            else Timestamp(received * HOUR)
        ),
    )


def signal(
    hour: int, target: float, strategy: str = "s", held: float = 0.0, late_ns: int = 0
) -> Signal:
    return Signal(
        ts=Timestamp(hour * HOUR + late_ns),
        id=f"{strategy}-{hour}-{target}",
        strategy=strategy,
        instrument=BTC,
        side="long" if target > 0 else "short" if target < 0 else "flat",
        target=target,
        held=held,
        price=100.0,
        forecast=0.0,
    )


def fills(messages: Sequence[Message], until: int) -> tuple[list[Fill], SimulatedBroker]:
    w = world()
    sim = SimulatedBroker("s", BTC, HOUR, Plus(), w.bus)
    seen = w.record(Fill)
    w.engine.run(messages, until=Timestamp(until * HOUR))
    return [m for m in seen if isinstance(m, Fill)], sim


def test_a_signal_is_filled_at_the_next_bars_open_when_that_bar_is_known() -> None:
    out, sim = fills([bar(0, 99, 100), signal(1, 2.0), bar(1, 102, 104)], until=2)
    (fill,) = out
    assert (fill.ts, fill.event_time) == (2 * HOUR, HOUR)
    assert (fill.quantity, fill.price, fill.commission, fill.position) == (2.0, 102.5, 0.5, 2.0)
    assert fill.signal == "s-1-2.0"
    assert sim.position == 2.0


def test_the_broker_trades_the_difference_and_sells_below_the_open() -> None:
    messages: list[Message] = [
        signal(1, 2.0),
        bar(1, 102, 104),
        signal(2, -1.0, held=2.0),
        bar(2, 103, 100),
    ]
    out, _ = fills(messages, until=3)
    assert [(f.quantity, f.price, f.position) for f in out] == [
        (2.0, 102.5, 2.0),
        (-3.0, 102.5, -1.0),
    ]
    assert out[1].commission == 0.75


def test_only_a_later_bar_of_its_own_stream_fills_a_signal() -> None:
    messages: list[Message] = [
        signal(1, 1.0),
        bar(0, 50, 50),  # an earlier bar, arriving late
        bar(1, 60, 60, instrument=ETH),  # another instrument
        bar(1, 70, 70, hours=2),  # another size
        bar(3, 103, 104),  # the next bar of the stream, after a gap
    ]
    out, _ = fills(messages, until=4)
    assert [(f.ts // HOUR, f.price) for f in out] == [(4, 103.5)]


def test_a_later_signal_replaces_one_not_yet_filled_and_no_trade_fills_nothing() -> None:
    messages: list[Message] = [
        signal(1, 2.0),
        signal(1, 5.0),
        bar(1, 100, 100),
        signal(2, 5.0),
        bar(2, 100, 100),
    ]
    out, sim = fills(messages, until=3)
    assert [f.quantity for f in out] == [5.0]
    assert sim.position == 5.0


def test_a_bar_that_arrived_late_fills_when_it_arrived() -> None:
    out, _ = fills([bar(0, 99, 99), signal(1, 1.0), bar(1, 100, 100, received=5)], until=5)
    assert [(f.ts // HOUR, f.event_time // HOUR) for f in out] == [(5, 1)]


def test_with_a_grace_period_the_next_bar_started_before_the_signal_and_fills_it() -> None:
    """Bars published a minute after they end: the signal on bar 0 is stamped 1h01."""
    minute = HOUR // 60
    graced: list[Message] = [
        bar(0, 99, 99, received_ns=HOUR + minute),
        signal(1, 1.0, late_ns=minute),
        bar(1, 100, 100, received_ns=2 * HOUR + minute),
    ]
    out, _ = fills(graced, until=3)
    assert [(f.ts, f.event_time, f.price) for f in out] == [(2 * HOUR + minute, HOUR, 100.5)]


def test_signals_of_another_strategy_are_not_filled() -> None:
    out, _ = fills([signal(1, 1.0, strategy="other"), bar(1, 100, 100)], until=2)
    assert out == []


def test_listeners_hear_each_fill() -> None:
    w = world()
    sim = SimulatedBroker("s", BTC, HOUR, Plus(), w.bus)
    heard: list[float] = []
    sim.on_fill(lambda fill: heard.append(fill.position))
    w.engine.run([signal(1, 3.0), bar(1, 100, 100)], until=Timestamp(2 * HOUR))
    assert heard == [3.0]


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"price": 0.0}, "price must be positive"),
        ({"commission": -1.0}, "commission must not be negative"),
        ({"quantity": float("nan")}, "quantity must be finite"),
    ],
)
def test_a_fill_that_cannot_be_is_refused_as_it_is_made(
    change: dict[str, float], reason: str
) -> None:
    fields = {"quantity": 1.0, "price": 100.0, "commission": 0.0, "position": 1.0, **change}
    with pytest.raises(InvalidValueError, match=reason):
        Fill(
            ts=Timestamp(0),
            event_time=Timestamp(0),
            strategy="s",
            instrument=BTC,
            signal="x",
            **fields,
        )
