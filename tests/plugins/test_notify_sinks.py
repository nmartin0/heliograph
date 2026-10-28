# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The ntfy and webhook sinks: what they post, and how a reply decides the retry."""

import asyncio
import json
from collections.abc import Callable, Coroutine
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from heliograph.app.dispatch import Dispatcher
from heliograph.core.errors import InvalidValueError
from heliograph.core.pipeline import Signal
from heliograph.core.sinks import Delivery, SinkUnavailableError
from heliograph.core.time import Timestamp, from_iso
from heliograph.plugins.notify_sinks import (
    Ntfy,
    NtfyParams,
    Webhook,
    WebhookParams,
    as_json,
    details,
    headline,
)
from tests.support.world import BTC

type Handler = Callable[[httpx.Request], httpx.Response]


def a_signal(**changes: object) -> Signal:
    fields: dict[str, object] = {
        "ts": from_iso("2026-10-28T09:30:00Z"),
        "id": "trend-abc-7",
        "strategy": "trend",
        "instrument": BTC,
        "side": "long",
        "target": 2.0,
        "held": 0.0,
        "price": 67_250.5,
        "forecast": 8.5,
        "forecasts": {"ewmac": 8.5, "slow": 3.0},
    }
    return Signal(**{**fields, **changes})  # type: ignore[arg-type]


class Server:
    """A mock HTTP server: every request it gets, and the reply it gives."""

    def __init__(self, handler: Handler | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self._handler = handler or (lambda request: httpx.Response(200))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._handler(request)


def serving(monkeypatch: pytest.MonkeyPatch, server: Server) -> None:
    transport = httpx.MockTransport(server)
    monkeypatch.setattr(Ntfy, "transport", transport)
    monkeypatch.setattr(Webhook, "transport", transport)


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> Server:
    mock = Server()
    serving(monkeypatch, mock)
    return mock


def run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coroutine)


async def emit_and_close(sink: Ntfy | Webhook, signal: Signal) -> Delivery:
    try:
        return await sink.emit(signal)
    finally:
        await sink.close()


# -- what is said -----------------------------------------------------------------------


def test_the_headline_says_what_to_hold_of_what_at_what_price() -> None:
    assert headline(a_signal()) == "BTC/USD: long 2 @ 67250.5"
    assert (
        headline(a_signal(side="flat", target=0.0, held=2.0)) == "BTC/USD: flat 0, was 2 @ 67250.5"
    )


def test_the_details_say_who_decided_and_why_and_how_to_find_it_again() -> None:
    text = details(a_signal(exit="trail", instead_of=-1.0))
    assert text.splitlines() == [
        "strategy trend, forecast 8.5",
        "  ewmac: 8.5",
        "  slow: 3",
        "set by exit trail",
        "instead of -1, which a risk gate refused",
        "2026-10-28T09:30:00Z · trend-abc-7",
    ]


def test_the_json_carries_every_field_of_the_signal() -> None:
    assert as_json(a_signal(vetoed_by=("calm",))) == {
        "id": "trend-abc-7",
        "ts": from_iso("2026-10-28T09:30:00Z"),
        "time": "2026-10-28T09:30:00Z",
        "strategy": "trend",
        "instrument": "kraken:BTC/USD",
        "side": "long",
        "target": 2.0,
        "held": 0.0,
        "price": 67250.5,
        "forecast": 8.5,
        "forecasts": {"ewmac": 8.5, "slow": 3.0},
        "vetoed_by": ["calm"],
        "exit": "",
        "instead_of": None,
        "schema": a_signal().schema,
    }


# -- ntfy -------------------------------------------------------------------------------


def test_ntfy_posts_the_details_to_the_topic_with_the_headline_as_title(server: Server) -> None:
    sink = Ntfy(NtfyParams(topic="mine", priority=4))
    assert run(emit_and_close(sink, a_signal())) == Delivery(True, "200")
    (request,) = server.requests
    assert (request.method, str(request.url)) == ("POST", "https://ntfy.sh/mine")
    assert request.headers["Title"] == "BTC/USD: long 2 @ 67250.5"
    assert request.headers["Priority"] == "4"
    assert request.headers["Tags"] == "long"
    assert request.content.decode() == details(a_signal())
    assert "Authorization" not in request.headers


