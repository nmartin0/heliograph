"""Errors raised while defining, finding and loading plug-ins."""

from heliograph.core.errors import HeliographError


class PluginError(HeliographError):
    """Base class for plug-in errors."""


class PluginDefinitionError(PluginError, TypeError):
    """A plug-in class is declared inconsistently, such as mismatched parameters."""
