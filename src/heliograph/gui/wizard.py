# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The Wizard: a configuration assembled as a graph, then run (D42).

It starts from one of the engine's saved configurations. Streams, components and
strategies are nodes; dragging from one node's handle to another reads an input or
makes a stage; clicking a node shows its settings (a form drawn from the plug-in's own
schema, D40); the configuration's text follows every change. Nothing here knows a
kind of plug-in or what makes a configuration good: the engine says what a strategy
takes, and it reads the finished text when it is run.
"""

import tomllib
from typing import Any

from nicegui import ui

from heliograph.gui.catalogue import kind_title
from heliograph.gui.client import Engine, EngineError
from heliograph.gui.flow import Flow
from heliograph.gui.form import SettingsForm
from heliograph.gui.forms import Schema
from heliograph.gui.tester import DEFAULT_NAME, follow_link
from heliograph.gui.wizard_model import (
    BAR_FIELDS,
    COMPONENT,
    DEFAULT_FIELD,
    STRATEGY,
    Rules,
    Wizard,
)
from heliograph.gui.work import Work


def go_to(address: str) -> None:
    """Leave this screen for ``address`` (the Tester, following the run just queued)."""
    ui.navigate.to(address)


class WizardScreen:
    """The screen: the picture, the settings of what is chosen in it, and the text."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.rules: Rules | None = None
        self.model: Wizard | None = None
        self.selected = ""
        self.form: SettingsForm | None = None
        self.plugins: list[dict[str, Any]] = []
        self._work = Work(self._say)

    def build(self, dark: ui.dark_mode) -> None:
        with ui.left_drawer(fixed=True).props("width=300 bordered breakpoint=500").classes("gap-2"):
            ui.label("Start from").classes("text-subtitle2")
            self.saved = ui.select(
                {}, label="Saved configuration", on_change=lambda e: self._work.start(self._start())
            ).classes("w-full")
            ui.label("Add").classes("text-subtitle2")
            self.kind = ui.select({}, label="Kind", on_change=lambda e: self._plugins_of()).classes(
                "w-full"
            )
            self.plugin = ui.select({}, label="Plug-in").classes("w-full")
            ui.button("Add component", on_click=self._add).props("no-caps")
            self.strategy_name = ui.input("Strategy name", value="strategy").classes("w-full")
            ui.button("Add strategy", on_click=self._add_strategy).props("no-caps")
            ui.label("Links from a stream read").classes("text-subtitle2")
            self.field = ui.select(
                list(BAR_FIELDS), value=DEFAULT_FIELD, label="Bar field"
            ).classes("w-full")
        with ui.column().classes("w-full gap-2"):
            with ui.row().classes("w-full no-wrap gap-4 items-start"):
                self.flow = Flow().style("height: 460px; flex: 1 1 0; min-width: 0")
                self.flow.on("link", self._link)
                self.flow.on("select", self._select)
                dark.on_value_change(lambda e: self.flow.set_dark(e.value))
                # Beside the picture, not below it: a form below pushes the picture out of sight.
                self.settings = ui.column().classes("gap-1").style("width: 320px; flex: none")
            with ui.row().classes("items-center gap-4"):
                self.chosen = ui.label("Nothing chosen")
                self.remove = ui.button("Remove", on_click=self._remove).props("flat no-caps")
            self.message = ui.label("").classes("text-negative w-full")
            self.message.style("white-space: pre-wrap")
            with ui.row().classes("items-center gap-4"):
                self.name = ui.input("Name of the run", value=DEFAULT_NAME)
                self.run_button = ui.button("Run as a backtest", on_click=self.run)
            with ui.row().classes("items-center gap-4"):
                self.keep_as = ui.input("Keep as")
                self.replace = ui.switch("Replace one of that name")
                self.keep_button = ui.button("Keep", on_click=self.keep).props("no-caps")
                self.kept = ui.label("")
            with ui.expansion("Configuration text").classes("w-full"):
                self.text = ui.label("").classes("w-full font-mono")
                self.text.style("white-space: pre-wrap")
        self._show_chosen()

    async def settled(self) -> None:
        """Wait until nothing a choice set going is still going."""
        await self._work.settled()

    def _say(self, text: str) -> None:
        self.message.text = text
        if text:
            self.message.run_method("scrollIntoView", {"block": "nearest"})

    async def load(self) -> None:
        """Ask the engine what a strategy takes, which plug-ins it has, and which
        configurations it keeps."""
        try:
            self.rules = Rules.of(await self.engine.pipeline())
            self.plugins = await self.engine.plugins()
            names = await self.engine.configs()
        except EngineError as exc:
            self._say(str(exc))
            return
        self.saved.set_options(names)
        kinds = [*sorted(self.rules.graph), *(k for _, k, _ in self.rules.roles)]
        self.kind.set_options({k: kind_title(k) for k in dict.fromkeys(kinds)})

    def _plugins_of(self) -> None:
        names = sorted(p["name"] for p in self.plugins if p["kind"] == self.kind.value)
        self.plugin.set_options(names, value=None)

    async def _start(self) -> None:
        name = self.saved.value
        if name is None or self.rules is None:
            return
        try:
            text = await self.engine.config(name)
            config = tomllib.loads(text)
        except (EngineError, tomllib.TOMLDecodeError) as exc:
            self._say(str(exc))
            return
        self._say("")
        self.model = Wizard(config, self.rules)
        self.name.value = name
        await self._choose("")

    def _need_model(self) -> Wizard | None:
        if self.model is None:
            self._say("start from a saved configuration first")
        return self.model

    async def _add(self) -> None:
        model = self._need_model()
        if model is None:
            return
        if not self.kind.value or not self.plugin.value:
            self._say("choose a kind and a plug-in to add")
            return
        self._say("")
        await self._choose(model.add_component(self.kind.value, self.plugin.value))

    async def _add_strategy(self) -> None:
        model = self._need_model()
        if model is None:
            return
        name = (self.strategy_name.value or "").strip()
        if not name:
            self._say("give the strategy a name")
            return
        self._say("")
        await self._choose(model.add_strategy(name))

    async def _link(self, event: Any) -> None:
        model = self._need_model()
        if model is None:
            return
        problems = model.connect(
            event.args["source"], event.args["target"], self.field.value or DEFAULT_FIELD
        )
        self._say("\n".join(problems))
        self._redraw()

    async def _select(self, event: Any) -> None:
        await self._choose(str(event.args.get("id") or ""))

    async def _choose(self, identity: str) -> None:
        """Mark a node or an edge, and show the settings of it if it is a component."""
        self.selected = identity
        self.form = None
        self.settings.clear()
        model = self.model
        if model is not None:
            kind, _, name = identity.partition(":")
            if kind == COMPONENT and model.kind_of(name) is not None:
                await self._settings_of(model, name)
        self._redraw()

    async def _settings_of(self, model: Wizard, name: str) -> None:
        plugin = str(model.plugin_of(name))
        kind = str(model.kind_of(name))
        try:
            found = await self.engine.plugin(kind, plugin)
        except EngineError as exc:
            self._say(str(exc))
            return
        schema: Schema = found["params"]
        self.form = SettingsForm(
            schema, on_change=lambda: self._params_changed(name), initial=model.params(name)
        )
        with self.settings:
            ui.label(f"{name} · {plugin} settings").classes("text-subtitle2")
            self.form.build()

    def _params_changed(self, name: str) -> None:
        model, form = self.model, self.form
        if model is None or form is None:
            return
        shown = {f.name for f in form.fields}
        kept = {k: v for k, v in model.params(name).items() if k not in shown}
        model.set_params(name, {**kept, **form.params()})
        self._redraw()

    async def _remove(self) -> None:
        model = self._need_model()
        if model is None or not self.selected:
            return
        if any(e.id == self.selected for e in model.edges()):
            model.disconnect(self.selected)
            problems: list[str] = []
        else:
            problems = model.remove(self.selected)
        self._say("\n".join(problems))
        if not problems:
            await self._choose("")

    def _redraw(self) -> None:
        model = self.model
        if model is None:
            return
        self.flow.show(model.flow(), self.selected)
        self.text.text = model.text()
        self._show_chosen()

    def _show_chosen(self) -> None:
        model = self.model
        if model is not None and any(e.id == self.selected for e in model.edges()):
            self.chosen.text = "A link is chosen"
        else:
            kind, _, name = self.selected.partition(":")
            self.chosen.text = (
                f"{kind}: {name}" if kind in (COMPONENT, STRATEGY, "stream") else "Nothing chosen"
            )
        self.remove.set_enabled(bool(self.selected))

    async def keep(self) -> None:
        """Keep the configuration as it stands among the engine's own, under the name given;
        the engine checks it first and says what is wrong if it would not run."""
        model = self._need_model()
        if model is None:
            return
        name = (self.keep_as.value or "").strip()
        if not name:
            self._say("name the configuration to keep it")
            return
        self._say("")
        self.kept.text = ""
        try:
            created = await self.engine.save_config(
                name, model.text(), replace=bool(self.replace.value)
            )
            names = await self.engine.configs()
        except EngineError as exc:
            self._say(str(exc))
            return
        self.saved.set_options(names)
        self.kept.text = f"Kept as {name}" if created else f"Replaced {name}"

    async def run(self) -> None:
        """Submit the configuration as it stands and go to the Tester to follow the run."""
        model = self._need_model()
        if model is None:
            return
        self._say("")
        try:
            queued = await self.engine.submit(
                text=model.text(), name=(self.name.value or "").strip() or DEFAULT_NAME
            )
        except EngineError as exc:
            self._say(str(exc))
            return
        go_to(follow_link(str(queued["id"])))
