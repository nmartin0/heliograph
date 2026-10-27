# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The Strategy Tester: a configuration submitted, its run followed, its report shown."""

from collections.abc import Awaitable, Callable
from typing import Any

from nicegui.testing.user import User

from heliograph.gui import app
from heliograph.gui.app import workspace
from heliograph.gui.client import Engine
from heliograph.gui.tester import StrategyTester, chart_link
from tests.support import window
from tests.support.window import fake, run_answers

CONFIG = 'name = "trend"\n'
REPORT = "Heliograph backtest report\n  trend: +3.2%\n"


def scenario(
    engine_at: Callable[[], Engine], play: Callable[[User, StrategyTester], Awaitable[None]]
) -> None:
    window.scenario(engine_at, app.tester, play)


def engine_saying(status: str, **more: Any) -> Callable[[], Engine]:
    """An engine that takes a submission as run ``r-1`` and says ``status`` of it."""
    answers: dict[str, Any] = {
        "/configs": {"configs": [{"name": "trend"}, {"name": "other"}]},
        "/configs/trend": {"name": "trend", "text": CONFIG},
        "/backtests": {"id": "r-1", "status": "queued"},
        "/backtests/r-1": {"id": "r-1", "status": status},
        "/backtests/r-1/report": {"id": "r-1", "status": status, "strategies": [], "text": REPORT},
    }
    return fake({**answers, **more})


def test_a_saved_configuration_fills_the_editor() -> None:
    async def play(user: User, screen: StrategyTester) -> None:
        assert screen.saved.options == ["trend", "other"]
        screen.saved.set_value("trend")
        await screen.settled()
        assert screen.text.value == CONFIG
        assert screen.name.value == "trend"

    scenario(engine_saying("complete"), play)


def test_a_run_is_followed_to_its_end_and_its_report_shown() -> None:
    async def play(user: User, screen: StrategyTester) -> None:
        screen.text.value = CONFIG
        await screen.run()
        assert screen.progress.text == "r-1: queued"
        assert not screen.go.enabled  # one run at a time
        assert screen.timer.active
        assert screen.report.text == ""
        await screen.poll()
        assert screen.progress.text == "r-1: complete"
        assert screen.report.text == REPORT
        assert screen.go.enabled
        assert not screen.timer.active
        assert screen.link.visible
        assert screen.link._props["href"] == "/?run=r-1"
        assert screen.message.text == ""

    scenario(engine_saying("complete"), play)


def test_a_run_that_goes_on_is_asked_after_again() -> None:
    async def play(user: User, screen: StrategyTester) -> None:
        screen.text.value = CONFIG
        await screen.run()
        await screen.poll()
        assert screen.progress.text == "r-1: running"
        assert screen.timer.active
        assert not screen.go.enabled
        assert screen.report.text == ""
        assert not screen.link.visible

    scenario(engine_saying("running"), play)


def test_a_run_that_failed_still_shows_its_report() -> None:
    async def play(user: User, screen: StrategyTester) -> None:
        screen.text.value = CONFIG
        await screen.run()
        await screen.poll()
        assert screen.progress.text == "r-1: failed: DataError: no data"
        assert screen.report.text == REPORT  # as far as it went
        assert screen.go.enabled

    scenario(engine_saying("failed: DataError: no data"), play)


def test_nothing_is_submitted_without_a_configuration() -> None:
    async def play(user: User, screen: StrategyTester) -> None:
        screen.text.value = "  \n"
        await screen.run()
        assert screen.message.text == "write or choose a configuration first"
        assert screen.run_id is None
        await screen.poll()  # nothing to ask after
        assert screen.progress.text == ""

    scenario(engine_saying("complete"), play)


def test_a_configuration_the_engine_refuses_is_told_and_nothing_runs() -> None:
    async def play(user: User, screen: StrategyTester) -> None:
        screen.text.value = "not a configuration"
        await screen.run()
        assert "engine says 400" in screen.message.text
        assert screen.run_id is None
        assert screen.go.enabled
        assert not screen.timer.active

    scenario(engine_saying("complete", **{"/backtests": 400}), play)


def test_a_run_with_no_report_says_so_and_offers_no_chart() -> None:
    async def play(user: User, screen: StrategyTester) -> None:
        screen.text.value = CONFIG
        await screen.run()
        await screen.poll()
        assert screen.progress.text == "r-1: dropped"
        assert "engine says 409" in screen.message.text
        assert screen.report.text == ""
        assert not screen.link.visible
        assert screen.go.enabled

    scenario(engine_saying("dropped", **{"/backtests/r-1/report": 409}), play)


def test_a_run_the_engine_forgot_ends_the_following() -> None:
    async def play(user: User, screen: StrategyTester) -> None:
        screen.text.value = CONFIG
        await screen.run()
        await screen.poll()
        assert "engine says 404" in screen.message.text
        assert not screen.timer.active
        assert screen.go.enabled

    scenario(engine_saying("running", **{"/backtests/r-1": 404}), play)


def test_the_engine_not_answering_is_told_on_the_page() -> None:
    async def play(user: User, screen: StrategyTester) -> None:
        assert "engine says 500" in screen.message.text
        screen.saved.set_options(["trend"], value="trend")
        await screen.settled()
        assert "engine says 404" in screen.message.text  # the text of "trend" is not there

    scenario(fake({"/configs": 500, "/configs/trend": 404}), play)


def test_the_link_names_a_run_so_the_workspace_can_be_told_it() -> None:
    assert chart_link("r-1") == "/?run=r-1"
    assert chart_link("a b/c") == "/?run=a%20b%2Fc"


def test_the_workspace_opens_on_the_run_it_is_told_though_not_the_newest() -> None:
    answers = {
        "/backtests": {
            "backtests": [
                {"id": "new-2", "name": "n", "status": "complete"},
                {"id": "old-1", "name": "o", "status": "complete"},
            ]
        },
        **run_answers("new-2", ("n@1h",)),
        **run_answers("old-1", ("o@1h",)),
    }

    async def play(user: User, screen: Any) -> None:
        assert screen.choices.run == "old-1"

    window.scenario(fake(answers), lambda engine: workspace(engine, "old-1"), play)

    async def newest(user: User, screen: Any) -> None:
        assert screen.choices.run == "new-2"  # a run it does not hold is not opened

    window.scenario(fake(answers), lambda engine: workspace(engine, "gone-0"), newest)


def test_a_new_run_clears_the_last_ones_report_until_it_has_its_own() -> None:
    async def play(user: User, screen: StrategyTester) -> None:
        screen.text.value = CONFIG
        await screen.run()
        await screen.poll()
        assert screen.report.text == REPORT
        await screen.run()
        assert screen.report.text == ""
        assert not screen.link.visible
        assert screen.timer.active

    scenario(engine_saying("complete"), play)
