# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from heliograph.core.errors import InvalidValueError
from heliograph.core.streams import BarSpec, StreamSpec
from heliograph.core.time import NANOS_PER_HOUR, NANOS_PER_MINUTE, Timestamp, from_iso
from tests.support.world import BTC


def test_bars_start_on_their_boundaries_counted_from_the_anchor() -> None:
    four_hours = BarSpec.of("4h")
    t = from_iso("2026-10-21T13:37:00Z")
    assert four_hours.start_of(t) == from_iso("2026-10-21T12:00:00Z")
    assert four_hours.start_of(from_iso("2026-10-21T12:00:00Z")) == from_iso("2026-10-21T12:00:00Z")


def test_an_anchor_shifts_every_boundary() -> None:
    shifted = BarSpec.of("4h", anchor=Timestamp(2 * NANOS_PER_HOUR))
    assert shifted.start_of(from_iso("2026-10-21T13:37:00Z")) == from_iso("2026-10-21T10:00:00Z")
    assert shifted.label() == "4h+2h"


@given(st.integers(-(10**18), 10**18), st.integers(1, 10**12), st.integers(-(10**15), 10**15))
def test_every_instant_lies_in_exactly_the_bar_that_starts_before_it(
    ts: int, size: int, anchor: int
) -> None:
    spec = BarSpec(size=size, anchor=Timestamp(anchor))
    start = spec.start_of(Timestamp(ts))
    assert start <= ts < start + size
    assert (start - anchor) % size == 0


def test_streams_are_named_for_labels() -> None:
    assert StreamSpec(instrument=BTC, bars=BarSpec.of("15m")).key == "kraken:BTC/USD@15m"
    assert StreamSpec(instrument=BTC).key == "kraken:BTC/USD@points"


def test_larger_bars_can_be_built_only_from_finer_bars_that_tile_them() -> None:
    hour, four = BarSpec.of("1h"), BarSpec.of("4h")
    assert four.can_be_built_from(hour)
    assert not hour.can_be_built_from(four)
    assert not BarSpec.of("1h").can_be_built_from(BarSpec.of("40m"))
    assert not four.can_be_built_from(BarSpec.of("1h", anchor=Timestamp(30 * NANOS_PER_MINUTE)))


def test_shifted_bars_tile_when_the_anchors_differ_by_whole_finer_bars() -> None:
    """Edges line up when the anchors are a whole number of finer bars apart, not only equal."""
    minutes = [Timestamp(m * NANOS_PER_MINUTE) for m in (15, 75, 45)]
    hour_from_quarter = BarSpec.of("1h", anchor=minutes[0])
    assert BarSpec.of("4h", anchor=minutes[0]).can_be_built_from(hour_from_quarter)
    assert BarSpec.of("4h", anchor=minutes[1]).can_be_built_from(hour_from_quarter)
    assert not BarSpec.of("4h", anchor=minutes[2]).can_be_built_from(hour_from_quarter)


def test_both_gap_policies_are_accepted() -> None:
    assert BarSpec(size=10, gaps="skip").gaps == "skip"
    assert BarSpec(size=10, gaps="carry").gaps == "carry"


@pytest.mark.parametrize(
    ("fields", "reason"),
    [
        ({"size": 0}, "size must be positive"),
        ({"size": 10, "grace": 10}, "grace"),
        ({"size": 10, "grace": -1}, "grace"),
        ({"size": 10, "gaps": "fill"}, "gaps"),
        ({"size": 10, "price": " "}, "must name a value"),
        ({"size": 10, "volume": ""}, "must name a value"),
        ({"size": True}, "must be an int"),
        ({"size": 1.5}, "must be an int"),
        ({"size": 10, "anchor": 2.5}, "must be an int"),
    ],
)
def test_impossible_specs_are_refused(fields: dict[str, Any], reason: str) -> None:
    with pytest.raises(InvalidValueError, match=reason):
        BarSpec(**fields)


def test_identity_tells_apart_specs_that_share_a_label() -> None:
    plain = StreamSpec(instrument=BTC, bars=BarSpec.of("1m"))
    patient = StreamSpec(instrument=BTC, bars=BarSpec.of("1m", grace=5 * 10**9))
    assert plain.key == patient.key
    assert plain.identity != patient.identity


def test_bars_of_different_values_cannot_be_built_from_each_other() -> None:
    assert not BarSpec.of("4h").can_be_built_from(BarSpec.of("1h", price="bid"))
    assert BarSpec.of("4h").can_be_built_from(BarSpec.of("1h", grace=60 * 10**9))
