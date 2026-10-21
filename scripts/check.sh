#!/usr/bin/env bash
# Run every project check. The same script runs locally and in CI.
# Each check runs even if an earlier one fails; the exit code is non-zero
# if any of them failed.
set -uo pipefail

cd "$(dirname "$0")/.."

failed=()

run() {
    local name="$1"
    shift
    echo "==> ${name}"
    if ! "$@"; then
        failed+=("${name}")
    fi
}

run "lockfile is current" uv lock --check
run "lint"                uv run --frozen ruff check .
run "format"              uv run --frozen ruff format --check .
run "types"               uv run --frozen mypy
run "layers"              uv run --frozen lint-imports
run "tests"               uv run --frozen pytest
run "tests (non-UTC)"     env TZ=America/St_Johns uv run --frozen pytest -q

if ((${#failed[@]})); then
    echo
    echo "FAILED: ${failed[*]}"
    exit 1
fi

echo
echo "All checks passed."
