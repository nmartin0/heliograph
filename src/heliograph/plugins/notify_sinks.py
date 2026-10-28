# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Sinks that tell a person over HTTP: ntfy, and a webhook (decision D46).

Both post one request per signal. How a reply is read decides what the
dispatcher does with it: a connection that failed, a timeout, a 429 or a 5xx
is ``SinkUnavailableError`` (asked again later); any other refusal (a 4xx, or
a URL that is no URL) is ``InvalidValueError`` (asking again cannot help).

A secret is never written in a configuration: ``token_env`` names the
environment variable that holds it, read when the sink is built, and a
variable that is not set stops the build with its name.

``ntfy`` publishes to a topic on ntfy.sh or a server of one's own.
``webhook`` posts the signal as JSON for anything that takes a webhook.
"""

import os
from collections.abc import Mapping
from typing import Any, ClassVar

import httpx
from pydantic import field_validator

from heliograph.core.errors import InvalidValueError
from heliograph.core.pipeline import Signal
from heliograph.core.sinks import Delivery, SinkUnavailableError
from heliograph.core.sources import Health
from heliograph.core.time import to_iso
from heliograph.plugins.conformance import Conformance
from heliograph.plugins.manifest import Manifest, Params, Plugin, param

AGAIN_LATER = frozenset({408, 425, 429})
"""Client errors that mean "not now" rather than "never"."""


def headline(signal: Signal) -> str:
    """A signal in one line: what to hold, of what, at what price."""
    held = f", was {signal.held:g}" if signal.held else ""
    return f"{signal.instrument.symbol}: {signal.side} {signal.target:g}{held} @ {signal.price:g}"


def details(signal: Signal) -> str:
    """The lines under the headline: who decided, why, and how to find it again."""
    lines = [
        f"strategy {signal.strategy}, forecast {signal.forecast:g}",
        *(f"  {name}: {value:g}" for name, value in sorted(signal.forecasts.items())),
    ]
    if signal.exit:
        lines.append(f"set by exit {signal.exit}")
    if signal.instead_of is not None:
        lines.append(f"instead of {signal.instead_of:g}, which a risk gate refused")
    lines.append(f"{to_iso(signal.ts)} · {signal.id}")
    return "\n".join(lines)


def as_json(signal: Signal) -> dict[str, Any]:
    """A signal as plain JSON values, every field."""
    return {
        "id": signal.id,
        "ts": signal.ts,
        "time": to_iso(signal.ts),
        "strategy": signal.strategy,
        "instrument": signal.instrument.key,
        "side": signal.side,
        "target": signal.target,
        "held": signal.held,
        "price": signal.price,
        "forecast": signal.forecast,
        "forecasts": dict(signal.forecasts),
        "vetoed_by": list(signal.vetoed_by),
        "exit": signal.exit,
        "instead_of": signal.instead_of,
        "schema": signal.schema,
    }


class _Http:
    """What the two sinks share: a lazily opened client, and reading a reply."""

    transport: ClassVar[httpx.AsyncBaseTransport | None] = None
    """Tests put a mock transport here; nothing else does."""

    _client: httpx.AsyncClient | None = None

    def _http(self, timeout: float) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=timeout, transport=type(self).transport)
        return self._client

    async def _post(
        self, url: str, timeout: float, *, headers: Mapping[str, str], **body: Any
    ) -> Delivery:
        try:
            reply = await self._http(timeout).post(url, headers=dict(headers), **body)
        except httpx.InvalidURL as exc:
            raise InvalidValueError(f"{url}: {exc}") from exc
        except httpx.HTTPError as exc:
            raise SinkUnavailableError(f"{url}: {type(exc).__name__}: {exc}") from exc
        if reply.is_success:
            return Delivery(True, f"{reply.status_code}")
        message = f"{url}: {reply.status_code} {reply.reason_phrase}"
        if reply.status_code >= 500 or reply.status_code in AGAIN_LATER:
            raise SinkUnavailableError(message)
        raise InvalidValueError(message)

    async def close(self) -> None:
        if self._client is not None:
            client, self._client = self._client, None
            await client.aclose()


def _secret(variable: str) -> str:
    """The value of environment variable ``variable``, or "" if none is named."""
    if not variable:
        return ""
    value = os.environ.get(variable)
    if not value:
        raise InvalidValueError(f"token_env names {variable}, which is not set")
    return value


def _web_address(url: str) -> str:
    if not url.startswith(("http://", "https://")) or " " in url:
        raise ValueError(f"{url!r} is not an http or https address")
    return url


class NtfyParams(Params):
    topic: str = param(
        ..., description="The ntfy topic to publish to (anyone who knows it can read)"
    )
    server: str = param("https://ntfy.sh", description="The ntfy server")
    token_env: str = param("", description="Environment variable holding an access token, if any")
    priority: int = param(3, description="ntfy priority, 1 (min) to 5 (urgent)", ge=1, le=5)
    timeout: float = param(10.0, description="Seconds before a request is abandoned", ge=1, le=120)

    @field_validator("server")
    @classmethod
    def _address(cls, server: str) -> str:
        return _web_address(server)

    @field_validator("topic")
    @classmethod
    def _topic(cls, topic: str) -> str:
        if not topic or "/" in topic or " " in topic:
            raise ValueError("a topic is one word, with no slash or space")
        return topic


class Ntfy(Plugin[NtfyParams], _Http):
    """Publishes each signal to an ntfy topic."""

    manifest = Manifest(
        kind="sinks",
        name="ntfy",
        version="1",
        summary="Notifies a phone or desktop through ntfy",
        params=NtfyParams,
    )
    conformance = Conformance(params={"topic": "heliograph-test"})

    def __init__(self, params: NtfyParams) -> None:
        super().__init__(params)
        self._token = _secret(params.token_env)

    @property
    def _url(self) -> str:
        return f"{self.params.server.rstrip('/')}/{self.params.topic}"

    async def emit(self, signal: Signal) -> Delivery:
        headers = {
            "Title": headline(signal),
            "Priority": str(self.params.priority),
            "Tags": signal.side,
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return await self._post(
            self._url, self.params.timeout, headers=headers, content=details(signal).encode()
        )

    async def health_check(self) -> Health:
        try:
            reply = await self._http(self.params.timeout).get(
                f"{self.params.server.rstrip('/')}/v1/health"
            )
        except httpx.HTTPError as exc:
            return Health(ok=False, detail=f"{type(exc).__name__}: {exc}")
        return Health(
            ok=reply.is_success, detail="" if reply.is_success else str(reply.status_code)
        )


class WebhookParams(Params):
    url: str = param(..., description="Where to POST each signal, as JSON")
    token_env: str = param("", description="Environment variable holding a bearer token, if any")
    timeout: float = param(10.0, description="Seconds before a request is abandoned", ge=1, le=120)

    @field_validator("url")
    @classmethod
    def _address(cls, url: str) -> str:
        return _web_address(url)


class Webhook(Plugin[WebhookParams], _Http):
    """Posts each signal as JSON to a URL."""

    manifest = Manifest(
        kind="sinks",
        name="webhook",
        version="1",
        summary="Posts each signal as JSON to a URL",
        params=WebhookParams,
    )
    conformance = Conformance(params={"url": "https://example.invalid/hook"})

    def __init__(self, params: WebhookParams) -> None:
        super().__init__(params)
        self._token = _secret(params.token_env)

    async def emit(self, signal: Signal) -> Delivery:
        headers = {"Idempotency-Key": signal.id}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return await self._post(
            self.params.url, self.params.timeout, headers=headers, json=as_json(signal)
        )

    async def health_check(self) -> Health:
        return Health(ok=True, detail="a webhook is not asked until there is something to say")
