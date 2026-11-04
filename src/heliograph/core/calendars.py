# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""When a market is open (decision D61).

A market that closes is quiet by design: a watch that counts silence must not
count the night, the weekend or a holiday. ``SessionCalendar`` says one thing,
how long a stretch of *open* time takes to pass, and the rest is built on it.
Crypto trades around the clock (``ALWAYS_OPEN``).
"""

from typing import Protocol

from heliograph.core.errors import InvalidValueError
from heliograph.core.time import Timestamp


class SessionCalendar(Protocol):
    """The hours a market is open."""

    @property
    def name(self) -> str:
        """What to call it, such as ``XNYS``; empty for a market that never closes."""
        ...

    def after_open_time(self, start: Timestamp, span: int) -> Timestamp:
        """The instant by which ``span`` nanoseconds of open time have passed since ``start``.

        Closed time does not count: from a ``start`` while closed, counting begins at the next
        open.
        """
        ...


class AlwaysOpen:
    """A market that never closes: open time is time."""

    name = ""

    def after_open_time(self, start: Timestamp, span: int) -> Timestamp:
        if span < 0:
            raise InvalidValueError(f"a span of open time cannot be negative, not {span}")
        return Timestamp(start + span)


ALWAYS_OPEN = AlwaysOpen()
