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
            why = str(exc) or type(exc).__name__  # a timeout says nothing of itself
            raise EngineError(f"the engine at {self.url} cannot be reached: {why}") from exc
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

    async def _take[T](self, kind: type[T], path: str, key: str | None = None, **kwargs: Any) -> T:
        """What ``GET path`` answers (under ``key``, if given), which must be a ``kind``:
        an answer in any other shape is not the engine's, and is said so."""
        body = await self._call("GET", path, **kwargs)
        found = body if key is None else (body.get(key) if isinstance(body, dict) else None)
        if not isinstance(found, kind):
            raise EngineError(
                f"what answers at {self.url}/api{path} is not the Heliograph engine's "
                f"(expected {key or 'an object'} to be a {kind.__name__})"
            )
        return found

    async def health(self) -> dict[str, Any]:
        return await self._take(dict, "/health")

    async def plugins(self) -> list[dict[str, Any]]:
        return await self._take(list, "/plugins", "plugins")

    async def plugin(self, kind: str, name: str) -> dict[str, Any]:
        return await self._take(dict, f"/plugins/{_part(kind)}/{_part(name)}")

    async def pipeline(self) -> dict[str, Any]:
        """What a strategy takes: ``roles``, its fields that name components, each with the
        plug-in ``kind`` it must be, whether it takes ``many`` and whether that kind reads
        ``inputs``; and the ``graph`` kinds, whose components others may read."""
        return await self._take(dict, "/pipeline")

    async def configs(self) -> list[str]:
        found = await self._take(list, "/configs", "configs")
        try:
            return [str(c["name"]) for c in found]
        except (KeyError, TypeError) as exc:
            raise EngineError(
                f"the engine at {self.url} lists configurations oddly: {exc!r}"
            ) from exc

    async def config(self, name: str) -> str:
        return await self._take(str, f"/configs/{_part(name)}", "text")

    async def submit(
        self, config_name: str | None = None, *, text: str | None = None, name: str = "backtest"
    ) -> dict[str, Any]:
        """Queue a backtest of the engine's configuration ``config_name``, or of ``text``
        (named ``name``); its ``id`` and status."""
        body = {"config_name": config_name} if text is None else {"config": text, "name": name}
        answer = await self._call("POST", "/backtests", json=body)
        if not isinstance(answer, dict):
            raise EngineError(f"the engine at {self.url} did not say what it queued")
        return answer

    async def backtests(self) -> list[dict[str, Any]]:
        return await self._take(list, "/backtests", "backtests")

    async def backtest(self, id_: str) -> dict[str, Any]:
        return await self._take(dict, f"/backtests/{_part(id_)}")

    async def report(self, id_: str) -> dict[str, Any]:
        return await self._take(dict, f"/backtests/{_part(id_)}/report")

    async def contents(self, id_: str) -> dict[str, Any]:
        return await self._take(dict, f"/backtests/{_part(id_)}/contents", "items")

    async def bars(self, id_: str, stream: str) -> list[dict[str, Any]]:
        return await self._take(
            list, f"/backtests/{_part(id_)}/bars", "items", params={"stream": stream}
        )

    async def node(self, id_: str, name: str) -> list[dict[str, Any]]:
        return await self._take(list, f"/backtests/{_part(id_)}/nodes/{_part(name)}", "items")

    async def signals(self, id_: str, strategy: str) -> list[dict[str, Any]]:
        return await self._take(
            list, f"/backtests/{_part(id_)}/signals", "items", params={"strategy": strategy}
        )

    async def fills(self, id_: str, strategy: str) -> list[dict[str, Any]]:
        return await self._take(
            list, f"/backtests/{_part(id_)}/fills", "items", params={"strategy": strategy}
        )

    async def equity(self, id_: str, strategy: str) -> list[dict[str, Any]]:
        return await self._take(list, f"/backtests/{_part(id_)}/equity/{_part(strategy)}", "items")


def _part(text: str) -> str:
    """``text`` as one segment of a path: a ``/`` in it is part of the name."""
    return quote(text, safe="")
