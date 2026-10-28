# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The Wizard's model: a configuration seen as a graph, and edited as one."""

import tomllib
from datetime import UTC, datetime
from typing import Any

import pytest

from heliograph.gui.wizard_model import COLUMN, ROW, Rules, Wizard, instant
from tests.support.pipeline import PIPELINE, trend

RULES = Rules.of(PIPELINE)
STREAM = "stream:btc@1h"


def wizard(config: dict[str, Any] | None = None) -> Wizard:
    return Wizard(trend() if config is None else config, RULES)


def edges(w: Wizard) -> set[tuple[str, str, str]]:
    return {(e.source, e.target, e.role) for e in w.edges()}


def test_the_rules_come_from_what_the_engine_says() -> None:
    assert RULES.role_of("sizers") == ("sizer", False)
    assert RULES.role_of("exits") == ("exits", True)
    assert RULES.role_of("indicators") is None
    assert RULES.takes_inputs("indicators")  # a graph kind reads
    assert RULES.takes_inputs("exits")  # optionally
    assert not RULES.takes_inputs("sizers")
    assert not RULES.takes_inputs("sources")  # a kind the engine does not list


def test_the_example_is_drawn_as_streams_components_and_a_strategy() -> None:
    w = wizard()
    kinds = {n.id: n.type for n in w.nodes()}
    assert kinds[STREAM] == "stream"
    assert kinds["component:ewmac16"] == "component"
    assert kinds["strategy:trend"] == "strategy"
    assert len(kinds) == 1 + 8 + 1


def test_inputs_and_stages_and_the_price_are_the_edges() -> None:
    got = edges(wizard())
    assert (STREAM, "component:ewmac16", "input") in got
    assert (STREAM, "component:atr24", "input") in got
    assert ("component:atr24", "component:stop", "input") in got
    assert ("component:ewmac32", "strategy:trend", "forecasters") in got
    assert ("component:blend", "strategy:trend", "combiner") in got
    assert ("component:vol", "strategy:trend", "sizer") in got
    assert ("component:stop", "strategy:trend", "exits") in got
    assert ("component:limits", "strategy:trend", "risk_gates") in got
    assert ("component:costs", "strategy:trend", "fills") in got
    assert (STREAM, "strategy:trend", "price") in got
    assert len(wizard().edges()) == 6 + 1 + 7  # inputs, the price, the stages


def test_an_edge_to_a_stream_is_labelled_with_the_bar_field_it_reads() -> None:
    labels = {e.id: e.label for e in wizard().edges()}
    assert labels["input:component:atr24:btc@1h.high"] == "high"
    assert labels["price:strategy:trend"] == "close"
    assert labels["forecasters:strategy:trend:ewmac16"] == ""  # a stage's kind is on its node


def test_the_layout_puts_readers_after_what_they_read() -> None:
    nodes = {n.id: n for n in wizard().nodes()}
    assert nodes[STREAM].x == 0
    assert nodes["component:ewmac16"].x == COLUMN
    assert nodes["component:atr24"].x == COLUMN
    stage = nodes["component:vol"].x
    assert stage > nodes["component:atr24"].x
    assert nodes["component:costs"].x == stage
    assert nodes["strategy:trend"].x == stage + COLUMN
    rows = [n.y for n in nodes.values() if n.x == stage]
    assert rows == sorted(rows)
    assert len(set(rows)) == len(rows)
    assert rows[1] - rows[0] == ROW


def test_a_node_that_reads_a_node_sits_one_column_further() -> None:
    w = wizard()
    w.add_component("indicators", "sma")
    assert w.connect("component:atr24", "component:sma", "close") == []
    nodes = {n.id: n for n in w.nodes()}
    assert nodes["component:sma"].x == 2 * COLUMN
    assert nodes["component:blend"].x == 3 * COLUMN


