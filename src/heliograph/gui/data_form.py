# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The dialog that asks for data: a source and its settings, the instrument, the span, and
the bars to build (D42).

It asks and hands what it was told to a function that says what is wrong, if anything;
what the engine checks of the whole it checks when the configuration is kept or run. The
source's settings are the form its own schema draws (D40), so a source that is installed
is asked for with no code written for it.
"""

from collections.abc import Callable
from typing import Any

from nicegui import ui

from heliograph.gui.client import Engine, EngineError
from heliograph.gui.form import SettingsForm
from heliograph.gui.wizard_model import INSTRUMENT_FIELDS, MOUNT_KINDS
from heliograph.gui.work import Work

SOURCES = "data_sources"
"""The kind of plug-in a source of data is."""

# The mount's ``kind`` and ``bar_size`` are the source's own settings when it has them.
Spec = dict[str, Any]
"""What the dialog was told: ``name``, ``source``, ``params``, ``instrument``, ``kind``,
``bar_size``, ``start`` and ``end`` (text), and ``bars``."""

INSTRUMENT_TITLES = {
    "symbol": "Symbol (such as BTC/USD)",
    "venue": "Venue (such as kraken)",
    "asset_class": "Asset class (such as crypto)",
    "quote_currency": "Quote currency (such as USD)",
}


class DataForm:
    """The dialog. ``sources`` names the data sources to choose from; ``on_add`` is given
    what was asked and answers with what is wrong with it (nothing, if it was taken)."""

    def __init__(
        self,
        engine: Engine,
        sources: Callable[[], list[str]],
        on_add: Callable[[Spec], list[str]],
    ) -> None:
        self.engine = engine
        self._sources = sources
        self._on_add = on_add
        self.form: SettingsForm | None = None
        self._work = Work(self._say)

    def build(self) -> None:
        with ui.dialog() as self.dialog, ui.card().classes("w-96 gap-1"):
            ui.label("Add data").classes("text-h6")
            self.name = ui.input("Name", value="data").classes("w-full")
            self.source = ui.select(
                [], label="Data source", on_change=lambda e: self._work.start(self._chose())
            ).classes("w-full")
            self.settings = ui.column().classes("w-full gap-1")
            self.instrument = {
                field: ui.input(INSTRUMENT_TITLES[field]).classes("w-full")
                for field in INSTRUMENT_FIELDS
            }
            self.kind = ui.select(
                list(MOUNT_KINDS),
                value=MOUNT_KINDS[0],
                label="Delivers (if its settings do not say)",
            ).classes("w-full")
            self.bar_size = ui.input("Size of the bars delivered (bars only)").classes("w-full")
            self.start = ui.input("From (such as 2026-01-01T00:00:00Z)").classes("w-full")
            self.end = ui.input("To").classes("w-full")
            self.bars = ui.input("Build bars of size", value="1h").classes("w-full")
            self.message = ui.label("").classes("text-negative w-full")
            self.message.style("white-space: pre-wrap")
            with ui.row():
                ui.button("Add", on_click=self.add).props("no-caps")
                ui.button("Cancel", on_click=self.dialog.close).props("flat no-caps")

    def open(self) -> None:
        """Ask for data, offering the sources there are."""
        self.source.set_options(self._sources())
        self._say("")
        self.dialog.open()

    async def settled(self) -> None:
        await self._work.settled()

    def _say(self, text: str) -> None:
        self.message.text = text

    async def _chose(self) -> None:
        """Draw the form of the chosen source's settings."""
        self.settings.clear()
        self.form = None
        if not self.source.value:
            return
        try:
            found = await self.engine.plugin(SOURCES, str(self.source.value))
        except EngineError as exc:
            self._say(str(exc))
            return
        self.form = SettingsForm(found["params"])
        with self.settings:
            self.form.build()

    def add(self) -> None:
        """Hand over what was asked; close if it was taken, say why not if it was not."""
        if self.form is not None and not self.form.valid:
            self._say("fix the settings of the source first")
            return
        told = {} if self.form is None else self.form.values
        # A source that says in its own settings what it delivers is believed, so the data
        # and the source cannot disagree; one that does not is asked here.
        spec: Spec = {
            "name": (self.name.value or "").strip(),
            "source": self.source.value or "",
            "params": {} if self.form is None else self.form.params(),
            "instrument": {k: box.value or "" for k, box in self.instrument.items()},
            "kind": told.get("kind") or self.kind.value,
            "bar_size": told.get("bar_size") or (self.bar_size.value or "").strip() or None,
            "start": self.start.value or "",
            "end": self.end.value or "",
            "bars": (self.bars.value or "").strip(),
        }
        problems = self._on_add(spec)
        self._say("\n".join(problems))
        if not problems:
            self.dialog.close()
