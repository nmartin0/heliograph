# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The window's pages driven in NiceGUI's simulated browser, on a real or a made-up engine."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from nicegui.testing.user import User
from nicegui.testing.user_simulation import user_simulation

from heliograph.gui.client import Engine


def scenario[Screen](
    engine_at: Callable[[], Engine],
    page: Callable[[Engine], Awaitable[Screen]],
    play: Callable[[User, Screen], Awaitable[None]],
) -> None:
    """Open ``page`` (``heliograph.gui.app``'s function for one screen) on an engine and
    play ``play`` on it."""

    async def go() -> None:
        engine = engine_at()
        shown: list[Screen] = []
        built = asyncio.Event()

        async def root() -> None:
            shown.append(await page(engine))
            built.set()

        async with user_simulation(root) as user:
            await user.open("/")
            # NiceGUI answers as soon as the browser has connected, while a page that
            # asks the engine is still being built: wait for the builder, not for time.
            await built.wait()
            await play(user, shown[0])
        await engine.close()

    asyncio.run(go())


def fake(answers: dict[str, Any]) -> Callable[[], Engine]:
    """An engine that answers each path (and no other, with 500) with the JSON given for it;
    an integer answers with that status and an error."""

    def answer(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api")
        if path in answers:
            found = answers[path]
            if isinstance(found, int):
                return httpx.Response(found, json={"error": f"engine says {found}"})
            return httpx.Response(200, json=found)
        return httpx.Response(500, json={"error": f"nothing for {path}"})

    def engine() -> Engine:
        http = httpx.AsyncClient(transport=httpx.MockTransport(answer), base_url="http://fake")
        return Engine("http://fake", http)

    return engine
