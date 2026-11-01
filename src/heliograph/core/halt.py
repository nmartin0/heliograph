# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The Halted state: a live session that refuses to add risk until its owner says so (D55).

Anything may halt a session: the owner by hand, or a watch that notices something wrong (no
prices, too many signals, a feed that gave up). Halting is the owner's to undo: only a
``Cleared`` from the owner ends it, and nothing clears itself.

While halted a strategy still sends signals that only take risk off (a stop, an exit to flat),
as with every risk gate (D34); entries and additions are refused and journalled as
``SignalRefused``. ``Halt`` is that gate: a live session puts it first on every strategy.

Both messages are stamped when they became known. The owner's are inputs, replayed; a watch's
are derived from the data and the clock, so a replay produces them again.
"""

from dataclasses import dataclass

from heliograph.core.bus import MessageBus
from heliograph.core.pipeline import Proposal
from heliograph.core.time import Timestamp

OWNER = "owner"


@dataclass(frozen=True, slots=True, kw_only=True)
class Halted:
    """The session was halted at ``ts`` by ``by`` ("owner", or the name of a watch)."""

    ts: Timestamp
    reason: str
    by: str = OWNER


@dataclass(frozen=True, slots=True, kw_only=True)
class Cleared:
    """The owner cleared the halt at ``ts``."""

    ts: Timestamp
    by: str = OWNER


class Halt:
    """A risk gate that refuses to add risk while the session is halted."""

    __slots__ = ("_halted",)

    def __init__(self, bus: MessageBus) -> None:
        self._halted: Halted | None = None
        bus.subscribe(Halted, self._on_halted)
        bus.subscribe(Cleared, self._on_cleared)

    @property
    def halted(self) -> Halted | None:
        """The halt in force (the first, if several came), or None."""
        return self._halted

    def _on_halted(self, message: Halted) -> None:
        if self._halted is None:
            self._halted = message

    def _on_cleared(self, message: Cleared) -> None:
        self._halted = None

    def check(self, proposal: Proposal) -> str | None:
        if self._halted is None:
            return None
        return f"halted by {self._halted.by}: {self._halted.reason}"

    def sent(self, proposal: Proposal) -> None:
        return None
