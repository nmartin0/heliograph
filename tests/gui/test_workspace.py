# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The Workspace screen, driven as a person would, in NiceGUI's simulated browser
(no JavaScript runs there: what the chart would draw is what the page hands it)."""

from collections.abc import Awaitable, Callable
from typing import Any

from nicegui.testing.user import User

from heliograph.gui.app import workspace
from heliograph.gui.chart import Chart
from heliograph.gui.client import Engine
from heliograph.gui.workspace import Workspace
from tests.support import window
from tests.support.api import RUN
from tests.support.window import fake


def scenario(
    engine_at: Callable[[], Engine], play: Callable[[User, Workspace], Awaitable[None]]
) -> None:
    """Open the Workspace page on an engine and play ``play`` on it."""
    window.scenario(engine_at, workspace, play)


def drawn(screen: Workspace) -> list[dict[str, Any]]:
    return list(screen.chart._props["spec"]["panes"])


def test_it_opens_on_the_newest_run_with_its_candles_and_equity(
    engine_at: Callable[[], Engine],
) -> None:
    async def play(user: User, screen: Workspace) -> None:
        await user.should_see("Heliograph")
        assert screen.choices.run == RUN
        assert (screen.choices.stream, screen.choices.strategy) == ("btc@1h", "trend")
        panes = drawn(screen)
        assert [s["id"] for s in panes[0]["series"]] == ["price", "volume"]
        assert panes[0]["series"][0]["markers"]  # signals and fills
        assert [p["series"][0]["title"] for p in panes[1:]] == ["trend equity"]
        user.find(kind=Chart)

    scenario(engine_at, play)


def test_a_node_the_run_holds_is_offered_and_drawn_in_a_pane_or_over_the_price(
    engine_at: Callable[[], Engine],
) -> None:
    async def play(user: User, screen: Workspace) -> None:
        for name in ("ewmac16 (ewmac)", "atr24 (atr)"):
            await user.should_see(name)  # offered, though nothing here names these plug-ins
        user.find("ewmac16 (ewmac)").click()
        await screen.settled()
        assert len(drawn(screen)) == 3
        assert [p["series"][0]["title"] for p in drawn(screen)[1:]] == ["ewmac16", "trend equity"]
        user.find("ewmac16 (ewmac)").click()  # off again
        await screen.settled()
        assert len(drawn(screen)) == 2
        await screen._node("atr24", True, True)  # on the price
        assert [s["title"] for s in drawn(screen)[0]["series"]][-1] == "atr24"
        assert len(drawn(screen)) == 2
        await screen._node_place("atr24", False)  # moved to a pane of its own
        assert [p["series"][0]["title"] for p in drawn(screen)[1:]] == ["atr24", "trend equity"]
        await screen._node_place("nothing", True)  # a node not shown: nothing to move
        assert len(drawn(screen)) == 3

    scenario(engine_at, play)


def test_what_is_drawn_follows_the_checkboxes(engine_at: Callable[[], Engine]) -> None:
    async def play(user: User, screen: Workspace) -> None:
        def markers() -> int:
            return len(drawn(screen)[0]["series"][0]["markers"])

        both = markers()
        await screen._toggled("fills", False)
        fewer = markers()
        assert 0 < fewer < both
        await screen._toggled("signals", False)
        assert markers() == 0
        await screen._toggled("volume", False)
        assert [s["id"] for s in drawn(screen)[0]["series"]] == ["price"]
        await screen._toggled("equity", False)
        assert len(drawn(screen)) == 1

    scenario(engine_at, play)


def test_choosing_a_stream_or_strategy_the_run_lacks_is_told_by_the_engine(
    engine_at: Callable[[], Engine],
) -> None:
    async def play(user: User, screen: Workspace) -> None:
        screen.strategy_select.set_options(["trend", "ghost"])
        screen.strategy_select.set_value("ghost")
        await screen.settled()
        assert "no strategy 'ghost'" in screen.message.text
        screen.strategy_select.set_value("trend")
        await screen.settled()
        assert not screen.message.text
        screen.stream_select.set_options(["btc@1h", "eth@1h"])
        screen.stream_select.set_value("eth@1h")
        await screen.settled()
        assert "no stream 'eth@1h'" in screen.message.text

    scenario(engine_at, play)


def test_an_engine_with_no_runs_shows_an_empty_chart(engine_at: Callable[[], Engine]) -> None:
    async def play(user: User, screen: Workspace) -> None:
        assert screen.choices.run is None
        assert drawn(screen) == []
        assert not screen.message.text

    scenario(fake({"/backtests": {"backtests": []}}), play)


def test_an_engine_that_fails_is_told_to_the_person(engine_at: Callable[[], Engine]) -> None:
    async def play(user: User, screen: Workspace) -> None:
        await user.should_see("engine says 503")

    scenario(fake({"/backtests": 503}), play)


def test_a_run_whose_contents_cannot_be_read_is_told_to_the_person(
    engine_at: Callable[[], Engine],
) -> None:
    runs = {"backtests": [{"id": "a-20260101T000000Z", "status": "complete"}]}

    async def play(user: User, screen: Workspace) -> None:
        await user.should_see("engine says 409")
        assert screen.choices.run == "a-20260101T000000Z"
        assert drawn(screen) == []

    scenario(fake({"/backtests": runs, "/backtests/a-20260101T000000Z/contents": 409}), play)


def test_only_the_runs_that_can_be_charted_are_offered_and_a_choice_opens_that_run(
    engine_at: Callable[[], Engine],
) -> None:
    runs: dict[str, Any] = {
        "backtests": [
            {"id": "d-20260104T000000Z", "status": "queued"},
            {"id": "c-20260103T000000Z", "status": "running"},
            {"id": "b-20260102T000000Z", "status": "failed: a mount it could not serve"},
            {"id": "a-20260101T000000Z", "status": "complete"},
            {"id": "z-20251231T000000Z", "status": "stopped (by a signal)"},
        ]
    }
    empty: dict[str, Any] = {"streams": [], "nodes": [], "strategies": []}
    answers: dict[str, Any] = {"/backtests": runs}
    answers |= {
        f"/backtests/{i}/contents": {"items": empty}
        for i in ("c-20260103T000000Z", "a-20260101T000000Z", "z-20251231T000000Z")
    }

    async def play(user: User, screen: Workspace) -> None:
        assert list(screen.runs) == [
            "c-20260103T000000Z",
            "a-20260101T000000Z",
            "z-20251231T000000Z",
        ]
        assert screen.choices.run == "c-20260103T000000Z"  # the newest that can be charted
        screen.run_select.set_value("a-20260101T000000Z")
        await screen.settled()
        assert screen.choices.run == "a-20260101T000000Z"
        assert drawn(screen) == []  # it holds no stream

    scenario(fake(answers), play)


def test_a_page_opened_again_offers_the_runs_again(engine_at: Callable[[], Engine]) -> None:
    async def play(user: User, screen: Workspace) -> None:
        await screen.load_runs()  # as when the person asks for the list again
        assert screen.choices.run == RUN
        assert len(drawn(screen)) == 2

    scenario(engine_at, play)
