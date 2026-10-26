# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The window's pages driven in NiceGUI's simulated browser, on a real or a made-up engine."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import unquote

import httpx
from nicegui import background_tasks, ui
from nicegui.client import Client
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

        async def build(client: Client) -> None:
            with client:
                shown.append(await page(engine))
            built.set()

        def root() -> None:
            # A page's builder has a time limit (3 s unless the page sets one), and a slow
            # machine or a loaded engine may need longer: return at once, build in a task.
            background_tasks.create(build(ui.context.client))

        async with user_simulation(root) as user:
            await user.open("/")
            # The screen is built after the page is answered: wait for it, not for time.
            await built.wait()
            await play(user, shown[0])
        await engine.close()

    asyncio.run(go())


class Gate:
    """A held-up answer: ``arrived`` is set when the engine is asked, and the answer
    waits for ``open()``."""

    def __init__(self) -> None:
        self.arrived = asyncio.Event()
        self._open = asyncio.Event()

    def open(self) -> None:
        self._open.set()

    async def pass_through(self) -> None:
        self.arrived.set()
        await self._open.wait()


def fake(answers: dict[str, Any]) -> Callable[[], Engine]:
    """An engine that answers each path (and no other, with 500) with the JSON given for it;
    an integer answers with that status and an error; a ``(gate, json)`` pair answers with
    the JSON once the ``Gate`` is opened (a slow engine, held up by a test)."""

    async def answer(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api")
        query = unquote(request.url.query.decode())
        path = f"{path}?{query}" if f"{path}?{query}" in answers else path
        if path in answers:
            found = answers[path]
            if isinstance(found, tuple):
                gate, found = found
                await gate.pass_through()
            if isinstance(found, int):
                return httpx.Response(found, json={"error": f"engine says {found}"})
            return httpx.Response(200, json=found)
        return httpx.Response(500, json={"error": f"nothing for {path}"})

    def engine() -> Engine:
        http = httpx.AsyncClient(transport=httpx.MockTransport(answer), base_url="http://fake")
        return Engine("http://fake", http)

    return engine


def run_answers(
    run: str,
    streams: tuple[str, ...] = ("btc@1h",),
    strategy: str = "trend",
    price: str | None = None,
    held: dict[str, Gate] | None = None,
) -> dict[str, Any]:
    """What an engine says of a small finished run ``run``: one bar a stream, one signal and
    one fill of ``strategy`` (which decides on ``price``, the first stream's close unless
    told), the equity. What ``held`` has a gate for is answered once the gate is open:
    ``"contents"``, or ``"bars:<stream>"``."""
    held = held or {}
    base = f"/backtests/{run}"
    bar = {
        "t": 3_600_000,
        "end_t": 7_200_000,
        "start": "x",
        "end": "x",
        "known": "x",
        "open": 1.0,
        "high": 2.0,
        "low": 0.5,
        "close": 1.5,
        "volume": 3.0,
    }
    contents: Any = {
        "items": {
            "streams": [
                {"stream": s, "mount": s.split("@")[0], "instrument": "k:X", "size": "1h"}
                for s in streams
            ],
            "nodes": [{"name": "fast", "kind": "forecasters", "plugin": "ewmac", "inputs": []}],
            "strategies": [{"name": strategy, "price": price or f"{streams[0]}.close"}],
        }
    }
    if "contents" in held:
        contents = (held["contents"], contents)
    out: dict[str, Any] = {f"{base}/contents": contents}
    for stream in streams:
        answer: Any = {"items": [bar]}
        if f"bars:{stream}" in held:
            answer = (held[f"bars:{stream}"], answer)
        out[f"{base}/bars?stream={stream}"] = answer
    out[f"{base}/signals"] = {
        "items": [{"kind": "signal", "t": 7_200_000, "side": "long", "target": 1.0}]
    }
    out[f"{base}/fills"] = {"items": [{"t": 7_200_000, "quantity": 1.0, "price": 1.5}]}
    out[f"{base}/equity/{strategy}"] = {"items": [{"t": 7_200_000, "value": 10.0}]}
    out[f"{base}/nodes/fast"] = {"items": [{"t": 7_200_000, "value": 1.0}]}
    return out
