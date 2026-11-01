# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""``heliograph answer``: leaves a person's answer where a live session will find it."""

import json
from pathlib import Path

import pytest

from heliograph.app.answers import inbox_for
from heliograph.app.cli import FAILED, main


def test_an_answer_is_left_in_the_sessions_inbox(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    journal = tmp_path / "live.sqlite"
    inbox_for(journal).mkdir()
    status = main(["answer", str(journal), "ab12", "took", "--price", "67500", "--quantity", "-2"])
    assert status == 0
    (left,) = inbox_for(journal).glob("*.json")
    assert json.loads(left.read_text()) == {
        "signal": "ab12",
        "verdict": "took",
        "price": 67500.0,
        "quantity": -2.0,
    }
    assert "took ab12: left in" in capsys.readouterr().out


def test_skipping_needs_nothing_else(tmp_path: Path) -> None:
    journal = tmp_path / "live.sqlite"
    inbox_for(journal).mkdir()
    assert main(["answer", str(journal), "ab12", "skipped"]) == 0


def test_without_a_session_it_says_so_and_leaves_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["answer", str(tmp_path / "live.sqlite"), "ab12", "took"]) == FAILED
    assert "no live session has started with it" in capsys.readouterr().err


def test_an_invalid_answer_is_refused_before_anything_is_left(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    journal = tmp_path / "live.sqlite"
    inbox_for(journal).mkdir()
    assert main(["answer", str(journal), "ab12", "skipped", "--price", "5"]) == FAILED
    assert "skipped signal has no price or quantity" in capsys.readouterr().err
    assert not list(inbox_for(journal).iterdir())
