# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""A journal kept in a SQLite file, one row per message, encoded as JSON by msgspec.

Each row holds the sequence number, origin, message type, timestamp and the
message encoded as canonical JSON, so two runs that delivered the same
messages produce byte-identical rows with the same msgspec version (recorded
in the header). Reading back needs the message types named explicitly: a
journal never imports code because a file names it.

The header's ``status`` says whether the run finished: ``running`` while it
is written, ``complete`` after a clean close, ``failed: <error>`` when the
run raised. Entries are written in batches, so a process that dies loses up
to ``batch - 1`` of the last entries; live use should set ``batch`` low.
"""

import contextlib
import os
import sqlite3
from collections.abc import Iterable, Iterator
from importlib.metadata import version
from pathlib import Path
from types import TracebackType
from typing import Any, Self, cast

import msgspec

from heliograph.core.errors import InvalidValueError
from heliograph.core.journal import JournalEntry, Origin
from heliograph.core.messages import FrozenValues, Message
from heliograph.plugins.manifest import Manifest, Params, Plugin, param

FORMAT = 1
RESERVED_HEADER = frozenset({"format", "msgspec", "status"})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS header (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS entries (
    seq     INTEGER PRIMARY KEY,
    origin  TEXT    NOT NULL,
    type    TEXT    NOT NULL,
    ts      INTEGER NOT NULL,
    payload BLOB    NOT NULL
);
"""


def type_name(kind: type) -> str:
    """How a message type is named in a journal: ``module:QualifiedName``."""
    return f"{kind.__module__}:{kind.__qualname__}"


def _encode_hook(value: object) -> object:
    if isinstance(value, FrozenValues):
        return dict(value)
    raise NotImplementedError(f"cannot journal a {type(value).__name__}")


_ENCODER = msgspec.json.Encoder(enc_hook=_encode_hook, order="deterministic")


class SqliteJournalParams(Params):
    path: str = param("journal.sqlite", description="Journal file, created if missing")
    batch: int = param(1000, description="Entries written per transaction", ge=1)


class SqliteJournal(Plugin[SqliteJournalParams]):
    """Records every bus message in a SQLite file; use as a context manager."""

    manifest = Manifest(
        kind="journals",
        name="sqlite",
        version="1",
        summary="Every bus message in a SQLite file, for exact replay",
        params=SqliteJournalParams,
    )

    def __init__(self, params: SqliteJournalParams, header: dict[str, str] | None = None) -> None:
        super().__init__(params)
        clash = RESERVED_HEADER & set(header or {})
        if clash:
            raise InvalidValueError(f"journal header keys {sorted(clash)} are reserved")
        path = Path(params.path)
        try:
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        except FileExistsError:
            raise InvalidValueError(
                f"journal {path} already exists; a journal is written once"
            ) from None
        except OSError as exc:
            raise InvalidValueError(f"cannot create journal {path}: {exc.strerror}") from exc
        self._db = sqlite3.connect(path)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)
        rows = {
            "format": str(FORMAT),
            "msgspec": version("msgspec"),
            "status": "running",
            **(header or {}),
        }
        self._db.executemany("INSERT INTO header VALUES (?, ?)", sorted(rows.items()))
        self._db.commit()
        self._pending: list[tuple[int, str, str, int, bytes]] = []
        self._closed = False

    def append(self, entry: JournalEntry) -> None:
        message = entry.message
        payload = _ENCODER.encode(message)
        self._pending.append(
            (entry.seq, entry.origin, type_name(type(message)), message.ts, payload)
        )
        if len(self._pending) >= self.params.batch:
            self.flush()

    def flush(self) -> None:
        """Write buffered entries."""
        if self._pending:
            self._db.executemany("INSERT INTO entries VALUES (?, ?, ?, ?, ?)", self._pending)
            self._db.commit()
            self._pending.clear()

    def close(self, status: str = "complete") -> None:
        """Write what is buffered, record ``status`` and close the file."""
        if self._closed:
            return
        try:
            self.flush()
            self._db.execute("UPDATE header SET value = ? WHERE key = 'status'", (status,))
            self._db.commit()
            # Back to a single file: a reader (read-only, so it cannot tidy up) would
            # otherwise leave -wal and -shm files beside a finished journal. Only
            # tidying: while another connection has the file open, it stays in WAL.
            with contextlib.suppress(sqlite3.OperationalError):
                self._db.execute("PRAGMA journal_mode=DELETE")
        finally:
            self._db.close()
            self._closed = True

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        self.close("complete" if error is None else f"failed: {type(error).__name__}: {error}")


def read_header(path: Path) -> dict[str, str]:
    """The header of a journal file."""
    with _open_existing(path) as db:
        return dict(db.execute("SELECT key, value FROM header ORDER BY key"))


def read_entries(path: Path, types: Iterable[type]) -> Iterator[JournalEntry]:
    """The entries of a journal file, decoded into ``types``, in sequence order.

    The file and its format are checked when this is called; entries are read
    as they are iterated.

    Raises:
        InvalidValueError: if there is no journal at ``path``, it was written
            in another format, or (while iterating) it names a type not in
            ``types``.
    """
    known = {type_name(kind): kind for kind in types}
    header = read_header(path)
    if header.get("format") != str(FORMAT):
        raise InvalidValueError(f"journal {path} has format {header.get('format')}")
    return _entries(path, known)


def _entries(path: Path, known: dict[str, type]) -> Iterator[JournalEntry]:
    with _open_existing(path) as db:
        for seq, origin, name, _, payload in db.execute("SELECT * FROM entries ORDER BY seq"):
            if name not in known:
                raise InvalidValueError(f"journal {path} holds {name}, which was not given")
            message = cast(Message, msgspec.json.decode(payload, type=known[name]))
            yield JournalEntry(seq, cast(Origin, origin), message)


def raw_rows(path: Path) -> list[tuple[Any, ...]]:
    """Every entry row exactly as stored, for byte-for-byte comparison."""
    with _open_existing(path) as db:
        return list(db.execute("SELECT seq, origin, type, ts, payload FROM entries ORDER BY seq"))


class _open_existing:  # noqa: N801  (a context manager used like a function)
    """A journal opened read-only; anything that is not one is ``InvalidValueError``."""

    def __init__(self, path: Path) -> None:
        if not path.is_file():
            raise InvalidValueError(f"no journal at {path}")
        # As a URI built by pathlib, so ? # and % in a name are the file's, not the URI's.
        self._db = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            tables = {name for (name,) in self._db.execute("SELECT name FROM sqlite_master")}
        except sqlite3.DatabaseError:
            tables = set()
        if not {"header", "entries"} <= tables:
            self._db.close()
            raise InvalidValueError(f"{path} is not a journal")

    def __enter__(self) -> sqlite3.Connection:
        return self._db

    def __exit__(self, *exc: object) -> None:
        self._db.close()
