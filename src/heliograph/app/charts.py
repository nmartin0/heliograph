# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""What a backtest's journal holds, as series a chart draws (decisions D19, D38).

Read from the journal alone, as the report is (D31), so a run and its
replay chart the same. Every item carries its times as ISO 8601 text and,
because a chart's axis needs numbers, ``t``: milliseconds since 1970 (D19).
A bar's ``t`` is its start, where a chart puts it; anything else's ``t`` is
when it became known (its ``ts``), so a value computed from a bar sits at
that bar's end, never before it could be known.

``start`` and ``end`` (nanoseconds) keep only what became known in
``[start, end)``.
"""

import math
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from heliograph.app.config import GRAPH_KINDS, RunConfig, parse_config
from heliograph.app.report import equity
from heliograph.app.run import JOURNALLED
from heliograph.core.broker import Fill
from heliograph.core.errors import InvalidValueError
from heliograph.core.graph import IndicatorValues
from heliograph.core.messages import Bar
from heliograph.core.pipeline import Signal, SignalRefused
from heliograph.core.time import NANOS_PER_MILLISECOND, format_span, to_iso
from heliograph.plugins.sqlite_journal import read_entries, read_header


class NotInRunError(InvalidValueError):
    """The run has no stream, indicator or strategy by that name."""


def _config(journal: Path) -> RunConfig:
    header = read_header(journal)
    if header.get("kind") != "backtest" or "config_text" not in header:
        raise InvalidValueError(f"{journal} is not the journal of a backtest run")
    return parse_config(header["config_text"]).config


def _t(ts: int) -> int:
    """Milliseconds for a chart's axis, rounded down."""
    return ts // NANOS_PER_MILLISECOND


def _number(value: float | None) -> float | None:
    """JSON has no NaN or infinity: such a number is null."""
    return value if value is None or math.isfinite(value) else None


def contents(journal: Path) -> dict[str, Any]:
    """What can be charted: the run's streams of bars, its graph's nodes (indicators,
    forecasters, gates) and its strategies, each with the stream it trades on.

    Raises:
        InvalidValueError: if ``journal`` is not a backtest's journal.
    """
    config = _config(journal)
    streams = [
        {
            "stream": f"{stream.mount}@{format_span(stream.bar_spec().size)}",
            "mount": stream.mount,
            "instrument": config.mounts[stream.mount].instrument.instrument().key,
            "size": format_span(stream.bar_spec().size),
        }
        for stream in config.streams
    ]
    nodes = [
        {"name": str(c.name), "kind": c.kind, "plugin": c.plugin, "inputs": list(c.inputs)}
        for c in config.components
        if c.kind in GRAPH_KINDS
    ]
    strategies = [{"name": s.name, "price": s.price} for s in config.strategies]
    return {"streams": streams, "nodes": nodes, "strategies": strategies}


def bars(
    journal: Path, stream: str, start: int | None = None, end: int | None = None
) -> list[dict[str, Any]]:
    """The bars of ``stream`` (``btc@1h``), in order.

    Raises:
        NotInRunError: if the run has no such stream.
    """
    config = _config(journal)
    found = config.stream_field(f"{stream}.close")
    if found is None:
        raise NotInRunError(f"the run has no stream {stream!r}")
    mount, spec, _ = found
    instrument = config.mounts[mount].instrument.instrument()
    size = spec.bar_spec().size
    out = []
    for entry in read_entries(journal, JOURNALLED, only=(Bar,), start=start, end=end):
        bar = entry.message
        assert isinstance(bar, Bar)
        if bar.instrument != instrument or bar.end - bar.start != size:
            continue
        out.append(
            {
                "t": _t(bar.start),
                "start": to_iso(bar.start),
                "end": to_iso(bar.end),
                "end_t": _t(bar.end),
                "known": to_iso(entry.message.ts),
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
            }
        )
    return out


