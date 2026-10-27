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
]


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
        assert "only a component or a strategy" in screen.message.text
        assert screen.selected == "stream:btc@1h"

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


def test_the_picture_follows_the_dark_switch() -> None:
    async def play(user: User, screen: WizardScreen) -> None:
        assert screen.flow._props["dark"] is True
        screen.flow.set_dark(False)
        assert screen.flow._props["dark"] is False

    scenario(engine_saying(), play)
