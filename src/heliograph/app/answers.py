# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""How a person's answers reach a live session: a folder of small files (decision D50).

A live session is a process of its own, so ``heliograph answer`` cannot call it.
It leaves a file in the session's inbox, a folder beside the journal
(``<journal>.answers``); the session looks in the folder now and then, turns each
file into a ``SignalAnswer`` stamped by its clock, and publishes it as an input.
A file is written under a temporary name and renamed, so the session never reads
half of one. An answer the session cannot use (not JSON, not an answer, a signal
it has not seen) is moved to ``rejected/`` with a ``.why`` beside it, never lost.
"""

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from heliograph.app.limits import Sleep
from heliograph.core.answers import SignalAnswer, Verdict
from heliograph.core.clock import Clock
from heliograph.core.engine import LiveEngine
from heliograph.core.errors import HeliographError
from heliograph.core.pipeline import Signal

FIELDS = ("signal", "verdict", "price", "quantity")
INTERVAL = 1.0
"""Seconds between looks in the inbox."""


def inbox_for(journal: Path) -> Path:
    """The inbox of the session journalling to ``journal``."""
    return journal.with_name(f"{journal.name}.answers")


def submit(
    inbox: Path,
    clock: Clock,
    signal: str,
    verdict: Verdict,
    price: float | None = None,
    quantity: float | None = None,
) -> Path:
    """Leave an answer in ``inbox``; the file written.

    Raises:
        HeliographError: if there is no inbox (no session has made it).
        InvalidValueError: if the answer is not valid.
    """
    if not inbox.is_dir():
        raise HeliographError(f"{inbox} does not exist: no live session has started with it")
    now = clock.now()
    SignalAnswer(ts=now, signal=signal, verdict=verdict, price=price, quantity=quantity)
    text = json.dumps(
        {"signal": signal, "verdict": verdict, "price": price, "quantity": quantity},
        sort_keys=True,
    )
    number = 0
    while True:  # linking is exclusive: two answers in one nanosecond get two names
        name = f"{now:020d}-{number}.json"
        pending = inbox / f"{name}.tmp"  # not *.json, so the session does not read it yet
        pending.write_text(text, encoding="utf-8")
        target = inbox / name
        try:
            os.link(pending, target)
        except FileExistsError:
            number += 1
            continue
        finally:
            pending.unlink()
        return target


class Answers:
    """Takes the answers left in an inbox and gives them to a live engine."""

    def __init__(
        self,
        engine: LiveEngine,
        clock: Clock,
        inbox: Path,
        sleep: Sleep,
        interval: float = INTERVAL,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._inbox = inbox
        self._sleep = sleep
        self._interval = interval
        self._seen: set[str] = set()
        inbox.mkdir(parents=True, exist_ok=True)
        engine.bus.subscribe(Signal, self._note)

    def _note(self, signal: Signal) -> None:
        self._seen.add(signal.id)

    async def watch(self) -> None:
        """Look in the inbox every ``interval`` seconds until cancelled."""
        while True:
            self.poll()
            await self._sleep(self._interval)

    def poll(self) -> int:
        """Publish the answers waiting, oldest first; how many were taken."""
        taken = 0
        for path in sorted(self._inbox.glob("*.json")):
            try:
                fields = _read(path)
                answer = SignalAnswer(ts=self._clock.now(), **fields)
                if answer.signal not in self._seen:
                    raise HeliographError(f"no signal {answer.signal} has been made")
            except (HeliographError, ValueError, TypeError, OSError) as exc:
                self._reject(path, str(exc))
                continue
            self._engine.receive(answer)
            path.unlink()
            taken += 1
        return taken

    def _reject(self, path: Path, why: str) -> None:
        rejected = self._inbox / "rejected"
        rejected.mkdir(exist_ok=True)
        (rejected / f"{path.name}.why").write_text(why + "\n", encoding="utf-8")
        os.replace(path, rejected / path.name)


def _read(path: Path) -> Mapping[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) - set(FIELDS):
        raise ValueError("not an answer: expected " + ", ".join(FIELDS))
    return data
