# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The window's client of the engine's API."""

import asyncio
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from heliograph.gui.client import Engine, EngineError
from tests.support.api import RUN


def run(engine_at: Callable[[], Engine], what: Callable[[Engine], Any]) -> Any:
    async def go() -> Any:
        engine = engine_at()
        try:
            return await what(engine)
        finally:
            await engine.close()

    return asyncio.run(go())


def test_it_reads_what_the_engine_offers(engine_at: Callable[[], Engine]) -> None:
    async def ask(engine: Engine) -> dict[str, Any]:
        return {
            "health": await engine.health(),
            "plugins": await engine.plugins(),
            "ewmac": await engine.plugin("forecasters", "ewmac"),
            "pipeline": await engine.pipeline(),
            "configs": await engine.configs(),
            "text": await engine.config("trend"),
            "runs": await engine.backtests(),
            "one": await engine.backtest(RUN),
            "report": await engine.report(RUN),
        }

    got = run(engine_at, ask)
    assert got["health"]["status"] == "ok"
    ewmac = next(p for p in got["plugins"] if (p["kind"], p["name"]) == ("forecasters", "ewmac"))
    assert ewmac["summary"]
    assert got["ewmac"]["params"]["properties"]["fast"]
    assert ("sizer", "sizers", False) in [
        (r["role"], r["kind"], r["many"]) for r in got["pipeline"]
    ]
    assert "trend" in got["configs"]
    assert 'mode = "backtest"' in got["text"]
    assert [r["id"] for r in got["runs"]] == [RUN]
    assert got["one"]["status"] == "complete"
    assert got["report"]["strategies"][0]["strategy"] == "trend"


def test_it_reads_a_runs_series(engine_at: Callable[[], Engine]) -> None:
    async def ask(engine: Engine) -> dict[str, Any]:
        return {
            "contents": await engine.contents(RUN),
            "bars": await engine.bars(RUN, "btc@1h"),
            "node": await engine.node(RUN, "ewmac16"),
            "signals": await engine.signals(RUN, "trend"),
            "fills": await engine.fills(RUN, "trend"),
            "equity": await engine.equity(RUN, "trend"),
        }

    got = run(engine_at, ask)
    assert [s["stream"] for s in got["contents"]["streams"]] == ["btc@1h"]
    assert len(got["bars"]) == 2880
    for series in ("node", "signals", "fills", "equity"):
        assert got[series], series
    assert {"t", "value"} <= set(got["node"][0])


def test_it_submits_a_configuration_by_name_or_by_text() -> None:
    sent: list[tuple[str, str, bytes]] = []

    def accept(request: httpx.Request) -> httpx.Response:
        sent.append((request.method, request.url.path, request.content))
        return httpx.Response(202, json={"id": "x-1", "status": "queued"})

    async def ask() -> list[dict[str, Any]]:
        http = httpx.AsyncClient(transport=httpx.MockTransport(accept), base_url="http://e")
        engine = Engine("http://e", http)
        return [await engine.submit("trend"), await engine.submit(text="mode = 1", name="mine")]

    assert asyncio.run(ask()) == [{"id": "x-1", "status": "queued"}] * 2
    assert [(m, p, json.loads(b)) for m, p, b in sent] == [
        ("POST", "/api/backtests", {"config_name": "trend"}),
        ("POST", "/api/backtests", {"config": "mode = 1", "name": "mine"}),
    ]


def test_what_the_engine_refuses_is_said_in_its_words(engine_at: Callable[[], Engine]) -> None:
    async def ask(engine: Engine) -> None:
        await engine.backtest("nonsense-20260101T000000Z")

    with pytest.raises(EngineError, match="no backtest") as caught:
        run(engine_at, ask)
    assert caught.value.status == 404


def test_an_engine_that_cannot_be_reached_is_said_to_be_so() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    async def ask() -> None:
        http = httpx.AsyncClient(transport=httpx.MockTransport(refuse), base_url="http://gone")
        await Engine("http://gone/", http).health()

    with pytest.raises(EngineError, match=r"engine at http://gone cannot be reached") as caught:
        asyncio.run(ask())
    assert caught.value.status == 0


def test_an_answer_that_is_not_json_is_still_an_error_with_its_status() -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="bad gateway")

    async def ask() -> None:
        http = httpx.AsyncClient(transport=httpx.MockTransport(broken), base_url="http://gw")
        await Engine("http://gw", http).health()

    with pytest.raises(EngineError, match="answered 502") as caught:
        asyncio.run(ask())
    assert caught.value.status == 502


@pytest.mark.parametrize(
    ("call", "body"),
    [
        (lambda e: e.health(), "not json at all"),
        (lambda e: e.plugins(), {"nope": []}),
        (lambda e: e.plugins(), {"plugins": "x"}),
        (lambda e: e.backtests(), [1, 2]),
        (lambda e: e.contents("r"), {"items": []}),
        (lambda e: e.bars("r", "s"), {"items": {}}),
        (lambda e: e.config("c"), {"text": 4}),
        (lambda e: e.configs(), {"configs": [{"nope": 1}]}),
        (lambda e: e.configs(), {"configs": [3]}),
        (lambda e: e.submit("c"), ["queued"]),
    ],
)
def test_an_answer_in_a_shape_the_engine_never_gives_is_an_engine_error(
    call: Callable[[Engine], Any], body: Any
) -> None:
    """A web server that is not the engine answering 200: the person is told, not shown
    a traceback."""

    def answer(request: httpx.Request) -> httpx.Response:
        if isinstance(body, str):
            return httpx.Response(200, text=body)
        return httpx.Response(200, json=body)

    async def ask() -> None:
        http = httpx.AsyncClient(transport=httpx.MockTransport(answer), base_url="http://other")
        await call(Engine("http://other", http))

    with pytest.raises(EngineError, match=r"other"):
        asyncio.run(ask())


def test_a_timeout_says_what_happened_though_httpx_says_nothing_of_it() -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("")

    async def ask() -> None:
        http = httpx.AsyncClient(transport=httpx.MockTransport(slow), base_url="http://slow")
        await Engine("http://slow", http).health()

    with pytest.raises(EngineError, match="cannot be reached: ReadTimeout"):
        asyncio.run(ask())


def test_a_slash_in_a_name_stays_in_its_segment_of_the_path() -> None:
    seen: list[str] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.raw_path.decode())
        return httpx.Response(200, json={"items": []})

    async def ask() -> None:
        http = httpx.AsyncClient(transport=httpx.MockTransport(record), base_url="http://e")
        engine = Engine("http://e", http)
        await engine.node("run", "a/b")
        await engine.equity("run", "x/y")

    asyncio.run(ask())
    assert seen == ["/api/backtests/run/nodes/a%2Fb", "/api/backtests/run/equity/x%2Fy"]
