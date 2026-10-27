# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The Strategy Tester: one backtest, from a configuration to its report (D41).

A configuration is picked from the engine's own, or written, and submitted; the
engine runs it and the screen follows the run until it ends, then shows the
engine's text report, whatever way the run ended, with a link to chart it in the
Workspace. Nothing here knows what a configuration holds: the engine reads it and
says what is wrong.
"""

from urllib.parse import quote

from nicegui import ui

from heliograph.gui.client import Engine, EngineError
from heliograph.gui.work import Work
from heliograph.gui.workspace import chartable

UNFINISHED = ("queued", "running")
"""Statuses of a run that is still to go on; any other is its end."""

DEFAULT_NAME = "backtest"
"""The name of a run when the box for it is left empty."""

POLL_SECONDS = 1.0
"""How often a run is asked after while it goes on."""


def chart_link(run: str) -> str:
    """Where the Workspace shows ``run``."""
    return f"/?run={quote(run, safe='')}"


def follow_link(run: str) -> str:
    """Where this screen follows ``run``: the address kept in the browser, so that a reload
    of the page goes on with the same run."""
    return f"/tester?run={quote(run, safe='')}"


class StrategyTester:
    """The screen: the configuration on the left of the page's work, the run's report below."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.run_id: str | None = None
        self.status = ""
        self._work = Work(self._say)

    def build(self) -> None:
        with ui.left_drawer(fixed=True).props("width=300 bordered breakpoint=500").classes("gap-2"):
            ui.label("Saved configurations").classes("text-subtitle2")
            self.saved = ui.select(
                {}, label="Configuration", on_change=lambda e: self._work.start(self._chose())
            ).classes("w-full")
            ui.button("Load the saved text again", on_click=self._chose).props(
                "flat dense no-caps color=white"
            )
        with ui.column().classes("w-full max-w-4xl gap-2"):
            self.name = ui.input("Name of the run", value=DEFAULT_NAME).classes("w-full")
            self.text = (
                ui.textarea("Configuration (TOML)")
                .props("outlined autogrow input-class=font-mono")
                .classes("w-full")
            )
            with ui.row().classes("items-center gap-4"):
                self.go = ui.button("Run", on_click=self.run)
                self.progress = ui.label("")
                self.link = ui.link("Chart it in the Workspace", "/")
                self.link.set_visibility(False)
            # Beside the Run button, not above a form that may be pages long: a message
            # at the top of the page is out of sight when Run is pressed at the bottom.
            self.message = ui.label("").classes("text-negative w-full")
            self.message.style("white-space: pre-wrap")
            self.report = ui.label("").classes("w-full font-mono")
            self.report.style("white-space: pre-wrap")
        self.timer = ui.timer(POLL_SECONDS, self.poll, active=False)

    async def settled(self) -> None:
        """Wait until nothing a choice set going is still going."""
        await self._work.settled()

    def _say(self, text: str) -> None:
        """Show ``text`` beside the Run button, and bring it into view: the editor above
        it may be long, and the person is at the bottom of it."""
        self.message.text = text
        if text:
            self.message.run_method("scrollIntoView", {"block": "nearest"})

    async def load(self) -> None:
        """Offer the configurations the engine keeps."""
        try:
            names = await self.engine.configs()
        except EngineError as exc:
            self._say(str(exc))
            return
        self.saved.set_options(names)

    async def _chose(self) -> None:
        name = self.saved.value
        if name is None:
            return
        try:
            self.text.value = await self.engine.config(name)
        except EngineError as exc:
            self._say(str(exc))
            return
        self._say("")
        self.name.value = name

    async def run(self) -> None:
        """Submit what the editor holds and follow the run."""
        if not (self.text.value or "").strip():
            self._say("write or choose a configuration first")
            return
        self._say("")
        self.report.text = ""
        self.link.set_visibility(False)
        try:
            queued = await self.engine.submit(
                text=self.text.value, name=(self.name.value or "").strip() or DEFAULT_NAME
            )
        except EngineError as exc:
            self._say(str(exc))
            return
        self.run_id = str(queued["id"])
        self._shown(str(queued.get("status", "queued")))
        self.go.disable()
        self.timer.activate()
        ui.navigate.history.replace(follow_link(self.run_id))

    async def follow(self, run: str) -> None:
        """Go on with ``run`` (one submitted before the page was opened, perhaps from it):
        show where it is, and its report if it has ended."""
        self.run_id = run
        self._shown("asking")
        self.go.disable()
        self.timer.activate()
        await self.poll()

    async def poll(self) -> None:
        """Ask after the run; when it has ended show its report."""
        if self.run_id is None:
            return
        try:
            described = await self.engine.backtest(self.run_id)
        except EngineError as exc:
            self._ended()
            self._say(str(exc))
            return
        status = str(described["status"])
        self._shown(status)
        if status.split(" ")[0].split(":")[0] in UNFINISHED:
            return
        self._ended()
        try:
            self.report.text = str((await self.engine.report(self.run_id))["text"])
        except EngineError as exc:  # e.g. a run dropped at a stop has none
            self._say(str(exc))
            return
        if chartable({"status": status}):  # a run that failed has nothing for the Workspace
            self.link.props(f'href="{chart_link(self.run_id)}"')
            self.link.set_visibility(True)

    def _shown(self, status: str) -> None:
        self.status = status
        self.progress.text = f"{self.run_id}: {status}"

    def _ended(self) -> None:
        self.timer.deactivate()
        self.go.enable()
