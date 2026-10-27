# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The Plug-ins screen: every installed plug-in, with a settings form (D40).

Nothing here names a plug-in or a kind. The list is the engine's
(``/api/plugins``), each form is generated from the plug-in's parameter schema,
so a plug-in installed tomorrow appears, configurable, with no code written
for it: the gate of Phase 3.
"""

from typing import Any

from nicegui import ui

from heliograph.gui.client import Engine, EngineError
from heliograph.gui.form import SettingsForm

KINDS = {
    "data_sources": "Data sources",
    "indicators": "Indicators",
    "forecasters": "Forecasters",
    "gates": "Gates",
    "combiners": "Combiners",
    "sizers": "Sizers",
    "exits": "Exits",
    "risk_gates": "Risk gates",
    "fills": "Fills",
    "instrument_providers": "Instrument providers",
    "journals": "Journals",
}
"""Names for the kinds the engine has today. A kind not here is shown under its own
name, so the engine may gain one without the window changing."""


def kind_title(kind: str) -> str:
    return KINDS.get(kind, kind.replace("_", " ").capitalize())


def grouped(plugins: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """The plug-ins by kind: kinds with the engine's own order of them kept, names sorted."""
    out: dict[str, list[dict[str, Any]]] = {}
    for found in plugins:
        out.setdefault(found["kind"], []).append(found)
    return {kind: sorted(group, key=lambda p: p["name"]) for kind, group in out.items()}


COMPONENT_KINDS = frozenset(
    {"indicators", "forecasters", "gates", "combiners", "sizers", "exits", "risk_gates", "fills"}
)
"""Kinds a configuration lists under ``[[components]]``."""


def snippet(kind: str, name: str, params: str) -> str:
    """What to put in a configuration to use ``name`` as the form says: its table for a
    component or a data source, otherwise just its ``params`` line."""
    if kind in COMPONENT_KINDS:
        head = f'[[components]]\nkind = "{kind}"\nplugin = "{name}"\nname = "{name}"\n'
        return f"{head}params = {params}\n"
    if kind == "data_sources":
        return f'[mounts.{name}]\nsource = "{name}"\nparams = {params}\n'
    return f"params = {params}\n"


class Catalogue:
    """The screen: the kinds and their plug-ins on the left, the chosen one's form."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.plugins: dict[str, list[dict[str, Any]]] = {}
        self.chosen: tuple[str, str] | None = None
        self.form: SettingsForm | None = None

    def build(self) -> None:
        with ui.left_drawer(fixed=True).props("width=300 bordered breakpoint=500"):
            self.search = ui.input("Filter", on_change=lambda e: self._listed()).classes("w-full")
            self.listing = ui.column().classes("w-full gap-0")
        with ui.column().classes("w-full max-w-3xl gap-2"):
            self.message = ui.label("").classes("text-negative")
            self.heading = ui.label("Choose a plug-in").classes("text-h6")
            self.summary = ui.label("").classes("text-body2")
            self.settings = ui.column().classes("w-full gap-1")
            self.preview = ui.code("", language="toml").classes("w-full")
            self.preview.set_visibility(False)

    async def load(self) -> None:
        try:
            self.plugins = grouped(await self.engine.plugins())
        except EngineError as exc:
            self.message.text = str(exc)
            return
        self._listed()

    def _listed(self) -> None:
        """Draw the list: each kind with the plug-ins whose name or summary has what was
        typed in the filter."""
        needle = (self.search.value or "").strip().lower()
        self.listing.clear()
        with self.listing:
            if needle and not any(
                needle in f"{p['name']} {p['summary']} {k}".lower()
                for k, g in self.plugins.items()
                for p in g
            ):
                ui.label("No plug-in matches.").classes("text-caption q-pa-sm")
            for kind, group in self.plugins.items():
                shown = [p for p in group if needle in f"{p['name']} {p['summary']} {kind}".lower()]
                if not shown:
                    continue
                ui.label(kind_title(kind)).classes("text-subtitle2 mt-2")
                for found in shown:
                    ui.item(
                        found["name"],
                        on_click=lambda _, k=kind, n=found["name"]: self.choose(k, n),
                    ).props("dense clickable")

    async def choose(self, kind: str, name: str) -> None:
        """Show ``name``'s settings."""
        self.message.text = ""
        try:
            found = await self.engine.plugin(kind, name)
        except EngineError as exc:
            self.message.text = str(exc)
            return
        self.chosen = (kind, name)
        self.heading.text = f"{name} · {kind_title(kind)} · {found['version']}"
        self.summary.text = found["summary"]
        self.settings.clear()
        self.form = SettingsForm(found["params"], on_change=self._previewed)
        with self.settings:
            self.form.build()
        self._previewed()

    def _previewed(self) -> None:
        """Show the settings as a configuration would take them, unless one is wrong."""
        assert self.chosen is not None
        assert self.form is not None
        kind, name = self.chosen
        self.preview.set_visibility(True)
        if self.form.valid:
            self.preview.set_content(snippet(kind, name, self.form.toml()))
        else:
            self.preview.set_content("# fix the settings marked above first")
