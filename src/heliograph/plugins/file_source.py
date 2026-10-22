# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""History from a CSV or Parquet file: one instrument, trades or bars, in time order.

A file holds one instrument's data; the mount says which instrument it is.
Each row is a trade (its time and value columns, ``price`` among them) or a
bar (the time it starts, and ``open``, ``high``, ``low``, ``close`` and
``volume``; it ends ``bar_size`` later). Times are ISO 8601 with an offset,
or whole numbers of seconds, milliseconds, microseconds or nanoseconds since
the epoch; a Parquet time column may also be a time-zone-aware timestamp.

Nothing is guessed. A missing column, an empty or non-numeric value, a time
without an offset, a row earlier than the one before it, or a bar starting
before the one before it ends stops the read with the file and row named: a
file is cleaned before it is used, not while. CSV files are UTF-8.
Rows are read in batches off the event loop, and the read stops at the first
row past the end of the request.

A file has no live data, no rate limit and no lookback. What the data's
terms say is the user's to declare (``terms``), since the file cannot.
"""

import csv
from collections.abc import AsyncIterator, Generator, Sequence
from contextlib import aclosing
from itertools import islice
from pathlib import Path
from typing import Any, Literal

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from pydantic import field_validator, model_validator

from heliograph.core.errors import InvalidValueError
from heliograph.core.messages import Bar, Instrument, Message, Point
from heliograph.core.sources import (
    Capabilities,
    DataKind,
    Health,
    HistoryRequest,
    SourceUnavailableError,
    Subscription,
    Term,
    Throttle,
)
from heliograph.core.time import (
    NANOS_PER_MICROSECOND,
    NANOS_PER_MILLISECOND,
    NANOS_PER_SECOND,
    Timestamp,
    from_iso,
    parse_span,
)
from heliograph.plugins.batches import off_loop
from heliograph.plugins.manifest import Manifest, Params, Plugin, param

type TimeUnit = Literal["iso", "s", "ms", "us", "ns"]

UNITS: dict[str, int] = {
    "s": NANOS_PER_SECOND,
    "ms": NANOS_PER_MILLISECOND,
    "us": NANOS_PER_MICROSECOND,
    "ns": 1,
}
BAR_COLUMNS = ("open", "high", "low", "close", "volume")
BATCH = 10_000
"""Rows read per trip off the event loop."""

type Row = tuple[int, dict[str, Any]]
"""A row's number in the file (the header is row 1) and its columns by name."""


class FileSourceParams(Params):
    path: str = param(..., description="The CSV (.csv) or Parquet (.parquet) file")
    kind: DataKind = param("trades", description="Trades, or bars")
    time_column: str = param("time", description="Column of event times (a bar's start)")
    time_unit: TimeUnit = param(
        "iso", description="Times as ISO 8601 with an offset, or epoch s, ms, us or ns"
    )
    values: tuple[str, ...] = param(
        ("price", "size"), description="Columns of a trade's values; price is required"
    )
    bar_size: str | None = param(None, description="Length of each bar, such as 1m or 1h")
    live_delay: str | None = param(
        None, description="How late live data would have arrived, such as 15m"
    )
    terms: tuple[Term, ...] = param((), description="Conditions of the data's terms of use")

    @field_validator("path")
    @classmethod
    def _known_format(cls, path: str) -> str:
        if Path(path).suffix.lower() not in (".csv", ".parquet"):
            raise ValueError(f"{path!r} is not a .csv or .parquet file")
        return path

    @field_validator("bar_size", "live_delay")
    @classmethod
    def _span(cls, text: str | None) -> str | None:
        if text is not None:
            try:
                parse_span(text)
            except InvalidValueError as exc:
                raise ValueError(str(exc)) from exc
        return text

    @model_validator(mode="after")
    def _consistent(self) -> "FileSourceParams":
        if (self.kind == "bars") != (self.bar_size is not None):
            raise ValueError("bar_size is given for bars, and only for bars")
        if self.kind == "trades" and "price" not in self.values:
            raise ValueError("a trade's values must include price")
        return self


