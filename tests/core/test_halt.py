# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The Halted state: refuses to add risk until the owner clears it."""

from heliograph.core.bus import MessageBus
from heliograph.core.clock import SimulatedClock
from heliograph.core.halt import Cleared, Halt, Halted
from heliograph.core.pipeline import Proposal
from heliograph.core.time import Timestamp
from tests.support.world import BTC

NOW = Timestamp(1_000)


def proposal() -> Proposal:
    return Proposal(
        ts=NOW, strategy="trend", instrument=BTC, price=100.0, previous=None, held=0.0, target=1.0
    )


def gate() -> tuple[Halt, MessageBus]:
    bus = MessageBus(SimulatedClock(NOW))
    return Halt(bus), bus


def test_nothing_is_refused_until_something_halts() -> None:
    halt, _ = gate()
    assert halt.halted is None
    assert halt.check(proposal()) is None


def test_a_halt_refuses_with_who_halted_and_why() -> None:
    halt, bus = gate()
    bus.publish(Halted(ts=NOW, reason="exchange maintenance"))
    assert halt.check(proposal()) == "halted by owner: exchange maintenance"
    bus.publish(Halted(ts=NOW, reason="no price for 5m", by="stale:btc"), origin="derived")
    # the first halt stands; a watch tripping later does not rewrite why
    assert halt.check(proposal()) == "halted by owner: exchange maintenance"


def test_a_watchs_halt_is_named_for_the_watch() -> None:
    halt, bus = gate()
    bus.publish(Halted(ts=NOW, reason="no price for 5m", by="stale:btc"), origin="derived")
    assert halt.check(proposal()) == "halted by stale:btc: no price for 5m"


def test_only_a_clear_ends_a_halt_and_clearing_when_not_halted_is_harmless() -> None:
    halt, bus = gate()
    bus.publish(Cleared(ts=NOW))
    assert halt.check(proposal()) is None
    bus.publish(Halted(ts=NOW, reason="x"))
    bus.publish(Halted(ts=NOW, reason="y"))
    assert halt.check(proposal()) is not None
    bus.publish(Cleared(ts=NOW))
    assert halt.check(proposal()) is None
