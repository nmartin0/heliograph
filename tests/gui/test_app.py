# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The window's page as the browser receives it, and the command that serves it."""

import asyncio
import re
from collections.abc import Callable
from pathlib import Path

import pytest
from nicegui import ui
from nicegui.client import Client
from nicegui.testing.user_simulation import user_simulation

from heliograph.app import cli
from heliograph.gui import app as window
from heliograph.gui.client import DEFAULT_ENGINE, Engine

STATIC = Path(window.__file__).parent / "static" / "lightweight_charts"


def test_the_page_the_browser_gets_loads_the_chart_and_its_library(
    engine_at: Callable[[], Engine], tmp_path: Path
) -> None:
    main = tmp_path / "main.py"
    main.write_text("from nicegui import ui\nui.run()\n")
    served: list[str] = []

    async def go() -> None:
        engine = engine_at()
        async with user_simulation(main_file=main) as user:
            window.create(engine)
            try:
                await user.open("/")
                html = (await user.http_client.get("/")).text
                served.append(html)
                library = re.search(r'"heliograph-lightweight-charts":\s*"([^"]+)"', html)
                assert library, "the page does not map the chart library"
                found = await user.http_client.get(library.group(1))
                assert found.status_code == 200
                assert "Lightweight Charts" in found.text[:300]
            finally:
                # NiceGUI, leaving a simulation, drops from sys.modules the module of every
                # page it served (and its parents, "heliograph" too), so the next test would
                # meet a second copy of Heliograph: it must not find this page.
                Client.page_routes.clear()
        await engine.close()

    asyncio.run(go())
    assert "Heliograph" in served[0]


def test_the_vendored_library_keeps_its_licence_and_notice() -> None:
    assert "Apache License" in (STATIC / "LICENSE").read_text()
    assert "Licensed under Apache License 2.0" in (STATIC / "index.js").read_text()[:400]


def test_the_vue_flow_bundle_keeps_the_licence_of_every_package_in_it() -> None:
    folder = STATIC.parent / "vue_flow"
    text = (folder / "LICENSE").read_text()
    for package in ("@vue-flow/core", "@vueuse/core", "@vueuse/shared", "d3-zoom", "d3-ease"):
        assert f"==== {package} " in text
    assert "Permission is hereby granted" in text
    assert (folder / "style.css").is_file()
    assert (folder / "theme-default.css").is_file()


def test_serve_runs_the_window_for_the_engine_it_is_told_of(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(window, "create", lambda engine: calls.append(("create", engine.url)))
    monkeypatch.setattr(ui, "run", lambda **kwargs: calls.append(("run", kwargs)))
    window.serve("http://engine:1/", "0.0.0.0", 9)
    assert calls[0] == ("create", "http://engine:1")
    assert calls[1][0] == "run"
    run = calls[1][1]
    assert isinstance(run, dict)
    assert (run["host"], run["port"], run["reload"], run["show"]) == ("0.0.0.0", 9, False, False)


@pytest.mark.parametrize(
    ("arguments", "environment", "expected"),
    [
        ([], None, (DEFAULT_ENGINE, "127.0.0.1", 8766)),
        (
            ["--engine", "http://e:2", "--host", "::1", "--port", "7"],
            "http://env:3",
            ("http://e:2", "::1", 7),
        ),
        ([], "http://env:3", ("http://env:3", "127.0.0.1", 8766)),
    ],
)
def test_the_gui_command_serves_the_window(
    monkeypatch: pytest.MonkeyPatch,
    arguments: list[str],
    environment: str | None,
    expected: tuple[str, str, int],
) -> None:
    seen: list[tuple[str, str, int]] = []
    monkeypatch.setattr(window, "serve", lambda *args: seen.append(args))
    if environment is None:
        monkeypatch.delenv("HELIOGRAPH_ENGINE", raising=False)
    else:
        monkeypatch.setenv("HELIOGRAPH_ENGINE", environment)
    assert cli.main(["gui", *arguments]) == 0
    assert seen == [expected]


def test_the_chart_element_imports_the_library_under_the_name_its_python_half_maps() -> None:
    """The browser half and the Python half name the library in two files, by hand."""
    python = (Path(window.__file__).parent / "chart.py").read_text()
    script = (Path(window.__file__).parent / "chart.js").read_text()
    mapped = re.search(r'esm=\{"([^"]+)":', python)
    imported = re.search(r'from "([^"]+)";', script)
    assert mapped
    assert imported
    assert mapped.group(1) == imported.group(1)


def test_the_chart_element_reads_the_scale_key_the_series_set() -> None:
    """A line on a scale of its own: the Python half writes ``scale``, the browser half reads it."""
    folder = Path(window.__file__).parent
    assert 'spec["scale"] = "left"' in (folder / "series.py").read_text()
    script = (folder / "chart.js").read_text()
    assert "spec.scale" in script
    assert 'priceScale("left", pane)' in script


def test_a_window_open_beyond_this_computer_says_it_has_no_sign_in(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(window, "serve", lambda *args: None)
    assert cli.main(["gui", "--host", "0.0.0.0"]) == 0
    assert "no sign-in" in capsys.readouterr().err
    assert cli.main(["gui", "--host", "::1"]) == 0
    assert capsys.readouterr().err == ""
