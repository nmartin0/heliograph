# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Reading batches off the event loop, for sources that read files.

A file is read by a plain generator of batches, each ``next`` run in a
worker thread so the event loop is never blocked, and each batch admitted
through the source's throttle. A thread cannot be cancelled: a read that is
cancelled while a batch is being read finishes in its thread regardless.
Closing a generator that thread is still running raises ``ValueError``
("generator already executing"), which would replace the cancellation and
leave the file open, so the generator is closed once the thread is done.
"""

import asyncio
from collections.abc import AsyncGenerator, Generator
from typing import Any

from heliograph.core.sources import Throttle


async def off_loop[T](batches: Generator[T], throttle: Throttle) -> AsyncGenerator[T]:
    """Each batch of ``batches``, read in a thread through ``throttle``.

    Use it with ``contextlib.aclosing`` so stopping early closes ``batches``.
    """
    reading: asyncio.Future[T | None] | None = None
    try:
        while True:
            async with throttle:
                # Shielded: a cancelled read still finishes in its thread, and
                # the generator is closed only once it has (see finally).
                reading = asyncio.ensure_future(asyncio.to_thread(next, batches, None))
                batch = await asyncio.shield(reading)
            if batch is None:
                return
            yield batch
    finally:
        _close_after(reading, batches)


def _close_after(reading: "asyncio.Future[Any] | None", batches: Generator[Any]) -> None:
    """Close ``batches`` now, or when the read still running in its thread ends."""
    if reading is None or reading.done():
        batches.close()
        return

    def close(done: "asyncio.Future[Any]") -> None:
        if not done.cancelled():
            done.exception()  # retrieved: nobody else will
        batches.close()

    reading.add_done_callback(close)
