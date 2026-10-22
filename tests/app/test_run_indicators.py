# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Indicators in a run from configuration: computed, journalled, recorded, replayed."""

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from heliograph.app.config import ConfigError, parse_config
from heliograph.app.run import JOURNALLED, replay_backtest, run_backtest
from heliograph.core.errors import InvalidValueError
from heliograph.core.graph import IndicatorValues
from heliograph.core.time import NANOS_PER_HOUR, from_iso
from heliograph.plugins.indicators import PeriodParams, Sma
from heliograph.plugins.manifest import Manifest, NoParams, Plugin, param
from heliograph.plugins.registry import Registry
from heliograph.plugins.sqlite_journal import raw_rows, read_entries, read_header
from tests.support.simulated import SimulatedTime

NOW = from_iso("2026-10-21T00:00:00Z")
START = from_iso("2026-10-01T00:00:00Z")
HOUR = NANOS_PER_HOUR


class NotAnIndicator(Plugin[NoParams]):
    manifest = Manifest(kind="indicators", name="nothing", version="1", summary="No update")


@pytest.fixture
def registry() -> Registry:
    registry = Registry()
    registry.discover("data_sources")
    registry.discover("indicators")
    registry.register(NotAnIndicator)
    return registry


def csv(tmp_path: Path) -> Path:
    """One trade an hour for eight hours, closing at 100, 101, ... 107."""
    path = tmp_path / "btc.csv"
    path.write_text(
        "time,price,size\n" + "".join(f"2026-10-01T{h:02d}:30:00Z,{100 + h},1\n" for h in range(8))
    )
    return path


def config(path: Path, components: str) -> str:
    return f"""
[mounts.btc]
source = "file"
params = {{ path = {str(path)!r} }}
kind = "trades"
start = 2026-10-01T00:00:00Z
end = 2026-10-01T08:00:00Z

[mounts.btc.instrument]
symbol = "BTC/USD"
venue = "kraken"
asset_class = "crypto"
quote_currency = "USD"

[[streams]]
mount = "btc"
bars = "1h"

[[streams]]
mount = "btc"
bars = "4h"
{components}"""


INDICATORS = """
[[components]]
kind = "indicators"
plugin = "sma"
name = "sma2"
inputs = ["btc@1h.close"]
params = { period = 2 }

[[components]]
kind = "indicators"
plugin = "ema"
name = "ema_of_sma"
inputs = ["sma2"]
params = { period = 2 }

[[components]]
kind = "indicators"
plugin = "sma"
name = "slow"
inputs = ["btc@4h.close"]
params = { period = 2 }
"""


def run(text: str, registry: Registry, journal: Path) -> Any:
    time = SimulatedTime(start=NOW)
    return run_backtest(parse_config(text), registry, journal, time.clock, time.advance)


def journalled_values(journal: Path) -> dict[int, dict[str, float]]:
    return {
        (e.message.ts - START) // HOUR: dict(e.message.values)
        for e in read_entries(journal, JOURNALLED)
        if isinstance(e.message, IndicatorValues)
    }


def test_a_run_computes_its_indicators_and_journals_their_values(
    tmp_path: Path, registry: Registry
) -> None:
    journal = tmp_path / "run.sqlite"
    run(config(csv(tmp_path), INDICATORS), registry, journal)
    values = journalled_values(journal)
    # sma2 of the 1h closes (100 at 01:00, 101 at 02:00, ...): 100.5 from 02:00. Its ema2
    # starts at 03:00 from (100.5 + 101.5) / 2 = 101, then weighs 2/3. The 4h close is
    # 103 at 04:00 and 107 at 08:00, so "slow", their mean of two, starts at 08:00.
    assert values[2] == {"sma2": 100.5}
    assert values[3] == {"sma2": 101.5, "ema_of_sma": 101.0}
    assert values[4] == {"sma2": 102.5, "ema_of_sma": pytest.approx(102.0)}
    assert values[5] == {"sma2": 103.5, "ema_of_sma": pytest.approx(103.0)}
    assert values[8]["slow"] == 105.0
    assert sorted(values) == [2, 3, 4, 5, 6, 7, 8]


def test_the_header_records_each_indicator_its_version_parameters_and_inputs(
    tmp_path: Path, registry: Registry
) -> None:
    journal = tmp_path / "run.sqlite"
    run(config(csv(tmp_path), INDICATORS), registry, journal)
    components = json.loads(read_header(journal)["components"])
    assert components["ema_of_sma"] == {
        "plugin": "ema",
        "version": "1",
        "params": {"period": 2},
        "inputs": ["sma2"],
    }
    assert list(components) == ["ema_of_sma", "slow", "sma2"]


def test_a_run_with_indicators_replays_byte_for_byte(tmp_path: Path, registry: Registry) -> None:
    original = tmp_path / "run.sqlite"
    run(config(csv(tmp_path), INDICATORS), registry, original)
    replayed = replay_backtest(original, tmp_path / "replay.sqlite", registry)
    assert raw_rows(replayed.journal) == raw_rows(original)


def test_a_replay_refuses_an_indicator_of_another_version_than_the_run_used(
    tmp_path: Path, registry: Registry
) -> None:
    original = tmp_path / "run.sqlite"
    run(config(csv(tmp_path), INDICATORS), registry, original)

    class NewerSma(Sma):
        manifest = Manifest(
            kind="indicators", name="sma", version="2", summary="Changed", params=PeriodParams
        )

    upgraded = Registry()
    upgraded.discover("data_sources")
    upgraded.register(NewerSma)
    upgraded.discover("indicators", entry_points=lambda group: [])
    upgraded.register(registry.get("indicators", "ema"))
    with pytest.raises(InvalidValueError, match="sma2 ran sma version 1; version 2 is installed"):
        replay_backtest(original, tmp_path / "replay.sqlite", upgraded)


