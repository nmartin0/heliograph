# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The data store: raw downloads, cleaned by rule, published as audited snapshots.

Three tiers, after Elysium's bronze, silver and gold (docs/design.md, "Data"):

**Raw.** Each download is one Parquet file holding what the source yielded
for one request, in the order it yielded it, with what was asked and when in
the file's metadata. A raw file is never edited: its name ends with the hash
of its bytes, and a file that no longer matches is refused. Downloads are
written whole or not at all.

**Clean.** A pure function of a dataset's raw files, so it is computed, never
kept. Downloads apply in the order they were made, and a later download
replaces what an earlier one said about the interval it covers (a re-download
or a vendor's correction). Each row is then checked; a row that fails is
quarantined with its reason, never dropped or repaired:

- outside the interval its download asked for (a bar must also end within it);
- not a valid point or bar (the message types' own checks);
- a trade without a positive ``price``, or with a negative ``size``;
- a bar with a price that is not positive, or not one bar size long;
- a bar starting before the bar before it ends (overlap or duplicate).

Rows are put in time order (trades at one instant keep their order).

**Published.** A snapshot is the clean data of some datasets, written once to
its own directory with a manifest, after an audit: every dataset has rows,
every raw row is accounted for (kept, quarantined or superseded), and as
many were read as the raw files declare. The manifest records the intervals
the downloads covered; the ``store`` source refuses a request outside them,
so a gap between downloads is an error, never a quiet hole. Its id
is the hash of its inputs (the raw files, the format and the pyarrow version
that writes the Parquet), so publishing the same raw files again gives the
same id, and ``verify`` can rebuild it from the raw files and compare bytes.
That is Phase 1's gate: a published snapshot rebuilds exactly from its raw
files. A run reads a snapshot through the ``store`` data source and records
its id.

Each download also records its source's live delay and terms, so a snapshot
carries them: data published from a source whose terms forbid live use, or
that arrives late live, is read on the same terms.

Layout under the root::

    raw/<dataset>/<downloaded at>-<hash>.parquet
    published/<snapshot id>/manifest.json
    published/<snapshot id>/<dataset>/data.parquet
    published/<snapshot id>/<dataset>/quarantine.parquet

where ``<dataset>`` is ``<source>/<instrument key, %-escaped>/<trades|bars-1h>``.
"""

import asyncio
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
from collections.abc import AsyncGenerator, AsyncIterator, Generator, Iterable, Mapping, Sequence
from contextlib import aclosing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import field_validator

from heliograph.core.clock import Clock
from heliograph.core.errors import HeliographError, InvalidValueError
from heliograph.core.messages import Bar, Instrument, Message, Point
from heliograph.core.sources import (
    DATA_KINDS,
    TERMS,
    Capabilities,
    DataKind,
    DataSource,
    Health,
    HistoryRequest,
    SourceUnavailableError,
    Subscription,
    Term,
    Throttle,
)
from heliograph.core.time import NANOS_PER_SECOND, Timestamp, format_span, to_datetime
from heliograph.plugins.batches import off_loop
from heliograph.plugins.conformance import Conformance
from heliograph.plugins.manifest import Manifest, Params, Plugin, param

FORMAT = 1
"""The store's file format and cleaning rules; part of every snapshot id.

Change it whenever either changes: the same raw files would otherwise give
the old id, and ``publish`` would return a snapshot cleaned by old rules.
"""

BAR_FIELDS = ("start", "end", "open", "high", "low", "close", "volume")
BATCH = 10_000
"""Rows read per trip off the event loop."""

_SLUG = re.compile(r"[a-z][a-z0-9_]*")
_RAW_NAME = re.compile(r"(?P<at>\d{8}T\d{6}\.\d{9}Z)-(?P<hash>[0-9a-f]{16})\.parquet")
_SNAPSHOT_ID = re.compile(r"[0-9a-f]{16}")


class StoreError(HeliographError):
    """The store cannot do what was asked: a raw file changed, an audit failed, no snapshot."""


def canonical_json(value: Any) -> str:
    """JSON with sorted keys and no spaces: one text for one value."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _stamp(ts: int) -> str:
    """A timestamp as a file-name-safe UTC text that sorts by time: 20261021T120000.000000001Z."""
    return f"{to_datetime(Timestamp(ts)):%Y%m%dT%H%M%S}.{ts % NANOS_PER_SECOND:09d}Z"


@dataclass(frozen=True, slots=True, kw_only=True)
class Dataset:
    """One source's data of one kind for one instrument: what raw files are grouped by."""

    source: str
    instrument: Instrument
    kind: DataKind
    bar_size: int | None = None

    def __post_init__(self) -> None:
        if not _SLUG.fullmatch(self.source):
            raise InvalidValueError(f"dataset source {self.source!r} must be a lowercase slug")
        if self.kind not in DATA_KINDS:
            raise InvalidValueError(f"unknown data kind {self.kind!r}")
        if (self.kind == "bars") != (self.bar_size is not None):
            raise InvalidValueError("a bar size is given for bars, and only for bars")
        if self.bar_size is not None and (type(self.bar_size) is not int or self.bar_size <= 0):
            raise InvalidValueError("bar size must be a positive int of nanoseconds")

    @classmethod
    def of(cls, source: str, request: HistoryRequest) -> "Dataset":
        return cls(
            source=source,
            instrument=request.instrument,
            kind=request.kind,
            bar_size=request.bar_size,
        )

    @property
    def name(self) -> str:
        """Its directory under ``raw/`` and in a snapshot: ``ccxt/kraken%3ABTC%2FUSD/trades``."""
        size = "trades" if self.bar_size is None else f"bars-{format_span(self.bar_size)}"
        return f"{self.source}/{quote(self.instrument.key, safe='')}/{size}"

    def to_json(self) -> dict[str, Any]:
        i = self.instrument
        return {
            "source": self.source,
            "instrument": {
                "symbol": i.symbol,
                "venue": i.venue,
                "asset_class": i.asset_class,
                "quote_currency": i.quote_currency,
            },
            "kind": self.kind,
            "bar_size": self.bar_size,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "Dataset":
        return cls(
            source=data["source"],
            instrument=Instrument(**data["instrument"]),
            kind=data["kind"],
            bar_size=data["bar_size"],
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class Download:
    """One raw file: what was asked of which source, when, and what it carried."""

    id: str
    path: Path
    dataset: Dataset
    params: Mapping[str, Any]
    start: int
    end: int
    downloaded_at: int
    rows: int
    live_delay: int
    terms: frozenset[str]

    @property
    def order(self) -> tuple[int, str]:
        """Downloads apply in the order they were made; the id breaks ties."""
        return self.downloaded_at, self.id


@dataclass(frozen=True, slots=True)
class Quarantined:
    """A raw row that failed a check, where it came from, and why."""

    download: str
    row: int
    reason: str
    data: str


@dataclass(slots=True)
class Cleaned:
    """A dataset's clean rows, its quarantine, and how every raw row was accounted for."""

    dataset: Dataset
    table: pa.Table
    quarantine: list[Quarantined]
    raw_rows: int
    superseded: int

    @property
    def rows(self) -> int:
        return self.table.num_rows


@dataclass(frozen=True, slots=True)
class Snapshot:
    """A published snapshot: its id, directory and manifest."""

    id: str
    path: Path
    manifest: Mapping[str, Any] = field(repr=False)

    def datasets(self) -> dict[str, Dataset]:
        """Every dataset in the snapshot, by name."""
        return {
            name: Dataset.from_json(entry["dataset"])
            for name, entry in self.manifest["datasets"].items()
        }

    def entry(self, dataset: Dataset) -> Mapping[str, Any]:
        """The manifest's entry for ``dataset``.

        Raises:
            StoreError: if the snapshot does not hold it.
        """
        try:
            entry: Mapping[str, Any] = self.manifest["datasets"][dataset.name]
        except KeyError:
            raise StoreError(f"snapshot {self.id} holds no {dataset.name}") from None
        return entry


# -- tables --------------------------------------------------------------------------


def _time_of(dataset: Dataset) -> str:
    return "event_time" if dataset.kind == "trades" else "start"


def _table(
    dataset: Dataset, rows: Sequence[Mapping[str, Any]], value_names: Iterable[str]
) -> pa.Table:
    """Rows as a table with the dataset's columns, in a fixed order."""
    if dataset.kind == "trades":
        names = ["event_time", *sorted(value_names)]
        types = [pa.int64()] + [pa.float64()] * (len(names) - 1)
    else:
        names = list(BAR_FIELDS)
        types = [pa.int64(), pa.int64()] + [pa.float64()] * 5
    columns = {
        name: pa.array([row.get(name) for row in rows], type=kind)
        for name, kind in zip(names, types, strict=True)
    }
    return pa.table(columns)


def _row(message: Message, dataset: Dataset) -> dict[str, Any]:
    """A source's message as a raw row, refusing what does not belong to the dataset."""
    if isinstance(message, Point | Bar) and message.instrument != dataset.instrument:
        raise InvalidValueError(
            f"asked for {dataset.instrument.key}, the source sent {message.instrument.key}"
        )
    if dataset.kind == "trades":
        if not isinstance(message, Point):
            raise InvalidValueError(f"asked for trades, the source sent a {type(message).__name__}")
        if "event_time" in message.values:
            raise InvalidValueError("a trade's values may not be named event_time")
        return {"event_time": message.event_time, **message.values}
    if not isinstance(message, Bar):
        raise InvalidValueError(f"asked for bars, the source sent a {type(message).__name__}")
    return {name: getattr(message, name) for name in BAR_FIELDS}


def _parquet(table: pa.Table, metadata: Mapping[str, Any] | None = None) -> bytes:
    """``table`` as Parquet bytes; the same table and metadata give the same bytes."""
    if metadata is not None:
        table = table.replace_schema_metadata({b"heliograph": canonical_json(metadata).encode()})
    buffer = io.BytesIO()
    pq.write_table(table, buffer, compression="zstd")
    return buffer.getvalue()


def _write_new(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` whole or not at all; an existing file is never replaced."""
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f".{path.name}.partial")
    partial.write_bytes(data)
    try:
        os.link(partial, path)  # fails if the file exists, unlike a rename
    except FileExistsError:
        if path.read_bytes() != data:
            raise StoreError(f"{path} exists with other contents") from None
    finally:
        partial.unlink()


# -- the store -----------------------------------------------------------------------


class Store:
    """Raw downloads, clean data and published snapshots under one directory."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.raw = root / "raw"
        self.published = root / "published"

    # raw

    async def download(
        self,
        source_name: str,
        params: Mapping[str, Any],
        source: DataSource,
        request: HistoryRequest,
        clock: Clock,
    ) -> Download:
        """Ask ``source`` for ``request`` and keep what it yields as a new raw file.

        ``clock`` is the real time: it records when the download was made.

        Raises:
            InvalidValueError: if the source sends another instrument or kind.
            Whatever the source raised; nothing is written then.
        """
        dataset = Dataset.of(source_name, request)
        rows: list[dict[str, Any]] = []
        names: set[str] = set()
        async with aclosing(_stream(source.history(request))) as stream:
            async for message in stream:
                row = _row(message, dataset)
                rows.append(row)
                names.update(row)
        names.discard("event_time")
        capabilities = source.capabilities
        downloaded_at = clock.now()
        metadata = {
            "format": FORMAT,
            "dataset": dataset.to_json(),
            "params": json.loads(canonical_json(dict(params))),
            "start": request.start,
            "end": request.end,
            "downloaded_at": downloaded_at,
            "rows": len(rows),
            "live_delay": capabilities.live_delay,
            "terms": sorted(capabilities.terms),
        }
        data = await asyncio.to_thread(_parquet, _table(dataset, rows, names), metadata)
        name = f"{_stamp(downloaded_at)}-{_sha256(data)[:16]}.parquet"
        path = self.raw / dataset.name / name
        await asyncio.to_thread(_write_new, path, data)
        return _download(path)

    def datasets(self) -> list[Dataset]:
        """Every dataset with a raw file, in name order."""
        found: dict[Path, Dataset] = {}
        for path in sorted(self.raw.glob("*/*/*/*.parquet")):
            if path.parent not in found:
                found[path.parent] = _download(path).dataset
        return sorted(found.values(), key=lambda d: d.name)

    def downloads(self, dataset: Dataset) -> list[Download]:
        """The dataset's raw files, in the order they were made.

        Raises:
            StoreError: if a raw file's bytes no longer match its name.
        """
        folder = self.raw / dataset.name
        found = [_checked(path) for path in sorted(folder.glob("*.parquet"))]
        for download in found:
            if download.dataset != dataset:
                raise StoreError(
                    f"{download.path} holds {download.dataset.instrument}, not "
                    f"{dataset.instrument}: two instruments share the key "
                    f"{dataset.instrument.key}, which names the folder"
                )
        return sorted(found, key=lambda d: d.order)

    # published

    def publish(self, datasets: Sequence[Dataset]) -> Snapshot:
        """Publish the clean data of ``datasets`` from all their raw files.

        Publishing the same raw files again returns the same snapshot.

        Raises:
            StoreError: if a dataset has no raw files or fails its audit;
                nothing is published then.
        """
        if not datasets:
            raise StoreError("a snapshot needs at least one dataset")
        chosen = {d.name: self.downloads(d) for d in sorted(datasets, key=lambda d: d.name)}
        missing = [name for name, downloads in chosen.items() if not downloads]
        if missing:
            raise StoreError(f"nothing downloaded for {', '.join(missing)}")
        snapshot_id = _snapshot_id({name: [d.id for d in ds] for name, ds in chosen.items()})
        if (self.published / snapshot_id / "manifest.json").is_file():
            return self.snapshot(snapshot_id)
        files, manifest = _build(snapshot_id, chosen)
        problems = _audit(manifest)
        if problems:
            raise StoreError("the snapshot fails its audit:\n" + "\n".join(problems))
        # Staged in a directory of its own, then renamed into place whole, so
        # a snapshot directory exists only complete. Two publishes of the same
        # snapshot at once each stage their own; the second to finish finds
        # the first's in place and discards its own.
        self.published.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{snapshot_id}-", dir=self.published))
        try:
            for relative, data in files.items():
                (staging / relative).parent.mkdir(parents=True, exist_ok=True)
                (staging / relative).write_bytes(data)
            try:
                os.rename(staging, self.published / snapshot_id)
            except OSError:
                if not (self.published / snapshot_id / "manifest.json").is_file():
                    raise
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        return self.snapshot(snapshot_id)

    def snapshot(self, snapshot_id: str) -> Snapshot:
        """The published snapshot ``snapshot_id``.

        Raises:
            StoreError: if there is none.
        """
        if not _SNAPSHOT_ID.fullmatch(snapshot_id):
            raise StoreError(f"{snapshot_id!r} is not a snapshot id")
        path = self.published / snapshot_id
        try:
            manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise StoreError(f"no snapshot {snapshot_id} in {self.published}") from None
        return Snapshot(snapshot_id, path, manifest)

    def verify(self, snapshot_id: str) -> list[str]:
        """Rebuild a snapshot from its raw files; every way it differs from what is published."""
        snapshot = self.snapshot(snapshot_id)
        problems: list[str] = []
        chosen: dict[str, list[Download]] = {}
        for name, entry in snapshot.manifest["datasets"].items():
            dataset = Dataset.from_json(entry["dataset"])
            try:
                have = {d.id: d for d in self.downloads(dataset)}
            except StoreError as exc:
                problems.append(f"{name}: {exc}")
                continue
            lost = [i for i in entry["downloads"] if i not in have]
            if lost:
                problems.append(f"{name}: raw files gone: {', '.join(lost)}")
                continue
            chosen[name] = [have[i] for i in entry["downloads"]]
        if problems:
            return problems
        rebuilt_id = _snapshot_id({name: [d.id for d in ds] for name, ds in chosen.items()})
        if rebuilt_id != snapshot_id:
            return [f"its raw files give the id {rebuilt_id}, not {snapshot_id}"]
        files, _ = _build(snapshot_id, chosen)
        for relative, data in sorted(files.items()):
            path = snapshot.path / relative
            if not path.is_file():
                problems.append(f"{relative} is missing")
            elif path.read_bytes() != data:
                problems.append(f"{relative} differs from its rebuild from the raw files")
        return problems


async def _stream(messages: AsyncIterator[Message]) -> AsyncGenerator[Message]:
    """``messages``, closed when done (a source's history is an async generator)."""
    try:
        async for message in messages:
            yield message
    finally:
        aclose = getattr(messages, "aclose", None)
        if aclose is not None:
            await aclose()


def _download(path: Path) -> Download:
    """The record of a raw file, from its name and metadata."""
    match = _RAW_NAME.fullmatch(path.name)
    if match is None:
        raise StoreError(f"{path} is not a raw file of this store")
    try:
        raw_meta = pq.read_schema(path).metadata or {}
        meta = json.loads(raw_meta[b"heliograph"])
    except (KeyError, ValueError, OSError, pa.ArrowException) as exc:
        raise StoreError(f"{path} is not a raw file of this store: {exc}") from exc
    if meta.get("format") != FORMAT:
        raise StoreError(f"{path} is in format {meta.get('format')}, not {FORMAT}")
    return Download(
        id=path.stem,
        path=path,
        dataset=Dataset.from_json(meta["dataset"]),
        params=meta["params"],
        start=meta["start"],
        end=meta["end"],
        downloaded_at=meta["downloaded_at"],
        rows=meta["rows"],
        live_delay=meta["live_delay"],
        terms=frozenset(meta["terms"]),
    )


def _checked(path: Path) -> Download:
    """A raw file's record, after checking its bytes still match its name."""
    match = _RAW_NAME.fullmatch(path.name)
    if match is None or _sha256(path.read_bytes())[:16] != match["hash"]:
        raise StoreError(f"{path} has changed since it was downloaded; raw files are never edited")
    return _download(path)


def _snapshot_id(downloads: Mapping[str, Sequence[str]]) -> str:
    inputs = {"format": FORMAT, "pyarrow": pa.__version__, "datasets": downloads}
    return _sha256(canonical_json(inputs).encode())[:16]


# -- cleaning --------------------------------------------------------------------------


def clean(downloads: Sequence[Download]) -> Cleaned:
    """A dataset's clean rows from its raw files (see the module docstring for the rules).

    Raises:
        InvalidValueError: if ``downloads`` is empty or mixes datasets.
    """
    if not downloads:
        raise InvalidValueError("cleaning needs at least one download")
    dataset = downloads[0].dataset
    if any(d.dataset != dataset for d in downloads):
        raise InvalidValueError("cleaning mixes datasets")
    time = _time_of(dataset)
    kept: list[_Kept] = []
    quarantine: list[Quarantined] = []
    raw_rows = superseded = 0
    for download in sorted(downloads, key=lambda d: d.order):
        before = len(kept)
        kept = [k for k in kept if not download.start <= k.time < download.end]
        superseded += before - len(kept)
        for number, row in enumerate(pq.read_table(download.path).to_pylist(), start=1):
            raw_rows += 1
            reason = _problem(dataset, download, row)
            if reason is None:
                kept.append(_Kept(row[time], len(kept) + superseded, download.id, number, row))
            else:
                quarantine.append(Quarantined(download.id, number, reason, canonical_json(row)))
    kept.sort(key=lambda k: (k.time, k.arrival))
    if dataset.kind == "bars":
        kept, overlapping = _without_overlaps(kept)
        quarantine += overlapping
    rows = [k.row for k in kept]
    names = {name for row in rows for name, value in row.items() if value is not None}
    names.discard("event_time")
    return Cleaned(dataset, _table(dataset, rows, names), quarantine, raw_rows, superseded)


@dataclass(frozen=True, slots=True)
class _Kept:
    """A raw row that passed its checks, with where it came from."""

    time: int
    arrival: int  # keeps the order of rows at one instant
    download: str
    number: int
    row: dict[str, Any]


def _problem(dataset: Dataset, download: Download, row: Mapping[str, Any]) -> str | None:
    """Why a raw row cannot be kept, or None."""
    happened = row[_time_of(dataset)]
    if not download.start <= happened < download.end:
        return f"at {happened}, outside the download's interval [{download.start}, {download.end})"
    if dataset.kind == "bars" and row["end"] > download.end:
        return f"ends at {row['end']}, after the download's interval ends at {download.end}"
    try:
        if dataset.kind == "trades":
            values = {k: v for k, v in row.items() if k != "event_time" and v is not None}
            Point(
                instrument=dataset.instrument,
                event_time=Timestamp(happened),
                received_at=Timestamp(happened),
                values=values,
            )
        else:
            Bar(instrument=dataset.instrument, **{k: row[k] for k in BAR_FIELDS})
    except (InvalidValueError, TypeError) as exc:
        return str(exc)
    if dataset.kind == "trades":
        price, size = row.get("price"), row.get("size")
        if price is None:
            return "has no price"
        if price <= 0:
            return f"price {price} is not positive"
        if size is not None and size < 0:
            return f"size {size} is negative"
        return None
    if min(row["open"], row["high"], row["low"], row["close"]) <= 0:
        return "a price is not positive"
    length = row["end"] - row["start"]
    if length != dataset.bar_size:
        assert dataset.bar_size is not None
        return f"lasts {format_span(length)}, not the dataset's {format_span(dataset.bar_size)}"
    return None


def _without_overlaps(bars: list[_Kept]) -> tuple[list[_Kept], list[Quarantined]]:
    """Bars in time order, without those starting before the bar before ends."""
    kept: list[_Kept] = []
    dropped: list[Quarantined] = []
    for bar in bars:
        if kept and bar.time < kept[-1].row["end"]:
            reason = f"overlaps the bar before, which ends at {kept[-1].row['end']}"
            dropped.append(Quarantined(bar.download, bar.number, reason, canonical_json(bar.row)))
        else:
            kept.append(bar)
    return kept, dropped


def _quarantine_table(quarantine: Sequence[Quarantined]) -> pa.Table:
    return pa.table(
        {
            "download": pa.array([q.download for q in quarantine], pa.string()),
            "row": pa.array([q.row for q in quarantine], pa.int64()),
            "reason": pa.array([q.reason for q in quarantine], pa.string()),
            "data": pa.array([q.data for q in quarantine], pa.string()),
        }
    )


def _build(
    snapshot_id: str, chosen: Mapping[str, Sequence[Download]]
) -> tuple[dict[str, bytes], dict[str, Any]]:
    """A snapshot's files, by path relative to its directory, and its manifest."""
    files: dict[str, bytes] = {}
    entries: dict[str, Any] = {}
    for name, downloads in sorted(chosen.items()):
        cleaned = clean(downloads)
        data = _parquet(cleaned.table)
        quarantined = _parquet(_quarantine_table(cleaned.quarantine))
        files[f"{name}/data.parquet"] = data
        files[f"{name}/quarantine.parquet"] = quarantined
        entries[name] = {
            "dataset": cleaned.dataset.to_json(),
            "downloads": [d.id for d in downloads],
            "raw_rows": cleaned.raw_rows,
            "rows": cleaned.rows,
            "quarantined": len(cleaned.quarantine),
            "superseded": cleaned.superseded,
            "declared_rows": sum(d.rows for d in downloads),
            "covered": _covered(downloads),
            "live_delay": max(d.live_delay for d in downloads),
            "terms": sorted(set().union(*(d.terms for d in downloads))),
            "data": _sha256(data),
            "quarantine": _sha256(quarantined),
        }
    manifest = {"format": FORMAT, "id": snapshot_id, "pyarrow": pa.__version__, "datasets": entries}
    files["manifest.json"] = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    return files, manifest


def _covered(downloads: Sequence[Download]) -> list[list[int]]:
    """The intervals the downloads asked for, merged where they meet or overlap."""
    merged: list[list[int]] = []
    for start, end in sorted((d.start, d.end) for d in downloads):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def _audit(manifest: Mapping[str, Any]) -> list[str]:
    """Every reason a snapshot may not be published."""
    problems = []
    for name, entry in manifest["datasets"].items():
        if entry["rows"] == 0:
            problems.append(f"{name}: no rows survive cleaning")
        if entry["raw_rows"] != entry["declared_rows"]:
            problems.append(
                f"{name}: {entry['raw_rows']} rows read from raw files that declare "
                f"{entry['declared_rows']}"
            )
        accounted = entry["rows"] + entry["quarantined"] + entry["superseded"]
        if accounted != entry["raw_rows"]:
            problems.append(
                f"{name}: {entry['raw_rows']} raw rows, but {accounted} kept, "
                "quarantined or superseded"
            )
    return problems


# -- reading a snapshot ------------------------------------------------------------------


class StoreSourceParams(Params):
    root: str = param(..., description="The store's directory")
    snapshot: str = param(..., description="The id of the published snapshot to read")
    source: str = param(..., description="Whose data to read: the source it was downloaded from")

    @field_validator("snapshot")
    @classmethod
    def _an_id(cls, snapshot: str) -> str:
        if not _SNAPSHOT_ID.fullmatch(snapshot):
            raise ValueError(f"{snapshot!r} is not a snapshot id (16 hexadecimal digits)")
        return snapshot


class StoreSource(Plugin[StoreSourceParams]):
    """History from a published snapshot, on the terms of the source it came from."""

    manifest = Manifest(
        kind="data_sources",
        name="store",
        version="1",
        summary="History from a published snapshot of the data store",
        params=StoreSourceParams,
    )
    conformance = Conformance(  # nothing is read when it is built
        params={"root": "store", "snapshot": "0123456789abcdef", "source": "file"}
    )

    def __init__(self, params: StoreSourceParams) -> None:
        super().__init__(params)
        self._throttle: Throttle | None = None
        self._snapshot: Snapshot | None = None

    @property
    def snapshot(self) -> Snapshot:
        """The snapshot read, loaded once.

        Raises:
            StoreError: if it is not published.
        """
        return self._load()

    def _load(self) -> Snapshot:
        if self._snapshot is None:
            self._snapshot = Store(Path(self.params.root)).snapshot(self.params.snapshot)
        return self._snapshot

    def _entries(self) -> list[Mapping[str, Any]]:
        return [
            entry
            for entry in self.snapshot.manifest["datasets"].values()
            if entry["dataset"]["source"] == self.params.source
        ]

    @property
    def capabilities(self) -> Capabilities:
        """What the snapshot holds of the source's data, with its live delay and terms."""
        entries = self._entries()
        kinds = {e["dataset"]["kind"] for e in entries}
        sizes = {e["dataset"]["bar_size"] for e in entries if e["dataset"]["bar_size"]}
        terms = {cast(Term, t) for e in entries for t in e["terms"] if t in TERMS}
        return Capabilities(
            history=frozenset(kinds),
            bar_sizes=frozenset(sizes),
            live_delay=max((e["live_delay"] for e in entries), default=0),
            terms=frozenset(terms),
        )

    def bind(self, throttle: Throttle) -> None:
        self._throttle = throttle

    async def history(self, request: HistoryRequest) -> AsyncIterator[Message]:
        """The snapshot's rows for the request, in time order.

        Raises:
            StoreError: if the snapshot does not hold the dataset for the whole
                request (a gap between downloads counts), or its file no longer
                matches the manifest.
        """
        throttle = self._throttle
        if throttle is None:
            raise InvalidValueError("the store source was used before it was bound")
        dataset = Dataset.of(self.params.source, request)
        entry = self.snapshot.entry(dataset)
        if not any(s <= request.start and request.end <= e for s, e in entry["covered"]):
            spans = ", ".join(f"[{s}, {e})" for s, e in entry["covered"])
            raise StoreError(
                f"snapshot {self.params.snapshot} holds {dataset.name} for {spans}, "
                f"not all of [{request.start}, {request.end})"
            )
        path = self.snapshot.path / dataset.name / "data.parquet"
        batches = _published_batches(path, entry["data"])
        async with aclosing(off_loop(batches, throttle)) as reading:
            async for batch in reading:
                for row in batch:
                    happened = row[_time_of(dataset)]
                    if happened >= request.end:
                        return
                    if happened >= request.start:
                        yield _message(dataset, row)

    def subscribe(self, subscriptions: Sequence[Subscription]) -> AsyncIterator[Message]:
        """Never: a snapshot has no live data, and its capabilities say so.

        Raises:
            SourceUnavailableError: always.
        """
        raise SourceUnavailableError(f"snapshot {self.params.snapshot} has no live data")

    async def health_check(self) -> Health:
        try:
            self._load()
        except StoreError as exc:
            return Health(ok=False, detail=str(exc))
        return Health(ok=True)

    async def close(self) -> None:
        pass


def _published_batches(path: Path, sha256: str) -> Generator[list[dict[str, Any]]]:
    """A published file's rows in batches, after checking it is the file published."""
    if _sha256(path.read_bytes()) != sha256:
        raise StoreError(f"{path} no longer matches its snapshot's manifest")
    with pq.ParquetFile(path) as table:
        for batch in table.iter_batches(batch_size=BATCH):
            yield batch.to_pylist()


def _message(dataset: Dataset, row: Mapping[str, Any]) -> Message:
    if dataset.kind == "trades":
        at = Timestamp(row["event_time"])
        values = {k: v for k, v in row.items() if k != "event_time" and v is not None}
        return Point(instrument=dataset.instrument, event_time=at, received_at=at, values=values)
    return Bar(instrument=dataset.instrument, **{k: row[k] for k in BAR_FIELDS})
