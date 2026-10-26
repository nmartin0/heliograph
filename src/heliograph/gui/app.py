# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The window's pages, served by NiceGUI, and the command that serves them (D39)."""

from nicegui import app, ui

from heliograph.gui.client import DEFAULT_ENGINE, Engine
from heliograph.gui.workspace import Workspace

TITLE = "Heliograph"
DEFAULT_PORT = 8766


async def workspace(engine: Engine) -> Workspace:
    """The Workspace page, built for the person who opened it, and filled from ``engine``."""
    ui.page_title(TITLE)
    ui.colors(primary="#34506b")
    dark = ui.dark_mode(True)
    with ui.header().classes("items-center justify-between"):
        ui.label(TITLE).classes("text-h6")
        ui.switch("dark", value=True).bind_value_to(dark)
    screen = Workspace(engine)
    screen.build()
    await screen.load_runs()
    return screen


def create(engine: Engine) -> None:
    """Register the pages, which speak to ``engine``."""

    @ui.page("/")
    async def home() -> None:
        await workspace(engine)

    app.on_shutdown(engine.close)


def serve(
    engine_url: str = DEFAULT_ENGINE, host: str = "127.0.0.1", port: int = DEFAULT_PORT
) -> None:
    """Serve the window until stopped. It listens on ``host`` and speaks to the engine at
    ``engine_url``."""
    create(Engine(engine_url))
    ui.run(host=host, port=port, title=TITLE, reload=False, show=False, dark=True)
