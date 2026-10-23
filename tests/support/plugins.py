# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Small plug-ins for tests of the registry and of composition."""

from heliograph.plugins.manifest import Manifest, NoParams, Params, Plugin, param


class WindowParams(Params):
    period: int = param(20, description="Bars in the window", ge=2)


class Sma(Plugin[WindowParams]):
    manifest = Manifest(
        kind="filters", name="sma", version="1", summary="Simple average", params=WindowParams
    )


class Units(Plugin[NoParams]):
    """A sizer holding the forecast as units: a strategy easy to follow by hand."""

    manifest = Manifest(kind="sizers", name="units", version="1", summary="Forecast as units")

    def target(self, forecast: float | None, price: float, held: float) -> float | None:
        return forecast
