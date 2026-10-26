# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The Workspace screen: one run's charts (D39).

A run is picked; its streams, graph nodes and strategies come from the engine's
``contents``, so what the screen offers is what the run holds, with no list of
indicators here. The view is a function of the choices made (the run, the stream,
the strategy, the nodes shown and where): a change of choice redraws it.
"""

import asyncio
from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Any

from nicegui import background_tasks, ui

from heliograph.gui import series
from heliograph.gui.chart import Chart
from heliograph.gui.client import Engine, EngineError

CHARTED = ("complete", "running", "interrupted", "stopped")
"""Statuses of a run whose journal can be charted (a running run's, as far as it has gone)."""


@dataclass
class Choices:
    """What the person has chosen to see."""

    run: str | None = None
    stream: str | None = None
    strategy: str | None = None
    nodes: dict[str, str] = field(default_factory=dict)
    """Node name -> where it is drawn: ``"price"`` over the candles, or ``"pane"``."""
    volume: bool = True
    signals: bool = True
    fills: bool = True
    equity: bool = True


def chartable(run: dict[str, str]) -> bool:
    return run["status"].split(" ")[0].split(":")[0] in CHARTED


async def draw(engine: Engine, choices: Choices) -> list[series.Spec]:
    """The panes of ``choices``: the stream's candles with markers, then a pane for
    each node, then the strategy's equity.

    Raises:
        EngineError: if the engine cannot give a series.
    """
    if choices.run is None or choices.stream is None:
        return []
    run, stream, strategy = choices.run, choices.stream, choices.strategy
    bars = await engine.bars(run, stream)
    markers: list[dict[str, Any]] = []
    if strategy is not None and choices.signals:
        markers += series.signal_markers(await engine.signals(run, strategy), bars)
    if strategy is not None and choices.fills:
        markers += series.fill_markers(await engine.fills(run, strategy))
    price = series.price_pane(stream, bars, sorted(markers, key=lambda m: m["time"]))
    if not choices.volume:
        price["series"] = [s for s in price["series"] if s["type"] != "volume"]
    panes = [price]
    for name, where in choices.nodes.items():
        rows = await engine.node(run, name)
        if where == "price":
            series.overlay(price, name, rows)
        else:
            panes.append(series.line_pane(name, rows))
    if strategy is not None and choices.equity:
        panes.append(
            series.line_pane(f"{strategy} equity", await engine.equity(run, strategy), steps=True)
        )
    return panes


class Workspace:
    """The screen: its controls on the left, its chart filling the rest."""

    def __init__(self, engine: Engine, *, dark: bool = True) -> None:
        self.engine = engine
        self.choices = Choices()
        self.runs: dict[str, dict[str, str]] = {}
        self.contents: dict[str, Any] = {}
        self._on_price: dict[str, bool] = {}
        self._working = 0
        self._idle = asyncio.Event()
        self._idle.set()
        self.dark = dark

    def build(self) -> None:
        """Lay the screen out in the page being served."""
        with ui.left_drawer(fixed=True).props("width=300 bordered").classes("gap-2") as drawer:
            self.drawer = drawer
            self.run_select = ui.select(
                {}, label="Run", on_change=lambda e: self._start(self._picked_run())
            ).classes("w-full")
            self.stream_select = ui.select(
                {}, label="Stream", on_change=lambda e: self._start(self._picked_stream())
            ).classes("w-full")
            self.strategy_select = ui.select(
                {}, label="Strategy", on_change=lambda e: self._start(self._picked_strategy())
            ).classes("w-full")
            with ui.row().classes("gap-3"):
                for label, key in (
                    ("Volume", "volume"),
                    ("Signals", "signals"),
                    ("Fills", "fills"),
                    ("Equity", "equity"),
                ):
                    ui.checkbox(
                        label,
                        value=getattr(self.choices, key),
                        on_change=lambda e, k=key: self._start(self._toggled(k, e.value)),
                    )
            ui.label("Indicators and other nodes").classes("text-subtitle2 mt-2")
            self.nodes_box = ui.column().classes("w-full gap-0")
        with ui.column().classes("w-full h-full gap-1"):
            self.message = ui.label("").classes("text-negative")
            self.chart = (
                Chart(dark=self.dark).classes("w-full").style("height: calc(100vh - 150px)")
            )
            with ui.row().classes("text-caption"):
                ui.label("Charts: TradingView Lightweight Charts™, © TradingView, Inc.")
                ui.link("tradingview.com", "https://www.tradingview.com/", new_tab=True)

    def _start(self, work: Awaitable[None]) -> None:
        """Run what a person's choice calls for, in the background, counted as work."""
        self._working += 1
        self._idle.clear()
        background_tasks.create(self._count(work))

    async def _count(self, work: Awaitable[None]) -> None:
        try:
            await work
        finally:
            self._working -= 1
            if not self._working:
                self._idle.set()

    async def settled(self) -> None:
        """Wait until nothing a choice set going is still going."""
        await self._idle.wait()

    async def load_runs(self) -> None:
        """Ask the engine for its runs, newest first, and offer the ones that can be charted;
        open the newest unless a run is open already."""
        try:
            found = await self.engine.backtests()
        except EngineError as exc:
            self.message.text = str(exc)
            return
        self.runs = {r["id"]: r for r in found if chartable(r)}
        self.run_select.set_options({i: f"{i} · {r['status']}" for i, r in self.runs.items()})
        if self.choices.run not in self.runs:
            await self.open_run(next(iter(self.runs), None))

    async def open_run(self, run: str | None) -> None:
        """Show ``run``: what it holds is offered, its first stream and strategy are drawn."""
        self.choices = Choices(run=run)
        self.message.text = ""
        self.run_select.set_value(run)  # the choice is made already: its handler sees that
        self.nodes_box.clear()
        if run is None:
            self.chart.show([])
            return
        try:
            self.contents = await self.engine.contents(run)
        except EngineError as exc:
            self.message.text = str(exc)
            return
        streams = [s["stream"] for s in self.contents["streams"]]
        strategies = [s["name"] for s in self.contents["strategies"]]
        self.choices.stream = streams[0] if streams else None
        self.choices.strategy = strategies[0] if strategies else None
        self.stream_select.set_options(streams, value=self.choices.stream)
        self.strategy_select.set_options(strategies, value=self.choices.strategy)
        with self.nodes_box:
            for node in self.contents["nodes"]:
                self._node_row(str(node["name"]), str(node["plugin"]))
        await self.redraw()

    async def _picked_run(self) -> None:
        if self.run_select.value != self.choices.run:
            await self.open_run(self.run_select.value)

    def _node_row(self, name: str, plugin: str) -> None:
        self._on_price[name] = False
        with ui.row().classes("items-center w-full justify-between no-wrap"):
            ui.checkbox(
                f"{name} ({plugin})",
                on_change=lambda e: self._start(self._node(name, e.value, self._on_price[name])),
            )
            ui.switch("on price", on_change=lambda e: self._start(self._node_place(name, e.value)))

    async def _node(self, name: str, shown: bool, on_price: bool) -> None:
        if shown:
            self.choices.nodes[name] = "price" if on_price else "pane"
        else:
            self.choices.nodes.pop(name, None)
        await self.redraw()

    async def _node_place(self, name: str, on_price: bool) -> None:
        self._on_price[name] = on_price
        if name in self.choices.nodes:
            self.choices.nodes[name] = "price" if on_price else "pane"
            await self.redraw()

    async def _picked_stream(self) -> None:
        if self.stream_select.value != self.choices.stream:
            self.choices.stream = self.stream_select.value
            await self.redraw()

    async def _picked_strategy(self) -> None:
        if self.strategy_select.value != self.choices.strategy:
            self.choices.strategy = self.strategy_select.value
            await self.redraw()

    async def _toggled(self, key: str, value: bool) -> None:
        setattr(self.choices, key, value)
        await self.redraw()

    async def redraw(self) -> None:
        try:
            panes = await draw(self.engine, self.choices)
        except EngineError as exc:
            self.message.text = str(exc)
            return
        self.message.text = ""
        self.chart.show(panes)
