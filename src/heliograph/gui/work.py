# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""What a screen sets going in the background when a person makes a choice, counted so
that a test (or a caller) can wait for it to be done rather than for a time."""

import asyncio
from collections.abc import Awaitable, Callable

from nicegui import background_tasks


class Work:
    """Background work counted. A bug in it is told to ``on_bug`` (to put on the screen)
    and raised on, for NiceGUI to log."""

    def __init__(self, on_bug: Callable[[str], None]) -> None:
        self._on_bug = on_bug
        self._working = 0
        self._idle = asyncio.Event()
        self._idle.set()

    def start(self, work: Awaitable[None]) -> None:
        """Run ``work`` in the background, counted."""
        self._working += 1
        self._idle.clear()
        background_tasks.create(self._count(work))

    async def _count(self, work: Awaitable[None]) -> None:
        try:
            await work
        except Exception as exc:  # a bug: say so on the screen, and let NiceGUI log it
            self._on_bug(f"something went wrong in the window: {exc!r}")
            raise
        finally:
            self._working -= 1
            if not self._working:
                self._idle.set()

    async def settled(self) -> None:
        """Wait until nothing set going is still going."""
        await self._idle.wait()