def indicator(
    plugin: str, inputs: list[str], name: str | None = "a", period: int | None = None
) -> str:
    """One indicator component, as TOML."""
    named = "" if name is None else f"name = {json.dumps(name)}\n"
    reads = f"inputs = {json.dumps(inputs)}\n" if inputs else ""
    reads += "" if period is None else f"params = {{ period = {period} }}\n"
    return f'[[components]]\nkind = "indicators"\nplugin = "{plugin}"\n{named}{reads}'


@pytest.mark.parametrize(
    ("components", "reason"),
    [
        (indicator("sma", ["btc@15m.close"]), "reads 'btc@15m.close', which is neither"),
        (
            indicator("sma", ["b"]) + indicator("sma", ["btc@1h.close"], name="b"),
            "reads 'b', which is neither an indicator defined before it",
        ),
        (indicator("sma", ["btc@1h.close"], name=None), "an indicator needs a name"),
        (indicator("sma", []), "an indicator needs inputs"),
        (indicator("sma", ["btc@1h.close"], name="btc@1h.close"), "is taken, or not letters"),
        (indicator("sma", ["btc@1h.vwap"]), "reads 'btc@1h.vwap'"),
    ],
)
def test_an_indicator_reading_what_does_not_exist_is_refused_when_read(
    tmp_path: Path, components: str, reason: str
) -> None:
    with pytest.raises(ConfigError, match=reason):
        parse_config(config(tmp_path / "btc.csv", components))


@pytest.mark.parametrize(
    ("components", "reason"),
    [
        (
            indicator("sma", ["btc@1h.close", "btc@4h.close"]),
            r"components\[0\] \(a\): reads 2 inputs; sma takes 1",
        ),
        (indicator("nothing", ["btc@1h.close"]), "nothing is not an indicator"),
        (indicator("nowhere", ["btc@1h.close"]), "nowhere"),
    ],
)
def test_an_indicator_that_cannot_run_is_refused_before_anything_is_written(
    tmp_path: Path, registry: Registry, components: str, reason: str
) -> None:
    journal = tmp_path / "run.sqlite"
    with pytest.raises(ConfigError, match=reason):
        run(config(csv(tmp_path), components), registry, journal)
    assert not journal.exists()


# -- found by review -------------------------------------------------------------------


def test_two_spellings_of_one_stream_field_read_the_same_input(
    tmp_path: Path, registry: Registry
) -> None:
    components = indicator("sma", ["btc@1h.close"], name="a", period=2) + indicator(
        "sma", ["btc@60m.close"], name="b", period=2
    )
    journal = tmp_path / "run.sqlite"
    run(config(csv(tmp_path), components), registry, journal)
    values = journalled_values(journal)
    assert values[2] == {"a": 100.5, "b": 100.5}


def test_an_indicator_reading_streams_of_different_grace_is_refused(tmp_path: Path) -> None:
    text = config(tmp_path / "btc.csv", indicator("ema", ["btc@1h.close"], name="x"))
    text = text.replace('bars = "4h"', 'bars = "4h"\ngrace = "1m"')
    text += indicator("sma", ["x", "btc@4h.close"], name="mixed")
    with pytest.raises(ConfigError, match="reads streams of different grace"):
        parse_config(text)


DOTTED_MOUNT = ("[mounts.btc", '[mounts."btc.usd"', -1)  # the mount and its instrument
FILTER = '[[components]]\nkind = "filters"\nplugin = "sma"\nname = "f"\n'
NAMED_FILTER = ("[[streams]]", FILTER + "[[streams]]", 1)
SPACED_NAME = ("[[streams]]", indicator("sma", ["btc@1h.close"], name="a b") + "[[streams]]", 1)


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (DOTTED_MOUNT, r"mount names \['btc.usd'\] must be"),
        (NAMED_FILTER, "only indicators take a name and inputs"),
        (SPACED_NAME, "'a b' is taken, or not"),
    ],
)
def test_names_that_cannot_be_read_back_are_refused(
    tmp_path: Path, change: tuple[str, str, int], reason: str
) -> None:
    text = config(tmp_path / "btc.csv", "").replace(*change)
    with pytest.raises(ConfigError, match=reason):
        parse_config(text)


def test_a_replay_refuses_an_indicator_whose_defaults_have_changed(
    tmp_path: Path, registry: Registry
) -> None:
    """Same plug-in, same version, a different default: the values would differ."""
    text = config(csv(tmp_path), indicator("sma", ["btc@1h.close"], name="avg"))
    original = tmp_path / "run.sqlite"
    run(text, registry, original)

    class ThreeParams(PeriodParams):
        period: int = param(3, description="Samples averaged", ge=2)

    class Sma3(Sma):
        manifest = Manifest(
            kind="indicators", name="sma", version="1", summary="Default 3", params=ThreeParams
        )

        def __init__(self, params: ThreeParams) -> None:
            super().__init__(params)

    changed = Registry()
    changed.discover("data_sources")
    changed.register(Sma3)
    with pytest.raises(InvalidValueError, match=r"avg ran with parameters \{'period': 20\}"):
        replay_backtest(original, tmp_path / "replay.sqlite", changed)


def test_a_journal_written_before_indicators_ran_still_replays(
    tmp_path: Path, registry: Registry
) -> None:
    original = tmp_path / "run.sqlite"
    run(config(csv(tmp_path), ""), registry, original)
    db = sqlite3.connect(original)
    with db:
        db.execute("DELETE FROM header WHERE key = 'components'")
    db.close()
    replayed = replay_backtest(original, tmp_path / "replay.sqlite", registry)
    assert raw_rows(replayed.journal) == raw_rows(original)
