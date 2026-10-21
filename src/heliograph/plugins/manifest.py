# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""How a plug-in describes itself: its manifest and its parameters.

A plug-in's parameters are a Pydantic model. From that one declaration the GUI
builds the settings panel, the optimizer reads ranges and steps, and presets
are saved and validated. Nobody writes a form or a search space by hand.
"""

import re
from dataclasses import dataclass
from typing import Any, ClassVar, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field

from heliograph.core.errors import InvalidValueError
from heliograph.plugins.errors import PluginDefinitionError

_SLUG = re.compile(r"[a-z][a-z0-9_]*")


class Params(BaseModel):
    """Base class for a plug-in's parameters.

    Immutable, unknown keys refused, and defaults validated like any other
    value, so a default outside its own bounds is caught.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)


class NoParams(Params):
    """Parameters of a plug-in that takes none."""


def param(
    default: Any,
    *,
    description: str,
    ge: float | None = None,
    le: float | None = None,
    step: float | None = None,
) -> Any:
    """Declare one parameter with its default, description, bounds and search step.

    ``step`` is the increment the optimizer and GUI sliders use; it appears in
    the JSON schema next to ``minimum`` and ``maximum``.

    Raises:
        InvalidValueError: if ``step`` is zero or negative.
    """
    if step is not None and step <= 0:
        raise InvalidValueError(f"step must be positive, got {step}")
    extra: dict[str, Any] | None = None if step is None else {"step": step}
    return Field(default, description=description, ge=ge, le=le, json_schema_extra=extra)


@dataclass(frozen=True, slots=True, kw_only=True)
class Manifest:
    """What a plug-in is, as shown in the catalog and checked by the registry.

    ``kind`` is the port the plug-in implements (``filters``, ``strategies``,
    ``data_sources``...) and ``name`` is unique within that kind. Both are
    lowercase slugs because they appear in configuration files.
    """

    kind: str
    name: str
    version: str
    summary: str
    params: type[Params] = NoParams

    def __post_init__(self) -> None:
        for field in ("kind", "name"):
            value = getattr(self, field)
            if not _SLUG.fullmatch(value):
                raise PluginDefinitionError(f"Manifest.{field} {value!r} must be a lowercase slug")
        if not self.version.strip() or not self.summary.strip():
            raise PluginDefinitionError(
                f"manifest {self.kind}/{self.name} needs a version and a summary"
            )
        if not (isinstance(self.params, type) and issubclass(self.params, Params)):
            raise PluginDefinitionError(
                f"manifest {self.kind}/{self.name}: params must subclass Params"
            )

    def params_schema(self) -> dict[str, Any]:
        """The JSON schema of the parameters: types, defaults, bounds, steps, help."""
        return self.params.model_json_schema()


class Plugin[P: Params]:
    """Base class for every plug-in: a manifest, and parameters given at construction.

    A subclass declares its parameter type twice, as ``Plugin[MyParams]`` for
    the type checker and as ``Manifest(params=MyParams)`` for the runtime; the
    two are checked against each other when the class is defined.
    """

    manifest: ClassVar[Manifest]

    def __init__(self, params: P) -> None:
        self.params = params

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        manifest = cls.__dict__.get("manifest")
        declared = _declared_params(cls)
        if (
            isinstance(manifest, Manifest)
            and declared is not None
            and not issubclass(manifest.params, declared)
        ):
            raise PluginDefinitionError(
                f"{cls.__qualname__} is a Plugin[{declared.__name__}] but its manifest "
                f"declares params={manifest.params.__name__}"
            )


def _declared_params(cls: type) -> type[Params] | None:
    """The ``P`` of ``class X(Plugin[P])``, if ``cls`` names it directly."""
    for base in getattr(cls, "__orig_bases__", ()):
        if get_origin(base) is Plugin:
            (argument,) = get_args(base)
            if isinstance(argument, type) and issubclass(argument, Params):
                return argument
    return None
