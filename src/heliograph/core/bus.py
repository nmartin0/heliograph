# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The message bus: how layers talk without holding references to each other."""

from collections import deque
from collections.abc import Callable
from typing import Any

from heliograph.core.clock import Clock
from heliograph.core.errors import InvalidTypeError, LookAheadError
from heliograph.core.journal import Journal, JournalEntry, Origin
from heliograph.core.messages import Message


class Subscription[M: Message]:
    """A handle returned by :meth:`MessageBus.subscribe`, used to unsubscribe."""

    __slots__ = ("_active", "_handler", "_message_type")

    def __init__(self, message_type: type[M], handler: Callable[[M], None]) -> None:
        self._message_type = message_type
        self._handler = handler
        self._active = True

    @property
    def message_type(self) -> type[M]:
        """The message type this subscription receives."""
        return self._message_type

    @property
    def active(self) -> bool:
        """False once unsubscribed."""
        return self._active


class MessageBus:
    """Delivers each message to every subscriber of its type, never ahead of the clock.

    Delivery is synchronous and breadth-first: a message published by a handler
    is queued and delivered after the current message has reached all of its
    subscribers. Subscribers receive messages in the order they subscribed.

    If a handler raises, the exception propagates to the publisher: the current
    message does not reach the remaining subscribers and every queued message is
    discarded. A failing handler ends the run; delivery is never half-resumed.

    Every accepted message gets the next sequence number and, with a journal,
    is recorded before it is delivered, in delivery order.
    """

    __slots__ = ("_clock", "_dispatching", "_journal", "_queue", "_sequence", "_subscriptions")

    def __init__(self, clock: Clock, journal: Journal | None = None) -> None:
        self._clock = clock
        self._journal = journal
        self._subscriptions: list[Subscription[Any]] = []
        self._queue: deque[Message] = deque()
        self._dispatching = False
        self._sequence = 0

    @property
    def sequence(self) -> int:
        """The sequence number of the last message accepted; 0 before the first."""
        return self._sequence

    @property
    def dispatching(self) -> bool:
        """True while messages are being delivered to handlers."""
        return self._dispatching

    def runs_on(self, clock: Clock) -> bool:
        """Whether publishing is checked against ``clock``.

        The clock itself is not exposed, so no component holding the bus can
        move a simulated clock.
        """
        return self._clock is clock

    def subscribe[M: Message](
        self, message_type: type[M], handler: Callable[[M], None]
    ) -> Subscription[M]:
        """Call ``handler`` with every published message that is a ``message_type``.

        Raises:
            InvalidTypeError: if ``message_type`` cannot be used with ``isinstance``,
                such as a protocol that is not runtime-checkable.
        """
        try:
            isinstance(None, message_type)
        except TypeError as exc:
            raise InvalidTypeError(f"cannot subscribe to {message_type!r}: {exc}") from exc
        subscription = Subscription(message_type, handler)
        self._subscriptions.append(subscription)
        return subscription

    def unsubscribe(self, subscription: Subscription[Any]) -> None:
        """Stop deliveries to a subscription, including any already queued."""
        subscription._active = False
        self._subscriptions = [s for s in self._subscriptions if s is not subscription]

    def publish(self, message: Message, *, origin: Origin = "input") -> None:
        """Deliver ``message`` to its subscribers.

        ``origin`` is recorded in the journal: ``input`` for outside data,
        ``engine`` for what the engine produces itself. A message published by
        a handler is always ``derived``, whatever is passed.

        Raises:
            LookAheadError: if the message is stamped later than the clock's
                current time, which would let a component see the future.
        """
        now = self._clock.now()
        if message.ts > now:
            raise LookAheadError(
                f"{type(message).__name__} stamped {message.ts} published at {now}"
            )
        self._sequence += 1
        if self._journal is not None:
            recorded: Origin = "derived" if self._dispatching else origin
            self._journal.append(JournalEntry(self._sequence, recorded, message))
        self._queue.append(message)
        if self._dispatching:
            return
        self._dispatching = True
        try:
            while self._queue:
                self._deliver(self._queue.popleft())
        except BaseException:
            self._queue.clear()
            raise
        finally:
            self._dispatching = False

    def _deliver(self, message: Message) -> None:
        for subscription in tuple(self._subscriptions):
            if subscription.active and isinstance(message, subscription.message_type):
                subscription._handler(message)
