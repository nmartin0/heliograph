# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

import math
from typing import Any

import pytest

from heliograph.core.errors import InvalidValueError
from heliograph.core.messages import Bar, Point, Settled
from heliograph.core.sources import (
    Capabilities,
    HistoryRequest,
    Listing,
    RateLimit,
    Subscription,
    known_in_history,
    known_now,
)
from heliograph.core.time import NANOS_PER_DAY, NANOS_PER_HOUR, NANOS_PER_MINUTE, Timestamp
from tests.support.world import BTC

HOUR, MIN, DAY = NANOS_PER_HOUR, NANOS_PER_MINUTE, NANOS_PER_DAY

FULL = Capabilities(
    history=frozenset({"trades", "bars"}),
    live=frozenset({"trades"}),
    bar_sizes=frozenset({MIN, HOUR}),
    lookback=30 * DAY,
)


def test_a_source_that_can_serve_a_request_has_no_problems() -> None:
    assert FULL.problems("trades", live=False, reach=DAY) == []
    assert FULL.problems("bars", live=False, bar_size=HOUR) == []
    assert FULL.problems("trades", live=True) == []


def test_every_reason_a_request_cannot_be_served_is_named() -> None:
    assert FULL.problems("bars", live=True, bar_size=HOUR) == ["offers no bars live"]
    assert FULL.problems("bars", live=False, bar_size=4 * HOUR) == [
        "offers no 4h bars (sizes: 1m, 1h)"
    ]
    assert FULL.problems("trades", live=False, reach=31 * DAY) == ["reaches back 4w2d, not 4w3d"]
    beyond = 2**63  # a start over 292 years ago is still reported, not a crash
    assert FULL.problems("trades", live=False, reach=beyond) == [
        f"reaches back 4w2d, not {beyond} ns"
    ]
    trades_only = Capabilities(history=frozenset({"trades"}))
    assert trades_only.problems("bars", live=True, bar_size=MIN) == [
        "offers no bars live",
        "offers no 1m bars (sizes: none)",
    ]


def test_live_use_of_a_source_whose_terms_forbid_it_is_refused() -> None:
    dumps = Capabilities(
        history=frozenset({"trades"}),
        live=frozenset({"trades"}),
        terms=frozenset({"no_live_trading", "personal_only"}),
    )
    assert dumps.problems("trades", live=False) == []
    assert dumps.problems("trades", live=True) == ["its terms forbid live use"]


def test_a_lookback_limit_applies_only_to_history_and_only_when_declared() -> None:
    unlimited = Capabilities(history=frozenset({"trades"}))
    assert unlimited.problems("trades", live=False, reach=10_000 * DAY) == []
    assert FULL.problems("trades", live=False, reach=30 * DAY) == []


@pytest.mark.parametrize(
    ("fields", "reason"),
    [
        ({"history": {"quotes"}}, "unknown data kinds"),
        ({"live": {"bars"}}, "must name its bar sizes"),
        ({"bar_sizes": {0}}, "positive ints"),
        ({"lookback": 0}, "lookback"),
        ({"live_delay": -1}, "live delay"),
        ({"concurrency": 0}, "concurrency"),
        ({"terms": {"free_for_all"}}, "unknown terms"),
    ],
)
def test_impossible_capabilities_are_refused(fields: dict[str, Any], reason: str) -> None:
    with pytest.raises(InvalidValueError, match=reason):
        Capabilities(**fields)


def test_capabilities_given_as_lists_are_frozen() -> None:
    capabilities = Capabilities(history=["trades"], terms=["attribution"])  # type: ignore[arg-type]
    assert capabilities.history == frozenset({"trades"})
    assert hash(capabilities) == hash(
        Capabilities(history=frozenset({"trades"}), terms=frozenset({"attribution"}))
    )


@pytest.mark.parametrize(("calls", "period"), [(0, 1), (1, 0), (1.5, 1), (True, 1)])
def test_a_rate_limit_counts_whole_positive_calls_and_nanoseconds(calls: Any, period: Any) -> None:
    with pytest.raises(InvalidValueError, match="positive int"):
        RateLimit(calls=calls, period=period)


