# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Enforcing a source's declared limits: the Throttle the app binds to each source.

A source declares how often it may be called and how many requests may be
in flight (``Capabilities.rate_limit`` and ``concurrency``); the app builds a
``RateThrottle`` from that and binds it, and the source enters it around each
request (core/sources.py). Sources never throttle themselves.

The rate limit is a sliding window: at most ``calls`` requests start within
any ``period`` nanoseconds. Time comes from a ``Clock`` and waiting from an
injected ``sleep``, so tests run on simulated time and never wait. The two
must agree: the real time with ``asyncio.sleep``, or a simulated clock with a
sleep that advances it.

A source outlives event loops (each ``SourceLoop`` has its own), so the
throttle is not tied to one: its concurrency slots are made for the loop
using them, and remade when another loop uses it after the first is done.
Using one throttle from two loops at once is refused.
"""

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable

from heliograph.core.clock import Clock
from heliograph.core.sources import Capabilities

type Sleep = Callable[[float], Awaitable[None]]


class RateThrottle:
    """At most ``rate_limit.calls`` starts per ``period`` and ``concurrency`` in flight."""

    def __init__(self, capabilities: Capabilities, clock: Clock, sleep: Sleep = asyncio.sleep):
        self._limit = capabilities.rate_limit
        self._concurrency = capabilities.concurrency
        self._clock = clock
        self._sleep = sleep
        self._loop: asyncio.AbstractEventLoop | None = None
        self._slots = asyncio.Semaphore(self._concurrency)
        self._users = 0  # requests inside or waiting, on self._loop
        self._starts: deque[int] = deque()

    def _slots_here(self) -> asyncio.Semaphore:
        """The concurrency slots for the running loop, made anew for a new loop."""
        loop = asyncio.get_running_loop()
        if loop is not self._loop:
            if self._users:
                raise RuntimeError("a throttle is in use on another event loop")
            self._loop = loop
            self._slots = asyncio.Semaphore(self._concurrency)
        return self._slots

    async def __aenter__(self) -> None:
        slots = self._slots_here()
        self._users += 1
        try:
            await slots.acquire()
        except BaseException:
            self._users -= 1
            raise
        try:
            await self._admit()
        except BaseException:
            # Cancelled or failed while waiting: the slot was never used.
            slots.release()
            self._users -= 1
            raise

    async def __aexit__(self, *exc: object) -> None:
        self._slots.release()
        self._users -= 1

    async def _admit(self) -> None:
        limit = self._limit
        if limit is None:
            return
        while True:
            now = self._clock.now()
            # Starts at or before now - period are outside the window (t - period, t].
            while self._starts and self._starts[0] <= now - limit.period:
                self._starts.popleft()
            if len(self._starts) < limit.calls:
                self._starts.append(now)
                return
            await self._sleep((self._starts[0] + limit.period - now) / 1e9)
