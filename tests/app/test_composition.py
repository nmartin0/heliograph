from pathlib import Path

import pytest

from heliograph.app.composition import build_components
from heliograph.app.config import ConfigError, RunConfig, load_config, parse_config
from heliograph.plugins.manifest import Manifest, NoParams, Params, Plugin, param
from heliograph.plugins.registry import Registry


class WindowParams(Params):
    period: int = param(20, description="Bars in the window", ge=2)


class Sma(Plugin[WindowParams]):
    manifest = Manifest(
        kind="filters", name="sma", version="1", summary="Simple average", params=WindowParams
    )


class Log(Plugin[NoParams]):
    manifest = Manifest(kind="sinks", name="log", version="1", summary="Logs signals")


CONFIG = """
[[components]]
kind = "filters"
plugin = "sma"
params = { period = 50 }

[[components]]
kind = "sinks"
plugin = "log"
"""


@pytest.fixture
def registry() -> Registry:
    registry = Registry()
    registry.register(Sma)
    registry.register(Log)
    return registry


def test_components_are_built_in_order_with_validated_params(registry: Registry) -> None:
    sma, log = build_components(parse_config(CONFIG).config, registry)
    assert isinstance(sma, Sma)
    assert sma.params == WindowParams(period=50)
    assert isinstance(log, Log)


def test_every_problem_is_reported_at_once_and_nothing_is_built() -> None:
    built: list[object] = []

    class Counting(Sma):
        manifest = Sma.manifest

        def __init__(self, params: WindowParams) -> None:
            built.append(self)
            super().__init__(params)

    registry = Registry()
    registry.register(Counting)
    config = parse_config(
        """
        [[components]]
        kind = "filters"
        plugin = "sma"

        [[components]]
        kind = "filters"
        plugin = "smaa"

        [[components]]
        kind = "filters"
        plugin = "sma"
        params = { period = 1 }
        """
    ).config
    with pytest.raises(ConfigError) as excinfo:
        build_components(config, registry)
    message = str(excinfo.value)
    assert "components[1] (filters/smaa): no filters plug-in named 'smaa'" in message
    assert "components[2] (filters/sma): invalid params" in message
    assert "greater than or equal to 2" in message
    assert built == []


def test_loaded_parameters_cannot_be_changed() -> None:
    config = parse_config(
        '[[components]]\nkind = "filters"\nplugin = "sma"\n'
        "params = { period = 50, levels = [1, 2], nested = { a = 1 } }\n"
    ).config
    params = config.components[0].params
    with pytest.raises(TypeError):
        params["period"] = 10  # type: ignore[index]
    with pytest.raises(TypeError):
        params["nested"]["a"] = 2
    assert params["levels"] == (1, 2)


NESTED = (
    '[[components]]\nkind = "filters"\nplugin = "sma"\n'
    "params = { period = 50, levels = [1, 2], nested = { a = 1, b = [3] } }\n"
)


def test_a_config_round_trips_through_json() -> None:
    config = parse_config(NESTED).config
    text = config.model_dump_json()
    assert '"levels":[1,2]' in text
    assert RunConfig.model_validate_json(text) == config


def test_dumping_a_config_gives_plain_values_without_warnings() -> None:
    dumped = parse_config(NESTED).config.model_dump()
    assert dumped["components"][0]["params"] == {
        "period": 50,
        "levels": [1, 2],
        "nested": {"a": 1, "b": [3]},
    }


def test_equal_configs_hash_equal_and_can_be_set_members() -> None:
    first, second = parse_config(NESTED).config, parse_config(NESTED).config
    assert hash(first) == hash(second)
    assert len({first, second, parse_config(CONFIG).config}) == 2


@pytest.mark.parametrize(
    ("value", "shown"),
    [
        ("1979-05-27T07:32:00", "params.when = 1979-05-27T07:32:00"),
        ("07:32:00", "params.when = 07:32:00"),
        ("[2026-01-01T00:00:00Z, 2026-01-01T09:30:00]", "params.when[1]"),
        ("{ open = 2026-01-01T09:30:00 }", "params.when.open"),
    ],
)
def test_a_time_without_an_offset_is_refused_where_it_is(value: str, shown: str) -> None:
    text = f'[[components]]\nkind = "filters"\nplugin = "sma"\nparams = {{ when = {value} }}\n'
    with pytest.raises(ConfigError, match="need a UTC offset") as excinfo:
        parse_config(text)
    assert shown in str(excinfo.value)


@pytest.mark.parametrize(
    "value", ["1979-05-27T07:32:00Z", "1979-05-27T07:32:00-05:00", "1979-05-27"]
)
def test_times_with_an_offset_and_whole_dates_are_accepted(value: str) -> None:
    text = f'[[components]]\nkind = "filters"\nplugin = "sma"\nparams = {{ when = {value} }}\n'
    assert "when" in parse_config(text).config.components[0].params


def test_a_misspelt_setting_is_refused() -> None:
    with pytest.raises(ConfigError, match="Extra inputs are not permitted"):
        parse_config('[[components]]\nkind = "filters"\nplugin = "sma"\nparam = {}\n')


def test_text_that_is_not_toml_is_refused() -> None:
    with pytest.raises(ConfigError, match="not valid TOML"):
        parse_config("[[components]\n")


def test_the_digest_fingerprints_the_exact_text() -> None:
    first = parse_config(CONFIG)
    assert first.digest == parse_config(CONFIG).digest
    assert first.digest != parse_config(CONFIG + "\n").digest
    assert len(first.digest) == 64


def test_a_config_file_is_loaded_and_named_in_errors(tmp_path: Path) -> None:
    good = tmp_path / "run.toml"
    good.write_text(CONFIG, encoding="utf-8")
    assert load_config(good) == parse_config(CONFIG)

    bad = tmp_path / "bad.toml"
    bad.write_text("components = 3\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=r"bad\.toml: invalid run configuration"):
        load_config(bad)

    with pytest.raises(ConfigError, match=r"cannot read .*missing\.toml"):
        load_config(tmp_path / "missing.toml")
