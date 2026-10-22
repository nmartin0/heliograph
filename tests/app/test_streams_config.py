# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Streams in the run configuration: bars of a size from a mount's trades."""

import pytest

from heliograph.app.config import ConfigError, RunConfig, parse_config
from heliograph.core.streams import BarSpec
from heliograph.core.time import NANOS_PER_HOUR, NANOS_PER_MINUTE

MOUNTS = """
[mounts.btc]
source = "file"
kind = "trades"
start = 2026-10-01T00:00:00Z
end = 2026-10-02T00:00:00Z
[mounts.btc.instrument]
symbol = "BTC/USD"
venue = "kraken"
asset_class = "crypto"
quote_currency = "USD"

[mounts.daily]
source = "file"
kind = "bars"
bar_size = "1d"
start = 2026-10-01T00:00:00Z
end = 2026-10-02T00:00:00Z
[mounts.daily.instrument]
symbol = "ETH/USD"
venue = "kraken"
asset_class = "crypto"
quote_currency = "USD"
"""


def streams(*tables: str) -> str:
    return MOUNTS + "".join(f"\n[[streams]]\n{table}\n" for table in tables)


def test_a_stream_names_a_mount_and_how_its_bars_are_cut() -> None:
    text = streams(
        'mount = "btc"\nbars = "1h"',
        'mount = "btc"\nbars = "4h"\ngrace = "30s"\ngaps = "carry"\nvolume = "amount"',
    )
    config = parse_config(text).config
    hourly, four_hourly = config.streams
    assert hourly.bar_spec() == BarSpec(size=NANOS_PER_HOUR)
    assert four_hourly.bar_spec() == BarSpec(
        size=4 * NANOS_PER_HOUR, grace=NANOS_PER_MINUTE // 2, gaps="carry", volume="amount"
    )
    assert RunConfig.model_validate_json(config.model_dump_json()) == config
    assert hash(config) == hash(parse_config(text).config)


@pytest.mark.parametrize(
    ("tables", "reason"),
    [
        (['mount = "nowhere"\nbars = "1h"'], "streams\\[0\\]: no mount named 'nowhere'"),
        (['mount = "daily"\nbars = "1d"'], "streams build bars from trades"),
        (['mount = "btc"\nbars = "1h"', 'mount = "btc"\nbars = "1h"'], "the same stream as"),
        (['mount = "btc"\nbars = "1 hour"'], "is not a span"),
        (['mount = "btc"\nbars = "1h"\ngrace = "1h"'], "grace must be"),
        (['mount = "btc"\nbars = "1h"\ngaps = "fill"'], "gaps"),
    ],
)
def test_a_stream_that_cannot_be_built_is_refused_when_read(tables: list[str], reason: str) -> None:
    with pytest.raises(ConfigError, match=reason):
        parse_config(streams(*tables))


def test_two_streams_of_one_size_but_different_grace_are_two_streams() -> None:
    text = streams('mount = "btc"\nbars = "1h"', 'mount = "btc"\nbars = "1h"\ngrace = "1m"')
    assert len(parse_config(text).config.streams) == 2
