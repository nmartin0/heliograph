# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""What the engine says a strategy takes (``/api/pipeline``), for tests of the Wizard."""

import tomllib
from pathlib import Path
from typing import Any

PIPELINE: dict[str, Any] = {
    "roles": [
        {"role": "forecasters", "kind": "forecasters", "many": True, "inputs": "required"},
        {"role": "gates", "kind": "gates", "many": True, "inputs": "required"},
        {"role": "combiner", "kind": "combiners", "many": False, "inputs": "none"},
        {"role": "sizer", "kind": "sizers", "many": False, "inputs": "none"},
        {"role": "exits", "kind": "exits", "many": True, "inputs": "optional"},
        {"role": "risk_gates", "kind": "risk_gates", "many": True, "inputs": "none"},
        {"role": "fills", "kind": "fills", "many": False, "inputs": "none"},
    ],
    "graph": ["forecasters", "gates", "indicators"],
}

TREND = Path(__file__).parents[2] / "examples" / "trend.toml"


def trend() -> dict[str, Any]:
    """The example configuration, as TOML gives it."""
    return tomllib.loads(TREND.read_text())
