# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Streams: what a consumer reads, independent of any chart.

Every consumer (an indicator, a chart, a strategy) is bound to its own stream:
an instrument and, for bars, how they are built. An indicator on a 1-hour
chart may read 15-minute or 4-hour bars; consumers of the same stream share
one bar builder.

Bars here are fixed-length intervals on UTC instants, which is exact for
markets that trade around the clock. Bars that follow an exchange's sessions
and local days (US equities) come with a session calendar in Phase 4.
"""

from dataclasses import dataclass
from typing import Literal

from heliograph.core.errors import InvalidValueError
from heliograph.core.messages import Instrument
from heliograph.core.time import Timestamp, format_span, parse_span

type GapPolicy = Literal["skip", "carry"]

UNIX_EPOCH = Timestamp(0)
"""The default anchor: bars line up with whole multiples of their size since 1970."""


@dataclass(frozen=True, slots=True, kw_only=True)
class BarSpec:
    """How bars are cut from points.

    Bars are the intervals ``[anchor + k*size, anchor + (k+1)*size)``. A bar
    closes ``grace`` nanoseconds after its end, so points that happened before
    the end but arrive slightly later still count; a point arriving after its
    bar closed is late and is not added. With ``gaps="skip"`` an interval with
    no points produces no bar; with ``"carry"`` it produces a flat bar at the
    last close with zero volume. ``price`` and ``volume`` name the point values
    to use; with ``volume=None`` bars have zero volume.
    """

    size: int
    anchor: Timestamp = UNIX_EPOCH
    grace: int = 0
    gaps: GapPolicy = "skip"
    price: str = "price"
    volume: str | None = "size"

    def __post_init__(self) -> None:
        for name in ("size", "anchor", "grace"):
            if type(getattr(self, name)) is not int:
                raise InvalidValueError(f"bar {name} must be an int of nanoseconds")
        if self.size <= 0:
            raise InvalidValueError(f"bar size must be positive, got {self.size}")
        if not 0 <= self.grace < self.size:
            raise InvalidValueError(
                f"grace must be at least 0 and less than the bar size, got {self.grace}"
            )
        if self.gaps not in ("skip", "carry"):
            raise InvalidValueError(f"gaps must be 'skip' or 'carry', got {self.gaps!r}")
        if not self.price.strip() or (self.volume is not None and not self.volume.strip()):
            raise InvalidValueError("price and volume must name a value")

    @classmethod
    def of(cls, size: str, **options: object) -> "BarSpec":
        """A spec from a written size such as ``"4h"``; other fields as keywords."""
        return cls(size=parse_span(size), **options)  # type: ignore[arg-type]

    def start_of(self, ts: Timestamp) -> Timestamp:
        """The start of the bar that contains ``ts``."""
        return Timestamp(ts - (ts - self.anchor) % self.size)

    def label(self) -> str:
        """A short name such as ``4h`` or ``15m+30m`` (with a shifted anchor)."""
        offset = self.anchor % self.size
        return format_span(self.size) + (f"+{format_span(offset)}" if offset else "")

    def can_be_built_from(self, finer: "BarSpec") -> bool:
        """Whether these bars are whole groups of ``finer`` bars, edge to edge, of the same values.

        Grace and gap policy are this spec's own and need not match.
        """
        return (
            self.size % finer.size == 0
            and (self.anchor - finer.anchor) % finer.size == 0
            and (self.price, self.volume) == (finer.price, finer.volume)
        )

    def identity(self) -> str:
        """Every field, for telling specs apart where the label would not."""
        return (
            f"{self.label()};grace={self.grace};gaps={self.gaps};"
            f"price={self.price};volume={self.volume}"
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class StreamSpec:
    """An instrument and, for bars, how they are built; ``bars=None`` is raw points."""

    instrument: Instrument
    bars: BarSpec | None = None

    @property
    def key(self) -> str:
        """A short name such as ``kraken:BTC/USD@4h`` for labels; specs differing
        only in grace, gaps or fields share it."""
        suffix = f"@{self.bars.label()}" if self.bars is not None else "@points"
        return f"{self.instrument.key}{suffix}"

    @property
    def identity(self) -> str:
        """A name that differs whenever any field does."""
        detail = f"[{self.bars.identity()}]" if self.bars is not None else ""
        return f"{self.key}{detail}"
