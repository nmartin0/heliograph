# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The simulated broker: a strategy's signals filled at the next bar's open, with costs.

A signal is made at the close of a bar of the strategy's price stream; the
broker fills it at the open of that stream's next bar (decision D31): the
first bar starting at or after the end of the latest bar of the stream
known when the signal came. (Not after the signal's own instant: with a
grace period, a bar is published a little after it ends, and so is the
signal decided on it, after the next bar has started.) The open is only
known once that bar is (at its close, or later for a bar that arrived
late), so the ``Fill`` is published then, stamped with that instant
and with the bar's start as when it happened. It is delivered before the
instant settles, so the strategy's next decision, made at that same close,
already holds the filled position.

A ``FillModel`` (a plug-in of kind ``fills``) prices each fill: the price
paid or received for the quantity traded, worse than the open by whatever
spread and slippage it charges, and the commission.

The broker trades the difference between the target signalled and the
position it holds. A later signal before a fill replaces the earlier one:
only the latest target is filled. Nothing here places a real order.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from heliograph.core.bus import MessageBus
from heliograph.core.errors import InvalidValueError
from heliograph.core.messages import Bar, Instrument
from heliograph.core.pipeline import Signal
from heliograph.core.time import Timestamp


class FillModel(Protocol):
    def price(self, open_: float, quantity: float) -> float:
        """What one unit costs (bought, ``quantity`` > 0) or fetches (sold) at this open."""
        ...

    def commission(self, price: float, quantity: float) -> float:
        """The commission on trading ``quantity`` units at ``price``, in the quote currency."""
        ...


@dataclass(frozen=True, slots=True, kw_only=True)
class Fill:
    """``quantity`` units of ``instrument`` traded at ``price`` (bought if positive).

    ``position`` is the strategy's position after it; ``commission`` what it
    cost besides the price; ``signal`` the id of the signal it filled.
    ``event_time`` is when it happened (the bar's open), ``ts`` when it
    became known (when that bar did).
    """

    ts: Timestamp
    event_time: Timestamp
    strategy: str
    instrument: Instrument
    signal: str
    quantity: float
    price: float
    commission: float
    position: float

    def __post_init__(self) -> None:
        for name in ("quantity", "price", "commission", "position"):
            if not math.isfinite(getattr(self, name)):
                raise InvalidValueError(f"Fill.{name} must be finite")
        if self.price <= 0:
            raise InvalidValueError(f"Fill.price must be positive, not {self.price}")
        if self.commission < 0:
            raise InvalidValueError(f"Fill.commission must not be negative, not {self.commission}")


class SimulatedBroker:
    """Fills one strategy's signals at the next open of its price stream; see the module."""

    __slots__ = (
        "_after",
        "_bus",
        "_ended",
        "_heard",
        "_instrument",
        "_model",
        "_pending",
        "_position",
        "_size",
        "_strategy",
    )

    def __init__(
        self,
        strategy: str,
        instrument: Instrument,
        size: int,
        model: FillModel,
        bus: MessageBus,
    ) -> None:
        self._strategy = strategy
        self._instrument = instrument
        self._size = size
        self._model = model
        self._bus = bus
        self._pending: Signal | None = None
        self._after = 0  # the pending signal fills at a bar starting at or after this
        self._ended: int | None = None  # the latest end of a bar of the stream
        self._position = 0.0
        self._heard: list[Callable[[Fill], None]] = []
        bus.subscribe(Signal, self._on_signal)
        bus.subscribe(Bar, self._on_bar)

    @property
    def position(self) -> float:
        return self._position

    def on_fill(self, listener: Callable[[Fill], None]) -> None:
        """Call ``listener`` with each fill, as it is published."""
        self._heard.append(listener)

    def _on_signal(self, signal: Signal) -> None:
        if signal.strategy == self._strategy:
            self._pending = signal  # a later signal replaces one not yet filled
            self._after = signal.ts if self._ended is None else self._ended

    def _on_bar(self, bar: Bar) -> None:
        if bar.instrument != self._instrument or bar.end - bar.start != self._size:
            return
        self._ended = bar.end if self._ended is None else max(self._ended, bar.end)
        pending = self._pending
        if pending is None or bar.start < self._after:
            return  # the bar the signal was made on, or an earlier one arriving late
        self._pending = None
        quantity = pending.target - self._position
        if quantity == 0:
            return
        price = self._model.price(bar.open, quantity)
        commission = self._model.commission(price, quantity)
        self._position = pending.target
        fill = Fill(
            ts=bar.ts,
            event_time=bar.start,
            strategy=self._strategy,
            instrument=self._instrument,
            signal=pending.id,
            quantity=quantity,
            price=price,
            commission=commission,
            position=self._position,
        )
        self._bus.publish(fill)
        for listener in self._heard:
            listener(fill)
