"""Heliograph: a plug-in based trading-signal engine."""

from importlib.metadata import version as _distribution_version

__all__ = ["__version__"]

# The version lives in pyproject.toml only; read it from the installed metadata.
__version__: str = _distribution_version("heliograph")
