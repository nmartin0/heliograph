# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""A settings form drawn from a plug-in's parameter schema (D40)."""

import json
from collections.abc import Callable
from typing import Any

from nicegui import ui

from heliograph.gui import forms
from heliograph.gui.forms import Field, Schema


def _default_hint(one: Field) -> str:
    """What a box left empty stands for: the default, as the person would type it."""
    if one.default is None or one.kind in (forms.BOOLEAN, forms.CHOICE):
        return ""
    if one.kind == forms.LIST:
        return ", ".join(one.default) + " (default)" if one.default else ""
    if one.kind == forms.JSON:
        return f"{json.dumps(one.default)} (default)"
    return f"{one.default} (default)"


class SettingsForm:
    """The form of ``schema``: one input per parameter, each checked as it changes.

    ``values`` holds what each input says (a number, a list, a parsed JSON value);
    ``errors`` says what is wrong with the ones that are; ``params()`` is what is
    worth sending (forms.changed). ``on_change`` is called after every change.
    """

    def __init__(self, schema: Schema, on_change: Callable[[], None] | None = None) -> None:
        self.fields = forms.fields(schema)
        self.values: dict[str, Any] = {f.name: f.default for f in self.fields}
        self.errors: dict[str, str] = {}
        self.inputs: dict[str, ui.element] = {}
        self._messages: dict[str, ui.label] = {}
        self._on_change = on_change

    def build(self) -> None:
        """Draw the inputs in the current container."""
        for one in self.fields:
            with ui.column().classes("w-full gap-0"):
                self.inputs[one.name] = self._input(one)
                if one.description:
                    ui.label(one.description).classes("text-caption text-grey")
                self._messages[one.name] = ui.label("").classes("text-caption text-negative")
        for (
            one
        ) in self.fields:  # what is wrong before anything is touched: a missing required value
            why = forms.problem(one, self.values[one.name])
            if why is not None:
                self.errors[one.name] = why
                self._messages[one.name].text = why

    def params(self) -> dict[str, Any]:
        return forms.changed(self.fields, self.values)

    @property
    def valid(self) -> bool:
        return not self.errors

    def toml(self) -> str:
        """The settings as the inline table a configuration takes."""
        return forms.toml_inline(self.params())

    def _input(self, one: Field) -> ui.element:
        label = one.title + (" *" if one.required else "")
        hint = _default_hint(one)
        match one.kind:
            case forms.INTEGER | forms.NUMBER:
                widget: Any = ui.number(
                    label,
                    value=one.default,
                    min=one.minimum,
                    max=one.maximum,
                    step=one.step or (1 if one.kind == forms.INTEGER else None),
                    precision=0 if one.kind == forms.INTEGER else None,
                    placeholder=hint,
                    on_change=lambda e, f=one: self._set(f, e.value),
                )
            case forms.BOOLEAN:
                widget = ui.switch(
                    label,
                    value=bool(one.default),
                    on_change=lambda e, f=one: self._set(f, e.value),
                )
            case forms.CHOICE:
                widget = ui.select(
                    list(one.choices),
                    label=label,
                    value=one.default,
                    on_change=lambda e, f=one: self._set(f, e.value),
                )
            case forms.LIST:
                widget = ui.input(
                    label + " (separated by commas)",
                    value=", ".join(one.default or []),
                    placeholder=hint,
                    on_change=lambda e, f=one: self._set(f, forms.read_list(e.value or "")),
                )
            case forms.JSON:
                widget = ui.textarea(
                    label + " (JSON)",
                    value="" if one.default is None else json.dumps(one.default),
                    placeholder=hint,
                    on_change=lambda e, f=one: self._set_json(f, e.value or ""),
                )
            case _:
                widget = ui.input(
                    label,
                    value="" if one.default is None else str(one.default),
                    placeholder=hint,
                    on_change=lambda e, f=one: self._set(f, e.value),
                )
        widget.classes("w-full")
        if hint:
            widget.props("stack-label")  # the label stays up, so the hint shows in a cleared box
        if one.nullable and one.kind in (forms.INTEGER, forms.NUMBER, forms.TEXT):
            widget.props("clearable")
        return widget  # type: ignore[no-any-return]

    def _set_json(self, one: Field, text: str) -> None:
        if not text.strip():
            self._set(one, None)
            return
        value, why = forms.read_json(text)
        if why is not None:
            self._show(one, why)
        else:
            self._set(one, value)

    def _set(self, one: Field, value: Any) -> None:
        if one.kind == forms.INTEGER and isinstance(value, float) and value.is_integer():
            value = int(value)  # a number box hands back 4.0 for 4
        self.values[one.name] = value
        self._show(one, forms.problem(one, value))

    def _show(self, one: Field, why: str | None) -> None:
        if why is None:
            self.errors.pop(one.name, None)
        else:
            self.errors[one.name] = why
        self._messages[one.name].text = why or ""
        if self._on_change is not None:
            self._on_change()
