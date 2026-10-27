# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The window's pages, served by NiceGUI, and the command that serves them (D39)."""

from nicegui import app, ui

from heliograph.gui.catalogue import Catalogue
from heliograph.gui.client import DEFAULT_ENGINE, Engine
from heliograph.gui.tester import StrategyTester
from heliograph.gui.workspace import Workspace

TITLE = "Heliograph"
DEFAULT_PORT = 8766
PAGE_TIMEOUT = 30.0
"""Seconds a page may take to be built: it asks the engine, whose first answer about
plug-ins imports every one of them (NiceGUI's own limit is 3)."""

SCREENS = (("Workspace", "/"), ("Tester", "/tester"), ("Plug-ins", "/plugins"))
"""The screens, in the order the header offers them."""


def frame(current: str) -> ui.dark_mode:
    """The header every screen has: the name, a link to each screen and the dark switch,
    whose mode is returned."""
    ui.page_title(TITLE)
    ui.colors(primary="#34506b")
    dark = ui.dark_mode(True)
    with ui.header().classes("items-center gap-4"):
        ui.label(TITLE).classes("text-h6")
        for name, path in SCREENS:
            link = ui.link(name, path).classes("text-white")
            if path == current:
                link.classes("text-bold underline")
        ui.space()
        ui.switch("dark", value=True).props("color=white").bind_value_to(dark)
    return dark


async def workspace(engine: Engine, run: str | None = None) -> Workspace:
    """The Workspace page, built for the person who opened it, and filled from ``engine``;
    it opens on ``run`` if the engine has that to chart, otherwise on the newest."""
    dark = frame("/")
    screen = Workspace(engine)
    screen.build(dark)
    await screen.load_runs(run)
    return screen


async def tester(engine: Engine) -> StrategyTester:
    """The Strategy Tester page, offering the engine's configurations."""
    frame("/tester")
    screen = StrategyTester(engine)
    screen.build()
    await screen.load()
    return screen


async def plugins(engine: Engine) -> Catalogue:
    """The Plug-ins page, filled from ``engine``."""
    frame("/plugins")
    screen = Catalogue(engine)
    screen.build()
    await screen.load()
    return screen


def create(engine: Engine) -> None:
    """Register the pages, which speak to ``engine``."""

    @ui.page("/", response_timeout=PAGE_TIMEOUT)
    async def home(run: str | None = None) -> None:
        await workspace(engine, run)

    @ui.page("/tester", response_timeout=PAGE_TIMEOUT)
    async def strategy_tester() -> None:
        await tester(engine)

    @ui.page("/plugins", response_timeout=PAGE_TIMEOUT)
    async def catalogue() -> None:
        await plugins(engine)

    app.on_shutdown(engine.close)


def serve(
    engine_url: str = DEFAULT_ENGINE, host: str = "127.0.0.1", port: int = DEFAULT_PORT
) -> None:
    """Serve the window until stopped. It listens on ``host`` and speaks to the engine at
    ``engine_url``."""
    create(Engine(engine_url))
    ui.run(host=host, port=port, title=TITLE, reload=False, show=False, dark=True)
