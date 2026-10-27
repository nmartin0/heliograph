# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The Plug-ins screen: every installed plug-in, configurable, with no code written for it."""

from collections.abc import Callable
from typing import Any

from nicegui import ui
from nicegui.testing.user import User

from heliograph.gui import catalogue as catalogue_module
from heliograph.gui.app import plugins
from heliograph.gui.catalogue import Catalogue
from heliograph.gui.client import Engine
from tests.support import window
from tests.support.window import fake

# A plug-in no code here has ever heard of, of a kind no code here has either.
ZORBO = {
    "kind": "oscillators",
    "name": "zorbo",
    "version": "0.3.1",
    "summary": "A made-up oscillator",
    "params": {
        "properties": {
            "length": {
                "default": 9,
                "minimum": 2,
                "maximum": 50,
                "type": "integer",
                "title": "Length",
            },
            "mode": {"enum": ["fast", "slow"], "default": "fast", "type": "string"},
            "smooth": {"default": False, "type": "boolean"},
            "label": {"default": "z", "type": "string"},
        },
        "type": "object",
    },
}
LISTED = {
    "plugins": [
        {
            "kind": "oscillators",
            "name": "zorbo",
            "version": "0.3.1",
            "summary": "A made-up oscillator",
        },
        {"kind": "indicators", "name": "sma", "version": "1", "summary": "Simple moving average"},
        {"kind": "indicators", "name": "alpha", "version": "1", "summary": "First of them"},
    ]
}


def scenario(engine_at: Callable[[], Engine], play: Any) -> None:
    window.scenario(engine_at, plugins, play)


def inputs(screen: Catalogue) -> dict[str, Any]:
    assert screen.form is not None
    return screen.form.inputs


def test_a_plugin_the_window_has_never_heard_of_appears_and_is_configurable(
    engine_at: Callable[[], Engine],
) -> None:
    """The gate of Phase 3: no GUI code for the plug-in, nor for its kind."""

    async def play(user: User, screen: Catalogue) -> None:
        await user.should_see("zorbo")
        await user.should_see("Oscillators")  # its kind, named after itself
        await screen.choose("oscillators", "zorbo")
        assert screen.heading.text == "zorbo · Oscillators · 0.3.1"
        assert screen.summary.text == "A made-up oscillator"
        assert list(inputs(screen)) == ["length", "mode", "smooth", "label"]
        assert [type(i) for i in inputs(screen).values()] == [
            ui.number,
            ui.select,
            ui.switch,
            ui.input,
        ]
        assert screen.preview.content == "params = {}\n"  # its kind is not a component

    scenario(
        fake({"/plugins": LISTED, "/plugins/oscillators/zorbo": ZORBO}),
        play,
    )


def test_what_is_changed_is_shown_as_the_configuration_would_take_it(
    engine_at: Callable[[], Engine],
) -> None:
    async def play(user: User, screen: Catalogue) -> None:
        await screen.choose("indicators", "sma")
        assert screen.preview.content == (
            '[[components]]\nkind = "indicators"\nplugin = "sma"\nname = "sma"\n'
            '# inputs = ["btc@1h.close"]  # required: a stream\'s field or a node it reads\n'
            "params = {}\n"
        )
        period = inputs(screen)["period"]
        assert isinstance(period, ui.number)
        period.set_value(5.0)  # a number box hands back a float
        assert screen.form is not None
        assert screen.form.values["period"] == 5
        assert isinstance(screen.form.values["period"], int)
        assert screen.preview.content.endswith("params = { period = 5 }\n")
        period.set_value(0)
        assert screen.form.errors == {"period": "must be at least 2"}
        assert not screen.form.valid
        assert "fix the settings" in screen.preview.content
        period.set_value(20)  # the default: nothing to send
        assert screen.form.errors == {}
        assert screen.preview.content.endswith("params = {}\n")

    scenario(engine_at, play)


def test_every_installed_plugin_gets_a_form_that_starts_at_its_defaults(
    engine_at: Callable[[], Engine],
) -> None:
    async def play(user: User, screen: Catalogue) -> None:
        count = 0
        for kind, group in screen.plugins.items():
            for found in group:
                await screen.choose(kind, found["name"])
                assert screen.message.text == ""
                assert screen.form is not None
                # Nothing is wrong at the start but a required value not yet given.
                assert set(screen.form.errors.values()) <= {"required"}, (kind, found["name"])
                assert screen.form.params() == {}, (kind, found["name"])
                count += 1
        assert count >= 20

    scenario(engine_at, play)


