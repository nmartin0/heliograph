# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""A run's series from the engine's API, as the chart element draws them (D39).

Pure functions of what the API returned, so they are tested without a browser.
The chart's axis counts seconds; the API's ``t`` counts milliseconds, rounded
down, and a chart refuses two points at one time, so the last of them stays.
Colours are roles (``up``, ``down``, ``flat``, ``muted``): the element picks the
theme's colour for each.
"""

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

type Rows = Sequence[Mapping[str, Any]]
type Spec = dict[str, Any]

PRICE = "price"
"""The id of the price series, which signal and fill markers sit on."""


def _seconds(t: int) -> int:
    return t // 1000


def _one_a_second(points: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """``points`` in time order, the last of any that share a second."""
    latest: dict[int, dict[str, Any]] = {}
    for point in points:
        latest[point["time"]] = point
    return [latest[time] for time in sorted(latest)]


def candles(bars: Rows) -> list[dict[str, Any]]:
    """Candlesticks from ``/bars`` rows."""
    return _one_a_second(
        {
            "time": _seconds(bar["t"]),
            "open": bar["open"],
            "high": bar["high"],
            "low": bar["low"],
            "close": bar["close"],
        }
        for bar in bars
    )


def volume(bars: Rows) -> list[dict[str, Any]]:
    """Volume columns, coloured by whether the bar closed up, from ``/bars`` rows."""
    return _one_a_second(
        {
            "time": _seconds(bar["t"]),
            "value": bar["volume"],
            "role": "up" if bar["close"] >= bar["open"] else "down",
        }
        for bar in bars
    )


def values(rows: Rows) -> list[dict[str, Any]]:
    """A line's points from ``/nodes`` or ``/equity`` rows; an unknown value (null)
    leaves a gap."""
    return _one_a_second(
        {"time": _seconds(row["t"])}
        if row["value"] is None
        else {"time": _seconds(row["t"]), "value": row["value"]}
        for row in rows
    )


def _closing(bars: Rows) -> dict[int, int]:
    """For each bar's end, in milliseconds, the start of the bar it ended."""
    return {bar["end_t"]: bar["t"] for bar in bars}


def _marker(time: int, role: str, shape: str, position: str) -> dict[str, Any]:
    return {"time": time, "role": role, "shape": shape, "position": position}


def signal_markers(signals: Rows, bars: Rows) -> list[dict[str, Any]]:
    """Markers for ``/signals`` rows: on the bar whose close the decision was made on
    (a signal is known as its bar ends), by what it said. A signal sent has an arrow
    to its side; one a risk gate refused, a muted circle."""
    closed = _closing(bars)
    out = []
    for signal in signals:
        start = closed.get(signal["t"])
        time = _seconds(signal["t"] if start is None else start)
        if signal["kind"] == "refused":
            out.append(_marker(time, "muted", "circle", "aboveBar"))
            continue
        side = signal["side"]
        if side == "long":
            out.append(_marker(time, "up", "arrowUp", "belowBar"))
        elif side == "short":
            out.append(_marker(time, "down", "arrowDown", "aboveBar"))
        else:
            out.append(_marker(time, "flat", "square", "aboveBar"))
    return sorted(out, key=lambda marker: marker["time"])


def fill_markers(fills: Rows) -> list[dict[str, Any]]:
    """Markers for ``/fills`` rows: a circle where each filled, by whether it bought or sold."""
    out = []
    for fill in fills:
        role = "up" if fill["quantity"] > 0 else "down"
        out.append(_marker(_seconds(fill["t"]), role, "circle", "inBar"))
    return sorted(out, key=lambda marker: marker["time"])


def price_pane(stream: str, bars: Rows, markers: Sequence[dict[str, Any]] = ()) -> Spec:
    """The pane of a stream's candles, with ``markers`` on them, and its volume below
    on a scale of its own."""
    return {
        "weight": 3,
        "series": [
            {
                "id": PRICE,
                "type": "candles",
                "title": stream,
                "data": candles(bars),
                "markers": list(markers),
            },
            {"id": "volume", "type": "volume", "title": "volume", "data": volume(bars)},
        ],
    }


def line_pane(title: str, rows: Rows, *, steps: bool = False) -> Spec:
    """A pane of one line, labelled ``title``; ``steps`` holds each value until the next."""
    return {
        "weight": 1,
        "series": [
            {
                "id": title,
                "type": "steps" if steps else "line",
                "title": title,
                "data": values(rows),
            }
        ],
    }


def overlay(pane: Spec, title: str, rows: Rows) -> None:
    """Draw a line over ``pane``'s price, on its scale."""
    pane["series"].append({"id": title, "type": "line", "title": title, "data": values(rows)})