def test_ntfy_on_a_server_of_ones_own_sends_its_token(
    server: Server, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NTFY_TOKEN", "tk_secret")
    sink = Ntfy(NtfyParams(topic="mine", server="http://ntfy.home:8080/", token_env="NTFY_TOKEN"))
    run(emit_and_close(sink, a_signal()))
    (request,) = server.requests
    assert str(request.url) == "http://ntfy.home:8080/mine"
    assert request.headers["Authorization"] == "Bearer tk_secret"


def test_a_token_variable_that_is_not_set_stops_the_build_naming_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NO_SUCH_TOKEN", raising=False)
    with pytest.raises(InvalidValueError, match="token_env names NO_SUCH_TOKEN, which is not set"):
        Ntfy(NtfyParams(topic="mine", token_env="NO_SUCH_TOKEN"))
    with pytest.raises(InvalidValueError, match="NO_SUCH_TOKEN"):
        Webhook(WebhookParams(url="https://h.example/x", token_env="NO_SUCH_TOKEN"))


@pytest.mark.parametrize("topic", ["", "a/b", "two words"])
def test_a_topic_is_one_word(topic: str) -> None:
    with pytest.raises(ValidationError, match="one word"):
        NtfyParams(topic=topic)


@pytest.mark.parametrize("address", ["ntfy.sh", "ftp://x.example", "https://x y.example"])
def test_a_server_or_url_must_be_an_http_address(address: str) -> None:
    with pytest.raises(ValidationError, match="not an http or https address"):
        NtfyParams(topic="mine", server=address)
    with pytest.raises(ValidationError, match="not an http or https address"):
        WebhookParams(url=address)


def test_ntfy_health_is_whether_the_server_answers_its_health_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock = Server(lambda request: httpx.Response(200, json={"healthy": True}))
    serving(monkeypatch, mock)
    sink = Ntfy(NtfyParams(topic="mine"))
    health = run(sink.health_check())
    assert health.ok
    assert str(mock.requests[0].url) == "https://ntfy.sh/v1/health"
    serving(monkeypatch, Server(lambda request: httpx.Response(503)))
    down = run(Ntfy(NtfyParams(topic="mine")).health_check())
    assert (down.ok, down.detail) == (False, "503")


def test_ntfy_health_when_the_server_cannot_be_reached_says_why(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    serving(monkeypatch, Server(refuse))
    health = run(Ntfy(NtfyParams(topic="mine")).health_check())
    assert (health.ok, health.detail) == (False, "ConnectError: no route")


# -- webhook ----------------------------------------------------------------------------


def test_a_webhook_posts_the_signal_as_json_with_its_id_as_the_idempotency_key(
    server: Server, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOOK_TOKEN", "hk")
    sink = Webhook(WebhookParams(url="https://hooks.example/heliograph", token_env="HOOK_TOKEN"))
    assert run(emit_and_close(sink, a_signal())) == Delivery(True, "200")
    (request,) = server.requests
    assert str(request.url) == "https://hooks.example/heliograph"
    assert json.loads(request.content) == as_json(a_signal())
    assert request.headers["Idempotency-Key"] == "trend-abc-7"
    assert request.headers["Authorization"] == "Bearer hk"


def test_a_webhook_has_nothing_to_check_until_it_has_something_to_say() -> None:
    health = run(Webhook(WebhookParams(url="https://h.example/x")).health_check())
    assert health.ok


# -- how a reply decides the retry ---------------------------------------------------------


@pytest.mark.parametrize("status", [408, 425, 429, 500, 502, 503])
def test_a_reply_that_means_not_now_is_unavailable(
    status: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    serving(monkeypatch, Server(lambda request: httpx.Response(status)))
    sink = Webhook(WebhookParams(url="https://h.example/x"))
    with pytest.raises(SinkUnavailableError, match=str(status)):
        run(emit_and_close(sink, a_signal()))


@pytest.mark.parametrize("status", [400, 401, 403, 404, 410])
def test_a_reply_that_means_never_is_a_refusal_not_retried(
    status: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    serving(monkeypatch, Server(lambda request: httpx.Response(status)))
    sink = Ntfy(NtfyParams(topic="mine"))
    with pytest.raises(InvalidValueError, match=str(status)):
        run(emit_and_close(sink, a_signal()))


@pytest.mark.parametrize("failure", [httpx.ConnectError("no route"), httpx.ReadTimeout("slow")])
def test_a_connection_that_failed_is_unavailable(
    failure: httpx.HTTPError, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise failure

    serving(monkeypatch, Server(fail))
    with pytest.raises(SinkUnavailableError, match=type(failure).__name__):
        run(emit_and_close(Webhook(WebhookParams(url="https://h.example/x")), a_signal()))


def test_an_address_httpx_cannot_use_is_a_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    serving(monkeypatch, Server())
    sink = Webhook(WebhookParams(url="http://example.com:port/x"))
    with pytest.raises(InvalidValueError, match=r"example\.com:port"):
        run(emit_and_close(sink, a_signal()))


def test_a_dispatcher_retries_a_sink_that_was_down_and_delivers_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replies = [httpx.Response(503), httpx.Response(503), httpx.Response(200)]
    server = Server(lambda request: replies.pop(0))
    serving(monkeypatch, server)
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    sinks = {"hook": Webhook(WebhookParams(url="https://h.example/x"))}
    dispatcher = Dispatcher(sinks, sleep=sleep, backoff=1.0)

    async def twice() -> tuple[bool, bool]:
        try:
            first = await dispatcher.dispatch(a_signal())
            second = await dispatcher.dispatch(a_signal())
            return first[0].ok, second[0].attempts == 0
        finally:
            await dispatcher.close()

    assert run(twice()) == (True, True)
    assert len(server.requests) == 3
    assert waits == [1.0, 2.0]
    assert {r.headers["Idempotency-Key"] for r in server.requests} == {"trend-abc-7"}


def test_closing_a_sink_that_never_sent_is_quiet_and_closing_twice_is_too() -> None:
    sink = Webhook(WebhookParams(url="https://h.example/x"))
    run(sink.close())
    run(sink.close())


def test_the_signal_time_is_not_needed_to_build_one() -> None:
    assert a_signal(ts=Timestamp(0)).ts == 0


class Watched(httpx.MockTransport):
    """A mock transport that knows whether it was closed."""

    closed = False

    async def aclose(self) -> None:
        self.closed = True


def test_closing_a_sink_closes_its_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = Watched(Server())
    monkeypatch.setattr(Webhook, "transport", transport)
    sink = Webhook(WebhookParams(url="https://h.example/x"))
    run(emit_and_close(sink, a_signal()))
    assert transport.closed
