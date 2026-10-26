# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The window's series, from the shapes the engine's API gives (D38, D39)."""

from typing import Any

from heliograph.gui import series

BARS = [
    {
        "t": 3_600_000,
        "end_t": 7_200_000,
        "open": 10.0,
        "high": 12.0,
        "low": 9.0,
        "close": 11.0,
        "volume": 5.0,
    },
    {
        "t": 7_200_000,
        "end_t": 10_800_000,
        "open": 11.0,
        "high": 11.5,
        "low": 8.0,
        "close": 9.0,
        "volume": 7.0,
    },
]


def signal(kind: str, t: int, **more: Any) -> dict[str, Any]:
    return {"kind": kind, "t": t, "target": 1.5, **more}


def test_candles_sit_at_their_start_in_seconds() -> None:
    assert series.candles(BARS) == [
        {"time": 3600, "open": 10.0, "high": 12.0, "low": 9.0, "close": 11.0},
        {"time": 7200, "open": 11.0, "high": 11.5, "low": 8.0, "close": 9.0},
    ]


def test_volume_is_coloured_by_whether_the_bar_closed_up() -> None:
    assert series.volume(BARS) == [
        {"time": 3600, "value": 5.0, "role": "up"},
        {"time": 7200, "value": 7.0, "role": "down"},
    ]


def test_a_line_leaves_a_gap_for_an_unknown_value() -> None:
    rows: list[dict[str, Any]] = [
        {"t": 1000, "value": 1.0},
        {"t": 2000, "value": None},
        {"t": 3000, "value": 3.0},
    ]
    assert series.values(rows) == [
        {"time": 1, "value": 1.0},
        {"time": 2},
        {"time": 3, "value": 3.0},
    ]


def test_a_chart_gets_one_point_a_second_the_last_of_them() -> None:
    rows: list[dict[str, Any]] = [
        {"t": 5000, "value": 1.0},
        {"t": 5400, "value": 2.0},
        {"t": 4000, "value": 0.5},
    ]
    assert series.values(rows) == [{"time": 4, "value": 0.5}, {"time": 5, "value": 2.0}]


def test_a_signal_sits_on_the_bar_whose_close_it_was_made_on() -> None:
    sent = [
        signal("signal", 7_200_000, side="long"),  # known as the first bar ended
        signal("signal", 10_800_000, side="short"),
        signal("signal", 10_800_000, side="flat"),
        signal("refused", 7_200_000),
    ]
    got = series.signal_markers(sent, BARS)
    assert [(m["time"], m["shape"], m["role"], m["position"]) for m in got] == [
        (3600, "arrowUp", "up", "belowBar"),
        (3600, "circle", "muted", "aboveBar"),
        (7200, "arrowDown", "down", "aboveBar"),
        (7200, "square", "flat", "aboveBar"),
    ]


def test_a_signal_off_every_bar_keeps_its_own_time() -> None:
    assert series.signal_markers([signal("signal", 99_000, side="long")], BARS)[0]["time"] == 99


def test_a_fill_is_a_circle_that_says_buy_or_sell() -> None:
    fills = [{"t": 9_000, "quantity": -2.0}, {"t": 3_000, "quantity": 1.0}]
    assert series.fill_markers(fills) == [
        {"time": 3, "role": "up", "shape": "circle", "position": "inBar"},
        {"time": 9, "role": "down", "shape": "circle", "position": "inBar"},
    ]


def test_a_price_pane_holds_candles_markers_and_volume_and_takes_overlays() -> None:
    pane = series.price_pane("btc@1h", BARS, [{"time": 1}])
    assert [(s["id"], s["type"]) for s in pane["series"]] == [
        ("price", "candles"),
        ("volume", "volume"),
    ]
    assert pane["series"][0]["title"] == "btc@1h"
    assert pane["series"][0]["markers"] == [{"time": 1}]
    series.overlay(pane, "fast", [{"t": 1000, "value": 2.0}])
    assert pane["series"][-1] == {
        "id": "fast", "type": "line", "title": "fast", "data": [{"time": 1, "value": 2.0}],
    }  # fmt: skip


def test_a_line_pane_is_labelled_and_can_hold_each_value_until_the_next() -> None:
    rows = [{"t": 1000, "value": 2.0}]
    assert series.line_pane("fast", rows)["series"][0]["type"] == "line"
    assert series.line_pane("equity", rows, steps=True)["series"][0]["type"] == "steps"
    assert series.line_pane("fast", rows)["series"][0]["title"] == "fast"