def test_the_flow_is_plain_data_for_the_front_end() -> None:
    flow = wizard().flow()
    node = next(n for n in flow["nodes"] if n["id"] == "component:ewmac16")
    assert node == {
        "id": "component:ewmac16",
        "type": "component",
        "label": "ewmac16",
        "sub": "forecasters · ewmac",
        "x": COLUMN,
        "y": node["y"],
    }
    edge = next(e for e in flow["edges"] if e["id"] == "forecasters:strategy:trend:ewmac16")
    assert (edge["source"], edge["target"], edge["role"]) == (
        "component:ewmac16",
        "strategy:trend",
        "forecasters",
    )


def test_what_the_picture_does_not_show_is_carried_through() -> None:
    config = trend()
    w = wizard(config)
    w.add_component("forecasters", "ewmac")
    out = tomllib.loads(w.text())
    assert out["mode"] == config["mode"]
    assert out["mounts"] == config["mounts"]
    assert out["streams"] == config["streams"]
    assert out["strategies"] == config["strategies"]
    assert out["components"][: len(config["components"])] == config["components"]
    assert config == trend()  # the original was not touched


def test_an_unchanged_configuration_is_written_back_as_the_same_configuration() -> None:
    assert tomllib.loads(wizard().text()) == trend()


def test_components_are_written_so_that_what_is_read_comes_first() -> None:
    config = trend()
    config["components"] = list(reversed(config["components"]))
    names = [c["name"] for c in tomllib.loads(wizard(config).text())["components"]]
    assert names.index("atr24") < names.index("stop")


def test_a_cycle_is_left_in_place_for_the_engine_to_name() -> None:
    config = trend()
    by_name = {c["name"]: c for c in config["components"]}
    by_name["ewmac16"]["inputs"] = ["ewmac32"]
    by_name["ewmac32"]["inputs"] = ["ewmac16"]
    names = [c["name"] for c in tomllib.loads(wizard(config).text())["components"]]
    assert sorted(names) == sorted(by_name)


def test_a_reference_to_nothing_is_kept_though_not_drawn() -> None:
    config = trend()
    config["components"][0]["inputs"] = ["eth@1h.close", "ghost"]
    w = wizard(config)
    assert not [e for e in w.edges() if e.target == "component:ewmac16"]
    assert tomllib.loads(w.text())["components"][0]["inputs"] == ["eth@1h.close", "ghost"]


def test_a_new_component_is_named_after_its_plugin_and_never_twice() -> None:
    w = wizard()
    first = w.add_component("forecasters", "breakout", {"lookback": 20})
    second = w.add_component("forecasters", "breakout")
    assert (first, second) == ("component:breakout", "component:breakout2")
    assert w.params("breakout") == {"lookback": 20}
    assert w.kind_of("breakout2") == "forecasters"
    assert w.plugin_of("breakout") == "breakout"
    added = w.config["components"][-1]
    assert added["inputs"] == []  # a node reads, so it has the key


def test_a_stage_that_reads_nothing_is_added_without_inputs() -> None:
    w = wizard()
    w.add_component("sizers", "fixed")
    assert "inputs" not in w.config["components"][-1]


def test_a_name_is_not_taken_by_a_strategy_either() -> None:
    w = wizard()
    assert w.add_component("forecasters", "trend") == "component:trend2"


def test_parameters_are_set_and_read_back_as_copies() -> None:
    w = wizard()
    w.set_params("ewmac16", {"fast": 8, "slow": 32})
    got = w.params("ewmac16")
    assert got == {"fast": 8, "slow": 32}
    got["fast"] = 1
    assert w.params("ewmac16")["fast"] == 8
    w.set_params("nothing", {"a": 1})
    assert w.params("nothing") == {}
    assert w.kind_of("nothing") is None
    assert w.plugin_of("nothing") is None


