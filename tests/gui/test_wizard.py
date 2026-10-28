# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The Wizard: a saved configuration drawn as a graph, edited, and run."""

import asyncio
import tomllib
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any

import pytest
from nicegui import ui
from nicegui.testing.user import User

from heliograph.gui import app
from heliograph.gui import wizard as wizard_module
from heliograph.gui.client import Engine
from heliograph.gui.flow import Flow
from heliograph.gui.wizard import WizardScreen
from tests.support import window
from tests.support.pipeline import PIPELINE, TREND
from tests.support.window import fake

TEXT = TREND.read_text()
EWMAC = {
    "kind": "forecasters",
    "name": "ewmac",
    "version": "1.0",
    "summary": "trend",
    "params": {
        "type": "object",
        "properties": {
            "fast": {"type": "integer", "default": 16, "title": "Fast"},
            "slow": {"type": "integer", "default": 64, "title": "Slow"},
            "scalar": {"type": "number", "default": 1.0, "title": "Scalar"},
        },
    },
}
PLUGINS = [
    {"kind": "forecasters", "name": "ewmac", "version": "1.0", "summary": "trend"},
    {"kind": "forecasters", "name": "breakout", "version": "1.0", "summary": "range"},
    {"kind": "sizers", "name": "fixed", "version": "1.0", "summary": "fixed"},
    {"kind": "data_sources", "name": "file", "version": "1.0", "summary": "a file"},
    {"kind": "data_sources", "name": "ccxt", "version": "1.0", "summary": "an exchange"},
]
FILE = {
    "kind": "data_sources",
    "name": "file",
    "version": "1.0",
    "summary": "a file",
    "params": {
        "type": "object",
        "properties": {"path": {"type": "string", "title": "Path"}},
        "required": ["path"],
    },
}


def scenario(
    engine_at: Callable[[], Engine], play: Callable[[User, WizardScreen], Awaitable[None]]
) -> None:
    window.scenario(engine_at, app.wizard, play)


def engine_saying(
    seen: list[tuple[str, str, Any]] | None = None, overrides: dict[str, Any] | None = None
) -> Callable[[], Engine]:
    answers: dict[str, Any] = {
        "/pipeline": PIPELINE,
        "/plugins": {"plugins": PLUGINS},
        "/plugins/forecasters/ewmac": EWMAC,
        "/plugins/data_sources/file": FILE,
        "/configs": {"configs": [{"name": "trend"}]},
        "/configs/trend": {"name": "trend", "text": TEXT},
        "/backtests": {"id": "r-1", "status": "queued"},
    }
    return fake({**answers, **(overrides or {})}, seen)


async def started(screen: WizardScreen) -> None:
    screen.saved.set_value("trend")
    await screen.settled()


def event(screen: WizardScreen, **args: Any) -> Any:
    return SimpleNamespace(args=args, sender=screen.flow)


def ids(screen: WizardScreen, key: str = "nodes") -> set[str]:
    return {item["id"] for item in screen.flow._props["picture"][key]}


def test_it_offers_what_the_engine_keeps_and_takes() -> None:
    async def play(user: User, screen: WizardScreen) -> None:
        assert screen.saved.options == ["trend"]
        assert screen.kind.options == {
            "forecasters": "Forecasters",
            "gates": "Gates",
            "indicators": "Indicators",
            "combiners": "Combiners",
            "sizers": "Sizers",
            "exits": "Exits",
            "risk_gates": "Risk gates",
            "fills": "Fills",
        }
        assert screen.message.text == ""
        assert not screen.remove.enabled

    scenario(engine_saying(), play)


def test_a_page_whose_engine_is_down_says_so() -> None:
    async def play(user: User, screen: WizardScreen) -> None:
        assert "engine says 500" in screen.message.text
        assert not screen.saved.options

    scenario(engine_saying(overrides={"/pipeline": 500}), play)


def test_a_saved_configuration_is_drawn_and_its_text_shown() -> None:
    async def play(user: User, screen: WizardScreen) -> None:
        await started(screen)
        assert "component:ewmac16" in ids(screen)
        assert "strategy:trend" in ids(screen)
        assert "stream:btc@1h" in ids(screen)
        assert len(ids(screen, "edges")) == 14
        assert tomllib.loads(screen.text.text) == tomllib.loads(TEXT)
        assert screen.name.value == "trend"
        assert screen.chosen.text == "Nothing chosen"

    scenario(engine_saying(), play)


