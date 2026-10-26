# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Heliograph's window (decision D39): a client of the engine's JSON API.

It holds no logic of its own and imports nothing else from ``heliograph``:
what it knows of the engine is what the API says (D18), so a plug-in the
engine installs appears here with no code written for it. Times arrive as
ISO 8601 text and milliseconds (D19).
"""
