# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Strategies in a run from configuration: assembled, checked, signalling, replayed."""

import json
from pathlib import Path
from typing import Any

import pytest

from heliograph.app.config import ConfigError, parse_config
from heliograph.app.run import JOURNALLED, replay_backtest, run_backtest
from heliograph.core.pipeline import Proposal, Signal, SignalRefused, signal_id
from heliograph.core.time import NANOS_PER_HOUR, from_iso
from heliograph.plugins.manifest import Manifest, NoParams, Plugin
from heliograph.plugins.registry import Registry
from heliograph.plugins.sqlite_journal import raw_rows, read_entries, read_header
from tests.support.simulated import SimulatedTime

NOW = from_iso("2026-10-21T00:00:00Z")
START = from_iso("2026-10-01T00:00:00Z")
HOUR = NANOS_PER_HOUR
# One trade an hour, zig-zagging upwards: 100, 102, 101, 103, 102, 104...
PRICES = [100 + h // 2 * 1 + (h % 2) * 2 for h in range(24)]


@pytest.fixture
def registry() -> Registry:
    registry = Registry()
    for kind in ("data_sources", "indicators", "forecasters", "gates", "combiners", "sizers"):
        registry.discover(kind)
    for kind in ("exits", "risk_gates"):
        registry.discover(kind)
    return registry


def csv(tmp_path: Path) -> Path:
    path = tmp_path / "btc.csv"
    path.write_text(
        "time,price,size\n"
        + "".join(f"2026-10-01T{h:02d}:30:00Z,{p},1\n" for h, p in enumerate(PRICES))
    )
    return path


def config(path: Path, rest: str, extra_stream: str = "") -> str:
    return f"""
[mounts.btc]
source = "file"
params = {{ path = {str(path)!r} }}
kind = "trades"
start = 2026-10-01T00:00:00Z
end = 2026-10-02T00:00:00Z

[mounts.btc.instrument]
symbol = "BTC/USD"
venue = "kraken"
asset_class = "crypto"
quote_currency = "USD"

[[streams]]
mount = "btc"
bars = "1h"
{extra_stream}
{rest}"""


def component(
    kind: str, plugin: str, name: str, inputs: list[str] | None = None, **params: Any
) -> str:
    out = f'[[components]]\nkind = "{kind}"\nplugin = "{plugin}"\nname = "{name}"\n'
    if inputs:
        out += f"inputs = {json.dumps(inputs)}\n"
    if params:
        out += (
            "params = { " + ", ".join(f"{k} = {json.dumps(v)}" for k, v in params.items()) + " }\n"
        )
    return out


def strategy(name: str = "trend", **fields: Any) -> str:
    base: dict[str, Any] = {"price": "btc@1h.close", "forecasters": ["lin"], "sizer": "vol"}
    body = "".join(f"{k} = {json.dumps(v)}\n" for k, v in {**base, **fields}.items())
    return f'[[strategies]]\nname = "{name}"\n{body}'


SIZER = component(
    "sizers", "vol_target", "vol", capital=1000.0, target=0.1, bars_per_year=100.0, vol_span=2
)
FORECASTER = component("forecasters", "linear", "lin", ["btc@1h.close"], scalar=2.0, centre=104.0)
WHOLE = (
    FORECASTER
    + component("gates", "threshold", "liquid", ["btc@1h.close"], above=102.0)
    + SIZER
    + component(
        "indicators", "atr", "atr2", ["btc@1h.high", "btc@1h.low", "btc@1h.close"], period=2
    )
    + component("exits", "trailing_stop", "stop", ["atr2"], multiple=0.5)
    + component("risk_gates", "limits", "limits", max_per_day=5)
    + strategy(gates=["liquid"], exits=["stop"], risk_gates=["limits"])
)


def run(text: str, registry: Registry, journal: Path) -> Any:
    time = SimulatedTime(start=NOW)
    return run_backtest(parse_config(text), registry, journal, time.clock, time.advance)


def decisions(journal: Path) -> list[Signal | SignalRefused]:
    return [
        e.message
        for e in read_entries(journal, JOURNALLED)
        if isinstance(e.message, Signal | SignalRefused)
    ]


def test_a_run_signals_what_its_strategy_decides_vetoes_and_stops_included(
    tmp_path: Path, registry: Registry
) -> None:
    journal = tmp_path / "run.sqlite"
    text = config(csv(tmp_path), WHOLE)
    run(text, registry, journal)
    signals = [m for m in decisions(journal) if isinstance(m, Signal)]
    assert [s.vetoed_by for s in signals if s.vetoed_by] == [("liquid",)]
    assert [s.exit for s in signals if s.exit] == ["stop"]
    assert all(s.strategy == "trend" and s.instrument.symbol == "BTC/USD" for s in signals)
    digest = parse_config(text).digest
    assert [s.id for s in signals] == [signal_id(digest, "trend", s.ts, s.target) for s in signals]
    # Each signal starts from the target of the one before it.
    held = 0.0
    for signal in signals:
        assert signal.held == held
        held = signal.target
    # A forecast of 2 x (close - 104), held to +-20, on the close each signal names.
    for signal in signals:
        assert signal.forecasts["lin"] == pytest.approx(max(-20, min(20, 2 * (signal.price - 104))))


def test_a_rate_limit_refuses_adding_risk_but_never_taking_it_off(
    tmp_path: Path, registry: Registry
) -> None:
    journal = tmp_path / "run.sqlite"
    run(
        config(csv(tmp_path), WHOLE.replace("max_per_day = 5", "max_per_day = 1")),
        registry,
        journal,
    )
    out = decisions(journal)
    kinds = [type(m).__name__ for m in out]
    assert kinds[:2] == ["Signal", "Signal"]
    assert out[1].target == 0.0  # the veto took the strategy flat: past the limit, yet sent
    assert set(kinds[2:]) == {"SignalRefused"}
    assert {m.refused_by for m in out[2:]} == {"limits"}  # type: ignore[union-attr]


def test_a_run_with_strategies_replays_byte_for_byte(tmp_path: Path, registry: Registry) -> None:
    original = tmp_path / "run.sqlite"
    run(config(csv(tmp_path), WHOLE), registry, original)
    replayed = replay_backtest(original, tmp_path / "replay.sqlite", registry)
    assert raw_rows(replayed.journal) == raw_rows(original)


def test_the_header_records_each_stage_and_each_strategy(
    tmp_path: Path, registry: Registry
) -> None:
    journal = tmp_path / "run.sqlite"
    run(config(csv(tmp_path), WHOLE), registry, journal)
    header = read_header(journal)
    components = json.loads(header["components"])
    assert components["vol"]["kind"] == "sizers"
    assert components["vol"]["params"]["buffer"] == 0.1  # defaults recorded
    assert components["stop"]["inputs"] == ["atr2"]
    (recorded,) = json.loads(header["strategies"])
    assert recorded["name"] == "trend"
    assert recorded["exits"] == ["stop"]


def test_one_forecaster_needs_no_combiner(tmp_path: Path, registry: Registry) -> None:
    journal = tmp_path / "run.sqlite"
    run(config(csv(tmp_path), FORECASTER + SIZER + strategy()), registry, journal)
    assert decisions(journal)


TWO = FORECASTER + component("forecasters", "linear", "lin2", ["btc@1h.close"]) + SIZER


@pytest.mark.parametrize(
    ("rest", "reason"),
    [
        (
            FORECASTER + SIZER + strategy(price="btc@4h.close"),
            "price 'btc@4h.close' is not a stream's",
        ),
        (
            FORECASTER + SIZER + strategy(forecasters=["vol"]),
            "'vol' is not a component of kind forecasters",
        ),
        (FORECASTER + SIZER + strategy(forecasters=[]), "needs at least one forecaster"),
        (TWO + strategy(forecasters=["lin", "lin2"]), "2 forecasters need a combiner"),
        (
            FORECASTER + SIZER + strategy() + strategy(name="again"),
            "'vol' is already strategy trend's",
        ),
        (FORECASTER + SIZER + strategy() + strategy(), "the name is taken"),
        (FORECASTER + SIZER, "no strategy uses this sizer"),
        (
            FORECASTER
            + SIZER
            + component("exits", "trailing_stop", "x", ["nowhere"])
            + strategy(exits=["x"]),
            "reads 'nowhere'; an exit reads indicators",
        ),
        (
            FORECASTER + component("sizers", "vol_target", "vol", ["lin"]) + strategy(),
            "a sizer reads no inputs; exits may",
        ),
        (FORECASTER + component("sizers", "vol_target", "") + strategy(), "a sizer needs a name"),
        (FORECASTER + SIZER + strategy(price="btc@1h.volume"), "is a volume, not a price"),
    ],
)
def test_a_strategy_that_cannot_be_assembled_is_refused_when_read(
    tmp_path: Path, rest: str, reason: str
) -> None:
    with pytest.raises(ConfigError, match=reason):
        parse_config(config(tmp_path / "btc.csv", rest))


def test_a_strategy_reading_streams_of_different_grace_is_refused(tmp_path: Path) -> None:
    rest = (
        component("forecasters", "linear", "slow", ["btc@4h.close"])
        + SIZER
        + strategy(forecasters=["slow"])
    )
    four = '[[streams]]\nmount = "btc"\nbars = "4h"\ngrace = "1m"\n'
    with pytest.raises(ConfigError, match=r"trend.*reads streams of different grace"):
        parse_config(config(tmp_path / "btc.csv", rest, four))


def test_a_combiner_weighting_other_forecasters_is_refused_before_anything_is_written(
    tmp_path: Path, registry: Registry
) -> None:
    rest = (
        TWO
        + '[[components]]\nkind = "combiners"\nplugin = "weighted"\nname = "blend"\n'
        + "params = { weights = { lin = 0.5, other = 0.5 } }\n"
        + strategy(forecasters=["lin", "lin2"], combiner="blend")
    )
    journal = tmp_path / "run.sqlite"
    with pytest.raises(ConfigError, match=r"combiner blend has weights for \['lin', 'other'\]"):
        run(config(csv(tmp_path), rest), registry, journal)
    assert not journal.exists()


class NotASizer(Plugin[NoParams]):
    manifest = Manifest(kind="sizers", name="nothing", version="1", summary="No target")


def test_a_stage_that_is_not_what_its_kind_needs_is_refused_before_anything_is_written(
    tmp_path: Path, registry: Registry
) -> None:
    registry.register(NotASizer)
    rest = FORECASTER + component("sizers", "nothing", "vol") + strategy()
    journal = tmp_path / "run.sqlite"
    with pytest.raises(ConfigError, match=r"components\[1\] \(vol\): nothing is not a sizer"):
        run(config(csv(tmp_path), rest), registry, journal)
    assert not journal.exists()


def test_an_exit_may_read_a_streams_field(tmp_path: Path, registry: Registry) -> None:
    rest = (
        FORECASTER
        + SIZER
        + component("exits", "trailing_stop", "stop", ["btc@1h.low"], multiple=1.0)
        + strategy(exits=["stop"])
    )
    journal = tmp_path / "run.sqlite"
    run(config(csv(tmp_path), rest), registry, journal)
    assert decisions(journal)


class CheckOnly(Plugin[NoParams]):
    """A risk gate that checks but is never told what was sent."""

    manifest = Manifest(kind="risk_gates", name="check_only", version="1", summary="No sent")

    def check(self, proposal: Proposal) -> str | None:
        return None


def test_a_risk_gate_that_cannot_hear_what_was_sent_is_refused(
    tmp_path: Path, registry: Registry
) -> None:
    registry.register(CheckOnly)
    rest = (
        FORECASTER
        + SIZER
        + component("risk_gates", "check_only", "gate")
        + strategy(risk_gates=["gate"])
    )
    with pytest.raises(
        ConfigError, match=r"\(gate\): check_only is not a risk gate \(it has no sent\)"
    ):
        run(config(csv(tmp_path), rest), registry, tmp_path / "run.sqlite")
