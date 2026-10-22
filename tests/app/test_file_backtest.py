# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""From a run configuration to stamped messages: a mount on a CSV file, end to end."""

from pathlib import Path

from heliograph.app.bridge import HistoryFeed, SourceLoop
from heliograph.app.config import parse_config
from heliograph.app.mounts import build_mounts
from heliograph.core.messages import Point
from heliograph.core.time import NANOS_PER_MINUTE, from_iso
from heliograph.plugins.registry import Registry
from tests.support.simulated import SimulatedTime

MIN = NANOS_PER_MINUTE


def test_a_backtest_mount_on_a_file_delivers_its_trades_as_they_would_have_been_known(
    tmp_path: Path,
) -> None:
    """Installed entry points, the mount checks, the throttle, the file and the bridge."""
    csv = tmp_path / "btc.csv"
    csv.write_text(
        "time,price,size\n"
        "2026-10-01T00:00:00Z,100,1\n"
        "2026-10-01T00:01:00Z,101,2\n"
        "2026-10-01T00:02:00Z,102,3\n"
    )
    config = parse_config(f"""
[mounts.btc]
source = "file"
params = {{ path = {str(csv)!r}, live_delay = "15m" }}
kind = "trades"
start = 2026-10-01T00:01:00Z
end = 2026-10-02T00:00:00Z

[mounts.btc.instrument]
symbol = "BTC/USD"
venue = "kraken"
asset_class = "crypto"
quote_currency = "USD"
""").config
    registry = Registry()
    registry.discover("data_sources")
    time = SimulatedTime(start=from_iso("2026-10-21T00:00:00Z"))
    (mount,) = build_mounts(config, registry, time.clock, time.advance)
    assert mount.request is not None

    with SourceLoop() as loop:
        got = list(HistoryFeed(loop, mount.source, mount.request))

    start = from_iso("2026-10-01T00:00:00Z")
    points = [p for p in got if isinstance(p, Point)]
    assert points == got
    assert [(p.event_time - start, p.ts - start) for p in points] == [
        (1 * MIN, 16 * MIN),
        (2 * MIN, 17 * MIN),
    ]
    assert [dict(p.values) for p in points] == [
        {"price": 101.0, "size": 2.0},
        {"price": 102.0, "size": 3.0},
    ]
