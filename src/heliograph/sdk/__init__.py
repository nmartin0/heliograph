# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""What a plug-in author imports: the stable surface of Heliograph (decision D32).

Everything a plug-in for the strategy pipeline needs is named here, so a
plug-in written outside this repository imports ``heliograph.sdk`` and
nothing deeper; the modules behind these names may move, these names will
not without a note in the changelog. This first cut covers the pipeline as
it stands (D30, D31): indicators, forecasters and gates (graph nodes),
combiners, sizers, exits, risk gates and fill models. A strategy is
assembled from them in a run's configuration; one written as a single class
comes later.

A forecaster, for instance::

    from collections.abc import Sequence

    from heliograph.sdk import Conformance, Manifest, Params, Plugin, Reference, param


    class MomentumParams(Params):
        scalar: float = param(10.0, description="Forecast per unit of change", ge=0.0)


    class Momentum(Plugin[MomentumParams]):
        \"\"\"The change in its input since the sample before, scaled.\"\"\"

        manifest = Manifest(
            kind="forecasters", name="momentum", version="1",
            summary="Scaled change since the previous sample", params=MomentumParams,
        )
        conformance = Conformance(
            references=[Reference(inputs=[[1.0], [1.5], [1.0]], outputs=[None, 5.0, -5.0],
                                  source="worked by hand: 10 x change")],
        )
        arity = 1

        def __init__(self, params: MomentumParams) -> None:
            super().__init__(params)
            self._last: float | None = None

        def update(self, inputs: Sequence[float | None]) -> float | None:
            (value,) = inputs
            if value is None:
                return None
            last, self._last = self._last, value
            return None if last is None else self.params.scalar * (value - last)

It is registered through an entry point in its own package's
``pyproject.toml``::

    [project.entry-points."heliograph.forecasters"]
    momentum = "my_plugins.momentum:Momentum"

and tested with the conformance kit (``heliograph.testkit``, with the
``testkit`` extra installed)::

    import pytest
    from heliograph.testkit import cases, run

    @pytest.mark.parametrize(("plugin", "check"), cases([Momentum]))
    def test_conformance(plugin, check):
        run(plugin, check)

What each kind must provide:

- **indicators, forecasters, gates** (``Indicator``): ``arity`` and
  ``update(inputs)``, one sample or None per instant. A forecaster's
  average absolute value should be about 10 (the pipeline caps it at
  ±20); a gate passes above 0.
- **combiners** (``Combiner``): ``combine(forecasts)``.
- **sizers** (``Sizer``): ``target(forecast, price, held)``, called at every
  close, the forecast None while forecasts warm up.
- **exits** (``Exit``): ``adjust(target, held, price, values)``.
- **risk gates** (``RiskGate``): ``check(proposal)``, a reason to refuse or
  None, and ``sent(proposal)``.
- **fills** (``FillModel``): ``price(open_, quantity)`` and
  ``commission(price, quantity)``.

A risk gate is handed ``Proposal``s. ``Signal``, ``SignalRefused``,
``Fill`` and ``Bar`` are not handed to any plug-in: they are the records a
run journals, named here for code that reads journals. Times are
``Timestamp``: whole nanoseconds since the epoch, UTC.
"""

from heliograph.core.broker import Fill, FillModel
from heliograph.core.errors import HeliographError, InvalidValueError
from heliograph.core.graph import Indicator
from heliograph.core.messages import Bar, Instrument
from heliograph.core.pipeline import (
    CAP,
    Combiner,
    Exit,
    Proposal,
    RiskGate,
    Signal,
    SignalRefused,
    Sizer,
)
from heliograph.core.time import (
    NANOS_PER_DAY,
    NANOS_PER_HOUR,
    NANOS_PER_MINUTE,
    NANOS_PER_SECOND,
    Timestamp,
    from_iso,
    to_datetime,
)
from heliograph.plugins.conformance import Conformance, Level, Reference, Setting
from heliograph.plugins.manifest import Manifest, NoParams, Params, Plugin, param

__all__ = [
    "CAP",
    "NANOS_PER_DAY",
    "NANOS_PER_HOUR",
    "NANOS_PER_MINUTE",
    "NANOS_PER_SECOND",
    "Bar",
    "Combiner",
    "Conformance",
    "Exit",
    "Fill",
    "FillModel",
    "HeliographError",
    "Indicator",
    "Instrument",
    "InvalidValueError",
    "Level",
    "Manifest",
    "NoParams",
    "Params",
    "Plugin",
    "Proposal",
    "Reference",
    "RiskGate",
    "Setting",
    "Signal",
    "SignalRefused",
    "Sizer",
    "Timestamp",
    "from_iso",
    "param",
    "to_datetime",
]
