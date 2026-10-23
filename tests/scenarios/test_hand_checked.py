# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Phase 2's gate: a backtest worked out by hand, its report matched exactly.

tests/scenarios/hand-checked.toml runs six hourly trades (100, 102, 101,
104, 103, 100) through a forecast of ``close - 100`` held as units, filled
at the next bar's open with half a 2% spread and a 0.1% commission.

By hand. Targets at the six closes: 0, 2, 1, 4, 3, 0, so five signals (the
first close's 0 is what is held). Each is filled at the next bar's open,
and the last has no next bar:

=====  ========  ======================  ==========  ===========
fill   quantity  price                   commission  cash after
=====  ========  ======================  ==========  ===========
1      +2        101 x 1.01 = 102.01     0.20402     -204.22402
2      -1        104 x 0.99 = 102.96     0.10296     -101.36698
3      +3        103 x 1.01 = 104.03     0.31209     -413.76907
4      -1        100 x 0.99 =  99.00     0.09900     -314.86807
=====  ========  ======================  ==========  ===========

Equity (cash plus position at the close) at the six closes: 0, 0,
-204.22402 + 2 x 101 = -2.22402, 2.63302, -1.76907, -14.86807; the largest
drawdown is from 2.63302 to -14.86807, 17.50109. Bought 2 + 3, sold 1 + 1;
traded 204.02 + 102.96 + 312.09 + 99.00 = 718.07. Paid beyond the opens:
2 x 1.01 + 1 x 1.04 + 3 x 1.03 + 1 x 1.00 = 7.15. In the market at 4 of
the 6 closes. The same loss by parts: trading at the opens loses 7.00,
less 7.15 of spread and 0.71807 of commission, is -14.86807.
"""

from pathlib import Path

import pytest
from inline_snapshot import snapshot

from heliograph.app.config import load_config
from heliograph.app.report import report
from heliograph.app.run import replay_backtest, run_backtest
from heliograph.core.time import from_iso
from heliograph.plugins.registry import Registry
from heliograph.plugins.sqlite_journal import raw_rows
from tests.support.plugins import Units
from tests.support.simulated import SimulatedTime

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def registry() -> Registry:
    registry = Registry.installed()
    registry.register(Units)
    return registry


def test_the_report_is_the_one_worked_out_by_hand(
    tmp_path: Path, registry: Registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(ROOT)  # the configuration's data path is the repository's
    time = SimulatedTime(start=from_iso("2026-10-23T00:00:00Z"))
    journal = tmp_path / "run.sqlite"
    loaded = load_config(ROOT / "tests" / "scenarios" / "hand-checked.toml")
    run_backtest(loaded, registry, journal, time.clock, time.advance)
    assert report(journal) == snapshot("""\
Backtest 2026-10-01T00:00:00Z to 2026-10-02T00:00:00Z
configuration 9eb670d5defd51c0

strategy trend (kraken:BTC/USD)
  signals sent          5
  signals refused       0
  closes doubted        0
  fills                 4
  units bought          5.00
  units sold            2.00
  value traded          718.07
  commission            0.72
  spread and slippage   7.15
  position at the end   3.00
  last close            100.00
  profit and loss       -14.87
  largest drawdown      17.50
  closes in the market  4 of 6 (66.7%)
""")
    replayed = replay_backtest(journal, tmp_path / "replay.sqlite", registry)
    assert raw_rows(replayed.journal) == raw_rows(journal)
    assert report(replayed.journal) == report(journal)
