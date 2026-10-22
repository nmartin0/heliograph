# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Small plug-ins for tests of the registry and of composition."""

from heliograph.plugins.manifest import Manifest, Params, Plugin, param


class WindowParams(Params):
    period: int = param(20, description="Bars in the window", ge=2)


class Sma(Plugin[WindowParams]):
    manifest = Manifest(
        kind="filters", name="sma", version="1", summary="Simple average", params=WindowParams
    )
