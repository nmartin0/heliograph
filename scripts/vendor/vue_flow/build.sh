#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary
# Rebuilds src/heliograph/gui/static/vue_flow from the locked npm packages.
# Needs node and npm; nothing here runs in the application or in the checks.
# Vue itself stays external: NiceGUI's import map already provides it.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
out="$here/../../../src/heliograph/gui/static/vue_flow"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
cp "$here/package.json" "$here/package-lock.json" "$here/entry.js" "$work/"
cd "$work"
npm ci --silent
npx esbuild entry.js --bundle --format=esm --external:vue --minify \
  --legal-comments=none --outfile=index.js
mkdir -p "$out"
cp index.js "$out/index.js"
cp node_modules/@vue-flow/core/dist/style.css "$out/style.css"
cp node_modules/@vue-flow/core/dist/theme-default.css "$out/theme-default.css"
{
  for p in @vue-flow/core @vueuse/core @vueuse/shared d3-color d3-dispatch d3-drag \
    d3-ease d3-interpolate d3-selection d3-timer d3-transition d3-zoom; do
    v="$(node -p "require('./node_modules/$p/package.json').version")"
    l="$(node -p "require('./node_modules/$p/package.json').license")"
    printf '==== %s %s (%s) ====\n\n' "$p" "$v" "$l"
    cat "node_modules/$p/LICENSE"
    printf '\n\n'
  done
} > "$out/LICENSE"
