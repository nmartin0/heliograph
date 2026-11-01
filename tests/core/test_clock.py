# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

import pytest

from heliograph.core.clock import Clock, ResumedClock, SimulatedClock, WallClock
from heliograph.core.errors import ClockError
from heliograph.core.time import NANOS_PER_DAY, Timestamp


def test_simulated_clock_starts_where_it_is_told() -> None:
    assert SimulatedClock(Timestamp(42)).now() == 42


def test_simulated_clock_moves_forward_and_may_stand_still() -> None:
    clock = SimulatedClock(Timestamp(0))
    clock.advance_to(Timestamp(10))
    clock.advance_to(Timestamp(10))
    assert clock.now() == 10


def test_simulated_clock_refuses_to_move_backwards() -> None:
    clock = SimulatedClock(Timestamp(10))
    with pytest.raises(ClockError, match="back"):
        clock.advance_to(Timestamp(9))
    assert clock.now() == 10


def test_wall_clock_reads_a_plausible_current_time() -> None:
    clock: Clock = WallClock()
    first, second = clock.now(), clock.now()
    assert second >= first
    # Later than 2026-01-01 and earlier than 2100-01-01.
    assert 20_454 * NANOS_PER_DAY < first < 47_482 * NANOS_PER_DAY


def test_wall_clock_holds_still_when_the_system_clock_steps_back() -> None:
    readings = iter([100, 150, 120, 140, 160])
    clock = WallClock(source=lambda: next(readings))
    assert [clock.now() for _ in range(4)] == [150, 150, 150, 160]


def test_a_resumed_clock_stands_where_it_is_told_and_then_follows_the_live_one() -> None:
    live = SimulatedClock(Timestamp(1_000))
    clock = ResumedClock(live)
    clock.stand_at(Timestamp(10))
    assert clock.now() == 10  # held, whatever the live clock says
    clock.stand_at(Timestamp(10))
    clock.stand_at(Timestamp(30))
    assert clock.now() == 30
    clock.release()
    assert clock.now() == 1_000
    live.advance_to(Timestamp(1_500))
    assert clock.now() == 1_500


def test_a_resumed_clock_never_goes_back() -> None:
    clock = ResumedClock(SimulatedClock(Timestamp(0)))
    clock.stand_at(Timestamp(50))
    with pytest.raises(ClockError, match="cannot move the clock back from 50 to 49"):
        clock.stand_at(Timestamp(49))


def test_a_resumed_clock_holds_where_the_old_session_stopped_if_the_live_one_is_behind() -> None:
    live = SimulatedClock(Timestamp(20))
    clock = ResumedClock(live)
    clock.stand_at(Timestamp(100))
    clock.release()
    assert clock.now() == 100  # the machine's clock is behind; wait for it
    live.advance_to(Timestamp(100))
    live.advance_to(Timestamp(101))
    assert clock.now() == 101


def test_a_resumed_clock_cannot_be_held_again_once_released() -> None:
    clock = ResumedClock(SimulatedClock(Timestamp(0)))
    clock.release()
    with pytest.raises(ClockError, match="released"):
        clock.stand_at(Timestamp(1))
