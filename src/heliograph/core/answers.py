# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""A person's answer to a signal: took it, or skipped it (decision D50).

Heliograph never trades; it tells a person what to trade. What the person then
did is news from outside the run, like a price: it is stamped when it was
learned, journalled as an input and replayed, so a live day with its answers
can be reproduced.

``quantity`` is what the person actually traded, in units, positive for a buy;
without it they are taken to have traded what the signal asked. ``price`` is
what they paid or received, kept for the comparison with the signal's paper
result and not used to decide anything.
"""

import math
from dataclasses import dataclass
from typing import Literal

from heliograph.core.errors import InvalidValueError
from heliograph.core.time import Timestamp

type Verdict = Literal["took", "skipped"]

VERDICTS: tuple[Verdict, ...] = ("took", "skipped")


@dataclass(frozen=True, slots=True, kw_only=True)
class SignalAnswer:
    """What the person did about the signal ``signal``, as of ``ts``.

    A later answer to the same signal replaces an earlier one: a person may
    change their mind, or correct a slip.
    """

    ts: Timestamp
    signal: str
    verdict: Verdict
    price: float | None = None
    quantity: float | None = None

    def __post_init__(self) -> None:
        if not self.signal:
            raise InvalidValueError("an answer needs the id of the signal it answers")
        if self.verdict not in VERDICTS:
            raise InvalidValueError(f"an answer is {' or '.join(VERDICTS)}, not {self.verdict!r}")
        if self.verdict == "skipped" and (self.price is not None or self.quantity is not None):
            raise InvalidValueError("a skipped signal has no price or quantity")
        if self.price is not None and not (math.isfinite(self.price) and self.price > 0):
            raise InvalidValueError(f"the price must be positive, not {self.price}")
        if self.quantity is not None and not (math.isfinite(self.quantity) and self.quantity != 0):
            raise InvalidValueError(
                f"the quantity must be a number other than 0, not {self.quantity}"
            )