def test_each_kind_of_input_sets_its_value(engine_at: Callable[[], Engine]) -> None:
    async def play(user: User, screen: Catalogue) -> None:
        await screen.choose("oscillators", "zorbo")
        form = screen.form
        assert form is not None
        inputs(screen)["mode"].set_value("slow")
        inputs(screen)["smooth"].set_value(True)
        inputs(screen)["label"].set_value("q")
        inputs(screen)["length"].set_value(7)
        assert form.params() == {"length": 7, "mode": "slow", "smooth": True, "label": "q"}
        inputs(screen)["length"].set_value(1)
        assert form.errors == {"length": "must be at least 2"}

    scenario(fake({"/plugins": LISTED, "/plugins/oscillators/zorbo": ZORBO}), play)


def test_a_list_and_a_json_parameter_are_set_from_text(engine_at: Callable[[], Engine]) -> None:
    odd = {
        **ZORBO,
        "params": {
            "properties": {
                "cols": {"default": ["a"], "items": {"type": "string"}, "type": "array"},
                "extra": {"type": "object", "additionalProperties": {"type": "number"}},
            },
            "required": ["extra"],
            "type": "object",
        },
    }

    async def play(user: User, screen: Catalogue) -> None:
        await screen.choose("oscillators", "zorbo")
        form = screen.form
        assert form is not None
        inputs(screen)["cols"].set_value("x, y")
        inputs(screen)["extra"].set_value("{nope")
        assert form.errors["extra"].startswith("not JSON")
        inputs(screen)["extra"].set_value('{"k": 1.5}')
        assert form.errors == {}
        assert form.params() == {"cols": ["x", "y"], "extra": {"k": 1.5}}
        assert screen.preview.content == 'params = { cols = ["x", "y"], extra = { k = 1.5 } }\n'
        inputs(screen)["extra"].set_value("")  # cleared: required, so it is asked for
        assert form.errors == {"extra": "required"}

    scenario(fake({"/plugins": LISTED, "/plugins/oscillators/zorbo": odd}), play)


def test_the_list_is_filtered_by_what_is_typed(engine_at: Callable[[], Engine]) -> None:
    async def play(user: User, screen: Catalogue) -> None:
        screen.search.set_value("moving")  # in sma's summary
        await user.should_see("sma")
        await user.should_not_see("zorbo")
        screen.search.set_value("zzz")
        await user.should_not_see("sma")
        screen.search.set_value("")
        await user.should_see("zorbo")

    scenario(fake({"/plugins": LISTED}), play)


def test_an_engine_that_fails_is_told_to_the_person(engine_at: Callable[[], Engine]) -> None:
    async def play(user: User, screen: Catalogue) -> None:
        await user.should_see("engine says 503")

    scenario(fake({"/plugins": 503}), play)

    async def play_choose(user: User, screen: Catalogue) -> None:
        await screen.choose("indicators", "sma")
        assert screen.message.text == "engine says 404"
        assert screen.chosen is None

    scenario(fake({"/plugins": LISTED, "/plugins/indicators/sma": 404}), play_choose)


def test_plugins_are_grouped_by_kind_with_names_in_order_and_kinds_titled() -> None:
    got = catalogue_module.grouped(LISTED["plugins"])
    assert {k: [p["name"] for p in v] for k, v in got.items()} == {
        "oscillators": ["zorbo"],
        "indicators": ["alpha", "sma"],
    }
    assert catalogue_module.kind_title("risk_gates") == "Risk gates"
    assert catalogue_module.kind_title("made_up_kind") == "Made up kind"


