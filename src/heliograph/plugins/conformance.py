# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""How a plug-in declares what the conformance kit should expect of it.

A plug-in class may carry ``conformance = Conformance(...)``. Without one,
every check of its kind runs and must pass. With one, it can:

- give the parameters the kit builds it with (when the defaults cannot
  build it, such as a file source's path);
- give reference values, the inputs and the outputs they must produce;
- for an indicator whose inputs are related, name the bar field each reads
  (``fields=("high", "low", "close")``), so the kit feeds it consistent
  bars rather than independent walks;
- soften a check: ``todo`` (expected to fail for now; the kit fails if it
  starts passing, so the setting is never stale) or ``off`` (not run), each
  with a written reason; or replace it with a named stronger test, which
  the kit confirms exists.

A setting for one check is narrower than the plug-in's ``default`` level,
and wins over it. The default covers only the checks of the plug-in's kind;
``manifest`` and ``params``, which every plug-in can meet, are held by their
own settings alone. Everything softened or replaced is listed by the kit's
report.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal

from heliograph.core.graph import BAR_FIELDS

type Level = Literal["error", "todo", "off", "replaced"]
LEVELS: frozenset[str] = frozenset({"error", "todo", "off", "replaced"})
COMMON: frozenset[str] = frozenset({"manifest", "params"})
"""The checks every plug-in meets, whatever its kind; ``default`` does not cover them."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Setting:
    """How one check is held: ``error`` unless softened, with why."""

    level: Level = "error"
    reason: str = ""
    by: str = ""
    """For ``replaced``: the stronger test, as ``path/to/test_file.py::test_name``."""

    def __post_init__(self) -> None:
        if self.level not in LEVELS:
            raise ValueError(f"no conformance level {self.level!r}; levels: {sorted(LEVELS)}")
        if self.level != "error" and not self.reason.strip():
            raise ValueError(f"a {self.level} check needs a written reason")
        if (self.level == "replaced") != bool(self.by.strip()):
            raise ValueError("a replaced check names its test in `by`, and only a replaced one")


@dataclass(frozen=True, slots=True, kw_only=True)
class Reference:
    """Inputs fed one update at a time, and the outputs they must produce."""

    inputs: Sequence[Sequence[float | None]]
    outputs: Sequence[float | None]
    source: str
    """Where the values come from: worked by hand, TA-Lib, a published example."""

    def __post_init__(self) -> None:
        if len(self.inputs) != len(self.outputs):
            raise ValueError("a reference gives one output per update")
        if not self.source.strip():
            raise ValueError("a reference says where its values come from")


@dataclass(frozen=True, slots=True, kw_only=True)
class Conformance:
    """What the kit expects of one plug-in; see the module docstring."""

    params: Mapping[str, Any] = field(default_factory=dict)
    references: Sequence[Reference] = ()
    fields: Sequence[str] = ()
    default: Setting = Setting()
    checks: Mapping[str, Setting] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "params", MappingProxyType(dict(self.params)))
        object.__setattr__(self, "checks", MappingProxyType(dict(self.checks)))
        object.__setattr__(self, "references", tuple(self.references))
        object.__setattr__(self, "fields", tuple(self.fields))
        unknown = [name for name in self.fields if name not in BAR_FIELDS]
        if unknown:
            raise ValueError(f"no bar fields {unknown}; fields: {list(BAR_FIELDS)}")

    def setting(self, check: str) -> Setting:
        """How ``check`` is held: its own setting, or else (for a check of the
        plug-in's kind) the plug-in's default."""
        if check in COMMON:
            return self.checks.get(check, Setting())
        return self.checks.get(check, self.default)


NONE = Conformance()
"""What the kit expects of a plug-in that declares nothing: every check, held as an error."""