def test_a_new_strategy_decides_at_the_first_streams_close() -> None:
    w = wizard()
    assert w.add_strategy("second") == "strategy:second"
    assert w.config["strategies"][-1] == {"name": "second", "price": "btc@1h.close"}
    assert (STREAM, "strategy:second", "price") in edges(w)
    assert w.add_strategy("trend") == "strategy:trend2"
    bare = Wizard({"mode": "live"}, RULES)
    bare.add_strategy("s")
    assert bare.config["strategies"] == [{"name": "s"}]


def test_a_stream_is_read_by_a_field_and_a_node_by_its_name() -> None:
    w = wizard()
    w.add_component("forecasters", "breakout")
    assert w.connect(STREAM, "component:breakout", "high") == []
    assert w.connect("component:ewmac16", "component:breakout") == []
    assert w.config["components"][-1]["inputs"] == ["btc@1h.high", "ewmac16"]
    assert (STREAM, "component:breakout", "input") in edges(w)
    assert ("component:ewmac16", "component:breakout", "input") in edges(w)


def test_a_bar_field_must_be_one_a_bar_has() -> None:
    w = wizard()
    w.add_component("forecasters", "breakout")
    assert "not 'vwap'" in w.connect(STREAM, "component:breakout", "vwap")[0]
    assert w.config["components"][-1]["inputs"] == []


def test_the_same_thing_is_not_read_twice() -> None:
    w = wizard()
    assert "already reads btc@1h.close" in w.connect(STREAM, "component:ewmac16")[0]


def test_a_stage_that_reads_nothing_cannot_be_given_inputs() -> None:
    assert "vol reads no inputs" in wizard().connect(STREAM, "component:vol")[0]
    assert "nothing reads no inputs" in wizard().connect(STREAM, "component:nothing")[0]


def test_only_a_node_can_be_read_by_another() -> None:
    w = wizard()
    assert "only an indicator" in w.connect("component:vol", "component:stop")[0]
    assert "only an indicator" in w.connect("component:nothing", "component:stop")[0]


def test_a_node_cannot_read_what_reads_it_or_itself() -> None:
    w = wizard()
    w.add_component("indicators", "sma")
    assert w.connect("component:atr24", "component:sma", "close") == []
    assert "cannot read it back" in w.connect("component:sma", "component:atr24")[0]
    assert "cannot read it back" in w.connect("component:sma", "component:sma")[0]
    assert "inputs" in w.config["components"][1]
    assert w._named("atr24")["inputs"] == ["btc@1h.high", "btc@1h.low", "btc@1h.close"]  # type: ignore[index]


def test_a_chain_that_loops_back_is_a_cycle_too() -> None:
    w = wizard()
    a = w.add_component("indicators", "sma")
    b = w.add_component("indicators", "ema")
    assert w.connect(a, b) == []
    assert w.connect(b, "component:sma")
    assert w.connect("component:sma", "component:ema") != []  # already read


def test_streams_and_strategies_cannot_be_the_wrong_end_of_an_edge() -> None:
    w = wizard()
    assert w.connect("strategy:trend", "component:stop") == ["a strategy feeds nothing"]
    assert w.connect(STREAM, STREAM) == ["a stream reads nothing"]
    assert w.connect(STREAM, "strategy:nothing")[0].startswith("there is no strategy")


def test_a_list_stage_is_appended_and_a_single_stage_is_replaced() -> None:
    w = wizard()
    other = w.add_component("forecasters", "breakout")
    assert w.connect(other, "strategy:trend") == []
    assert w.config["strategies"][0]["forecasters"] == ["ewmac16", "ewmac32", "breakout"]
    sizer = w.add_component("sizers", "fixed")
    assert w.connect(sizer, "strategy:trend") == []
    assert w.config["strategies"][0]["sizer"] == "fixed"
    assert ("component:vol", "strategy:trend", "sizer") not in edges(w)


def test_a_stage_already_in_the_strategy_is_not_added_again() -> None:
    assert "already uses ewmac16" in wizard().connect("component:ewmac16", "strategy:trend")[0]


