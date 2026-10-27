// SPDX-FileCopyrightText: 2026 Nicholas Martin
// SPDX-License-Identifier: LicenseRef-Proprietary
//
// The chart element's browser half (D39): draws the ``spec`` the Python side
// builds (gui/series.py) with TradingView's Lightweight Charts, one chart whose
// panes share a time axis and a crosshair. Zoom and crosshair stay here.

import {
  CandlestickSeries,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineType,
  createChart,
  createSeriesMarkers,
} from "heliograph-lightweight-charts";

const PALETTES = {
  dark: {
    background: "#0f1419", text: "#aab4c0", grid: "#1e2630",
    up: "#3fb68b", down: "#e5645c", flat: "#d6a64a", muted: "#7d8a99",
    lines: ["#5aa9e6", "#d6a64a", "#b58bd9", "#6fcf97", "#e08f5f", "#8fc3c9"],
  },
  light: {
    background: "#ffffff", text: "#3a4552", grid: "#e6eaef",
    up: "#1f9d74", down: "#d04a42", flat: "#b0802a", muted: "#7d8a99",
    lines: ["#1f77b4", "#b0802a", "#8d5fb5", "#2e9e5e", "#c4703f", "#4f9aa3"],
  },
};

export default {
  template: `<div></div>`,
  props: { spec: Object, dark: Boolean },
  mounted() {
    this.chart = null;
    this.draw();
  },
  watch: {
    spec() { this.draw(); },
    dark() { this.draw(); },
  },
  beforeUnmount() {
    this.dispose();
  },
  methods: {
    dispose() {
      if (this.chart) this.chart.remove();
      this.chart = null;
    },
    draw() {
      this.dispose();
      const panes = (this.spec && this.spec.panes) || [];
      if (!panes.length) return;
      const palette = this.dark ? PALETTES.dark : PALETTES.light;
      const chart = createChart(this.$el, {
        autoSize: true,
        layout: { background: { color: palette.background }, textColor: palette.text, attributionLogo: true },
        grid: { vertLines: { color: palette.grid }, horzLines: { color: palette.grid } },
        crosshair: { mode: CrosshairMode.Normal },
        timeScale: { timeVisible: true, secondsVisible: false },
      });
      this.chart = chart;
      let colour = 0;
      panes.forEach((pane, index) => {
        for (const spec of pane.series) {
          const series = this.add(chart, spec, index, palette, () => palette.lines[colour++ % palette.lines.length]);
          series.setData(spec.data.map((p) => (p.role ? { ...p, color: palette[p.role] } : p)));
          if (spec.markers && spec.markers.length) {
            createSeriesMarkers(series, spec.markers.map((m) => ({
              time: m.time, position: m.position, shape: m.shape, color: palette[m.role],
            })));
          }
        }
      });
      chart.panes().forEach((pane, index) => pane.setStretchFactor(panes[index].weight || 1));
      chart.timeScale().fitContent();
    },
    add(chart, spec, pane, palette, next) {
      const title = spec.title || "";
      if (spec.type === "candles") {
        return chart.addSeries(CandlestickSeries, {
          title, upColor: palette.up, downColor: palette.down, borderVisible: false,
          wickUpColor: palette.up, wickDownColor: palette.down,
        }, pane);
      }
      if (spec.type === "volume") {
        const series = chart.addSeries(HistogramSeries, {
          title, priceFormat: { type: "volume" }, priceScaleId: "volume", lastValueVisible: false,
          priceLineVisible: false,
        }, pane);
        chart.priceScale("volume", pane).applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
        return series;
      }
      const options = {
        title, color: next(), lineWidth: 2, lineType: spec.type === "steps" ? LineType.WithSteps : LineType.Simple,
        priceLineVisible: false,
      };
      // A line not in the price's units has a scale of its own, at the left, so it does
      // not stretch the price's.
      if (spec.scale) options.priceScaleId = spec.scale;
      const line = chart.addSeries(LineSeries, options, pane);
      if (spec.scale === "left") chart.priceScale("left", pane).applyOptions({ visible: true });
      return line;
    },
  },
};
