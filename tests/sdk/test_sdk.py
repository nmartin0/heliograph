# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The SDK is enough: a plug-in written from its names alone passes the kit and runs."""

import ast
import textwrap
from collections.abc import Sequence
from pathlib import Path

import pytest

import heliograph.sdk
from heliograph.sdk import Conformance, Manifest, Params, Plugin, Reference, param
from heliograph.testkit import cases, run


# The SDK docstring's example, word for word (the last test checks it).
class MomentumParams(Params):
    scalar: float = param(10.0, description="Forecast per unit of change", ge=0.0)


class Momentum(Plugin[MomentumParams]):
    """The change in its input since the sample before, scaled."""

    manifest = Manifest(
        kind="forecasters",
        name="momentum",
        version="1",
        summary="Scaled change since the previous sample",
        params=MomentumParams,
    )
    conformance = Conformance(
        references=[
            Reference(
                inputs=[[1.0], [1.5], [1.0]],
                outputs=[None, 5.0, -5.0],
                source="worked by hand: 10 x change",
            )
        ],
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


@pytest.mark.parametrize(("plugin", "check"), cases([Momentum]))
def test_a_forecaster_written_from_the_sdk_passes_the_kit(plugin: object, check: str) -> None:
    run(plugin, check)  # type: ignore[arg-type]


def test_every_name_it_promises_is_there() -> None:
    assert sorted(heliograph.sdk.__all__) == sorted(set(heliograph.sdk.__all__))
    for name in heliograph.sdk.__all__:
        assert hasattr(heliograph.sdk, name), name


def test_the_docstrings_example_is_this_test_files() -> None:
    """The example a reader copies is the one tested here."""
    doc = heliograph.sdk.__doc__ or ""
    example = doc[doc.rindex("\n", 0, doc.index("class MomentumParams")) + 1 : doc.index("It is")]
    ours = Path(__file__).read_text()
    tested = ours[ours.index("class MomentumParams") : ours.index("@pytest.mark")]
    assert ast.dump(ast.parse(textwrap.dedent(example))) == ast.dump(ast.parse(tested))