def test_a_snippet_is_the_table_a_configuration_takes_for_that_kind() -> None:
    assert catalogue_module.snippet("forecasters", "ewmac", "{ fast = 4 }") == (
        '[[components]]\nkind = "forecasters"\nplugin = "ewmac"\nname = "ewmac"\n'
        "params = { fast = 4 }\n"
    )
    assert catalogue_module.snippet("forecasters", "ewmac", "{}", "required") == (
        '[[components]]\nkind = "forecasters"\nplugin = "ewmac"\nname = "ewmac"\n'
        '# inputs = ["btc@1h.close"]  # required: a stream\'s field or a node it reads\n'
        "params = {}\n"
    )
    assert catalogue_module.snippet("exits", "stop", "{}", "optional") == (
        '[[components]]\nkind = "exits"\nplugin = "stop"\nname = "stop"\n'
        '# inputs = ["atr24"]  # optional: the nodes it reads\n'
        "params = {}\n"
    )
    assert catalogue_module.snippet("sizers", "vol", "{}", "none").count("inputs") == 0
    assert catalogue_module.snippet("data_sources", "file", "{}") == (
        '[mounts.file]\nsource = "file"\nparams = {}\n'
    )
    assert catalogue_module.snippet("journals", "sqlite", "{}") == "params = {}\n"


def test_a_required_value_not_yet_given_is_asked_for_from_the_start(
    engine_at: Callable[[], Engine],
) -> None:
    required = {
        **ZORBO,
        "params": {
            "properties": {"venue": {"type": "string", "title": "Venue"}},
            "required": ["venue"],
            "type": "object",
        },
    }

    async def play(user: User, screen: Catalogue) -> None:
        await screen.choose("oscillators", "zorbo")
        form = screen.form
        assert form is not None
        assert form.errors == {"venue": "required"}
        assert "fix the settings" in screen.preview.content
        inputs(screen)["venue"].set_value("kraken")
        assert form.errors == {}
        assert screen.preview.content == 'params = { venue = "kraken" }\n'

    scenario(fake({"/plugins": LISTED, "/plugins/oscillators/zorbo": required}), play)


def test_a_filter_nothing_matches_says_so(engine_at: Callable[[], Engine]) -> None:
    async def play(user: User, screen: Catalogue) -> None:
        screen.search.set_value("zzzz")
        await user.should_see("No plug-in matches.")
        screen.search.set_value("")
        await user.should_not_see("No plug-in matches.")
        await user.should_see("zorbo")

    scenario(fake({"/plugins": LISTED}), play)


def test_the_snippet_reminds_of_the_inputs_the_engine_says_a_plugin_takes(
    engine_at: Callable[[], Engine],
) -> None:
    async def play(user: User, screen: Catalogue) -> None:
        await screen.choose("indicators", "sma")
        assert screen.inputs == "required"
        assert "# inputs = " in screen.preview.content
        await screen.choose("indicators", "alpha")  # an engine that says nothing of inputs
        assert screen.inputs == "none"
        assert "inputs" not in screen.preview.content

    sma = {
        "kind": "indicators",
        "name": "sma",
        "version": "1",
        "summary": "s",
        "inputs": "required",
    }
    alpha = {"kind": "indicators", "name": "alpha", "version": "1", "summary": "a"}
    scenario(
        fake(
            {
                "/plugins": LISTED,
                "/plugins/indicators/sma": {**sma, "params": ZORBO["params"]},
                "/plugins/indicators/alpha": {**alpha, "params": ZORBO["params"]},
            }
        ),
        play,
    )


def test_a_box_that_can_be_left_empty_shows_what_that_stands_for(
    engine_at: Callable[[], Engine],
) -> None:
    base: Any = ZORBO
    schema = {
        **base,
        "params": {
            "properties": {
                **base["params"]["properties"],
                "cols": {"default": ["a", "b"], "items": {"type": "string"}, "type": "array"},
                "none": {"default": [], "items": {"type": "string"}, "type": "array"},
                "extra": {"default": {"x": 1}, "type": "object"},
                "bare": {"type": "string"},
            },
            "type": "object",
        },
    }

    async def play(user: User, screen: Catalogue) -> None:
        await screen.choose("oscillators", "zorbo")
        boxes = inputs(screen)
        hints = {name: box._props.get("placeholder") for name, box in boxes.items()}
        assert hints == {
            "length": "9 (default)",
            "mode": None,  # a choice has its default selected
            "smooth": None,  # so has a switch
            "label": "z (default)",
            "cols": "a, b (default)",
            "none": "",  # an empty default: nothing to show
            "extra": '{"x": 1} (default)',
            "bare": "",  # no default at all
        }
        assert boxes["length"]._props["stack-label"] is True  # the label up, the hint in view
        assert "stack-label" not in boxes["bare"]._props
        assert "stack-label" not in boxes["mode"]._props  # a choice has no empty state

    scenario(fake({"/plugins": LISTED, "/plugins/oscillators/zorbo": schema}), play)