def test_a_configuration_that_is_not_toml_is_said_not_drawn() -> None:
    async def play(user: User, screen: WizardScreen) -> None:
        await started(screen)
        assert "Expected" in screen.message.text or "Invalid" in screen.message.text
        assert screen.model is None
        assert screen.flow._props["picture"]["nodes"] == []

    scenario(engine_saying(overrides={"/configs/trend": {"name": "trend", "text": "= ="}}), play)


def test_a_configuration_the_engine_cannot_read_is_said() -> None:
    async def play(user: User, screen: WizardScreen) -> None:
        await started(screen)
        assert "engine says 404" in screen.message.text
        assert screen.model is None

    scenario(engine_saying(overrides={"/configs/trend": 404}), play)


def test_nothing_can_be_done_before_a_configuration_is_chosen() -> None:
    async def play(user: User, screen: WizardScreen) -> None:
        for act in (
            screen._add,
            screen._add_strategy,
            screen.run,
            screen.keep,
            screen._remove,
            lambda: screen._link(event(screen, source="a", target="b")),
        ):
            screen._say("")
            await act()
            assert screen.message.text == "start from a saved configuration first"

    scenario(engine_saying(), play)


def test_a_component_is_added_by_kind_and_plugin_and_chosen() -> None:
    async def play(user: User, screen: WizardScreen) -> None:
        await started(screen)
        await screen._add()
        assert screen.message.text == "choose a kind and a plug-in to add"
        screen.kind.set_value("forecasters")
        assert screen.plugin.options == ["breakout", "ewmac"]
        assert screen.plugin.value is None
        screen.plugin.set_value("breakout")
        await screen._add()
        assert screen.message.text == ""
        assert "component:breakout" in ids(screen)
        assert screen.selected == "component:breakout"
        assert screen.chosen.text == "component: breakout"
        assert screen.remove.enabled
        assert 'plugin = "breakout"' in screen.text.text

    scenario(engine_saying(overrides={"/plugins/forecasters/breakout": EWMAC}), play)


def test_a_strategy_is_added_by_name() -> None:
    async def play(user: User, screen: WizardScreen) -> None:
        await started(screen)
        screen.strategy_name.set_value("  ")
        await screen._add_strategy()
        assert screen.message.text == "give the strategy a name"
        screen.strategy_name.set_value("second")
        await screen._add_strategy()
        assert "strategy:second" in ids(screen)
        assert screen.selected == "strategy:second"
        assert screen.chosen.text == "strategy: second"

    scenario(engine_saying(), play)


def test_a_link_drawn_in_the_browser_changes_the_model_or_says_why_not() -> None:
    async def play(user: User, screen: WizardScreen) -> None:
        await started(screen)
        screen.field.set_value("high")
        await screen._link(event(screen, source="stream:btc@1h", target="component:ewmac16"))
        assert screen.message.text == ""
        assert "btc@1h.high" in screen.text.text
        await screen._link(event(screen, source="stream:btc@1h", target="component:ewmac16"))
        assert "already reads" in screen.message.text
        await screen._link(event(screen, source="strategy:trend", target="component:stop"))
        assert screen.message.text == "a strategy feeds nothing"

    scenario(engine_saying(), play)


def test_the_flow_element_reports_links_and_selections_to_the_page() -> None:
    async def play(user: User, screen: WizardScreen) -> None:
        await started(screen)
        flow = user.find(kind=Flow)
        flow.trigger("select", {"id": "stream:btc@1h"})
        flow.trigger("link", {"source": "stream:btc@1h", "target": "component:atr24"})
        for _ in range(20):
            await asyncio.sleep(0)
        assert screen.selected == "stream:btc@1h"
        assert "already reads" in screen.message.text

    scenario(engine_saying(), play)


def test_a_component_chosen_shows_its_settings_as_they_are() -> None:
    async def play(user: User, screen: WizardScreen) -> None:
        await started(screen)
        await screen._select(event(screen, id="component:ewmac32"))
        assert screen.form is not None
        assert screen.form.values == {"fast": 32, "slow": 128, "scalar": 2.65}
        box = screen.form.inputs["fast"]
        assert isinstance(box, ui.number)
        assert box.value == 32  # the box shows it, not the default
        assert screen.chosen.text == "component: ewmac32"

    scenario(engine_saying(), play)


