"""Errors raised by the core. Every Heliograph error derives from HeliographError."""


class HeliographError(Exception):
    """Base class for all errors raised by Heliograph."""


class ClockError(HeliographError):
    """A clock was asked to move backwards."""
