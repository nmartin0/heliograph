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
    trades_on: str | None = None
    """The stream the strategy chosen decides on: its signals and fills are drawn there."""
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
    if strategy is not None and choices.trades_on == stream:  # on other streams, none
        if choices.signals:
            markers += series.signal_markers(await engine.signals(run, strategy), bars)
        if choices.fills:
            markers += series.fill_markers(await engine.fills(run, strategy))
    price = series.price_pane(stream, bars, sorted(markers, key=lambda m: m["time"]))
    if not choices.volume:
        price["series"] = [s for s in price["series"] if s["type"] != "volume"]
    panes = [price]
    for name, where in dict(choices.nodes).items():  # a copy: a choice may change it meanwhile
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

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.choices = Choices()
        self.runs: dict[str, dict[str, str]] = {}
        self.contents: dict[str, Any] = {}
        self._on_price: dict[str, bool] = {}
        self._working = 0
        self._idle = asyncio.Event()
        self._idle.set()
        self.dark_mode: ui.dark_mode | None = None
        self._generation = 0
        """Counts the choices made: an answer that arrives after a later choice is dropped."""

    def build(self, dark_mode: ui.dark_mode | None = None) -> None:
        """Lay the screen out in the page being served. The chart follows ``dark_mode``."""
        self.dark_mode = dark_mode
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
            dark = True if dark_mode is None else bool(dark_mode.value)
            self.chart = Chart(dark=dark).classes("w-full").style("height: calc(100vh - 150px)")
            if dark_mode is not None:
                dark_mode.on_value_change(lambda e: self.chart.set_dark(bool(e.value)))
            with ui.row().classes("text-caption gap-1"):
                ui.label("TradingView Lightweight Charts™ Copyright (c) 2026 TradingView, Inc.")
                ui.link(
                    "https://www.tradingview.com/", "https://www.tradingview.com/", new_tab=True
                )

    def _start(self, work: Awaitable[None]) -> None:
        """Run what a person's choice calls for, in the background, counted as work."""
        self._working += 1
        self._idle.clear()
        background_tasks.create(self._count(work))

    async def _count(self, work: Awaitable[None]) -> None:
        try:
            await work
        except Exception as exc:  # a bug: say so on the screen, and let NiceGUI log it
            self.message.text = f"something went wrong in the window: {exc!r}"
            raise
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
        """Show ``run``: what it holds is offered, its first stream and strategy are drawn.
        The display choices (volume, signals, fills, equity) stay as they were."""
        self._generation += 1
        mine = self._generation
        was = self.choices
        self.choices = Choices(
            run=run, volume=was.volume, signals=was.signals, fills=was.fills, equity=was.equity
        )
        self.message.text = ""
        self.run_select.set_value(run)  # the choice is made already: its handler sees that
        self.nodes_box.clear()
        self._on_price = {}
        self.stream_select.set_options([], value=None)  # nothing of the run before this one
        self.strategy_select.set_options([], value=None)
        if run is None:
            self.chart.show([])
            return
        try:
            contents = await self.engine.contents(run)
        except EngineError as exc:
            self._failed(mine, exc)
            return
        if mine != self._generation:
            return  # another choice was made while the engine answered
        self.contents = contents
        streams = [s["stream"] for s in contents["streams"]]
        strategies = [s["name"] for s in contents["strategies"]]
        self.choices.stream = streams[0] if streams else None
        self.choices.strategy = strategies[0] if strategies else None
        self.choices.trades_on = self._trades_on(self.choices.strategy)
        self.stream_select.set_options(streams, value=self.choices.stream)
        self.strategy_select.set_options(strategies, value=self.choices.strategy)
        with self.nodes_box:
            for node in contents["nodes"]:
                self._node_row(str(node["name"]), str(node["plugin"]))
        await self._draw(mine)

    def _trades_on(self, strategy: str | None) -> str | None:
        """The stream ``strategy`` decides on (its price, without the field: ``btc@1h``)."""
        for found in self.contents.get("strategies", ()):
            if found["name"] == strategy:
                return str(found["price"]).rsplit(".", 1)[0]
        return None

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
        if name not in self._on_price:
            return  # a node of the run before this one
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
            self.choices.trades_on = self._trades_on(self.choices.strategy)
            await self.redraw()

    async def _toggled(self, key: str, value: bool) -> None:
        setattr(self.choices, key, value)
        await self.redraw()

    async def redraw(self) -> None:
        """Draw the choices as they are now; a draw overtaken by a later choice is dropped."""
        self._generation += 1
        await self._draw(self._generation)

    async def _draw(self, generation: int) -> None:
        try:
            panes = await draw(self.engine, self.choices)
        except EngineError as exc:
            self._failed(generation, exc)
            return
        if generation != self._generation:
            return
        self.message.text = ""
        self.chart.show(panes)

    def _failed(self, generation: int, exc: EngineError) -> None:
        """Say what the engine said, and draw nothing: the chart left on show would belong
        to choices no longer made. (An error overtaken by a later choice is not shown.)"""
        if generation != self._generation:
            return
        self.message.text = str(exc)
        self.chart.show([])
