# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The desktop sink: the command it builds for each system, and how a failure is read."""

import asyncio
from collections.abc import Iterator, Sequence

import pytest

from heliograph.core.errors import InvalidValueError
from heliograph.core.sinks import Alert, AlertSink, SinkUnavailableError
from heliograph.core.time import from_iso
from heliograph.plugins.desktop_sink import Desktop, DesktopParams, command_for
from tests.plugins.test_notify_sinks import a_signal


class Shell:
    """A stand-in for running a program: every command it was given, and its answer."""

    def __init__(self, status: int = 0, errors: str = "") -> None:
        self.commands: list[list[str]] = []
        self._answer = (status, errors)

    def __call__(self, command: Sequence[str]) -> tuple[int, str]:
        self.commands.append(list(command))
        return self._answer


@pytest.fixture
def linux() -> Iterator[Shell]:
    shell = Shell()
    Desktop.run, Desktop.platform = shell, "linux"
    yield shell
    Desktop.run, Desktop.platform = None, None


def test_linux_runs_notify_send_with_the_text_as_arguments() -> None:
    assert command_for("linux", "-T", "a; rm -rf ~", urgent=False) == [
        "notify-send", "-u", "normal", "--", "-T", "a; rm -rf ~",
    ]  # fmt: skip
    assert command_for("linux", "T", "t", urgent=True)[2] == "critical"


def test_macos_quotes_the_text_for_applescript() -> None:
    command = command_for("darwin", 'say "hi"', "a\\b", urgent=False)
    assert command == [
        "osascript",
        "-e",
        'display notification "a\\\\b" with title "say \\"hi\\""',
    ]


def test_another_system_is_refused_with_its_name() -> None:
    with pytest.raises(InvalidValueError, match="not supported on win32"):
        command_for("win32", "T", "t", urgent=False)


def test_a_signal_is_shown_with_its_headline_and_details(linux: Shell) -> None:
    delivery = asyncio.run(Desktop(DesktopParams()).emit(a_signal()))
    assert delivery.ok
    [command] = linux.commands
    assert command[:2] == ["notify-send", "-u"]
    assert command[-2] == "BTC/USD: long 2 @ 67250.5"
    assert "strategy trend, forecast 8.5" in command[-1]


def test_an_alert_is_always_urgent(linux: Shell) -> None:
    sink = Desktop(DesktopParams())
    assert isinstance(sink, AlertSink)
    asyncio.run(sink.alert(Alert(from_iso("2026-10-28T09:30:00Z"), "a1", "Halted", "no price")))
    assert linux.commands == [["notify-send", "-u", "critical", "--", "Halted", "no price"]]


def test_a_program_that_failed_is_asked_again_later(linux: Shell) -> None:
    Desktop.run = Shell(1, "Cannot open display")
    with pytest.raises(SinkUnavailableError, match=r"notify-send failed \(1\): Cannot open"):
        asyncio.run(Desktop(DesktopParams()).emit(a_signal()))


def test_a_program_that_is_missing_is_not_asked_again() -> None:
    Desktop.run, Desktop.platform = None, "linux"
    try:
        # A name no system has, so the real runner finds nothing to start.
        with pytest.raises(InvalidValueError, match="is not installed"):
            asyncio.run(_missing())
    finally:
        Desktop.platform = None


async def _missing() -> None:
    from heliograph.plugins.desktop_sink import _run

    await _run(["heliograph-no-such-program"])


def test_health_says_whether_the_system_is_supported() -> None:
    Desktop.platform = "win32"
    try:
        health = asyncio.run(Desktop(DesktopParams()).health_check())
    finally:
        Desktop.platform = None
    assert not health.ok
    assert "win32" in health.detail
