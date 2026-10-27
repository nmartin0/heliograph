# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Settings forms generated from a plug-in's parameter schema (D40).

A plug-in declares its parameters as a Pydantic model; the engine serves that
model's JSON Schema (``/api/plugins/<kind>/<name>``). Everything here reads
the schema and nothing else, so a plug-in that is installed gets a form with no
code written for it. ``fields`` turns a schema into a flat list of ``Field``;
``Form`` (``form.py``'s NiceGUI half) draws them.

The schema's subset that Pydantic emits for the parameters in use is
understood: integers, numbers, strings, booleans, enums (``$ref`` into
``$defs``), nullable values (``anyOf`` with null) and lists of strings, with
their defaults, bounds, ``step`` hints and descriptions. Anything else
(nested objects, mappings, lists of other things) becomes a ``json`` field:
a text box that holds JSON, so it can still be set.
"""

import json
from dataclasses import dataclass, field
from typing import Any

type Schema = dict[str, Any]

INTEGER, NUMBER, TEXT, BOOLEAN, CHOICE, LIST, JSON = (
    "integer",
    "number",
    "text",
    "boolean",
    "choice",
    "list",
    "json",
)


@dataclass(frozen=True)
class Field:
    """One parameter: what to ask, how to bound it, what it is unless changed."""

    name: str
    title: str
    kind: str
    default: Any = None
    description: str = ""
    required: bool = False
    nullable: bool = False
    minimum: float | None = None
    maximum: float | None = None
    exclusive_minimum: float | None = None
    exclusive_maximum: float | None = None
    step: float | None = None
    choices: tuple[Any, ...] = field(default_factory=tuple)
    shape: str | None = None
    """For a JSON text box: the schema's type (``object`` or ``array``) the value must be."""


def _resolve(node: Schema, defs: Schema) -> Schema:
    """``node`` with a ``$ref`` into ``$defs`` replaced by what it points at (its own
    keys, such as a description or a default, win)."""
    ref = node.get("$ref")
    if not isinstance(ref, str) or not ref.startswith("#/$defs/"):
        return node
    target = defs.get(ref.removeprefix("#/$defs/"), {})
    return {**target, **{k: v for k, v in node.items() if k != "$ref"}}


def _kind(node: Schema) -> str:
    if "enum" in node:
        return CHOICE
    kind = node.get("type")
    if kind in (INTEGER, NUMBER, BOOLEAN):
        return str(kind)
    if kind == "string":
        return TEXT
    items = node.get("items")
    if kind == "array" and isinstance(items, dict) and items.get("type") == "string":
        return LIST
    return JSON


def _field(name: str, node: Schema, required: bool, defs: Schema) -> Field:
    node = _resolve(node, defs)
    nullable = False
    options = node.get("anyOf")
    if isinstance(options, list):
        rest = [_resolve(o, defs) for o in options if o.get("type") != "null"]
        nullable = len(rest) < len(options)
        if len(rest) == 1:
            node = {**{k: v for k, v in node.items() if k != "anyOf"}, **rest[0]}
    kind = _kind(node)
    return Field(
        name=name,
        title=str(node.get("title") or name.replace("_", " ").capitalize()),
        kind=kind,
        default=node.get("default"),
        description=str(node.get("description") or ""),
        required=required,
        nullable=nullable,
        minimum=node.get("minimum"),
        maximum=node.get("maximum"),
        exclusive_minimum=node.get("exclusiveMinimum"),
        exclusive_maximum=node.get("exclusiveMaximum"),
        step=node.get("step"),
        choices=tuple(node.get("enum", ())),
        shape=str(node["type"])
        if kind == JSON and node.get("type") in ("object", "array")
        else None,
    )


def fields(schema: Schema) -> list[Field]:
    """The parameters ``schema`` (an object's JSON Schema) declares, in its order."""
    defs = schema.get("$defs", {})
    required = set(schema.get("required", ()))
    return [
        _field(name, node, name in required, defs)
        for name, node in schema.get("properties", {}).items()
    ]


def problem(one: Field, value: Any) -> str | None:
    """Why ``value`` is not allowed for ``one``, or None if it is.

    The engine checks again when the parameters are used; this is for the person at the
    form, so a mistake is shown beside the box it is in. An empty value is allowed when
    the field is nullable or has a default (it then means "leave that out").
    """
    if value is None or value == "" or value == []:
        if one.required and one.default is None and not one.nullable:
            return "required"
        return None
    if one.kind in (INTEGER, NUMBER):
        if isinstance(value, bool) or not isinstance(value, int | float):
            return "must be a number"
        if one.kind == INTEGER and value != int(value):
            return "must be a whole number"
        if one.minimum is not None and value < one.minimum:
            return f"must be at least {one.minimum:g}"
        if one.maximum is not None and value > one.maximum:
            return f"must be at most {one.maximum:g}"
        if one.exclusive_minimum is not None and value <= one.exclusive_minimum:
            return f"must be more than {one.exclusive_minimum:g}"
        if one.exclusive_maximum is not None and value >= one.exclusive_maximum:
            return f"must be less than {one.exclusive_maximum:g}"
    if one.kind == JSON and one.shape == "object" and not isinstance(value, dict):
        return "must be a JSON object, like {...}"
    if one.kind == JSON and one.shape == "array" and not isinstance(value, list):
        return "must be a JSON list, like [...]"
    if one.kind == CHOICE and value not in one.choices:
        return "must be one of " + ", ".join(str(c) for c in one.choices)
    return None


def read_json(text: str) -> tuple[Any, str | None]:
    """The value ``text`` holds as JSON, or why it does not."""
    try:
        return json.loads(text), None
    except ValueError as exc:
        return None, f"not JSON: {exc}"


def read_list(text: str) -> list[str]:
    """A comma-separated text as a list, its blanks dropped."""
    return [part.strip() for part in text.split(",") if part.strip()]


def changed(form: list[Field], values: dict[str, Any]) -> dict[str, Any]:
    """The parameters worth sending: those given that differ from their default, and
    every required one that is given. Leaving a default out keeps a configuration
    short, and follows the plug-in if its default changes; an empty value is left out
    (``problem`` says when that is a mistake)."""
    out: dict[str, Any] = {}
    for one in form:
        value = values.get(one.name)
        if value is None or value == "" or value == []:
            continue
        if one.required or value != one.default:
            out[one.name] = value
    return out


def toml_inline(params: dict[str, Any]) -> str:
    """``params`` as the inline table a configuration takes: ``{ fast = 4, slow = 16 }``."""
    if not params:
        return "{}"
    return "{ " + ", ".join(f"{key} = {_toml(value)}" for key, value in params.items()) + " }"


def _toml(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value)  # a JSON string is a TOML basic string
    if isinstance(value, list):
        return "[" + ", ".join(_toml(v) for v in value) + "]"
    if isinstance(value, dict):
        return toml_inline(value)
    return json.dumps(value)
