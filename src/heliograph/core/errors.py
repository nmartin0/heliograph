# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Errors raised by the core. Every Heliograph error derives from HeliographError."""


class HeliographError(Exception):
    """Base class for all errors raised by Heliograph."""


class InvalidValueError(HeliographError, ValueError):
    """A value was refused because it is malformed or out of range."""


class InvalidTypeError(HeliographError, TypeError):
    """An argument was refused because it is the wrong kind of thing."""


class ClockError(HeliographError):
    """A clock was asked to move backwards."""


class LookAheadError(HeliographError):
    """A message stamped later than the current time was published."""


class OutOfOrderError(HeliographError):
    """A feed produced a message stamped earlier than the one before it."""


class AlarmLoopError(HeliographError, RuntimeError):
    """Alarms kept firing at one instant, so time could never move on."""


class ReentrancyError(HeliographError, RuntimeError):
    """A run was started while another run or a message delivery was in progress."""