def test_a_setting_changed_is_in_the_text_and_what_the_form_does_not_know_stays() -> None:
    async def play(user: User, screen: WizardScreen) -> None:
        assert screen.rules is not None
        await started(screen)
        assert screen.model is not None
        screen.model.set_params("ewmac32", {"fast": 32, "slow": 128, "legacy": True})
        await screen._select(event(screen, id="component:ewmac32"))
        assert screen.form is not None
        box = screen.form.inputs["fast"]
        assert isinstance(box, ui.number)
        box.set_value(8)
        params = screen.model.params("ewmac32")
        assert params["fast"] == 8
        assert params["slow"] == 128
        assert params["legacy"] is True
        assert "fast = 8" in screen.text.text

    scenario(engine_saying(), play)


def test_settings_that_cannot_be_got_are_said() -> None:
    async def play(user: User, screen: WizardScreen) -> None:
        await started(screen)
        await screen._select(event(screen, id="component:ewmac32"))
        assert "engine says 500" in screen.message.text
        assert screen.form is None
        screen._params_changed("ewmac32")  # nothing to read the settings from

    scenario(engine_saying(overrides={"/plugins/forecasters/ewmac": 500}), play)


def test_choosing_a_stream_or_nothing_shows_no_settings() -> None:
    async def play(user: User, screen: WizardScreen) -> None:
        await started(screen)
        await screen._select(event(screen, id="stream:btc@1h"))
        assert screen.form is None
        assert screen.chosen.text == "stream: btc@1h"
        await screen._select(event(screen, id=""))
        assert screen.chosen.text == "Nothing chosen"
        assert not screen.remove.enabled
        await screen._select(event(screen, id="component:nothing"))
        assert screen.form is None

    scenario(engine_saying(), play)


def test_a_component_removed_leaves_the_picture_without_it() -> None:
    async def play(user: User, screen: WizardScreen) -> None:
        await started(screen)
        await screen._select(event(screen, id="component:atr24"))
        await screen._remove()
        assert "component:atr24" not in ids(screen)
        assert screen.selected == ""
        await screen._remove()  # nothing is chosen: nothing happens
        assert screen.message.text == ""

    scenario(engine_saying(), play)


def test_a_link_chosen_is_removed_as_a_link() -> None:
    async def play(user: User, screen: WizardScreen) -> None:
        await started(screen)
        await screen._select(event(screen, id="forecasters:strategy:trend:ewmac16"))
        assert screen.chosen.text == "A link is chosen"
        await screen._remove()
        assert "forecasters:strategy:trend:ewmac16" not in ids(screen, "edges")
        assert "component:ewmac16" in ids(screen)

    scenario(engine_saying(), play)


def test_what_cannot_be_removed_is_said() -> None:
    async def play(user: User, screen: WizardScreen) -> None:
        await started(screen)
        await screen._select(event(screen, id="stream:btc@1h"))
        await screen._remove()
        assert "btc@1h is read by" in screen.message.text
        assert screen.selected == "stream:btc@1h"
        await screen._select(event(screen, id="nonsense:x"))
        await screen._remove()
        assert "only a component" in screen.message.text

    scenario(engine_saying(), play)


