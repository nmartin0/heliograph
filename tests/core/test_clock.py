import pytest

from heliograph.core.clock import Clock, SimulatedClock, WallClock
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
