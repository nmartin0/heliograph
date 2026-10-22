# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Simulated time for asynchronous code: a clock and a sleep that moves it on."""

from heliograph.core.clock import SimulatedClock
from heliograph.core.time import NANOS_PER_SECOND, Timestamp


class SimulatedTime:
    """A clock, and a sleep that advances it instead of waiting.

    Pass ``clock`` and ``advance`` wherever code takes a clock and a sleep
    (the throttle, the live bridge), and nothing in the test waits.
    """

    def __init__(self, start: int = 0) -> None:
        self.clock = SimulatedClock(Timestamp(start))
        self.slept: list[float] = []

    async def advance(self, seconds: float) -> None:
        """Stands in for asyncio.sleep: moves the clock on instead of waiting.

        Code asking to wait no time would spin for ever, so that fails.
        """
        assert seconds > 0, f"asked to wait {seconds} s, which would spin"
        self.slept.append(seconds)
        self.clock.advance_to(Timestamp(self.clock.now() + round(seconds * NANOS_PER_SECOND)))