def node(
    journal: Path, name: str, start: int | None = None, end: int | None = None
) -> list[dict[str, Any]]:
    """The values of the graph node ``name``, each when it changed.

    Raises:
        NotInRunError: if the run has no such node.
    """
    config = _config(journal)
    if not any(str(c.name) == name and c.kind in GRAPH_KINDS for c in config.components):
        raise NotInRunError(f"the run has no indicator, forecaster or gate {name!r}")
    out = []
    for entry in read_entries(journal, JOURNALLED, only=(IndicatorValues,), start=start, end=end):
        values = entry.message
        assert isinstance(values, IndicatorValues)
        if name in values.changed:
            out.append(
                {
                    "t": _t(values.ts),
                    "time": to_iso(values.ts),
                    "value": _number(values.values[name]),
                }
            )
    return out


def signals(
    journal: Path, strategy: str | None = None, start: int | None = None, end: int | None = None
) -> list[dict[str, Any]]:
    """The signals sent and refused, in order; of ``strategy`` only, if named.

    Raises:
        NotInRunError: if the run has no such strategy.
    """
    _known(journal, strategy)
    out: list[dict[str, Any]] = []
    for entry in read_entries(
        journal, JOURNALLED, only=(Signal, SignalRefused), start=start, end=end
    ):
        message = entry.message
        assert isinstance(message, Signal | SignalRefused)
        if strategy is not None and message.strategy != strategy:
            continue
        common = {
            "t": _t(message.ts),
            "time": to_iso(message.ts),
            "id": message.id,
            "strategy": message.strategy,
            "instrument": message.instrument.key,
            "target": message.target,
            "held": message.held,
            "price": message.price,
            "exit": message.exit,
        }
        if isinstance(message, Signal):
            out.append(
                {
                    "kind": "signal",
                    **common,
                    "side": message.side,
                    "forecast": message.forecast,
                    "forecasts": dict(message.forecasts),
                    "vetoed_by": list(message.vetoed_by),
                    "instead_of": message.instead_of,
                }
            )
        else:
            out.append(
                {
                    "kind": "refused",
                    **common,
                    "refused_by": message.refused_by,
                    "reason": message.reason,
                    "doubted": message.doubted,
                }
            )
    return out


def fills(
    journal: Path, strategy: str | None = None, start: int | None = None, end: int | None = None
) -> list[dict[str, Any]]:
    """The simulated fills, in order; of ``strategy`` only, if named. A fill's ``t`` is
    when it happened (the bar's open), where a chart marks it.

    Raises:
        NotInRunError: if the run has no such strategy.
    """
    _known(journal, strategy)
    out = []
    for entry in read_entries(journal, JOURNALLED, only=(Fill,), start=start, end=end):
        fill = entry.message
        assert isinstance(fill, Fill)
        if strategy is not None and fill.strategy != strategy:
            continue
        out.append(
            {
                "t": _t(fill.event_time),
                "time": to_iso(fill.event_time),
                "known": to_iso(fill.ts),
                "strategy": fill.strategy,
                "instrument": fill.instrument.key,
                "signal": fill.signal,
                "quantity": fill.quantity,
                "price": fill.price,
                "commission": fill.commission,
                "position": fill.position,
            }
        )
    return out


def equity_of(
    journal: Path, strategy: str, start: int | None = None, end: int | None = None
) -> list[dict[str, Any]]:
    """``strategy``'s equity as the report counts it, at each close it was marked.

    Raises:
        NotInRunError: if the run has no such strategy.
    """
    _known(journal, strategy)
    curve = equity(journal)[strategy]
    return [
        {"t": _t(ts), "time": to_iso(ts), "value": _number(value)}
        for ts, value in curve
        if (start is None or ts >= start) and (end is None or ts < end)
    ]


def _known(journal: Path, strategy: str | None) -> None:
    if strategy is None:
        return
    names: Iterable[str] = (s.name for s in _config(journal).strategies)
    if strategy not in set(names):
        raise NotInRunError(f"the run has no strategy {strategy!r}")
