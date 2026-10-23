# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The strategy stages: combiner, sizer, exit and risk gate, against values worked by hand."""

import pytest
from pydantic import ValidationError

from heliograph.core.errors import InvalidValueError
from heliograph.core.pipeline import Proposal
from heliograph.core.time import NANOS_PER_HOUR, Timestamp
from heliograph.plugins.stages import (
    Limits,
    LimitsParams,
    TrailingStop,
    TrailingStopParams,
    VolTarget,
    VolTargetParams,
    Weighted,
    WeightedParams,
)
from tests.support.world import BTC

HOUR = NANOS_PER_HOUR

# -- weighted ---------------------------------------------------------------------------


def test_weighted_sums_by_weight_times_the_multiplier() -> None:
    combiner = Weighted(WeightedParams(weights={"a": 0.25, "b": 0.75}, multiplier=2.0))
    assert combiner.combine({"a": 8.0, "b": 4.0}) == 2.0 * (2.0 + 3.0)
    assert combiner.expects() == frozenset({"a", "b"})


def test_weighted_with_no_weights_weighs_every_forecast_the_same() -> None:
    combiner = Weighted(WeightedParams())
    assert combiner.combine({"a": 8.0, "b": 4.0, "c": 0.0}) == 4.0
    assert combiner.expects() is None


def test_weighted_given_other_forecasts_than_its_weights_is_refused() -> None:
    combiner = Weighted(WeightedParams(weights={"a": 1.0}))
    with pytest.raises(InvalidValueError, match=r"weights for \['a'\], given \['b'\]"):
        combiner.combine({"b": 1.0})


@pytest.mark.parametrize(
    ("weights", "reason"),
    [({"a": 0.5, "b": 0.4}, "must sum to 1"), ({"a": 1.5, "b": -0.5}, "not negative")],
)
def test_weights_that_do_not_sum_to_one_or_are_negative_are_refused(
    weights: dict[str, float], reason: str
) -> None:
    with pytest.raises(ValidationError, match=reason):
        WeightedParams(weights=weights)


# -- vol_target -------------------------------------------------------------------------


def sizer(**params: float) -> VolTarget:
    """Volatility of changes exactly 1 on prices alternating 100, 101: the average
    position is target x capital / (1 x sqrt(bars_per_year)) = 0.1 x 1000 / 10 = 10."""
    base = {
        "capital": 1000.0,
        "target": 0.1,
        "bars_per_year": 100.0,
        "vol_span": 2.0,
        "max_leverage": 5.0,
    }
    return VolTarget(VolTargetParams(**{**base, **params}))  # type: ignore[arg-type]


def test_vol_target_sizes_the_forecast_against_the_average_position() -> None:
    s = sizer(buffer=0.0)
    assert [s.target(10.0, p, 0.0) for p in (100, 101)] == [None, None]  # two changes to warm
    assert s.target(10.0, 100, 0.0) == pytest.approx(10.0)
    assert s.average() == pytest.approx(10.0)
    assert s.target(-5.0, 101, 0.0) == pytest.approx(-5.0)


def test_vol_target_keeps_the_position_held_within_its_buffer_and_trades_to_its_edge() -> None:
    """Buffer 0.1 x the average of 10: within 1 unit of 10, no trade; outside, to 9 or 11."""
    s = sizer(buffer=0.1)
    for price in (100, 101):
        s.target(None, price, 0.0)
    assert s.target(10.0, 100, 9.5) == 9.5
    assert s.target(10.0, 101, 8.5) == pytest.approx(9.0)
    assert s.target(10.0, 100, 12.0) == pytest.approx(11.0)


def test_vol_target_may_trade_to_the_target_itself() -> None:
    s = sizer(buffer=0.1, to_edge=False)
    for price in (100, 101):
        s.target(None, price, 0.0)
    assert s.target(10.0, 100, 8.5) == pytest.approx(10.0)


def test_vol_target_holds_a_position_to_its_leverage_in_value() -> None:
    """Capital 1000 at a price of 100, leverage 1: never more than 10 units either way."""
    s = sizer(buffer=0.0, max_leverage=1.0, to_edge=False)
    for price in (100, 101):
        s.target(None, price, 0.0)
    assert s.target(20.0, 100, 0.0) == pytest.approx(10.0)  # wanted 20
    assert s.target(-20.0, 101, 0.0) == pytest.approx(-1000.0 / 101)


