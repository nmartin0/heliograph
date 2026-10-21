# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The journal: every message on the bus, numbered, in the order delivered.

Jane Street's Concord keeps one ordered log and recovers by replaying it; this
is the same idea at our scale. The bus numbers each message and hands it to
the journal before delivering it, recording where it came from:

- ``input``: data from outside, published by the engine from its feeds or by
  a live source;
- ``engine``: alarms, Settled and RunEnded marks, which the engine produces from the
  components' own state;
- ``derived``: what handlers published in reaction.

Only inputs are replayed. Feeding them to the same components reproduces
every engine and derived message, so a replayed journal equals the original
entry for entry; that is how a backtest or a live day is reproduced exactly.

A replay reproduces the original only if it starts from the same state:
- the same components, built with the same parameters in the same order
  (subscription order decides delivery order);
- the clock starting at the same instant;
- the same runs: each run ends with a RunEnded mark, and :func:`runs` splits
  a journal back into each run's inputs and where it stopped.
The journal's header is the place to record the configuration that built the
components; the inputs and run boundaries are in the entries.
"""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Literal, Protocol

from heliograph.core.messages import Message, RunEnded
from heliograph.core.time import Timestamp

type Origin = Literal["input", "engine", "derived"]


@dataclass(frozen=True, slots=True)
class JournalEntry:
    """One message as the bus delivered it."""

    seq: int
    origin: Origin
    message: Message


class Journal(Protocol):
    """Where the bus records messages."""

    def append(self, entry: JournalEntry) -> None:
        """Record ``entry``; entries arrive with consecutive sequence numbers."""
        ...


class MemoryJournal:
    """A journal held in a list, for tests and short runs."""

    __slots__ = ("entries",)

    def __init__(self) -> None:
        self.entries: list[JournalEntry] = []

    def append(self, entry: JournalEntry) -> None:
        self.entries.append(entry)


def inputs(entries: Iterable[JournalEntry]) -> Iterator[Message]:
    """The input messages of a journal, in order: what a replay feeds the engine."""
    return (entry.message for entry in entries if entry.origin == "input")


def runs(entries: Iterable[JournalEntry]) -> list[tuple[list[Message], Timestamp | None]]:
    """Each run's input messages and the instant it stopped at, in order.

    A run without a RunEnded mark (the process stopped mid-run) comes last,
    with None for where it stopped.
    """
    found: list[tuple[list[Message], Timestamp | None]] = []
    current: list[Message] = []
    for entry in entries:
        if entry.origin == "input":
            current.append(entry.message)
        elif entry.origin == "engine" and isinstance(entry.message, RunEnded):
            found.append((current, entry.message.ts))
            current = []
    if current:
        found.append((current, None))
    return found