class FileSource(Plugin[FileSourceParams]):
    """Trades or bars for one instrument, read from a CSV or Parquet file."""

    manifest = Manifest(
        kind="data_sources",
        name="file",
        version="1",
        summary="History from a CSV or Parquet file",
        params=FileSourceParams,
    )

    def __init__(self, params: FileSourceParams) -> None:
        super().__init__(params)
        self._throttle: Throttle | None = None

    @property
    def path(self) -> Path:
        return Path(self.params.path)

    @property
    def capabilities(self) -> Capabilities:
        p = self.params
        return Capabilities(
            history=frozenset({p.kind}),
            bar_sizes=frozenset() if p.bar_size is None else frozenset({parse_span(p.bar_size)}),
            live_delay=0 if p.live_delay is None else parse_span(p.live_delay),
            terms=frozenset(p.terms),
        )

    def bind(self, throttle: Throttle) -> None:
        self._throttle = throttle

    async def history(self, request: HistoryRequest) -> AsyncIterator[Message]:
        """The file's rows with event times in the request's interval.

        Raises:
            SourceUnavailableError: if the file cannot be opened.
            InvalidValueError: naming the file and row, if a row cannot be read
                or goes back in time, or if the request is not for this file's
                kind of data.
        """
        if request.kind != self.params.kind or request.bar_size != self._bar_size:
            raise InvalidValueError(f"{self.path} holds {self._describe()}, not what was asked")
        throttle = self._throttle
        if throttle is None:
            raise InvalidValueError(f"{self.path}: the source was used before it was bound")
        previous: int | None = None
        free_from: int | None = None  # where the previous bar ends; bars may not overlap
        async with aclosing(off_loop(self._batches(), throttle)) as batches:
            async for batch in batches:
                for number, row in batch:
                    where = f"{self.path} row {number}"
                    try:
                        message, happened = self._message(request.instrument, row)
                    except InvalidValueError as exc:
                        raise InvalidValueError(f"{where}: {exc}") from exc
                    if previous is not None and happened < previous:
                        raise InvalidValueError(
                            f"{where}: time goes back from {previous} to {happened}"
                        )
                    if isinstance(message, Bar):
                        if free_from is not None and happened < free_from:
                            raise InvalidValueError(
                                f"{where}: the bar starting at {happened} overlaps the one "
                                f"before, which ends at {free_from}; is bar_size right?"
                            )
                        free_from = message.end
                    previous = happened
                    if happened >= request.end:
                        return
                    if happened >= request.start:
                        yield message

    def subscribe(self, subscriptions: Sequence[Subscription]) -> AsyncIterator[Message]:
        """Never: a file has no live data, and its capabilities say so.

        Raises:
            SourceUnavailableError: always.
        """
        raise SourceUnavailableError(f"{self.path}: a file has no live data")

    async def health_check(self) -> Health:
        if self.path.is_file():
            return Health(ok=True)
        return Health(ok=False, detail=f"{self.path} is not a file")

    async def close(self) -> None:
        pass

    @property
    def _bar_size(self) -> int | None:
        return None if self.params.bar_size is None else parse_span(self.params.bar_size)

    def _describe(self) -> str:
        return "trades" if self.params.bar_size is None else f"{self.params.bar_size} bars"

    def _columns(self) -> tuple[str, ...]:
        p = self.params
        return (p.time_column, *(p.values if p.kind == "trades" else BAR_COLUMNS))

    def _batches(self) -> Generator[list[Row]]:
        """Batches of rows. The file is opened by the first and closed with the generator."""
        if self.path.suffix.lower() == ".csv":
            return _csv_batches(self.path, self._columns())
        return _parquet_batches(self.path, self._columns(), self.params.time_column)

    def _message(self, instrument: Instrument, row: dict[str, Any]) -> tuple[Message, int]:
        p = self.params
        happened = Timestamp(_time(row[p.time_column], p.time_unit))
        if p.kind == "trades":
            values = {name: _number(name, row[name]) for name in p.values}
            point = Point(
                instrument=instrument, event_time=happened, received_at=happened, values=values
            )
            return point, happened
        size = self._bar_size
        assert size is not None  # FileSourceParams requires it for bars
        o, h, low, c, v = (_number(name, row[name]) for name in BAR_COLUMNS)
        end = Timestamp(happened + size)
        bar = Bar(
            instrument=instrument,
            start=happened,
            end=end,
            open=o,
            high=h,
            low=low,
            close=c,
            volume=v,
        )
        return bar, happened


