# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The example strategy (examples/trend.toml) over its committed data, as LEAN's
regression algorithms do: what was delivered, decided and filled, and the report.

The data is made up (scripts/make_example_data.py, checked by check.sh to be
what the script writes). A change in behaviour shows as a difference here,
to accept (``uv run pytest tests/scenarios --inline-snapshot=fix``) or to
reject; the counts are kept apart from the report so a feed that delivers
less is seen as that.
"""

from collections import Counter
from pathlib import Path

import pytest
from inline_snapshot import snapshot

from heliograph.app.config import load_config
from heliograph.app.report import report
from heliograph.app.run import JOURNALLED, RunSummary, run_backtest
from heliograph.core.time import from_iso
from heliograph.plugins.registry import Registry
from heliograph.plugins.sqlite_journal import read_entries, read_header
from tests.support.simulated import SimulatedTime

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> RunSummary:
    journal = tmp_path_factory.mktemp("example") / "trend.sqlite"
    time = SimulatedTime(start=from_iso("2026-10-23T00:00:00Z"))
    with pytest.MonkeyPatch.context() as patch:
        patch.chdir(ROOT)  # the configuration's data path is the repository's
        loaded = load_config(ROOT / "examples" / "trend.toml")
        return run_backtest(loaded, Registry.installed(), journal, time.clock, time.advance)


def test_what_was_delivered_decided_and_filled(run: RunSummary) -> None:
    counts = Counter(type(e.message).__name__ for e in read_entries(run.journal, JOURNALLED))
    assert run.delivered == 8640  # three trades an hour for 120 days
    assert read_header(run.journal)["status"] == "complete"
    assert dict(sorted(counts.items())) == snapshot(
        {
            "AlarmFired": 2880,
            "Bar": 2880,
            "Fill": 275,
            "IndicatorValues": 2856,
            "Point": 8640,
            "RunEnded": 1,
            "Settled": 11520,
            "Signal": 275,
            "SignalRefused": 85,
        }
    )


def test_the_report(run: RunSummary) -> None:
    assert report(run.journal) == snapshot("""\
Backtest 2026-01-01T00:00:00Z to 2026-05-01T00:00:00Z
configuration 3bb4054eb5d03e54

strategy trend (kraken:BTC/USD)
  signals sent          275
  signals refused       85
  fills                 275
  units bought          1.98
  units sold            1.98
  value traded          127,129.07
  commission            127.13
  spread and slippage   63.56
  position at the end   0.00
  last close            23,339.44
  profit and loss       869.15
  largest drawdown      320.32
  closes in the market  485 of 2880 (16.8%)
""")
