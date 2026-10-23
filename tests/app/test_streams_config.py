# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Streams in the run configuration: bars of a size from a mount's trades or bars."""

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
        (['mount = "daily"\nbars = "36h"'], "not a whole number of them"),
        (['mount = "daily"\nbars = "12h"'], "not a whole number of them"),
        (['mount = "daily"\nbars = "1d"\ngrace = "1m"'], "they take no grace or gaps"),
        (['mount = "daily"\nbars = "1d"\ngaps = "carry"'], "they take no grace or gaps"),
        (['mount = "daily"\nbars = "7d"\ngaps = "carry"'], "bars built from bars skip gaps"),
        (['mount = "daily"\nbars = "7d"\nprice = "mid"'], "use the bars' own"),
        (['mount = "daily"\nbars = "7d"\nvolume = ""'], "use the bars' own"),
        (
            ['mount = "btc"\nbars = "1h"', 'mount = "btc"\nbars = "1h"'],
            "mounts.btc already has 1h bars",
        ),
        (['mount = "btc"\nbars = "1 hour"'], "is not a span"),
        (['mount = "btc"\nbars = "1h"\ngrace = "1h"'], "grace must be"),
        (['mount = "btc"\nbars = "1h"\ngaps = "fill"'], "gaps"),
    ],
)
def test_a_stream_that_cannot_be_built_is_refused_when_read(tables: list[str], reason: str) -> None:
    with pytest.raises(ConfigError, match=reason):
        parse_config(streams(*tables))


def test_two_streams_of_one_size_on_one_mount_are_refused_even_with_different_grace() -> None:
    """Bars say their instrument and size, not their grace: such streams could not be
    told apart by what reads them."""
    text = streams('mount = "btc"\nbars = "1h"', 'mount = "btc"\nbars = "1h"\ngrace = "1m"')
    with pytest.raises(ConfigError, match=r"mounts\.btc already has 1h bars"):
        parse_config(text)


def a_week_of_days(text: str) -> str:
    """The daily mount over a week, so 7d bars (from 1970-01-01, a Thursday) fit it whole."""
    at = text.index("[mounts.daily]")
    return text[:at] + text[at:].replace("end = 2026-10-02", "end = 2026-10-08", 1)


def test_a_stream_on_bars_is_the_bars_themselves_or_built_from_them() -> None:
    config = parse_config(
        a_week_of_days(
            streams('mount = "daily"\nbars = "1d"', 'mount = "daily"\nbars = "7d"\ngrace = "1h"')
        )
    ).config
    same, weekly = config.streams
    daily = config.mounts["daily"]
    assert same.built_from(daily) is None
    assert weekly.built_from(daily) == 24 * NANOS_PER_HOUR


@pytest.mark.parametrize(
    ("edge", "mount"), [("start", "btc"), ("end", "btc"), ("start", "daily"), ("end", "daily")]
)
def test_a_mount_starting_or_ending_inside_a_bar_built_from_it_is_refused(
    edge: str, mount: str
) -> None:
    """A bar built from part of its interval would be published as whole."""
    bars = {"btc": "1h", "daily": "2d"}[mount]
    text = streams(f'mount = "{mount}"\nbars = "{bars}"')
    at = text.index(f"[mounts.{mount}]")
    if mount == "btc":
        moment = "2026-10-01T00:00:00Z" if edge == "start" else "2026-10-02T00:00:00Z"
        later = moment.replace("T00:00", "T00:40")
        text = text[:at] + text[at:].replace(f"{edge} = {moment}", f"{edge} = {later}", 1)
    elif edge == "end":
        # Days since 1970: 2026-09-30 is even (a 2-day bar starts there), 10-03 odd.
        text = text[:at] + text[at:].replace("start = 2026-10-01", "start = 2026-09-30", 1)
        text = text[:at] + text[at:].replace("end = 2026-10-02", "end = 2026-10-03", 1)
    # (start, daily: 2026-10-01 is odd, inside a 2-day bar; 10-02 even, on a boundary.)
    with pytest.raises(ConfigError, match=f"mounts.{mount}'s {edge} falls inside a {bars} bar"):
        parse_config(text)


def test_a_built_stream_whose_grace_would_make_bars_wait_on_each_other_is_refused() -> None:
    text = a_week_of_days(streams('mount = "daily"\nbars = "7d"\ngrace = "6d"'))
    with pytest.raises(ConfigError, match=r"grace must be shorter than the bar less one"):
        parse_config(text)


def test_streams_that_close_together_may_be_read_together() -> None:
    """Hours built from minutes close on their last minute, whatever their grace: they and
    the minutes as they come close at the same time, so a node may read both."""
    text = """
[mounts.btc]
source = "file"
kind = "bars"
bar_size = "1m"
start = 2026-10-01T00:00:00Z
end = 2026-10-02T00:00:00Z
[mounts.btc.instrument]
symbol = "BTC/USD"
venue = "kraken"
asset_class = "crypto"
quote_currency = "USD"

[[streams]]
mount = "btc"
bars = "1m"

[[streams]]
mount = "btc"
bars = "1h"
grace = "5m"

[[components]]
kind = "indicators"
plugin = "sma"
name = "both"
inputs = ["btc@1m.close", "btc@1h.close"]
"""
    config = parse_config(text).config
    assert config.misaligned({}) == []
    assert config.misaligned({"btc": 60_000_000_000}) == []  # one delay moves both


def test_mounts_known_after_different_delays_cannot_be_read_together() -> None:
    text = (
        streams('mount = "btc"\nbars = "1h"', 'mount = "daily"\nbars = "1d"')
        + """
[[components]]
kind = "indicators"
plugin = "sma"
name = "spread"
inputs = ["btc@1h.close", "daily@1d.close"]
"""
    )
    config = parse_config(text).config
    assert config.misaligned({"btc": 0, "daily": 0}) == []
    (problem,) = config.misaligned({"btc": 0, "daily": 300_000_000_000})
    assert "btc@1h 0s, daily@1d 5m" in problem


def test_a_mounts_own_bars_may_start_where_its_exchange_starts_them() -> None:
    """Weekly candles start on Mondays; 7-day bars from 1970 on Thursdays. Bars used as
    they come are whole whatever the grid, so only bars built here must fit one."""
    text = streams('mount = "daily"\nbars = "1d"')
    at = text.index("[mounts.daily]")
    text = text[:at] + text[at:].replace('bar_size = "1d"', 'bar_size = "7d"', 1)
    text = text.replace('mount = "daily"\nbars = "1d"', 'mount = "daily"\nbars = "7d"')
    text = text[:at] + text[at:].replace("start = 2026-10-01", "start = 2026-09-28", 1)
    text = text[:at] + text[at:].replace("end = 2026-10-02", "end = 2026-10-12", 1)
    assert parse_config(text).config.streams[0].bars == "7d"


def test_mounts_of_different_kinds_are_compared_only_once_their_delays_are_known() -> None:
    """Trades with a grace of 1m and bars as they come: whether they close together depends
    on the bars' source's delay, which the configuration does not know."""
    text = streams('mount = "btc"\nbars = "1h"\ngrace = "1m"', 'mount = "daily"\nbars = "1d"')
    text += """
[[components]]
kind = "indicators"
plugin = "atr"
name = "mixed"
inputs = ["btc@1h.high", "btc@1h.low", "daily@1d.close"]
"""
    config = parse_config(text).config  # not refused here
    assert config.misaligned({"btc": 0, "daily": 60_000_000_000}) == []
    assert config.misaligned({"btc": 0, "daily": 0}) != []
