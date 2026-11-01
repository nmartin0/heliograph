# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""A backtest's or live session's text report, computed from its journal alone (decision D31).

For each strategy the run recorded: the signals sent and refused, the fills,
what they cost, and the result. Equity is the strategy's cash (starting at 0)
plus its position marked at the close; it is marked once per close of the
strategy's price stream, after that instant has settled, so a fill at a
bar's open is counted at that bar's close. The costs are the commissions,
plus what fills paid beyond the open (half the spread and the slippage).
A strategy without a fill model was not simulated: only its signals are
reported. A run that failed or did not finish says so under its heading.

A live session's report also says what the person did (decision D54): how many
signals they took, skipped or let expire, the position they hold, and their
result beside the signal's own paper result. They are worked out as
``core.answers.Followed`` does it: a signal counts for them unless skipped or
expired (one still waiting counts, as the strategy took it), moving the position
to its target, or by the quantity they said they traded. A trade is at the price
they gave, else the paper fill's, else the close the signal was made on; their
result is marked at the last close and takes no costs of theirs into account.

The report reads only the journal: the configuration from its header, and
the entries. So it is the same for a run and its replay, and can be
compared as text (the scenario tests, D4c).
"""

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from heliograph.app.config import parse_config
from heliograph.app.run import JOURNALLED
from heliograph.core.answers import SignalAnswer, SignalExpired
from heliograph.core.broker import Fill
from heliograph.core.errors import InvalidValueError
from heliograph.core.messages import Bar, Settled
from heliograph.core.pipeline import Signal, SignalRefused
from heliograph.core.time import Timestamp, to_datetime
from heliograph.plugins.sqlite_journal import read_entries, read_header


@dataclass(slots=True)
class _Told:
    """A signal as the person saw it, and what they did about it."""

    target: float
    price: float  # the close it was made on
    paper: float | None = None  # the paper fill's price
    verdict: str | None = None  # took, skipped, expired; None while waiting
    quantity: float | None = None
    paid: float | None = None


@dataclass(frozen=True, slots=True)
class Yours:
    """What a person did about a strategy's signals, and how it came out for them."""

    took: int
    skipped: int
    expired: int
    waiting: int
    position: float
    result: float
    """Marked at the last close, at the prices they paid, with no costs of theirs."""


@dataclass(slots=True)
class _Book:
    """One strategy's running account, as the journal is read."""

    name: str
    instrument: str
    size: int
    filled: bool
    signals: int = 0
    refused: int = 0
    doubted: int = 0
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
    marked: list[int] = field(default_factory=list)  # when each equity was marked
    opened: tuple[int, float] | None = None  # the latest bar's start and open
    told: dict[str, "_Told"] = field(default_factory=dict)  # each signal, in the order made

    def mark(self, ts: int) -> None:
        if self.close is None:
            return
        self.last = self.close
        self.closes += 1
        self.exposed += self.position != 0
        self.equity.append(self.cash + self.position * self.close)
        self.marked.append(ts)
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
    doubted: int
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
    yours: Yours | None = None
    """Set for a live session; None for a backtest, whose person answers nothing."""


def figures(journal: Path) -> tuple[dict[str, str], list[Figures]]:
    """The run's header, and each strategy's figures, from the backtest or live ``journal``.

    Raises:
        InvalidValueError: if ``journal`` is not a backtest's or live session's journal.
    """
    header, books = _read(journal)
    live = header["kind"] == "live"
    return header, [_figures(book, live) for book in books]


def equity(journal: Path) -> dict[str, list[tuple[Timestamp, float]]]:
    """Each strategy's equity, as the report counts it, and when it was marked: at
    each close of its price stream, once that instant had settled.

    Raises:
        InvalidValueError: if ``journal`` is not a backtest's journal.
    """
    _, books = _read(journal)
    return {
        book.name: [
            (Timestamp(ts), value) for ts, value in zip(book.marked, book.equity, strict=True)
        ]
        for book in books
    }


def _read(journal: Path) -> tuple[dict[str, str], list[_Book]]:
    header = read_header(journal)
    if header.get("kind") not in ("backtest", "live") or "config_text" not in header:
        raise InvalidValueError(f"{journal} is not the journal of a backtest run or live session")
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
    for entry in read_entries(
        journal,
        JOURNALLED,
        only=(Bar, Signal, SignalRefused, Fill, Settled, SignalAnswer, SignalExpired),
    ):
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
            books[message.strategy].told[message.id] = _Told(message.target, message.price)
        elif isinstance(message, SignalAnswer):
            for book in books.values():
                one_told = book.told.get(message.signal)
                if one_told is not None:
                    one_told.verdict, one_told.quantity, one_told.paid = (
                        message.verdict,
                        message.quantity,
                        message.price,
                    )
        elif isinstance(message, SignalExpired):
            for book in books.values():
                one_told = book.told.get(message.signal)
                if one_told is not None and one_told.verdict is None:
                    one_told.verdict = "expired"
        elif isinstance(message, SignalRefused):
            if message.doubted:
                books[message.strategy].doubted += 1
            else:
                books[message.strategy].refused += 1
        elif isinstance(message, Fill):
            _fill(books[message.strategy], message)
            paper = books[message.strategy].told.get(message.signal)
            if paper is not None:
                paper.paper = message.price
        elif isinstance(message, Settled):
            for book in books.values():
                book.mark(message.ts)
    return header, list(books.values())


def report(journal: Path) -> str:
    """The text report of the backtest ``journal`` recorded.

    Raises:
        InvalidValueError: if ``journal`` is not a backtest's journal.
    """
    header, all_figures = figures(journal)
    lines = [
        f"Backtest {_when(header['start'])} to {_when(header['end'])}"
        if header["kind"] == "backtest"
        else f"Live session from {_when(header['start'])}",
        f"configuration {header['config_digest'][:16]}",
    ]
    late = {name: count for name, count in json.loads(header.get("late", "{}")).items() if count}
    if late:
        listed = ", ".join(f"{name} {count}" for name, count in late.items())
        lines.append(f"LATE DATA LEFT OUT (after its bar closed): {listed}")
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


def _yours(book: _Book) -> Yours:
    """What the person did, folded in the order of the signals (see the module)."""
    position = cash = 0.0
    counts = {"took": 0, "skipped": 0, "expired": 0, None: 0}
    for told in book.told.values():
        counts[told.verdict] += 1
        if told.verdict in ("skipped", "expired"):
            continue
        after = told.target if told.quantity is None else position + told.quantity
        price = told.paid or told.paper or told.price
        cash -= (after - position) * price
        position = after
    result = cash + position * (book.last or 0.0)
    return Yours(
        counts["took"], counts["skipped"], counts["expired"], counts[None], position, result
    )


def _figures(book: _Book, live: bool = False) -> Figures:
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
        doubted=book.doubted,
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
        yours=_yours(book) if live and book.filled else None,
    )


def _lines(f: Figures) -> list[str]:
    exposure = f.exposed / f.closes if f.closes else 0.0
    rows = [
        ("signals sent", f"{f.signals}"),
        ("signals refused", f"{f.refused}"),
        ("closes doubted", f"{f.doubted}"),
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
    if f.yours is not None:
        y = f.yours
        rows += [
            (
                "your answers",
                f"took {y.took}, skipped {y.skipped}, expired {y.expired}, waiting {y.waiting}",
            ),
            ("your position", _number(y.position)),
            ("your profit and loss", _number(y.result)),
            ("against the paper result", _number(y.result - f.result)),
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
