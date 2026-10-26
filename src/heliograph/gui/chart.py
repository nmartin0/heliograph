# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The chart element: TradingView's Lightweight Charts in a NiceGUI page (D39).

Lightweight Charts 5.2.1 (Apache-2.0) is vendored in ``static/lightweight_charts``
with its licence; the chart shows its attribution logo, which with the notice
in THIRD_PARTY_NOTICES.md meets the licence's attribution terms.
"""

from collections.abc import Sequence

from nicegui.element import Element

from heliograph.gui.series import Spec


class Chart(
    Element,
    component="chart.js",
    esm={"heliograph-lightweight-charts": "static/lightweight_charts"},
):
    """One chart of ``panes`` (``gui/series.py`` builds them) with a shared time axis."""

    def __init__(self, panes: Sequence[Spec] = (), *, dark: bool = True) -> None:
        super().__init__()
        self._props["spec"] = {"panes": list(panes)}
        self._props["dark"] = dark

    def show(self, panes: Sequence[Spec]) -> None:
        """Draw ``panes`` in place of what was drawn."""
        self._props["spec"] = {"panes": list(panes)}
        self.update()

    def set_dark(self, dark: bool) -> None:
        """Draw in the dark theme, or the light one."""
        self._props["dark"] = dark
        self.update()
