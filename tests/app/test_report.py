# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""A run with simulated fills, and its report, against figures worked out by hand."""

import json
import sqlite3
from pathlib import Path

import pytest

from heliograph.app.config import ConfigError
from heliograph.app.report import figures, report
from heliograph.app.run import JOURNALLED
from heliograph.core.broker import Fill
from heliograph.core.errors import InvalidValueError
from heliograph.core.pipeline import Signal
from heliograph.plugins.registry import Registry
from heliograph.plugins.sqlite_journal import (
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


def test_a_journal_that_is_not_a_backtests_has_no_report(tmp_path: Path) -> None:
    path = tmp_path / "other.sqlite"
    SqliteJournal(SqliteJournalParams(path=str(path)), {"kind": "live"}).close()
    with pytest.raises(InvalidValueError, match="is not the journal of a backtest run"):
        report(path)


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