class Nanos(int):
    """A time a Parquet timestamp column already gave in nanoseconds."""


def _unavailable(path: Path, exc: OSError) -> SourceUnavailableError:
    return SourceUnavailableError(f"{path}: {exc.strerror or exc}")


def _missing(path: Path, have: Sequence[str], columns: Sequence[str]) -> None:
    missing = [c for c in columns if c not in have]
    if missing:
        raise InvalidValueError(f"{path} has no column {', '.join(missing)}")


def _csv_batches(path: Path, columns: Sequence[str]) -> Generator[list[Row]]:
    try:
        # utf-8-sig: a byte-order mark (Excel writes one) is not part of the header.
        handle = path.open(newline="", encoding="utf-8-sig")
    except OSError as exc:
        raise _unavailable(path, exc) from exc
    with handle:
        reader = csv.DictReader(handle)
        try:
            _missing(path, reader.fieldnames or (), columns)
            rows = ((reader.line_num, row) for row in reader)
            while batch := list(islice(rows, BATCH)):
                yield batch
        except UnicodeDecodeError as exc:
            raise InvalidValueError(
                f"{path} is not UTF-8 text, at or after row {reader.line_num + 1}: {exc.reason}"
            ) from exc


def _parquet_batches(path: Path, columns: Sequence[str], time_column: str) -> Generator[list[Row]]:
    try:
        table = pq.ParquetFile(path)
    except OSError as exc:
        raise _unavailable(path, exc) from exc
    except pa.ArrowException as exc:
        raise InvalidValueError(f"{path} is not a Parquet file: {exc}") from exc
    with table:
        _missing(path, table.schema_arrow.names, columns)
        first = 1  # Parquet has no header row: rows count from 1
        for batch in table.iter_batches(batch_size=BATCH, columns=list(columns)):
            data = {name: _column(batch.column(name), path) for name in columns}
            yield [
                (first + i, {name: values[i] for name, values in data.items()})
                for i in range(batch.num_rows)
            ]
            first += batch.num_rows


def _column(values: "pa.Array[Any]", path: Path) -> list[Any]:
    """A Parquet column as Python values; a timestamp as nanoseconds, never as a datetime.

    A datetime would lose the nanoseconds, and a timestamp without a time zone
    is refused: its instant is unknown.
    """
    if not pa.types.is_timestamp(values.type):
        return values.to_pylist()
    if values.type.tz is None:
        raise InvalidValueError(f"{path}: the time column has no time zone")
    scale = UNITS[values.type.unit]
    counts: list[int | None] = pc.cast(values, pa.int64()).to_pylist()
    return [None if n is None else Nanos(n * scale) for n in counts]


def _time(value: Any, unit: TimeUnit) -> int:
    """An event time as nanoseconds, from a Parquet timestamp, ISO text or a whole count."""
    if value is None or value == "":
        raise InvalidValueError("the time is empty")
    if isinstance(value, Nanos):
        return value
    if unit == "iso":
        if not isinstance(value, str):
            raise InvalidValueError(f"the time {value!r} is not ISO 8601 text")
        return from_iso(value)
    if isinstance(value, bool | float):
        raise InvalidValueError(f"the time {value!r} is not a whole number of {unit}")
    try:
        count = int(value)
    except (TypeError, ValueError) as exc:
        raise InvalidValueError(f"the time {value!r} is not a whole number of {unit}") from exc
    return count * UNITS[unit]


def _number(name: str, value: Any) -> float:
    if value is None or value == "":
        raise InvalidValueError(f"{name} is empty")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise InvalidValueError(f"{name} is {value!r}, not a number") from exc