def test_running_submits_the_text_and_goes_to_the_tester(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    went: list[str] = []
    monkeypatch.setattr(wizard_module, "go_to", went.append)
    seen: list[tuple[str, str, Any]] = []

    async def play(user: User, screen: WizardScreen) -> None:
        await started(screen)
        screen.name.set_value("")
        await screen.run()
        method, path, body = next(s for s in seen if s[0] == "POST")
        assert (method, path) == ("POST", "/backtests")
        assert body["name"] == "backtest"
        assert tomllib.loads(body["config"]) == tomllib.loads(TEXT)
        assert went == ["/tester?run=r-1"]

    scenario(engine_saying(seen), play)


def test_a_configuration_the_engine_refuses_is_said_and_the_screen_stays(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    went: list[str] = []
    monkeypatch.setattr(wizard_module, "go_to", went.append)

    async def play(user: User, screen: WizardScreen) -> None:
        await started(screen)
        await screen.run()
        assert "engine says 400" in screen.message.text
        assert went == []

    scenario(engine_saying(overrides={"/backtests": 400}), play)


def test_a_configuration_is_kept_among_the_engines_and_then_offered() -> None:
    seen: list[tuple[str, str, Any]] = []
    both = {"configs": [{"name": "trend"}, {"name": "mine"}]}

    async def play(user: User, screen: WizardScreen) -> None:
        await started(screen)
        await screen.keep()
        assert screen.message.text == "name the configuration to keep it"
        screen.saved.set_options(["trend"])
        screen.keep_as.set_value(" mine ")
        await screen.keep()
        method, path, body = next(s for s in seen if s[0] == "PUT")
        assert (method, path) == ("PUT", "/configs/mine")
        assert body["replace"] is False
        assert tomllib.loads(body["config"]) == tomllib.loads(TEXT)
        assert screen.message.text == ""
        assert screen.kept.text == "Kept as mine"
        assert screen.saved.options == ["trend", "mine"]
        screen.replace.set_value(True)
        await screen.keep()
        assert [s[2]["replace"] for s in seen if s[0] == "PUT"] == [False, True]

    scenario(
        engine_saying(seen, {"/configs": both, "/configs/mine": {"name": "mine", "created": True}}),
        play,
    )


def test_a_configuration_replaced_is_said_to_be() -> None:
    async def play(user: User, screen: WizardScreen) -> None:
        await started(screen)
        screen.keep_as.set_value("mine")
        await screen.keep()
        assert screen.kept.text == "Replaced mine"

    scenario(engine_saying(overrides={"/configs/mine": {"name": "mine", "created": False}}), play)


def test_a_configuration_the_engine_will_not_keep_is_said_and_not_claimed_kept() -> None:
    async def play(user: User, screen: WizardScreen) -> None:
        await started(screen)
        screen.keep_as.set_value("mine")
        screen.kept.text = "Kept as an earlier one"
        await screen.keep()
        assert "engine says 409" in screen.message.text
        assert screen.kept.text == ""

    scenario(engine_saying(overrides={"/configs/mine": 409}), play)


async def fill(screen: WizardScreen, **told: str) -> None:
    """Ask for data in the dialog, with the usual answers except those in ``told``."""
    data = screen.data
    data.open()
    data.source.set_value("file")
    await data.settled()
    assert data.form is not None
    box = data.form.inputs["path"]
    assert isinstance(box, ui.input)
    box.set_value("eth.csv")
    data.name.set_value("eth")
    for field, value in {
        "symbol": "ETH/USD",
        "venue": "kraken",
        "asset_class": "crypto",
        "quote_currency": "USD",
    }.items():
        data.instrument[field].set_value(value)
    data.start.set_value(told.get("start", "2026-01-01T00:00:00Z"))
    data.end.set_value(told.get("end", "2026-02-01T00:00:00Z"))
    data.bars.set_value(told.get("bars", "1h"))


def test_a_configuration_can_be_started_empty_and_given_data() -> None:
    async def play(user: User, screen: WizardScreen) -> None:
        assert not ids(screen)  # nothing drawn before a start
        await screen._start_empty()
        assert screen.model is not None
        assert ids(screen) == set()
        assert tomllib.loads(screen.text.text)["mode"] == "backtest"
        await fill(screen)
        assert screen.data.settings.default_slot.children  # the source's own form is drawn
        screen.data.add()
        assert screen.data.message.text == ""
        assert not screen.data.dialog.value  # closed: it was taken
        assert ids(screen) == {"stream:eth@1h"}
        picture = screen.flow._props["picture"]["nodes"]
        assert picture[0]["sub"] == "file · ETH/USD"
        mount = tomllib.loads(screen.text.text)["mounts"]["eth"]
        assert mount["params"] == {"path": "eth.csv"}
        assert mount["kind"] == "trades"
        assert mount["start"].year == 2026

    scenario(engine_saying(), play)


def test_what_a_source_says_it_delivers_is_believed_over_the_dialogs_own_boxes() -> None:
    async def play(user: User, screen: WizardScreen) -> None:
        await screen._start_empty()
        await fill(screen)
        assert screen.data.form is not None
        screen.data.form.values.update({"kind": "bars", "bar_size": "5m"})
        screen.data.kind.set_value("trades")
        screen.data.bar_size.set_value("1h")
        screen.data.add()
        assert screen.model is not None
        mount = screen.model.config["mounts"]["eth"]
        assert (mount["kind"], mount["bar_size"]) == ("bars", "5m")

    scenario(engine_saying(), play)


def test_a_source_that_does_not_say_is_asked_in_the_dialog() -> None:
    async def play(user: User, screen: WizardScreen) -> None:
        await screen._start_empty()
        await fill(screen)
        screen.data.kind.set_value("bars")
        screen.data.bar_size.set_value(" 1h ")
        screen.data.add()
        assert screen.model is not None
        mount = screen.model.config["mounts"]["eth"]
        assert (mount["kind"], mount["bar_size"]) == ("bars", "1h")

    scenario(engine_saying(), play)


def test_data_can_be_added_to_a_saved_configuration_too() -> None:
    async def play(user: User, screen: WizardScreen) -> None:
        await started(screen)
        await fill(screen)
        screen.data.add()
        assert {"stream:btc@1h", "stream:eth@1h"} <= ids(screen)

    scenario(engine_saying(), play)


def test_the_data_dialog_offers_the_sources_and_waits_for_a_configuration() -> None:
    async def play(user: User, screen: WizardScreen) -> None:
        screen._open_data()
        assert screen.message.text == "start from a saved configuration first"
        await screen._start_empty()
        screen._open_data()
        assert screen.data.source.options == ["ccxt", "file"]
        assert screen.data.dialog.value

    scenario(engine_saying(), play)


def test_data_that_is_not_whole_is_said_in_the_dialog_which_stays_open() -> None:
    async def play(user: User, screen: WizardScreen) -> None:
        await screen._start_empty()
        await fill(screen, bars="")
        screen.data.instrument["venue"].set_value("")
        screen.data.add()
        assert "the instrument needs venue" in screen.data.message.text
        assert screen.data.dialog.value
        assert ids(screen) == set()
        screen.data.instrument["venue"].set_value("kraken")
        screen.data.add()
        assert "give the size of the bars" in screen.data.message.text
        assert screen.model is not None
        assert screen.model.config.get("mounts", {}) == {}  # nothing half added

    scenario(engine_saying(), play)


def test_a_time_without_an_offset_is_said_not_taken() -> None:
    async def play(user: User, screen: WizardScreen) -> None:
        await screen._start_empty()
        await fill(screen, start="2026-01-01T00:00:00")
        screen.data.add()
        assert "needs a UTC offset" in screen.data.message.text
        assert screen.data.dialog.value

    scenario(engine_saying(), play)


def test_settings_of_the_source_that_are_wrong_are_said_first() -> None:
    async def play(user: User, screen: WizardScreen) -> None:
        await screen._start_empty()
        screen.data.open()
        screen.data.source.set_value("file")
        await screen.data.settled()
        screen.data.add()  # the path is required and empty
        assert screen.data.message.text == "fix the settings of the source first"
        assert ids(screen) == set()

    scenario(engine_saying(), play)


def test_a_source_whose_settings_cannot_be_got_is_said() -> None:
    async def play(user: User, screen: WizardScreen) -> None:
        await screen._start_empty()
        screen.data.open()
        screen.data.source.set_value("ccxt")
        await screen.data.settled()
        assert "nothing for" in screen.data.message.text
        assert screen.data.form is None
        screen.data.source.set_value(None)
        await screen.data.settled()
        assert screen.data.form is None

    scenario(engine_saying(), play)


def test_data_added_with_no_configuration_is_refused() -> None:
    async def play(user: User, screen: WizardScreen) -> None:
        assert screen._add_data({}) == ["start from a configuration first"]

    scenario(engine_saying(), play)


def test_starting_empty_waits_for_the_engines_rules() -> None:
    async def play(user: User, screen: WizardScreen) -> None:
        await screen._start_empty()
        assert screen.model is None

    scenario(engine_saying(overrides={"/pipeline": 500}), play)


def test_the_picture_follows_the_dark_switch() -> None:
    async def play(user: User, screen: WizardScreen) -> None:
        assert screen.flow._props["dark"] is True
        screen.flow.set_dark(False)
        assert screen.flow._props["dark"] is False

    scenario(engine_saying(), play)
