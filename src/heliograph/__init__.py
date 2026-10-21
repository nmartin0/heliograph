# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Heliograph: a plug-in based trading-signal engine."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _distribution_version

__all__ = ["__version__"]

# The version lives in pyproject.toml only; read it from the installed metadata.
# Run from a source folder that was never installed, there is none to read.
try:
    __version__: str = _distribution_version("heliograph")
except PackageNotFoundError:
    __version__ = "0+unknown"
