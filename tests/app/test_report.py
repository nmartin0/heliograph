# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""A run with simulated fills, and its report, against figures worked out by hand."""

import json
import sqlite3
from pathlib import Path
from typing import Literal

import pytest

from heliograph.app.config import ConfigError
from heliograph.app.report import figures, report
from heliograph.app.run import JOURNALLED
from heliograph.core.answers import SignalAnswer, SignalExpired, Verdict
from heliograph.core.broker import Fill
from heliograph.core.errors import InvalidValueError
from heliograph.core.halt import Cleared, Halted
from heliograph.core.journal import JournalEntry, Origin
from heliograph.core.pipeline import Signal, SignalRefused
from heliograph.core.time import Timestamp
from heliograph.plugins.registry import Registry
from heliograph.plugins.sqlite_journal import (
    RESERVED_HEADER,
    SqliteJournal,
    SqliteJournalParams,
    read_entries,
    read_header,
)
from tests.app.test_run_strategies import HOUR, START, component, config, run, strategy
from tests.support.plugins import Units


@pytest.fixture
def registry() -> Registry:
    registry = Registry()
    for kind in ("data_sources", "indicators", "forecasters", "sizers", "fills", "risk_gates"):
        registry.discover(kind)
    registry.register(Units)
    return registry


# One trade an hour, so each 1h bar opens and closes at its price.
PRICES = [100, 102, 101, 104, 103, 100]


def csv(tmp_path: Path) -> Path:
    path = tmp_path / "btc.csv"
    path.write_text(
        "time,price,size\n"
        + "".join(f"2026-10-01T{h:02d}:30:00Z,{p},1\n" for h, p in enumerate(PRICES))
    )
    return path


# The forecast is close - 100, held as units: targets 0, 2, 1, 4, 3, 0 at the closes.
COSTS = component("fills", "next_open", "costs", spread=0.02, commission=0.001)
REST = (
    component("forecasters", "linear", "lin", ["btc@1h.close"], scalar=1.0, centre=100.0)
    + component("sizers", "units", "units")
    + COSTS
    + strategy(forecasters=["lin"], sizer="units", fills="costs")
)


