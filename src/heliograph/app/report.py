# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""A backtest's text report, computed from its journal alone (decision D31).

For each strategy the run recorded: the signals sent and refused, the fills,
what they cost, and the result. Equity is the strategy's cash (starting at 0)
plus its position marked at the close; it is marked once per close of the
strategy's price stream, after that instant has settled, so a fill at a
bar's open is counted at that bar's close. The costs are the commissions,
plus what fills paid beyond the open (half the spread and the slippage).
A strategy without a fill model was not simulated: only its signals are
reported. A run that failed or did not finish says so under its heading.

The report reads only the journal: the configuration from its header, and
the entries. So it is the same for a run and its replay, and can be
compared as text (the scenario tests, D4c).
"""

import math
from dataclasses import dataclass, field
from pathlib import Path

from heliograph.app.config import parse_config
from heliograph.app.run import JOURNALLED
from heliograph.core.broker import Fill
from heliograph.core.errors import InvalidValueError
from heliograph.core.messages import Bar, Settled
from heliograph.core.pipeline import Signal, SignalRefused
from heliograph.core.time import Timestamp, to_datetime
from heliograph.plugins.sqlite_journal import read_entries, read_header


@dataclass(slots=True)
class _Book:
    """One strategy's running account, as the journal is read."""

    name: str
    instrument: str
    size: int
    filled: bool
    signals: int = 0
    refused: int = 0
    fills: int = 0
    bought: float = 0.0
    sold: float = 0.0
    traded: float = 0.0  # value, |quantity| x price
    commission: float = 0.0
    slipped: float = 0.0  # paid beyond the open
    cash: float = 0.0
    position: float = 0.0
    close: float | None = None  # the close at the instant being read
    last: float | None = None  # the last close marked
    closes: int = 0
    exposed: int = 0
    equity: list[float] = field(default_factory=list)
    opened: tuple[int, float] | None = None  # the latest bar's start and open

    def mark(self) -> None:
        if self.close is None:
            return
        self.last = self.close
        self.closes += 1
        self.exposed += self.position != 0
        self.equity.append(self.cash + self.position * self.close)
        self.close = None


@dataclass(frozen=True, slots=True)
class Figures:
    """What one strategy did over a run, in its instrument's units and quote currency."""

    strategy: str
    instrument: str
    filled: bool
    """Whether a broker filled its signals; if not, nothing below ``refused`` was simulated."""
    signals: int
    refused: int
    fills: int
    bought: float
    sold: float
    traded: float
    commission: float
    slipped: float
    position: float
    last: float | None
    result: float
    drawdown: float
    closes: int
    exposed: int


def figures(journal: Path) -> tuple[dict[str, str], list[Figures]]:
    """The run's header, and each strategy's figures, from the backtest ``journal``.

    Raises:
        InvalidValueError: if ``journal`` is not a backtest's journal.
    """
    header = read_header(journal)
    if header.get("kind") != "backtest" or "config_text" not in header:
        raise InvalidValueError(f"{journal} is not the journal of a backtest run")
    config = parse_config(header["config_text"]).config
    books: dict[str, _Book] = {}
    for strategy in config.strategies:
        found = config.stream_field(strategy.price)
        assert found is not None  # a configuration that ran had it
        mount, stream, _ = found
        instrument = config.mounts[mount].instrument.instrument().key
        books[strategy.name] = _Book(
            strategy.name, instrument, stream.bar_spec().size, strategy.fills is not None
        )
    for entry in read_entries(journal, JOURNALLED):
        message = entry.message
        if isinstance(message, Bar):
            for book in books.values():
                if (
                    message.instrument.key == book.instrument
                    and message.end - message.start == book.size
                ):
                    book.close = message.close
                    book.opened = (message.start, message.open)
        elif isinstance(message, Signal):
            books[message.strategy].signals += 1
        elif isinstance(message, SignalRefused):
            books[message.strategy].refused += 1
        elif isinstance(message, Fill):
            _fill(books[message.strategy], message)
        elif isinstance(message, Settled):
            for book in books.values():
                book.mark()
    return header, [_figures(book) for book in books.values()]


