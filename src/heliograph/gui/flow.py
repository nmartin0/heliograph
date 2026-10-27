# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The flow element: Vue Flow's node graph in a NiceGUI page (D42).

Vue Flow 1.48.2 (MIT) is vendored in ``static/vue_flow`` with the licences of the
packages bundled with it. The browser draws the nodes and edges it is given and
tells the page what the person did (``link``: dragged one node's handle to another;
``select``: clicked a node, an edge or the empty pane). It changes nothing itself:
the page answers by changing the model, which sends a new picture.
"""

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from nicegui import ui
from nicegui.element import Element

STATIC = Path(__file__).parent / "static" / "vue_flow"

STYLE = """
.hg-flow .vue-flow { background: #f4f6f8; }
.body--dark .hg-flow .vue-flow { background: #0f1419; }
.hg-node { padding: 6px 12px; border-radius: 6px; border: 1px solid #8895a3; background: #fff;
  color: #1d2630; min-width: 150px; font-size: 12px; }
.body--dark .hg-node { background: #1a222c; color: #dbe3ec; border-color: #3b4756; }
.hg-stream { border-style: dashed; }
.hg-strategy { border-width: 2px; border-color: #34506b; }
.hg-title { font-weight: 600; }
.hg-sub { opacity: 0.7; }
.vue-flow__node.hg-selected .hg-node { outline: 2px solid #5aa9e6; }
.vue-flow__edge-path { stroke: #7d8a99; }
.vue-flow__edge.hg-selected .vue-flow__edge-path { stroke: #5aa9e6; stroke-width: 3; }
.vue-flow__edge-text { fill: #3a4552; font-size: 10px; }
.vue-flow__edge-textbg { fill: transparent; }
.body--dark .vue-flow__edge-text { fill: #aab4c0; }
"""
"""How the nodes and edges look, in both themes (Quasar marks the dark one on the body)."""

Picture = Mapping[str, Sequence[Mapping[str, Any]]]


class Flow(
    Element,
    component="flow.js",
    esm={"heliograph-vue-flow": "static/vue_flow"},
):
    """A picture of ``nodes`` and ``edges`` (``wizard_model.Wizard.flow()`` builds it)."""

    def __init__(self, picture: Picture | None = None, *, dark: bool = True) -> None:
        super().__init__()
        for name in ("style.css", "theme-default.css"):
            ui.add_css((STATIC / name).read_text())
        ui.add_css(STYLE)
        self.classes("hg-flow")
        self._props["picture"] = _plain(picture)
        self._props["selected"] = ""
        self._props["dark"] = dark

    def show(self, picture: Picture, selected: str = "") -> None:
        """Draw ``picture``, with the node or edge ``selected`` marked."""
        self._props["picture"] = _plain(picture)
        self._props["selected"] = selected
        self.update()

    def set_dark(self, dark: bool) -> None:
        self._props["dark"] = dark
        self.update()


def _plain(picture: Picture | None) -> dict[str, Any]:
    picture = picture or {}
    return {"nodes": list(picture.get("nodes", ())), "edges": list(picture.get("edges", ()))}
