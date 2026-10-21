# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

import pytest
from pydantic import ValidationError

from heliograph.core.errors import InvalidValueError
from heliograph.plugins.errors import PluginDefinitionError
from heliograph.plugins.manifest import Manifest, NoParams, Params, Plugin, param


class WindowParams(Params):
    period: int = param(20, description="Bars in the window", ge=2, le=500, step=1)
    weight: float = param(0.5, description="Blend weight", ge=0, le=1)


class MovingAverage(Plugin[WindowParams]):
    manifest = Manifest(
        kind="filters",
        name="moving_average",
        version="1.0.0",
        summary="Average of the last N closes",
        params=WindowParams,
    )


def test_the_schema_carries_everything_the_gui_and_optimizer_need() -> None:
    period = MovingAverage.manifest.params_schema()["properties"]["period"]
    assert period == {
        "default": 20,
        "description": "Bars in the window",
        "maximum": 500,
        "minimum": 2,
        "step": 1,
        "title": "Period",
        "type": "integer",
    }


def test_a_parameter_without_a_step_has_none_in_its_schema() -> None:
    weight = MovingAverage.manifest.params_schema()["properties"]["weight"]
    assert "step" not in weight


def test_parameters_are_validated_against_their_bounds() -> None:
    assert WindowParams(period=50).period == 50
    with pytest.raises(ValidationError, match="greater than or equal to 2"):
        WindowParams(period=1)


def test_unknown_parameters_are_refused() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        WindowParams.model_validate({"period": 20, "perod": 30})


def test_parameters_cannot_be_changed_after_creation() -> None:
    params = WindowParams()
    with pytest.raises(ValidationError, match="frozen"):
        params.period = 30  # type: ignore[misc]


def test_a_plugin_keeps_its_parameters() -> None:
    assert MovingAverage(WindowParams(period=7)).params.period == 7


def test_plugins_without_parameters_use_no_params() -> None:
    manifest = Manifest(kind="sinks", name="log", version="1", summary="Logs signals")
    assert manifest.params is NoParams
    assert manifest.params_schema()["properties"] == {}


@pytest.mark.parametrize("name", ["Moving Average", "1st", "", "moving-average"])
def test_names_must_be_lowercase_slugs(name: str) -> None:
    with pytest.raises(PluginDefinitionError, match="lowercase slug"):
        Manifest(kind="filters", name=name, version="1", summary="x")


def test_a_manifest_needs_a_version_and_summary() -> None:
    with pytest.raises(PluginDefinitionError, match="version and a summary"):
        Manifest(kind="filters", name="sma", version=" ", summary="x")


@pytest.mark.parametrize("params", [WindowParams(), int, "WindowParams"])
def test_params_must_be_a_params_class(params: object) -> None:
    with pytest.raises(PluginDefinitionError, match="params must subclass Params"):
        Manifest(kind="filters", name="sma", version="1", summary="x", params=params)  # type: ignore[arg-type]


def test_a_default_outside_its_own_bounds_is_caught() -> None:
    class Broken(Params):
        period: int = param(1, description="Bars in the window", ge=2)

    with pytest.raises(ValidationError, match="greater than or equal to 2"):
        Broken()


@pytest.mark.parametrize("step", [0, -1, -0.5])
def test_a_step_must_be_positive(step: float) -> None:
    with pytest.raises(InvalidValueError, match="step must be positive"):
        param(10, description="x", ge=0, le=20, step=step)


def test_the_plugin_type_and_its_manifest_must_agree_on_params() -> None:
    with pytest.raises(PluginDefinitionError, match=r"Plugin\[WindowParams\].*params=NoParams"):

        class Mismatched(Plugin[WindowParams]):
            manifest = Manifest(kind="filters", name="mismatched", version="1", summary="x")


def test_a_manifest_may_use_a_subclass_of_the_declared_params() -> None:
    class WiderParams(WindowParams):
        offset: int = param(0, description="Shift", ge=0)

    class Shifted(Plugin[WindowParams]):
        manifest = Manifest(
            kind="filters", name="shifted", version="1", summary="x", params=WiderParams
        )

    assert Shifted.manifest.params is WiderParams
