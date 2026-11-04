# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""A market that never closes."""

import pytest

from heliograph.core.calendars import ALWAYS_OPEN
from heliograph.core.errors import InvalidValueError
from heliograph.core.time import NANOS_PER_HOUR, Timestamp


def test_open_time_is_time_when_a_market_never_closes() -> None:
    assert ALWAYS_OPEN.name == ""
    assert ALWAYS_OPEN.after_open_time(Timestamp(5), 3 * NANOS_PER_HOUR) == Timestamp(
        5 + 3 * NANOS_PER_HOUR
    )


def test_a_negative_span_of_open_time_is_refused() -> None:
    with pytest.raises(InvalidValueError, match="cannot be negative"):
        ALWAYS_OPEN.after_open_time(Timestamp(5), -1)