def test_vol_target_sizes_against_a_floored_volatility_in_a_quiet_market() -> None:
    """500 changes of 1, then 20 of 0.01: unfloored, the volatility would fall to
    (1 - 2/37)^10 (0.57) of itself and the position grow by 3/4; floored at 5% of the last 500
    volatilities, it holds. (Past about 25 such bars the floor follows them down:
    staleness, not the floor, covers a feed that stops.)"""
    s = sizer(buffer=0.0, vol_span=36.0, max_leverage=1e2)
    price = 100.0
    for step in range(500):
        price += 1 if step % 2 else -1
        s.target(None, price, 0.0)
    before = s.target(10.0, price, 0.0)
    for step in range(20):
        price += 0.01 if step % 2 else -0.01
        after = s.target(10.0, price, 0.0)
    assert before is not None
    assert after is not None
    assert after == pytest.approx(before, rel=0.05)


def test_vol_target_neither_drains_nor_sizes_up_on_a_stalled_feed() -> None:
    """Past stall_after (3) unchanged prices, the feed has stalled: the average position
    stays put, and the first move after is measured against the volatility from before."""
    s = sizer(buffer=0.0, vol_span=4.0)
    for price in (100, 101, 100, 101, 100):
        s.target(None, price, 0.0)
    for _ in range(2):
        s.target(10.0, 100, 0.0)  # two unchanged prices count, as a market can be still
    stalled = s.average()
    for _ in range(50):
        assert s.target(10.0, 100, 0.0) == pytest.approx(stalled)
    s.target(10.0, 101, 0.0)
    assert s.average() is not None
    assert s.average() <= stalled  # type: ignore[operator]


def test_vol_target_cannot_size_while_prices_have_not_moved() -> None:
    s = sizer()
    assert [s.target(10.0, 100, 0.0) for _ in range(5)] == [None] * 5


# -- trailing_stop ----------------------------------------------------------------------


def test_a_long_is_stopped_from_its_entry_close_and_stays_out_while_still_wanted() -> None:
    """As the pipeline calls it: held is the position before each decision."""
    stop = TrailingStop(TrailingStopParams(multiple=2.0))
    assert stop.adjust(1.0, 0.0, 100.0, [1.0]) == 1.0  # enters at 100: stop 98
    assert stop.adjust(1.0, 1.0, 97.0, [1.0]) == 0.0  # below the stop at the first close after
    assert stop.adjust(1.0, 0.0, 110.0, [1.0]) == 0.0  # still wanting long: stays out
    assert stop.adjust(0.0, 0.0, 110.0, [1.0]) == 0.0  # flat: let go
    assert stop.adjust(1.0, 0.0, 110.0, [1.0]) == 1.0  # and may go long again


def test_a_long_stop_trails_the_best_close() -> None:
    stop = TrailingStop(TrailingStopParams(multiple=2.0))
    assert stop.adjust(1.0, 0.0, 100.0, [2.0]) == 1.0  # stop 96
    assert stop.adjust(1.0, 1.0, 105.0, [2.0]) == 1.0  # best 105: stop 101
    assert stop.adjust(1.0, 1.0, 101.0, [2.0]) == 0.0


def test_a_short_is_stopped_at_the_best_close_plus_its_multiple() -> None:
    stop = TrailingStop(TrailingStopParams(multiple=1.0))
    assert stop.adjust(-1.0, 0.0, 100.0, [3.0]) == -1.0  # enters at 100: stop 103
    assert stop.adjust(-1.0, -1.0, 95.0, [3.0]) == -1.0  # best 95: stop 98
    assert stop.adjust(-1.0, -1.0, 98.0, [3.0]) == 0.0
    assert stop.adjust(1.0, 0.0, 98.0, [3.0]) == 1.0  # the other side: let go at once


def test_a_strategy_leaving_or_reversing_on_its_own_is_not_stopped() -> None:
    """Past the stop, but the strategy itself wants flat, then short: no stop, no latch."""
    stop = TrailingStop(TrailingStopParams(multiple=1.0))
    stop.adjust(1.0, 0.0, 100.0, [1.0])
    assert stop.adjust(0.0, 1.0, 90.0, [1.0]) == 0.0  # its own exit
    assert stop.adjust(1.0, 0.0, 91.0, [1.0]) == 1.0  # free to go long again
    assert stop.adjust(-1.0, 1.0, 80.0, [1.0]) == -1.0  # a reversal is kept, not made flat
    assert stop.adjust(-1.0, -1.0, 80.5, [1.0]) == -1.0  # short from 80: stop 81


def test_a_stop_waits_for_its_input() -> None:
    stop = TrailingStop(TrailingStopParams())
    assert stop.adjust(1.0, 1.0, 50.0, [None]) == 1.0
    with pytest.raises(InvalidValueError, match="reads one input, given 2"):
        stop.adjust(1.0, 1.0, 50.0, [1.0, 2.0])


# -- limits -----------------------------------------------------------------------------