def test_signals_are_filled_at_the_next_open_less_half_the_spread(
    tmp_path: Path, registry: Registry
) -> None:
    journal = tmp_path / "run.sqlite"
    run(config(csv(tmp_path), REST), registry, journal)
    messages = [e.message for e in read_entries(journal, JOURNALLED)]
    signals = [m for m in messages if isinstance(m, Signal)]
    fills = [m for m in messages if isinstance(m, Fill)]
    # The signal at the close ending hour 2 holds what was filled at that bar's open.
    assert [(s.target, s.held) for s in signals] == [(2, 0), (1, 2), (4, 1), (3, 4), (0, 3)]
    # Each fills at the next bar's open, known at its close; the last has no next bar.
    assert [(f.ts - START) // HOUR for f in fills] == [3, 4, 5, 6]
    assert [(f.event_time - START) // HOUR for f in fills] == [2, 3, 4, 5]
    assert [f.signal for f in fills] == [s.id for s in signals[:4]]
    assert [f.quantity for f in fills] == [2, -1, 3, -1]
    assert [f.price for f in fills] == pytest.approx([102.01, 102.96, 104.03, 99.0])
    assert [f.commission for f in fills] == pytest.approx([0.20402, 0.10296, 0.31209, 0.099])
    assert [f.position for f in fills] == [2, 1, 4, 3]


def test_the_figures_are_the_ones_worked_out_by_hand(tmp_path: Path, registry: Registry) -> None:
    """Cash after each fill: -204.22402, -101.36698, -413.76907, -314.86807.

    Equity at the six closes: 0, 0, -2.22402, 2.63302, -1.76907, -14.86807.
    The same result by parts: -7 traded at the opens, -7.15 spread, -0.71807 commission.
    """
    journal = tmp_path / "run.sqlite"
    run(config(csv(tmp_path), REST), registry, journal)
    _, (one,) = figures(journal)
    assert (one.strategy, one.instrument, one.signals, one.refused, one.fills) == (
        "trend",
        "kraken:BTC/USD",
        5,
        0,
        4,
    )
    assert (one.bought, one.sold, one.position, one.last) == (5, 2, 3, 100)
    assert one.traded == pytest.approx(718.07)
    assert one.commission == pytest.approx(0.71807)
    assert one.slipped == pytest.approx(7.15)
    assert one.result == pytest.approx(-14.86807)
    assert one.drawdown == pytest.approx(2.63302 + 14.86807)
    assert (one.closes, one.exposed) == (6, 4)


def test_a_strategy_without_fills_reports_its_signals_and_no_result(
    tmp_path: Path, registry: Registry
) -> None:
    journal = tmp_path / "run.sqlite"
    unfilled = REST.replace('fills = "costs"\n', "").replace(COSTS, "")
    run(config(csv(tmp_path), unfilled), registry, journal)
    _, (one,) = figures(journal)
    assert (one.filled, one.signals, one.fills) == (False, 5, 0)
    assert report(journal).splitlines()[3:] == [
        "strategy trend (kraken:BTC/USD)",
        "  signals sent     5",
        "  signals refused  0",
        "  closes doubted   0",
        "  fills            not simulated (no fill model)",
    ]


def test_with_a_grace_period_the_same_fills_come_a_minute_later(
    tmp_path: Path, registry: Registry
) -> None:
    plain, graced = tmp_path / "plain.sqlite", tmp_path / "graced.sqlite"
    text = config(csv(tmp_path), REST)
    run(text, registry, plain)
    run(text.replace('bars = "1h"\n', 'bars = "1h"\ngrace = "1m"\n'), registry, graced)

    def fills(journal: Path) -> list[Fill]:
        return [e.message for e in read_entries(journal, JOURNALLED) if isinstance(e.message, Fill)]

    minute = HOUR // 60
    assert [(f.ts + minute, f.event_time, f.quantity, f.price) for f in fills(plain)] == [
        (f.ts, f.event_time, f.quantity, f.price) for f in fills(graced)
    ]
    assert figures(plain)[1] == figures(graced)[1]


# Short, then reversing to long: targets 0, -3, 2, 1 at the closes.
FLIP = [100, 97, 102, 101]


def test_a_short_reversed_to_a_long_is_accounted_by_hand(
    tmp_path: Path, registry: Registry
) -> None:
    """Sell 3 at 102 x 0.99 = 100.98, then buy 5 at 101 x 1.01 = 102.01.

    Cash: 302.94 - 0.30294 = 302.63706, then - 510.05 - 0.51005 = -207.92299.
    Equity at the closes: 0, 0, 302.63706 - 3 x 102 = -3.36294, -207.92299 + 2 x 101.
    """
    path = tmp_path / "btc.csv"
    path.write_text(
        "time,price,size\n"
        + "".join(f"2026-10-01T{h:02d}:30:00Z,{p},1\n" for h, p in enumerate(FLIP))
    )
    journal = tmp_path / "run.sqlite"
    run(config(path, REST), registry, journal)
    _, (one,) = figures(journal)
    assert (one.signals, one.fills, one.bought, one.sold, one.position) == (3, 2, 5, 3, 2)
    assert one.slipped == pytest.approx(3.06 + 5.05)
    assert one.commission == pytest.approx(0.30294 + 0.51005)
    assert one.result == pytest.approx(-5.92299)
    assert one.drawdown == pytest.approx(5.92299)
    assert (one.closes, one.exposed) == (4, 2)


def test_a_journal_that_is_not_a_backtests_or_a_sessions_has_no_report(tmp_path: Path) -> None:
    path = tmp_path / "other.sqlite"
    SqliteJournal(SqliteJournalParams(path=str(path)), {"kind": "x"}).close()
    with pytest.raises(InvalidValueError, match="is not the journal of a backtest run or live"):
        report(path)


def as_live(
    source: Path,
    target: Path,
    *later: SignalAnswer | SignalExpired | Halted | Cleared | SignalRefused,
) -> list[str]:
    """``source``'s entries as a live session's journal, then ``later`` ones; its signal ids."""
    header = {k: v for k, v in read_header(source).items() if k not in RESERVED_HEADER}
    header["kind"] = "live"
    entries = list(read_entries(source, JOURNALLED))
    with SqliteJournal(SqliteJournalParams(path=str(target)), header) as journal:
        for entry in entries:
            journal.append(entry)
        for seq, message in enumerate(later, start=len(entries) + 1):
            origin: Origin = (
                "derived" if isinstance(message, SignalExpired | SignalRefused) else "input"
            )
            journal.append(JournalEntry(seq, origin, message))
    return [e.message.id for e in entries if isinstance(e.message, Signal)]


type Answer = tuple[Verdict, float | None, float | None] | Literal["expired"]


def told(tmp_path: Path, registry: Registry, answers: dict[int, Answer]) -> Path:
    """A live journal of the run above in which signal ``n`` is answered as ``answers[n]``.

    An answer is (verdict, price, quantity), or "expired".
    """
    source = tmp_path / "run.sqlite"
    run(config(csv(tmp_path), REST), registry, source)
    ids = [e.message.id for e in read_entries(source, JOURNALLED) if isinstance(e.message, Signal)]
    later: list[SignalAnswer | SignalExpired] = []
    for n, answer in sorted(answers.items()):
        ts = Timestamp(START + (8 + n) * HOUR)
        if answer == "expired":
            later.append(SignalExpired(ts=ts, signal=ids[n]))
        else:
            verdict, price, quantity = answer
            later.append(
                SignalAnswer(ts=ts, signal=ids[n], verdict=verdict, price=price, quantity=quantity)
            )
    target = tmp_path / "live.sqlite"
    as_live(source, target, *later)
    return target


TOOK: Answer = ("took", None, None)
SKIPPED: Answer = ("skipped", None, None)


def test_a_person_who_answers_nothing_is_taken_to_have_done_what_has_happened(
    tmp_path: Path, registry: Registry
) -> None:
    """Held 2, 1, 4, 3 at the paper fills' prices; the last signal, to be flat, had no fill yet.

    Cash: -204.02, then +102.96, -312.09 and +99 (the paper fills' prices: no costs).
    """
    journal = told(tmp_path, registry, {})
    _, (one,) = figures(journal)
    assert one.yours is not None
    assert (one.yours.took, one.yours.skipped, one.yours.expired, one.yours.waiting) == (0, 0, 0, 5)
    assert one.yours.position == 3
    assert one.yours.result == pytest.approx(-14.15)


def test_skipped_and_expired_signals_leave_the_person_where_they_were(
    tmp_path: Path, registry: Registry
) -> None:
    """Took 0 (2 at 102.01) and 3 (up to 3 at 99), skipped 1 and 2, the last expired.

    The person holds 3 at the end: cash -204.02 - 99 = -303.02, plus 3 x 100.
    """
    journal = told(
        tmp_path,
        registry,
        {0: TOOK, 1: SKIPPED, 2: SKIPPED, 3: TOOK, 4: "expired"},
    )
    _, (one,) = figures(journal)
    assert one.yours is not None
    assert (one.yours.took, one.yours.skipped, one.yours.expired, one.yours.waiting) == (2, 2, 1, 0)
    assert one.yours.position == 3
    assert one.yours.result == pytest.approx(-3.02)


def test_the_price_and_quantity_a_person_gave_are_the_ones_used(
    tmp_path: Path, registry: Registry
) -> None:
    """Bought 1.5 at 103 (cash -154.5), skipped three, then took the last: sold 1.5 at 100."""
    journal = told(
        tmp_path,
        registry,
        {
            0: ("took", 103.0, 1.5),
            1: SKIPPED,
            2: SKIPPED,
            3: SKIPPED,
            4: TOOK,
        },
    )
    _, (one,) = figures(journal)
    assert one.yours is not None
    assert one.yours.position == 0
    assert one.yours.result == pytest.approx(-4.5)


def test_an_answer_after_an_expiry_replaces_it_and_the_person_may_change_their_mind(
    tmp_path: Path, registry: Registry
) -> None:
    source = tmp_path / "run.sqlite"
    run(config(csv(tmp_path), REST), registry, source)
    ids = [e.message.id for e in read_entries(source, JOURNALLED) if isinstance(e.message, Signal)]
    target = tmp_path / "live.sqlite"
    as_live(
        source,
        target,
        SignalExpired(ts=Timestamp(START + 8 * HOUR), signal=ids[0]),
        SignalAnswer(ts=Timestamp(START + 9 * HOUR), signal=ids[0], verdict="took"),
        SignalAnswer(ts=Timestamp(START + 10 * HOUR), signal=ids[1], verdict="took"),
        SignalAnswer(ts=Timestamp(START + 11 * HOUR), signal=ids[1], verdict="skipped"),
    )
    _, (one,) = figures(target)
    assert one.yours is not None
    assert (one.yours.took, one.yours.skipped, one.yours.expired, one.yours.waiting) == (1, 1, 0, 3)


def test_a_sessions_report_says_what_the_person_did_beside_the_paper_result(
    tmp_path: Path, registry: Registry
) -> None:
    journal = told(tmp_path, registry, {0: TOOK, 1: SKIPPED, 4: "expired"})
    lines = report(journal).splitlines()
    assert lines[0].startswith("Live session from ")
    text = "\n".join(lines)
    assert "your answers" in text
    assert "took 1, skipped 1, expired 1, waiting 2" in text
    assert "your position" in text
    assert "against the paper result" in text


def test_a_backtest_has_no_answers_to_report(tmp_path: Path, registry: Registry) -> None:
    journal = tmp_path / "run.sqlite"
    run(config(csv(tmp_path), REST), registry, journal)
    _, (one,) = figures(journal)
    assert one.yours is None
    assert "your answers" not in report(journal)


@pytest.mark.parametrize(
    ("rest", "reason"),
    [
        (
            REST.replace('fills = "costs"', 'fills = "units"'),
            "'units' is not a component of kind fills",
        ),
        (REST.replace('fills = "costs"\n', ""), r"\(costs\): no strategy uses this fill"),
    ],
)
def test_fills_that_cannot_be_assembled_are_refused(
    tmp_path: Path, registry: Registry, rest: str, reason: str
) -> None:
    with pytest.raises(ConfigError, match=reason):
        run(config(csv(tmp_path), rest), registry, tmp_path / "run.sqlite")


def test_closes_doubted_are_counted_apart_from_signals_refused(
    tmp_path: Path, registry: Registry
) -> None:
    """A bad print of 150 between 101 and 103: doubted by limits, counted as such."""
    path = tmp_path / "btc.csv"
    path.write_text(
        "time,price,size\n"
        + "".join(
            f"2026-10-01T{h:02d}:30:00Z,{p},1\n"
            for h, p in enumerate([100, 102, 101, 150, 103, 100])
        )
    )
    rest = REST.replace('fills = "costs"', 'fills = "costs"\nrisk_gates = ["limits"]') + component(
        "risk_gates", "limits", "limits", max_jump=0.2
    )
    journal = tmp_path / "run.sqlite"
    run(config(path, rest), registry, journal)
    _, (one,) = figures(journal)
    assert (one.doubted, one.refused) == (1, 0)
    assert "  closes doubted        1\n" in report(journal)


def test_late_data_a_run_left_out_is_recorded_and_reported(
    tmp_path: Path, registry: Registry
) -> None:
    journal = tmp_path / "run.sqlite"
    run(config(csv(tmp_path), REST), registry, journal)
    assert json.loads(read_header(journal)["late"]) == {"btc@1h": 0}
    assert "LATE" not in report(journal)  # nothing left out, nothing said
    with sqlite3.connect(journal) as db:  # as if three trades had come after their bar
        db.execute("UPDATE header SET value = ? WHERE key = 'late'", ('{"btc@1h":3}',))
    db.close()
    assert report(journal).splitlines()[2] == (
        "LATE DATA LEFT OUT (after its bar closed): btc@1h 3"
    )


def halted_journal(tmp_path: Path, registry: Registry, *later: Halted | Cleared) -> Path:
    source = tmp_path / "run.sqlite"
    run(config(csv(tmp_path), REST), registry, source)
    target = tmp_path / "live.sqlite"
    as_live(source, target, *later)
    return target


def test_a_halt_the_owner_cleared_is_reported_with_who_halted_it_and_why(
    tmp_path: Path, registry: Registry
) -> None:
    journal = halted_journal(
        tmp_path,
        registry,
        Halted(ts=Timestamp(START + 7 * HOUR), reason="exchange maintenance"),
        Cleared(ts=Timestamp(START + 9 * HOUR)),
    )
    lines = report(journal).splitlines()
    assert lines[2:4] == [
        "HALTED 2026-10-01T07:00:00Z by owner: exchange maintenance",
        "halt cleared 2026-10-01T09:00:00Z",
    ]
    assert "STILL HALTED" not in "\n".join(lines)


def test_a_session_still_halted_at_the_end_of_its_journal_says_so(
    tmp_path: Path, registry: Registry
) -> None:
    journal = halted_journal(
        tmp_path,
        registry,
        Halted(ts=Timestamp(START + 7 * HOUR), reason="no price of BTC/USD for 5m", by="silence:t"),
        Halted(ts=Timestamp(START + 8 * HOUR), reason="a second halt says nothing new"),
    )
    lines = report(journal).splitlines()
    assert lines[2:4] == [
        "HALTED 2026-10-01T07:00:00Z by silence:t: no price of BTC/USD for 5m",
        "STILL HALTED at the end of the journal",
    ]


def test_signals_a_halt_kept_in_are_counted_apart(tmp_path: Path, registry: Registry) -> None:
    source = tmp_path / "run.sqlite"
    run(config(csv(tmp_path), REST), registry, source)
    instrument = next(
        e.message.instrument
        for e in read_entries(source, JOURNALLED)
        if isinstance(e.message, Signal)
    )

    def refused(hour: int, by: str) -> SignalRefused:
        return SignalRefused(
            ts=Timestamp(START + hour * HOUR),
            id=f"r{hour}",
            strategy="trend",
            instrument=instrument,
            target=1.0,
            held=0.0,
            price=100.0,
            exit="",
            refused_by=by,
            reason="why",
        )

    target = tmp_path / "live.sqlite"
    as_live(source, target, refused(7, "halt"), refused(8, "halt"), refused(9, "limits"))
    _, (one,) = figures(target)
    assert (one.refused, one.by_halt) == (3, 2)
    rows = [line.split() for line in report(target).splitlines()]
    refused_row = next(r for r in rows if r[:2] == ["signals", "refused"])
    halt_row = next(r for r in rows if r[:4] == ["of", "which", "by", "a"])
    assert (refused_row[-1], halt_row[-1]) == ("3", "2")
    assert rows.index(halt_row) == rows.index(refused_row) + 1


def test_a_backtest_report_has_no_halt_lines(tmp_path: Path, registry: Registry) -> None:
    journal = tmp_path / "run.sqlite"
    run(config(csv(tmp_path), REST), registry, journal)
    text = report(journal)
    assert "HALTED" not in text
    assert "of which by a halt" not in text


def test_a_clear_when_nothing_was_halted_says_nothing(tmp_path: Path, registry: Registry) -> None:
    journal = halted_journal(tmp_path, registry, Cleared(ts=Timestamp(START + 7 * HOUR)))
    text = report(journal)
    assert "halt cleared" not in text
    assert "HALTED" not in text
