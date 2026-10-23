# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Runs on a mount of bars: its own bars, bars built from them, read and traded on."""

from pathlib import Path

import pytest

from heliograph.app.config import parse_config
from heliograph.app.run import JOURNALLED, replay_backtest, run_backtest
from heliograph.core.broker import Fill
from heliograph.core.graph import IndicatorValues
from heliograph.core.messages import Bar
from heliograph.core.time import NANOS_PER_MINUTE, from_iso
from heliograph.plugins.registry import Registry
from heliograph.plugins.sqlite_journal import raw_rows, read_entries
from tests.support.plugins import Units
from tests.support.simulated import SimulatedTime

MIN = NANOS_PER_MINUTE
START = from_iso("2026-10-01T00:00:00Z")
# Quarter-hour candles for three hours; the last hour lacks its last two quarters.
# minute, open, high, low, close, volume
QUARTERS = [
    (0, 100, 103, 99, 102, 1),
    (15, 102, 104, 101, 101, 2),
    (30, 101, 101, 97, 98, 3),
    (45, 98, 100, 98, 99, 4),
    (60, 99, 106, 99, 105, 1),
    (75, 105, 105, 104, 104, 1),
    (90, 104, 108, 103, 107, 1),
    (105, 107, 107, 106, 106, 1),
    (120, 106, 106, 100, 101, 5),
    (135, 101, 102, 100, 100, 5),
]


@pytest.fixture
def registry() -> Registry:
    registry = Registry.installed(["data_sources", "indicators", "forecasters", "fills"])
    registry.register(Units)
    return registry


def config(tmp_path: Path, rest: str = "") -> str:
    path = tmp_path / "quarters.csv"
    path.write_text(
        "time,open,high,low,close,volume\n"
        + "".join(
            f"2026-10-01T{m // 60:02d}:{m % 60:02d}:00Z,{o},{h},{lo},{c},{v}\n"
            for m, o, h, lo, c, v in QUARTERS
        )
    )
    return f"""
[mounts.btc]
source = "file"
params = {{ path = {str(path)!r}, kind = "bars", bar_size = "15m" }}
kind = "bars"
bar_size = "15m"
start = 2026-10-01T00:00:00Z
end = 2026-10-01T03:00:00Z

[mounts.btc.instrument]
symbol = "BTC/USD"
venue = "kraken"
asset_class = "crypto"
quote_currency = "USD"

[[streams]]
mount = "btc"
bars = "15m"

[[streams]]
mount = "btc"
bars = "1h"

[[components]]
kind = "indicators"
plugin = "sma"
name = "fast"
inputs = ["btc@15m.close"]
params = {{ period = 2 }}

[[components]]
kind = "indicators"
plugin = "sma"
name = "slow"
inputs = ["btc@1h.close"]
params = {{ period = 2 }}
{rest}"""


def run(text: str, registry: Registry, journal: Path) -> None:
    time = SimulatedTime(start=from_iso("2026-10-23T00:00:00Z"))
    run_backtest(parse_config(text), registry, journal, time.clock, time.advance)


def test_hours_are_built_from_quarters_when_their_last_quarter_is_known(
    tmp_path: Path, registry: Registry
) -> None:
    journal = tmp_path / "run.sqlite"
    run(config(tmp_path), registry, journal)
    bars = [e.message for e in read_entries(journal, JOURNALLED) if isinstance(e.message, Bar)]
    hours = [b for b in bars if b.end - b.start == 60 * MIN]
    assert len(bars) - len(hours) == len(QUARTERS)  # the quarters are journalled as they came
    assert [((b.ts - START) // MIN, b.open, b.high, b.low, b.close, b.volume) for b in hours] == [
        (60, 100, 104, 97, 99, 10),
        (120, 99, 108, 99, 106, 4),
        (195, 106, 106, 100, 100, 10),  # no 2:45 quarter: one quarter after its end
    ]


def test_indicators_read_the_quarters_and_the_hours(tmp_path: Path, registry: Registry) -> None:
    journal = tmp_path / "run.sqlite"
    run(config(tmp_path), registry, journal)
    values = [
        ((e.message.ts - START) // MIN, dict(e.message.values))
        for e in read_entries(journal, JOURNALLED)
        if isinstance(e.message, IndicatorValues)
    ]
    slow = {m: v["slow"] for m, v in values if "slow" in v}
    assert (slow[120], slow[195]) == ((99 + 106) / 2, (106 + 100) / 2)
    assert min(slow) == 120  # its second hour
    fast = {m: v["fast"] for m, v in values if "fast" in v}
    assert (min(fast), fast[30]) == (30, (102 + 101) / 2)


def test_a_strategy_on_built_hours_is_filled_at_the_next_hours_open(
    tmp_path: Path, registry: Registry
) -> None:
    """Forecast close - 100 held as units: -1 at 1:00 (close 99), 6 at 2:00 (close 106)."""
    rest = """
[[components]]
kind = "forecasters"
plugin = "linear"
name = "lin"
inputs = ["btc@1h.close"]
params = { scalar = 1.0, centre = 100.0 }

[[components]]
kind = "sizers"
plugin = "units"
name = "units"

[[components]]
kind = "fills"
plugin = "next_open"
name = "free"

[[strategies]]
name = "trend"
price = "btc@1h.close"
forecasters = ["lin"]
sizer = "units"
fills = "free"
"""
    journal = tmp_path / "run.sqlite"
    run(config(tmp_path, rest), registry, journal)
    fills = [e.message for e in read_entries(journal, JOURNALLED) if isinstance(e.message, Fill)]
    assert [
        ((f.ts - START) // MIN, (f.event_time - START) // MIN, f.quantity, f.price) for f in fills
    ] == [(120, 60, -1, 99), (195, 120, 7, 106)]
    replayed = replay_backtest(journal, tmp_path / "replay.sqlite", registry)
    assert raw_rows(replayed.journal) == raw_rows(journal)


def test_a_mount_known_late_still_builds_whole_hours_and_replays(
    tmp_path: Path, registry: Registry
) -> None:
    """Quarters known 15 minutes after their end: each hour still takes all its quarters."""
    text = config(tmp_path).replace('bar_size = "15m" }', 'bar_size = "15m", live_delay = "15m" }')
    journal = tmp_path / "run.sqlite"
    run(text, registry, journal)
    bars = [e.message for e in read_entries(journal, JOURNALLED) if isinstance(e.message, Bar)]
    hours = [b for b in bars if b.end - b.start == 60 * MIN]
    assert [((b.ts - START) // MIN, b.close, b.volume) for b in hours] == [
        (75, 99, 10),
        (135, 106, 4),
        (210, 100, 10),
    ]
    replayed = replay_backtest(journal, tmp_path / "replay.sqlite", registry)
    assert raw_rows(replayed.journal) == raw_rows(journal)
