# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Builders shared by more than one test file.

Plain modules imported by name, never through a conftest: two conftests on
the path shadow each other (docs/testing.md, "Shared builders"). A helper
moves here when its second caller appears, not before.
"""
