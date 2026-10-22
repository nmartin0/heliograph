#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

# Fail if the working tree holds anything beyond tool caches: a changed or new
# file, or an ignored one a test or control left behind (a journal, a data
# folder, a break the controls script did not restore). Run after the checks.
#
# Usage: scripts/check_clean_tree.sh [repository]
set -euo pipefail

cd "${1:-$(dirname "$0")/..}"

caches='^!! (\.venv/|\.coverage$|\.hypothesis/|\.import_linter_cache/|\.mypy_cache/|\.pytest_cache/|\.ruff_cache/|(.*/)?__pycache__/$)'
left="$(git status --porcelain --ignored | grep -vE "${caches}" || true)"

if [ -n "${left}" ]; then
    echo "The working tree changed:"
    echo "${left}"
    exit 1
fi
echo "The working tree is as it was, apart from tool caches."