def report(journal: Path) -> str:
    """The text report of the backtest ``journal`` recorded.

    Raises:
        InvalidValueError: if ``journal`` is not a backtest's journal.
    """
    header, all_figures = figures(journal)
    lines = [
        f"Backtest {_when(header['start'])} to {_when(header['end'])}",
        f"configuration {header['config_digest'][:16]}",
    ]
    if header.get("status") != "complete":
        lines.append(
            f"NOT COMPLETE ({header.get('status')}): the figures cover what was journalled"
        )
    for one in all_figures:
        lines += ["", *_lines(one)]
    return "\n".join(lines) + "\n"


def _fill(book: _Book, fill: Fill) -> None:
    book.fills += 1
    if fill.quantity > 0:
        book.bought += fill.quantity
    else:
        book.sold -= fill.quantity
    book.traded += abs(fill.quantity) * fill.price
    book.commission += fill.commission
    # A fill is journalled just after the bar whose open it was filled at.
    if book.opened is None or book.opened[0] != fill.event_time:
        raise InvalidValueError(
            f"the journal has a fill for {book.name} at {fill.event_time} "
            "with no bar of its stream opening then"
        )
    book.slipped += (fill.price - book.opened[1]) * fill.quantity
    book.cash -= fill.quantity * fill.price + fill.commission
    book.position = fill.position


def _figures(book: _Book) -> Figures:
    peak = drawdown = 0.0
    for value in book.equity:
        peak = max(peak, value)
        drawdown = max(drawdown, peak - value)
    return Figures(
        strategy=book.name,
        instrument=book.instrument,
        filled=book.filled,
        signals=book.signals,
        refused=book.refused,
        fills=book.fills,
        bought=book.bought,
        sold=book.sold,
        traded=book.traded,
        commission=book.commission,
        slipped=book.slipped,
        position=book.position,
        last=book.last,
        result=book.equity[-1] if book.equity else 0.0,
        drawdown=drawdown,
        closes=book.closes,
        exposed=book.exposed,
    )


def _lines(f: Figures) -> list[str]:
    exposure = f.exposed / f.closes if f.closes else 0.0
    rows = [
        ("signals sent", f"{f.signals}"),
        ("signals refused", f"{f.refused}"),
    ]
    if not f.filled:
        rows.append(("fills", "not simulated (no fill model)"))
        return _table(f, rows)
    rows += [
        ("fills", f"{f.fills}"),
        ("units bought", _number(f.bought)),
        ("units sold", _number(f.sold)),
        ("value traded", _number(f.traded)),
        ("commission", _number(f.commission)),
        ("spread and slippage", _number(f.slipped)),
        ("position at the end", _number(f.position)),
        ("last close", "-" if f.last is None else _number(f.last)),
        ("profit and loss", _number(f.result)),
        ("largest drawdown", _number(f.drawdown)),
        ("closes in the market", f"{f.exposed} of {f.closes} ({exposure:.1%})"),
    ]
    return _table(f, rows)


def _table(f: Figures, rows: list[tuple[str, str]]) -> list[str]:
    width = max(len(label) for label, _ in rows)
    return [f"strategy {f.strategy} ({f.instrument})"] + [
        f"  {label.ljust(width)}  {value}" for label, value in rows
    ]


def _number(value: float) -> str:
    """Two decimals, thousands separated; never -0.00."""
    rounded = round(value, 2) + 0.0
    return f"{rounded:,.2f}" if math.isfinite(rounded) else str(value)


def _when(nanos: str) -> str:
    return to_datetime(Timestamp(int(nanos))).strftime("%Y-%m-%dT%H:%M:%SZ")