def test_a_stage_serves_one_strategy() -> None:
    w = wizard()
    second = w.add_strategy("second")
    assert "already strategy trend's" in w.connect("component:vol", second)[0]
    assert "sizer" not in w.config["strategies"][1]


def test_an_indicator_is_not_a_stage() -> None:
    got = wizard().connect("component:atr24", "strategy:trend")
    assert got == ["atr24 is not a stage of a strategy"]


def test_the_price_is_set_by_a_stream_and_a_field() -> None:
    w = wizard()
    assert w.connect(STREAM, "strategy:trend", "open") == []
    assert w.config["strategies"][0]["price"] == "btc@1h.open"


def test_removing_a_component_removes_every_edge_to_or_from_it() -> None:
    w = wizard()
    assert w.remove("component:atr24") == []
    assert w._named("atr24") is None
    assert w._named("stop")["inputs"] == []  # type: ignore[index]
    assert not [e for e in w.edges() if "atr24" in e.id]
    assert w.remove("component:ewmac16") == []
    assert w.config["strategies"][0]["forecasters"] == ["ewmac32"]
    assert w.remove("component:vol") == []
    assert "sizer" not in w.config["strategies"][0]
    assert w.config["strategies"][0]["fills"] == "costs"
    assert w.remove("component:ewmac16")  # already gone


def test_a_strategy_is_removed_with_its_edges() -> None:
    w = wizard()
    assert w.remove("strategy:trend") == []
    assert w.config["strategies"] == []
    assert not [e for e in w.edges() if e.target == "strategy:trend"]


def test_a_stream_that_is_read_cannot_be_removed() -> None:
    w = wizard()
    got = w.remove(STREAM)
    assert "btc@1h is read by" in got[0]
    assert "ewmac16" in got[0]
    assert "trend" in got[0]  # the strategy that decides at its close
    assert len(w.config["streams"]) == 1
    assert w.remove("nonsense")


def test_a_stream_that_nothing_reads_is_removed_with_its_mount() -> None:
    w = Wizard({"mode": "backtest"}, RULES)
    assert w.add_mount("eth", "file", {}, INSTRUMENT, "trades") == []
    assert w.add_stream("eth", "1h") == []
    assert w.add_stream("eth", "4h") == []
    assert w.remove("stream:eth@1h") == []
    assert "eth" in w.config["mounts"]  # another stream is of it
    assert w.remove("stream:eth@4h") == []
    assert w.config["mounts"] == {}
    assert w.config["streams"] == []
    assert w.remove("stream:eth@4h") == ["there is no stream eth@4h"]


INSTRUMENT = {
    "symbol": "ETH/USD",
    "venue": "kraken",
    "asset_class": "crypto",
    "quote_currency": "USD",
}


def test_data_is_added_as_a_mount_the_engine_can_read_back() -> None:
    w = Wizard({"mode": "backtest"}, RULES)
    start, end = instant("2026-01-01T00:00:00Z"), instant("2026-02-01T00:00:00+00:00")
    assert w.add_mount("eth", "file", {"path": "a.csv"}, INSTRUMENT, "bars", "1h", start, end) == []
    assert w.add_stream("eth", " 1h ") == []
    out = tomllib.loads(w.text())
    assert out["mounts"]["eth"] == {
        "source": "file",
        "kind": "bars",
        "params": {"path": "a.csv"},
        "instrument": INSTRUMENT,
        "bar_size": "1h",
        "start": start,
        "end": end,
    }
    assert out["streams"] == [{"mount": "eth", "bars": "1h"}]
    stream = next(n for n in w.nodes() if n.id == "stream:eth@1h")
    assert stream.sub == "file · ETH/USD"


def test_a_stream_of_a_mount_with_no_instrument_is_described_by_its_source_or_not_at_all() -> None:
    config = {"mounts": {"m": {"source": "file"}}, "streams": [{"mount": "m", "bars": "1h"}]}
    assert next(iter(Wizard(config, RULES).nodes())).sub == "file"
    config = {"streams": [{"mount": "m", "bars": "1h"}]}
    assert next(iter(Wizard(config, RULES).nodes())).sub == "bars"


