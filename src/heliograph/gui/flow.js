// SPDX-FileCopyrightText: 2026 Nicholas Martin
// SPDX-License-Identifier: LicenseRef-Proprietary
//
// The flow element's browser half (D42): draws the ``picture`` the Python side builds
// (gui/wizard_model.py) with Vue Flow and reports what the person did. It changes
// nothing itself. Where a person dragged a node stays here, across new pictures.

import { computed, h, nextTick, watch } from "vue";
import { Handle, MarkerType, Position, VueFlow, useVueFlow } from "heliograph-vue-flow";

let counter = 0;

const card = (kind, handles) => (props) =>
  h("div", { class: `hg-node hg-${kind}` }, [
    handles.includes("in") ? h(Handle, { type: "target", position: Position.Left }) : null,
    h("div", { class: "hg-title" }, props.data.label),
    h("div", { class: "hg-sub" }, props.data.sub),
    handles.includes("out") ? h(Handle, { type: "source", position: Position.Right }) : null,
  ]);

const SLOTS = {
  "node-stream": card("stream", ["out"]),
  "node-component": card("component", ["in", "out"]),
  "node-strategy": card("strategy", ["in"]),
};

export default {
  props: { picture: Object, selected: String, dark: Boolean },
  emits: ["link", "select"],
  setup(props, { emit }) {
    const id = `hg-flow-${counter++}`;
    const { fitView } = useVueFlow({ id });
    const moved = {};
    const nodes = computed(() =>
      (props.picture.nodes || []).map((n) => ({
        id: n.id,
        type: n.type,
        position: moved[n.id] || { x: n.x, y: n.y },
        data: { label: n.label, sub: n.sub },
        class: n.id === props.selected ? "hg-selected" : "",
      })),
    );
    const edges = computed(() =>
      (props.picture.edges || []).map((e) => ({
        id: e.id,
        source: e.source,
        target: e.target,
        label: e.label,
        type: "smoothstep",
        markerEnd: MarkerType.ArrowClosed,
        class: `hg-edge hg-${e.role}` + (e.id === props.selected ? " hg-selected" : ""),
      })),
    );
    // A new or removed node may be out of sight: show them all again (not on every change,
    // which would undo the person's own zoom).
    watch(
      () => nodes.value.length,
      () => nextTick(() => setTimeout(() => fitView({ padding: 0.1 }), 50)),
    );
    return () =>
      h("div", { style: "height: 100%; width: 100%" }, [
        h(
          VueFlow,
          {
            id,
            nodes: nodes.value,
            edges: edges.value,
            fitViewOnInit: true,
            deleteKeyCode: null,
            nodesConnectable: true,
            minZoom: 0.3,
            maxZoom: 1.5,
            onConnect: (c) => emit("link", { source: c.source, target: c.target }),
            onNodeClick: ({ node }) => emit("select", { id: node.id }),
            onEdgeClick: ({ edge }) => emit("select", { id: edge.id }),
            onPaneClick: () => emit("select", { id: "" }),
            onNodeDragStop: ({ node }) => {
              moved[node.id] = { x: node.position.x, y: node.position.y };
            },
          },
          SLOTS,
        ),
      ]);
  },
};
