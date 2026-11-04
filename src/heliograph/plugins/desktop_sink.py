# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""A sink that tells a person on the machine the session runs on (decision D60).

It shows a desktop notification with the program the system already has:
``notify-send`` on Linux, ``osascript`` on macOS. Nothing is installed and no
secret is involved. The program is run directly, never through a shell, so
text from a signal cannot be taken for a command.

How a failure is read decides what the dispatcher does with it: no such
program (or no supported system) is ``InvalidValueError``, as asking again
cannot help; a program that ran and failed (no screen to show on, say) is
``SinkUnavailableError``, asked again later.
"""

import asyncio
import sys
from collections.abc import Callable, Sequence
from typing import ClassVar

from heliograph.core.errors import InvalidValueError
from heliograph.core.pipeline import Signal
from heliograph.core.sinks import Alert, Delivery, SinkUnavailableError
from heliograph.core.sources import Health
from heliograph.plugins.conformance import Conformance
from heliograph.plugins.manifest import Manifest, Params, Plugin, param
from heliograph.plugins.notify_sinks import details, headline

type Run = Callable[[Sequence[str]], tuple[int, str]]
"""Runs a command, giving its exit status and what it wrote to stderr."""


async def _run(command: Sequence[str]) -> tuple[int, str]:
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise InvalidValueError(f"{command[0]} is not installed") from exc
    _, errors = await process.communicate()
    return process.returncode or 0, errors.decode(errors="replace").strip()


def _applescript(text: str) -> str:
    """``text`` as an AppleScript string literal."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def command_for(platform: str, title: str, text: str, urgent: bool) -> list[str]:
    """The command that shows ``title`` and ``text`` on ``platform`` (``sys.platform``)."""
    if platform.startswith("linux"):
        # "--" so a title that begins with a dash is not read as an option.
        return ["notify-send", "-u", "critical" if urgent else "normal", "--", title, text]
    if platform == "darwin":
        script = f"display notification {_applescript(text)} with title {_applescript(title)}"
        return ["osascript", "-e", script]
    raise InvalidValueError(f"desktop notifications are not supported on {platform}")


class DesktopParams(Params):
    urgent: bool = param(False, description="Ask for the notification to stay until dismissed")


class Desktop(Plugin[DesktopParams]):
    """Shows each signal, and each alert, as a desktop notification."""

    manifest = Manifest(
        kind="sinks",
        name="desktop",
        version="1",
        summary="Notifies on the desktop of the machine the session runs on",
        params=DesktopParams,
    )
    conformance = Conformance(params={})

    run: ClassVar[Run | None] = None
    """Tests put a fake here; nothing else does."""
    platform: ClassVar[str | None] = None
    """Tests put a platform name here; nothing else does."""

    async def _show(self, title: str, text: str, *, urgent: bool) -> Delivery:
        command = command_for(type(self).platform or sys.platform, title, text, urgent)
        runner = type(self).run
        status, errors = await (_run(command) if runner is None else _as_async(runner, command))
        if status != 0:
            raise SinkUnavailableError(f"{command[0]} failed ({status}): {errors or 'no reason'}")
        return Delivery(True, command[0])

    async def emit(self, signal: Signal) -> Delivery:
        return await self._show(headline(signal), details(signal), urgent=self.params.urgent)

    async def alert(self, alert: Alert) -> Delivery:
        return await self._show(alert.title, alert.text, urgent=True)

    async def health_check(self) -> Health:
        try:
            command_for(type(self).platform or sys.platform, "", "", False)
        except InvalidValueError as exc:
            return Health(ok=False, detail=str(exc))
        return Health(ok=True, detail="notifications are shown when there is something to say")

    async def close(self) -> None:
        return None


async def _as_async(runner: Run, command: Sequence[str]) -> tuple[int, str]:
    return runner(command)
