# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Write the example's made-up trades: examples/data/btc-synthetic.csv.

Three trades an hour (at minutes 5, 25 and 45) for 120 days from
2026-01-01, priced by a random walk whose drift changes every 10 to 20 days
between up, down and none, so a trend strategy has something to find and
something to lose on. Seeded, and written with fixed formatting, so the
same file comes out every time: the scenario tests depend on it byte for
byte.

    uv run python scripts/make_example_data.py          # rewrite the file
    uv run python scripts/make_example_data.py --check  # fail if it differs
"""

import argparse
import math
import random
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "examples" / "data" / "btc-synthetic.csv"
START = datetime(2026, 1, 1, tzinfo=UTC)
DAYS = 120
MINUTES = (5, 25, 45)
VOL = 0.004  # per trade, about 0.7% an hour
DRIFTS = (0.0006, -0.0006, 0.0)  # per trade


def rows() -> list[str]:
    rng = random.Random(20260101)
    price = 40_000.0
    out = ["time,price,size"]
    drift, until = 0.0, 0
    for hour in range(DAYS * 24):
        if hour >= until:
            drift = rng.choice(DRIFTS)
            until = hour + rng.randint(10, 20) * 24
        for minute in MINUTES:
            price *= math.exp(drift + VOL * rng.gauss())
            size = rng.randint(1, 500) / 100
            when = START + timedelta(hours=hour, minutes=minute)
            out.append(f"{when:%Y-%m-%dT%H:%M:%SZ},{price:.2f},{size:.2f}")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if the file differs")
    text = "\n".join(rows()) + "\n"
    if parser.parse_args().check:
        if not OUT.is_file() or OUT.read_text() != text:
            print(f"{OUT.relative_to(ROOT)} is not what this script writes", file=sys.stderr)
            return 1
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
