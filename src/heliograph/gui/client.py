# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The engine's JSON API, as the window asks it (D18, D37, D38)."""

from typing import Any
from urllib.parse import quote

import httpx

DEFAULT_ENGINE = "http://127.0.0.1:8765"
"""Where ``heliograph serve`` listens unless told otherwise."""


class EngineError(Exception):
    """The engine could not answer: it said so (``status``), or could not be reached
    (``status`` 0). The message is the engine's own, fit to show a person."""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


class Engine:
    """One engine, by its address. Pass ``http`` to speak through another transport."""

    def __init__(self, url: str = DEFAULT_ENGINE, http: httpx.AsyncClient | None = None) -> None:
        self.url = url.rstrip("/")
        self._http = http or httpx.AsyncClient(base_url=self.url, timeout=30.0)

    async def close(self) -> None:
        await self._http.aclose()

    async def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = await self._http.request(method, "/api" + path, **kwargs)
        except httpx.HTTPError as exc:
            raise EngineError(f"the engine at {self.url} cannot be reached: {exc}") from exc
        try:
            body = response.json()
        except ValueError:
            body = None
        if response.status_code >= 400:
            said = body.get("error") if isinstance(body, dict) else None
            raise EngineError(
                said or f"the engine answered {response.status_code}", response.status_code
            )
        return body

    async def health(self) -> dict[str, Any]:
        return dict(await self._call("GET", "/health"))

    async def plugins(self) -> list[dict[str, Any]]:
        return list((await self._call("GET", "/plugins"))["plugins"])

    async def plugin(self, kind: str, name: str) -> dict[str, Any]:
        return dict(await self._call("GET", f"/plugins/{quote(kind)}/{quote(name)}"))

    async def configs(self) -> list[str]:
        return [str(c["name"]) for c in (await self._call("GET", "/configs"))["configs"]]

    async def config(self, name: str) -> str:
        return str((await self._call("GET", f"/configs/{quote(name)}"))["text"])

    async def submit(
        self, config_name: str | None = None, *, text: str | None = None, name: str = "backtest"
    ) -> dict[str, Any]:
        """Queue a backtest of the engine's configuration ``config_name``, or of ``text``
        (named ``name``); its ``id`` and status."""
        body = {"config_name": config_name} if text is None else {"config": text, "name": name}
        return dict(await self._call("POST", "/backtests", json=body))

    async def backtests(self) -> list[dict[str, Any]]:
        return list((await self._call("GET", "/backtests"))["backtests"])

    async def backtest(self, id_: str) -> dict[str, Any]:
        return dict(await self._call("GET", f"/backtests/{quote(id_)}"))

    async def report(self, id_: str) -> dict[str, Any]:
        return dict(await self._call("GET", f"/backtests/{quote(id_)}/report"))

    async def _items(self, id_: str, what: str, **params: str) -> Any:
        return (await self._call("GET", f"/backtests/{quote(id_)}/{what}", params=params))["items"]

    async def contents(self, id_: str) -> dict[str, Any]:
        return dict(await self._items(id_, "contents"))

    async def bars(self, id_: str, stream: str) -> list[dict[str, Any]]:
        return list(await self._items(id_, "bars", stream=stream))

    async def node(self, id_: str, name: str) -> list[dict[str, Any]]:
        return list(await self._items(id_, f"nodes/{quote(name)}"))

    async def signals(self, id_: str, strategy: str) -> list[dict[str, Any]]:
        return list(await self._items(id_, "signals", strategy=strategy))

    async def fills(self, id_: str, strategy: str) -> list[dict[str, Any]]:
        return list(await self._items(id_, "fills", strategy=strategy))

    async def equity(self, id_: str, strategy: str) -> list[dict[str, Any]]:
        return list(await self._items(id_, f"equity/{quote(strategy)}"))