def proposal(hour: float, price: float, previous: float | None = None) -> Proposal:
    return Proposal(
        ts=Timestamp(int(hour * HOUR)),
        strategy="s",
        instrument=BTC,
        price=price,
        previous=previous,
        held=0,
        target=1,
    )


def test_limits_doubt_a_price_jump_from_the_previous_close() -> None:
    gate = Limits(LimitsParams(max_jump=0.25))
    assert gate.suspect(proposal(0, 100.0)) is None  # no previous close
    reason = gate.suspect(proposal(1, 130.0, previous=100.0))
    assert reason is not None
    assert "moved 30.0% from the last close trusted (100.0 to 130.0)" in reason
    assert gate.suspect(proposal(60 * 24, 130.0, previous=128.0)) is None  # a drift: no jump
    assert gate.check(proposal(1, 130.0, previous=100.0)) is None  # not a matter of rate


def test_limits_count_only_signals_sent_in_any_24_hours() -> None:
    gate = Limits(LimitsParams(max_per_day=2))
    assert gate.check(proposal(0, 100.0)) is None
    assert gate.check(proposal(0.5, 100.0)) is None  # checked, but never sent: not counted
    gate.sent(proposal(0, 100.0))
    gate.sent(proposal(1, 100.0))
    assert gate.check(proposal(2, 100.0)) == "2 signals in the last 24 hours already"
    assert gate.check(proposal(24, 100.0)) is None  # the one at hour 0 has aged out


def test_a_refused_reversal_leaves_the_best_close_of_the_side_held() -> None:
    """Long from 100, best 120; a reversal proposed at 95 is refused, so long is still held:
    the stop still trails from 120, and fires at 97."""
    stop = TrailingStop(TrailingStopParams(multiple=3.0))
    stop.adjust(1.0, 0.0, 100.0, [1.0])
    assert stop.adjust(1.0, 1.0, 120.0, [1.0]) == 1.0  # best 120: stop 117
    assert stop.adjust(-1.0, 1.0, 118.0, [1.0]) == -1.0  # proposed, then refused: still long
    assert stop.adjust(1.0, 1.0, 116.0, [1.0]) == 0.0  # below 117, from the best of 120


def test_a_stop_stands_while_the_sizer_cannot_size() -> None:
    stop = TrailingStop(TrailingStopParams(multiple=1.0))
    stop.adjust(1.0, 0.0, 100.0, [1.0])
    assert stop.adjust(1.0, 1.0, 98.0, [1.0]) == 0.0  # stopped
    assert stop.adjust(None, 1.0, 98.0, [1.0]) is None  # the sizer cannot size this close
    assert stop.adjust(1.0, 0.0, 99.0, [1.0]) == 0.0  # still stopped out of the long


def test_vol_target_must_be_told_its_bars_in_a_year() -> None:
    """The old default, 8760, was right only for hourly bars: daily ones were sized
    sqrt(24), about 4.9 times, too small."""
    with pytest.raises(ValidationError, match="bars_per_year"):
        VolTargetParams()


def test_a_capped_position_does_not_trade_at_every_close() -> None:
    """A quiet market at a steady forecast of 10: the position is capped at 10 units' worth
    and stays put as the price wanders, instead of following the cap at every close."""
    s = sizer(buffer=0.1, max_leverage=1.0, target=1.0)  # the average is 100 units: capped
    for price in (100, 101):
        s.target(None, price, 0.0)
    held = s.target(10.0, 100.0, 0.0)
    assert held is not None
    assert held == pytest.approx(9.0)  # to the buffer's edge below the cap of 10
    # The cap moves with the price (9.9 to 10 units), within the buffer of 1: no trade.
    assert {s.target(10.0, p, held) for p in (101.0, 100.5, 100.0, 101.0)} == {held}
    assert s.target(10.0, 100.0, 20.0) == pytest.approx(10.0)  # far over the cap: back to it


def test_a_floor_that_could_never_apply_is_refused() -> None:
    with pytest.raises(ValidationError, match="floor_min"):
        VolTargetParams(bars_per_year=365.0, floor_window=50, floor_min=100)


@pytest.mark.parametrize(
    ("params", "reason"),
    [
        ({"buffer": 1.0}, "buffer"),  # a buffer as wide as the position never trades
    ],
)
def test_vol_target_settings_that_could_never_trade_are_refused(
    params: dict[str, float], reason: str
) -> None:
    with pytest.raises(ValidationError, match=reason):
        VolTargetParams(bars_per_year=365.0, **params)  # type: ignore[arg-type]


def test_limits_that_would_doubt_every_close_are_refused() -> None:
    with pytest.raises(ValidationError, match="max_jump"):
        LimitsParams(max_jump=0.0)
