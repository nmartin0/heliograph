# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Signal sinks: the port through which a signal leaves Heliograph (decision D46).

A sink is where a person is told: a phone notification, a desktop popup, a
webhook, a log. It does one thing asynchronously, ``emit`` a signal, and says
what happened in a ``Delivery``. The core declares the protocol and owns no
event loop; the app's dispatcher (app/dispatch.py) calls every sink for every
signal, retrying each on its own.

How a sink fails says what to do about it:

- ``SinkUnavailableError``: not now (unreachable, over its limit); asking
  again may work, so the dispatcher does, with backoff;
- ``InvalidValueError``: never (a bad address, a refused key); asking again
  cannot help, so it is not retried;
- ``Delivery(ok=False)``: the sink answered and declined this signal.

A signal's ``id`` is stable, so a sink that is asked twice about the same
signal (a retry after a lost reply) should deliver it once.

Besides signals a sink may take an ``Alert`` (a halt, or its clearing; D58) by also
having ``alert``. A sink without it is simply not told: ``AlertSink`` says which can.
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from heliograph.core.errors import HeliographError
from heliograph.core.pipeline import Signal
from heliograph.core.sources import Health
from heliograph.core.time import Timestamp


class SinkUnavailableError(HeliographError):
    """A sink could not deliver now: unreachable, refused for the moment, or over its limit."""


@dataclass(frozen=True, slots=True)
class Delivery:
    """What a sink says about one signal: delivered, or declined with the reason."""

    ok: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class Delivered:
    """What became of one signal at one sink, as the run records it.

    It happens outside the run (a request over the network), so it is an input
    like market data: stamped when it was learned, journalled, and replayed.
    ``attempts`` is 0 for a signal never given to the sink.
    """

    ts: Timestamp
    signal: str
    sink: str
    ok: bool
    attempts: int
    detail: str = ""


@dataclass(frozen=True, slots=True)
class Alert:
    """Something the person should know that is not a signal: a halt, or its clearing.

    ``id`` is stable (the same event always has the same id), so an alert asked twice
    is delivered once, as with signals.
    """

    ts: Timestamp
    id: str
    title: str
    text: str


class SignalSink(Protocol):
    """Where signals go, asynchronously."""

    async def emit(self, signal: Signal) -> Delivery:
        """Deliver ``signal``, once however often it is asked.

        Raises:
            SinkUnavailableError: if it cannot be delivered now.
            InvalidValueError: if it can never be delivered as configured.
        """
        ...

    async def health_check(self) -> Health:
        """Whether the sink can deliver now."""
        ...

    async def close(self) -> None:
        """Release what the sink holds (connections, files)."""
        ...


@runtime_checkable
class AlertSink(Protocol):
    """A sink that can also tell the person an ``Alert`` (optional)."""

    async def alert(self, alert: Alert) -> Delivery:
        """Deliver ``alert``, once however often it is asked; fails as ``emit`` does."""
        ...