def test_a_history_request_is_a_non_empty_interval_with_a_size_only_for_bars() -> None:
    start, end = Timestamp(0), Timestamp(HOUR)
    HistoryRequest(instrument=BTC, kind="bars", start=start, end=end, bar_size=MIN)
    with pytest.raises(InvalidValueError, match="end after it starts"):
        HistoryRequest(instrument=BTC, kind="trades", start=end, end=end)
    with pytest.raises(InvalidValueError, match="only for bars"):
        HistoryRequest(instrument=BTC, kind="trades", start=start, end=end, bar_size=MIN)
    with pytest.raises(InvalidValueError, match="only for bars"):
        HistoryRequest(instrument=BTC, kind="bars", start=start, end=end)
    with pytest.raises(InvalidValueError, match="unknown data kind"):
        HistoryRequest(instrument=BTC, kind="quotes", start=start, end=end)  # type: ignore[arg-type]


def test_a_subscription_has_a_size_only_for_bars() -> None:
    Subscription(instrument=BTC, kind="trades")
    with pytest.raises(InvalidValueError, match="only for bars"):
        Subscription(instrument=BTC, kind="bars")
    with pytest.raises(InvalidValueError, match="bar size must be a positive int"):
        Subscription(instrument=BTC, kind="bars", bar_size=-5)


def trade(at: int, received: int | None = None) -> Point:
    known = Timestamp(at if received is None else received)
    return Point(instrument=BTC, event_time=Timestamp(at), received_at=known, values={"price": 1.0})


def bar(end: int, received: int | None = None) -> Bar:
    known = None if received is None else Timestamp(received)
    return Bar(
        instrument=BTC,
        start=Timestamp(end - MIN),
        end=Timestamp(end),
        open=1,
        high=1,
        low=1,
        close=1,
        volume=0,
        received_at=known,
    )


def test_history_is_known_its_declared_delay_after_it_happened() -> None:
    """Whatever the source stamped, a backtest learns of it no sooner than live would."""
    assert known_in_history(trade(10 * MIN, received=0), 15_000).ts == 10 * MIN + 15_000
    assert known_in_history(trade(10 * MIN), 0).ts == 10 * MIN
    assert known_in_history(bar(HOUR, received=2 * HOUR), 0).received_at is None
    assert known_in_history(bar(HOUR), 15_000).ts == HOUR + 15_000


def test_live_data_is_known_when_it_arrives() -> None:
    now = Timestamp(HOUR + 7)
    assert known_now(trade(10 * MIN), now).ts == now
    assert known_now(bar(HOUR), Timestamp(HOUR)).received_at is None
    assert known_now(bar(HOUR), now).ts == now


def test_a_bar_arriving_before_it_closed_is_refused() -> None:
    with pytest.raises(InvalidValueError, match="before it closed"):
        known_now(bar(HOUR), Timestamp(HOUR - 1))


def test_a_source_may_yield_only_points_and_bars() -> None:
    with pytest.raises(InvalidValueError, match="not a Point or Bar"):
        known_in_history(Settled(Timestamp(0)), 0)
    with pytest.raises(InvalidValueError, match="not a Point or Bar"):
        known_now(Settled(Timestamp(0)), Timestamp(0))


def test_a_listing_names_the_venue_symbol_and_positive_increments() -> None:
    Listing(instrument=BTC, venue_symbol="XXBTZUSD", price_increment=0.1)
    with pytest.raises(InvalidValueError, match="venue symbol"):
        Listing(instrument=BTC, venue_symbol=" ")
    with pytest.raises(InvalidValueError, match="size_increment"):
        Listing(instrument=BTC, venue_symbol="XXBTZUSD", size_increment=0)
    with pytest.raises(InvalidValueError, match="price_increment must be positive and finite"):
        Listing(instrument=BTC, venue_symbol="XXBTZUSD", price_increment=math.inf)
