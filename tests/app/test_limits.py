# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The throttle on simulated time: sleeping advances the clock, nothing waits."""

import asyncio

import pytest
from hypothesis import given
from hypothesis import strategies as st

from heliograph.app.limits import RateThrottle
from heliograph.core.sources import Capabilities, RateLimit
from heliograph.core.time import Timestamp
from tests.support.simulated import SimulatedTime

SECOND = 10**9


def throttle(
    calls: int | None, period: int = 10 * SECOND, concurrency: int = 1
) -> tuple[RateThrottle, SimulatedTime]:
    time = SimulatedTime()
    limit = None if calls is None else RateLimit(calls=calls, period=period)
    capabilities = Capabilities(rate_limit=limit, concurrency=concurrency)
    return RateThrottle(capabilities, time.clock, time.advance), time


async def starts(gate: RateThrottle, time: SimulatedTime, n: int) -> list[int]:
    out: list[int] = []
    for _ in range(n):
        async with gate:
            out.append(time.clock.now())
    return out


def test_requests_start_at_most_n_per_period() -> None:
    gate, time = throttle(calls=3)
    started = asyncio.run(starts(gate, time, 7))
    assert started == [0, 0, 0, 10 * SECOND, 10 * SECOND, 10 * SECOND, 20 * SECOND]


def test_without_a_rate_limit_nothing_waits() -> None:
    gate, time = throttle(calls=None)
    assert asyncio.run(starts(gate, time, 50)) == [0] * 50
    assert time.slept == []


@given(
    st.integers(1, 5),
    st.integers(1, 20),
    st.lists(st.integers(0, 15 * SECOND), min_size=1, max_size=30),
)
def test_no_window_of_one_period_ever_holds_more_than_n_starts(
    calls: int, period_seconds: int, pauses: list[int]
) -> None:
    """Whatever the gaps between requests, every window (t - period, t] is within the limit."""
    period = period_seconds * SECOND
    gate, time = throttle(calls=calls, period=period)

    async def scenario() -> list[int]:
        out: list[int] = []
        for pause in pauses:
            time.clock.advance_to(Timestamp(time.clock.now() + pause))
            async with gate:
                out.append(time.clock.now())
        return out

    started = asyncio.run(scenario())
    assert len(started) == len(pauses)
    for end in started:
        assert sum(1 for t in started if end - period < t <= end) <= calls


def test_no_more_requests_are_in_flight_than_the_declared_concurrency() -> None:
    """Forced: three requests, each held inside until released; the third waits for a slot."""
    gate, _ = throttle(calls=None, concurrency=2)
    inside: list[str] = []
    most = 0
    release = {name: asyncio.Event() for name in "abc"}

    async def request(name: str) -> None:
        nonlocal most
        async with gate:
            inside.append(name)
            most = max(most, len(inside))
            await release[name].wait()
            inside.remove(name)

    async def scenario() -> list[str]:
        tasks = [asyncio.create_task(request(name)) for name in "abc"]
        await asyncio.sleep(0)  # let all three try to enter
        await asyncio.sleep(0)
        held = sorted(inside)
        release["a"].set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        release["b"].set()
        release["c"].set()
        await asyncio.gather(*tasks)
        return held

    held = asyncio.run(scenario())
    assert held == ["a", "b"]
    assert most == 2


def test_a_request_that_fails_inside_frees_its_slot() -> None:
    gate, _ = throttle(calls=None, concurrency=1)

    async def scenario() -> None:
        with pytest.raises(RuntimeError):
            async with gate:
                raise RuntimeError("request failed")
        async with gate:  # would wait for ever if the slot had leaked
            pass

    asyncio.run(asyncio.wait_for(scenario(), timeout=1))


def test_a_request_cancelled_while_waiting_for_the_rate_frees_its_slot() -> None:
    gate, time = throttle(calls=1, concurrency=1)
    entered = asyncio.Event()

    async def hang(_: float) -> None:
        entered.set()
        await asyncio.Event().wait()  # never wakes: only cancellation ends it

    async def scenario() -> None:
        async with gate:
            pass  # uses the one call of this period
        gate._sleep = hang  # the next entry waits for the rate, for ever
        waiting = asyncio.create_task(gate.__aenter__())
        await entered.wait()
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        gate._sleep = time.advance
        async with gate:  # the cancelled wait must not hold the slot
            pass

    asyncio.run(asyncio.wait_for(scenario(), timeout=1))


def test_a_throttle_serves_one_event_loop_after_another() -> None:
    """Each SourceLoop has its own loop; a source and its throttle outlive them."""
    gate, _ = throttle(calls=None, concurrency=1)

    async def two_at_once() -> None:
        async def request() -> None:
            async with gate:
                await asyncio.sleep(0)  # hold the slot so the other must wait

        await asyncio.gather(request(), request())

    asyncio.run(two_at_once())
    asyncio.run(two_at_once())  # a new loop: the slots are made again


def test_a_throttle_in_use_on_one_loop_is_refused_on_another() -> None:
    gate, _ = throttle(calls=None, concurrency=1)
    entered, release = asyncio.Event(), asyncio.Event()

    async def hold() -> None:
        async with gate:
            entered.set()
            await release.wait()

    async def scenario() -> None:
        holder = asyncio.create_task(hold())
        await entered.wait()

        async def elsewhere() -> None:
            async with gate:
                pass

        with pytest.raises(RuntimeError, match="in use on another event loop"):
            await asyncio.to_thread(asyncio.run, elsewhere())
        release.set()
        await holder

    asyncio.run(scenario())
