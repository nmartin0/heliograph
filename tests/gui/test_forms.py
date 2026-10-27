# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Forms from parameter schemas (D40): what a schema says, read without naming a plug-in."""

from typing import Any

import pytest

from heliograph.gui import forms
from heliograph.gui.forms import Field

SCHEMA: dict[str, Any] = {
    "$defs": {"Unit": {"enum": ["s", "ms"], "type": "string", "title": "Unit"}},
    "properties": {
        "period": {
            "default": 14,
            "description": "Bars in the smoothing",
            "maximum": 100,
            "minimum": 1,
            "step": 1,
            "title": "Period",
            "type": "integer",
        },
        "ratio": {"default": 0.5, "exclusiveMinimum": 0.0, "maximum": 1.0, "type": "number"},
        "name": {"type": "string", "title": "Label"},
        "on": {"default": True, "type": "boolean"},
        "unit": {"$ref": "#/$defs/Unit", "default": "s", "description": "Time unit"},
        "size": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
        "columns": {"default": ["a", "b"], "items": {"type": "string"}, "type": "array"},
        "nested": {"type": "object", "additionalProperties": {"type": "number"}},
        "mixed": {"items": {"type": "integer"}, "type": "array"},
    },
    "required": ["name"],
    "type": "object",
}


def by_name(schema: dict[str, Any]) -> dict[str, Field]:
    return {f.name: f for f in forms.fields(schema)}


def test_each_parameter_becomes_a_field_in_the_schemas_order() -> None:
    assert [f.name for f in forms.fields(SCHEMA)] == list(SCHEMA["properties"])


def test_a_number_has_its_default_bounds_step_and_help() -> None:
    period = by_name(SCHEMA)["period"]
    assert (period.kind, period.default, period.minimum, period.maximum, period.step) == (
        forms.INTEGER,
        14,
        1,
        100,
        1,
    )
    assert (period.title, period.description) == ("Period", "Bars in the smoothing")
    ratio = by_name(SCHEMA)["ratio"]
    assert (ratio.kind, ratio.exclusive_minimum, ratio.maximum) == (forms.NUMBER, 0.0, 1.0)


def test_a_title_missing_is_made_from_the_name() -> None:
    assert by_name({"properties": {"floor_window": {"type": "integer"}}})["floor_window"].title == (
        "Floor window"
    )


def test_a_name_without_a_default_that_the_schema_requires_is_required() -> None:
    fields = by_name(SCHEMA)
    assert (fields["name"].required, fields["name"].kind, fields["name"].title) == (
        True,
        forms.TEXT,
        "Label",
    )
    assert not fields["period"].required


def test_an_enum_behind_a_ref_is_a_choice_that_keeps_its_own_default_and_help() -> None:
    unit = by_name(SCHEMA)["unit"]
    assert (unit.kind, unit.choices, unit.default, unit.description) == (
        forms.CHOICE,
        ("s", "ms"),
        "s",
        "Time unit",
    )


def test_a_value_or_null_is_that_value_and_nullable() -> None:
    size = by_name(SCHEMA)["size"]
    assert (size.kind, size.nullable, size.default) == (forms.TEXT, True, None)


def test_a_list_of_strings_is_a_list_and_anything_else_complex_is_json() -> None:
    fields = by_name(SCHEMA)
    assert (fields["columns"].kind, fields["columns"].default) == (forms.LIST, ["a", "b"])
    assert fields["nested"].kind == forms.JSON
    assert fields["mixed"].kind == forms.JSON
    assert fields["on"].kind == forms.BOOLEAN


def test_a_ref_that_points_nowhere_is_still_a_field() -> None:
    got = by_name({"properties": {"x": {"$ref": "#/$defs/Gone", "default": 1}}})["x"]
    assert (got.kind, got.default) == (forms.JSON, 1)


@pytest.mark.parametrize(
    ("name", "value", "expected"),
    [
        ("period", 14, None),
        ("period", 0, "must be at least 1"),
        ("period", 101, "must be at most 100"),
        ("period", 2.5, "must be a whole number"),
        ("period", "x", "must be a number"),
        ("period", True, "must be a number"),
        ("ratio", 0.0, "must be more than 0"),
        ("ratio", 0.1, None),
        ("ratio", 1.5, "must be at most 1"),
        ("unit", "ms", None),
        ("unit", "h", "must be one of s, ms"),
        ("name", "", "required"),
        ("name", None, "required"),
        ("name", "x", None),
        ("size", None, None),
        ("period", None, None),  # empty: left out, the default stands
        ("columns", [], None),
    ],
)
def test_a_value_is_checked_against_what_the_schema_allows(
    name: str, value: Any, expected: str | None
) -> None:
    assert forms.problem(by_name(SCHEMA)[name], value) == expected


def test_a_json_box_holds_the_shape_the_schema_says() -> None:
    fields = by_name(SCHEMA)
    assert (fields["nested"].shape, fields["mixed"].shape) == ("object", "array")
    assert fields["period"].shape is None  # only a JSON box has one
    nested, mixed = fields["nested"], fields["mixed"]
    assert forms.problem(nested, {"a": 1}) is None
    assert forms.problem(nested, [1]) == "must be a JSON object, like {...}"
    assert forms.problem(nested, 3) == "must be a JSON object, like {...}"
    assert forms.problem(mixed, [1]) is None
    assert forms.problem(mixed, {"a": 1}) == "must be a JSON list, like [...]"


def test_a_value_is_refused_below_an_exclusive_ceiling() -> None:
    one = Field("x", "X", forms.NUMBER, exclusive_maximum=1.0)
    assert forms.problem(one, 1.0) == "must be less than 1"
    assert forms.problem(one, 0.9) is None


def test_only_what_differs_from_its_default_is_worth_sending_and_what_is_required() -> None:
    fields = forms.fields(SCHEMA)
    values = {"period": 14, "ratio": 0.25, "name": "n", "on": False, "unit": "s", "size": ""}
    assert forms.changed(fields, values) == {"ratio": 0.25, "name": "n", "on": False}
    assert forms.changed(fields, {"name": "x", "period": 14}) == {"name": "x"}  # required: always
    assert forms.changed(fields, {"name": "", "period": None}) == {}  # empty: left out
    assert forms.changed(fields, {"size": "1h", "columns": ["a"]}) == {
        "size": "1h",
        "columns": ["a"],
    }
    assert forms.changed(fields, {}) == {}


def test_settings_are_written_as_the_inline_table_a_configuration_takes() -> None:
    assert forms.toml_inline({}) == "{}"
    assert (
        forms.toml_inline(
            {
                "fast": 4,
                "scalar": 3.75,
                "on": False,
                "unit": 'm"s',
                "cols": ["a", "b"],
                "n": {"x": 1},
            }
        )
        == '{ fast = 4, scalar = 3.75, on = false, unit = "m\\"s", cols = ["a", "b"], n = { x = 1 } }'  # noqa: E501
    )


def test_text_is_read_as_a_list_or_as_json() -> None:
    assert forms.read_list(" a, b ,, c ") == ["a", "b", "c"]
    assert forms.read_list("") == []
    assert forms.read_json('{"a": 1}') == ({"a": 1}, None)
    value, why = forms.read_json("{nope")
    assert value is None
    assert why is not None
    assert why.startswith("not JSON")