def test_data_that_is_not_whole_is_not_added() -> None:
    w = Wizard({"mode": "backtest"}, RULES)
    bad = w.add_mount("1x", "", {}, {"symbol": "A"}, "ticks")
    assert len(bad) == 4
    assert "letters, digits" in bad[0]
    assert w.add_mount("x", "file", {}, INSTRUMENT, "bars")  # bars need a size
    assert w.add_mount("x", "file", {}, INSTRUMENT, "trades", "1h")  # trades take none
    assert "mounts" not in w.config
    assert w.add_mount("x", "file", {}, INSTRUMENT, "trades") == []
    assert "already data named x" in w.add_mount("x", "file", {}, INSTRUMENT, "trades")[0]


def test_params_of_a_mount_are_copied_in() -> None:
    w = Wizard({}, RULES)
    params: dict[str, Any] = {"nested": {"a": 1}}
    w.add_mount("x", "file", params, INSTRUMENT, "trades")
    params["nested"]["a"] = 2
    assert w.config["mounts"]["x"]["params"] == {"nested": {"a": 1}}


def test_a_stream_is_added_only_for_data_that_is_there_once() -> None:
    w = Wizard({}, RULES)
    assert w.add_stream("x", "1h") == ["there is no data named x"]
    w.add_mount("x", "file", {}, INSTRUMENT, "trades")
    assert w.add_stream("x", " ") == ["give the size of the bars, such as 1h"]
    assert w.add_stream("x", "1h") == []
    assert w.add_stream("x", "1h") == ["there already are 1h bars of x"]
    assert w.config["streams"] == [{"mount": "x", "bars": "1h"}]


def test_data_and_its_bars_are_added_together_or_not_at_all() -> None:
    w = Wizard({}, RULES)
    refused = w.add_data("x", "file", {}, INSTRUMENT, "trades", None, None, None, "")
    assert refused == ["give the size of the bars, such as 1h"]
    assert w.config.get("mounts", {}) == {}
    assert w.add_data("x", "file", {}, INSTRUMENT, "trades", None, None, None, "1h") == []
    assert list(w.config["mounts"]) == ["x"]
    assert w.add_data("x", "file", {}, INSTRUMENT, "trades", None, None, None, "1h")  # a repeat
    assert w.config["streams"] == [{"mount": "x", "bars": "1h"}]


def test_a_moment_is_text_with_an_offset_or_nothing() -> None:
    assert instant("  ") is None
    assert instant("2026-01-01T00:00:00Z") == datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="needs a UTC offset"):
        instant("2026-01-01T00:00:00")
    with pytest.raises(ValueError, match="not a date and time"):
        instant("yesterday")


def test_an_input_edge_is_removed_by_its_id() -> None:
    w = wizard()
    w.disconnect("input:component:atr24:btc@1h.high")
    assert w._named("atr24")["inputs"] == ["btc@1h.low", "btc@1h.close"]  # type: ignore[index]


def test_a_stage_edge_is_removed_by_its_id() -> None:
    w = wizard()
    w.disconnect("forecasters:strategy:trend:ewmac16")
    assert w.config["strategies"][0]["forecasters"] == ["ewmac32"]
    w.disconnect("sizer:strategy:trend:vol")
    assert "sizer" not in w.config["strategies"][0]
    w.disconnect("price:strategy:trend")
    assert w.config["strategies"][0]["price"] == ""
    before = tomllib.loads(w.text())
    w.disconnect("no such edge")
    assert tomllib.loads(w.text()) == before


def test_an_edge_of_a_strategy_that_is_gone_is_ignored() -> None:
    w = wizard()
    edge = next(e.id for e in w.edges() if e.role == "fills")
    w.config["strategies"] = []
    w.disconnect(edge)  # nothing to disconnect it from
    assert w.config["strategies"] == []
