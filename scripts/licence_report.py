# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Check the licences of Heliograph's runtime dependencies and keep their notice current.

The runtime dependencies are read from uv.lock: everything the project needs,
directly or indirectly, outside development groups, on every platform. Each
package's licence comes from its installed metadata, corrected where that is
wrong, empty or not installed on this platform by
``[tool.heliograph.licences.overrides]`` in pyproject.toml (and its project
link by ``...projects``). The table in THIRD_PARTY_NOTICES.md is generated
from the same data.

The policy is docs/licensing.md:

- permissive licences pass;
- copyleft licences (GPL, LGPL, MPL, EPL, CDDL) pass, marked as copyleft in
  the table so a new one shows up when the change is reviewed;
- AGPL, source-available and proprietary licences, and licences this script
  cannot identify, fail unless listed with a reason in
  ``[tool.heliograph.licences.exceptions]``.

An SPDX expression takes the best of its ``OR`` choices and the worst of its
``AND`` parts, so "MIT OR AGPL-3.0-only" is used under MIT.

Usage::

    python3 scripts/licence_report.py           # check (what check.sh runs)
    python3 scripts/licence_report.py --write   # regenerate the table
"""

import argparse
import re
import sys
import tomllib
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, metadata
from pathlib import Path
from typing import Any, Literal

ROOT = Path(__file__).resolve().parents[1]
NOTICES = ROOT / "THIRD_PARTY_NOTICES.md"
START = "<!-- licence-report:start -->"
END = "<!-- licence-report:end -->"

type Kind = Literal["permissive", "copyleft", "denied", "unknown"]

PERMISSIVE = {
    "0BSD",
    "Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "BSL-1.0",
    "CC0-1.0",
    "HPND",
    "ISC",
    "MIT",
    "MIT-CMU",
    "NCSA",
    "PSF-2.0",
    "Python-2.0",
    "Unlicense",
    "Zlib",
}
COPYLEFT = re.compile(r"^(L?GPL|MPL|EPL|CDDL)-", re.IGNORECASE)
DENIED = re.compile(
    r"AGPL|SSPL|BUSL|Business[- ]Source|Commons[- ]Clause|Elastic|Proprietary", re.IGNORECASE
)
RANK: dict[Kind, int] = {"permissive": 0, "copyleft": 1, "unknown": 2, "denied": 3}

CLASSIFIERS = {
    "License :: OSI Approved :: MIT License": "MIT",
    "License :: OSI Approved :: BSD License": "BSD-3-Clause",
    "License :: OSI Approved :: Apache Software License": "Apache-2.0",
    "License :: OSI Approved :: ISC License (ISCL)": "ISC",
    "License :: OSI Approved :: Python Software Foundation License": "PSF-2.0",
    "License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
}


@dataclass(frozen=True, slots=True)
class Dependency:
    name: str
    version: str
    licence: str
    kind: Kind
    project: str


def normalise(name: str) -> str:
    """A package name as PEP 503 compares them: lower case, runs of -_. as one -."""
    return re.sub(r"[-_.]+", "-", name).lower()


# ---- SPDX expressions -------------------------------------------------------


class _Expression:
    """A recursive-descent reader for SPDX expressions that judges as it reads.

    expression := term ("OR" term)*;  term := factor ("AND" factor)*;
    factor := atom ("WITH" exception)?;  atom := identifier | "(" expression ")"
    Operators may be written in any case.
    """

    def __init__(self, text: str) -> None:
        self.tokens = re.findall(r"\(|\)|[^\s()]+", text)
        self.at = 0

    def judge(self) -> Kind:
        kind = self._expression()
        if self.at != len(self.tokens):
            raise ValueError("trailing text")
        return kind

    def _peek(self) -> str:
        return self.tokens[self.at].upper() if self.at < len(self.tokens) else ""

    def _take(self) -> str:
        if self.at >= len(self.tokens):
            raise ValueError("unexpected end")
        self.at += 1
        return str(self.tokens[self.at - 1])

    def _expression(self) -> Kind:
        kinds = [self._term()]
        while self._peek() == "OR":
            self._take()
            kinds.append(self._term())
        return min(kinds, key=RANK.__getitem__)

    def _term(self) -> Kind:
        kinds = [self._factor()]
        while self._peek() == "AND":
            self._take()
            kinds.append(self._factor())
        return max(kinds, key=RANK.__getitem__)

    def _factor(self) -> Kind:
        kind = self._atom()
        if self._peek() == "WITH":
            self._take()
            if DENIED.search(self._take()):
                return "denied"
        return kind

    def _atom(self) -> Kind:
        token = self._take()
        if token == "(":
            kind = self._expression()
            if self._take() != ")":
                raise ValueError("unbalanced parentheses")
            return kind
        if token == ")" or token.upper() in ("AND", "OR", "WITH"):
            raise ValueError(f"unexpected {token!r}")
        if DENIED.search(token):
            return "denied"
        if token in PERMISSIVE:
            return "permissive"
        return "copyleft" if COPYLEFT.match(token) else "unknown"


def classify(expression: str) -> Kind:
    """The policy's verdict on a licence: an SPDX expression, or free text."""
    try:
        return _Expression(expression).judge()
    except ValueError:
        return "denied" if DENIED.search(expression) else "unknown"


# ---- Reading the dependencies ----------------------------------------------


def runtime_packages(lock: dict[str, Any]) -> list[tuple[str, str]]:
    """Name and version of every package the project needs at run time, on any platform."""
    packages = {normalise(p["name"]): p for p in lock["package"]}
    roots = [p for p in lock["package"] if p.get("source", {}).get("editable") == "."]
    if len(roots) != 1:
        sys.exit("uv.lock: cannot find the project itself (one editable source)")

    def edges(dependencies: list[dict[str, Any]]) -> list[tuple[str, tuple[str, ...]]]:
        return [(normalise(d["name"]), tuple(d.get("extra", ()))) for d in dependencies]

    needed: set[str] = set()
    walked: set[tuple[str, str | None]] = set()
    stack = edges(roots[0].get("dependencies", []))
    while stack:
        name, extras = stack.pop()
        if name not in packages:
            sys.exit(f"uv.lock: {name} is required but not locked; run `uv lock`")
        needed.add(name)
        package = packages[name]
        for group in (None, *extras):
            if (name, group) in walked:
                continue
            walked.add((name, group))
            if group is None:
                stack += edges(package.get("dependencies", []))
            else:
                stack += edges(package.get("optional-dependencies", {}).get(group, []))
    return sorted((packages[n]["name"], packages[n]["version"]) for n in needed)


def licence_of(name: str, overrides: dict[str, str]) -> str:
    if normalise(name) in overrides:
        return overrides[normalise(name)]
    meta = metadata(name)
    if meta.get("License-Expression"):
        return str(meta["License-Expression"])
    for classifier in meta.get_all("Classifier") or []:
        if classifier in CLASSIFIERS:
            return CLASSIFIERS[classifier]
    return str(meta.get("License") or "").strip() or "UNKNOWN"


def project_of(name: str, projects: dict[str, str]) -> str:
    if normalise(name) in projects:
        return projects[normalise(name)]
    meta = metadata(name)
    entries = (entry.partition(",") for entry in meta.get_all("Project-URL") or [])
    urls = {label.strip().lower(): url.strip() for label, _, url in entries}
    for label in ("source", "source code", "repository", "homepage"):
        if label in urls:
            return str(urls[label])
    return str(meta.get("Home-page") or "")


def section(settings: dict[str, Any], name: str) -> dict[str, str]:
    """One settings table, with package names normalised."""
    return {normalise(key): str(value) for key, value in settings.get(name, {}).items()}


def collect(
    packages: list[tuple[str, str]], settings: dict[str, Any]
) -> tuple[list[Dependency], list[str]]:
    overrides, projects = section(settings, "overrides"), section(settings, "projects")
    dependencies: list[Dependency] = []
    found: list[str] = []
    for name, version in packages:
        try:
            licence, project = licence_of(name, overrides), project_of(name, projects)
        except PackageNotFoundError:
            licence = overrides.get(normalise(name), "UNKNOWN")
            project = projects.get(normalise(name), "")
            if normalise(name) not in overrides:
                found.append(
                    f"{name} {version} is not installed on this platform; "
                    "record its licence under [tool.heliograph.licences.overrides]"
                )
        dependencies.append(Dependency(name, version, licence, classify(licence), project))
    return dependencies, found


# ---- Report ----------------------------------------------------------------


def cell(text: str) -> str:
    """Text safe for one markdown table cell: one short line, pipes escaped."""
    lines = text.strip().splitlines()
    if len(lines) > 1 or (lines and len(lines[0]) > 60):
        return "see package metadata"
    return (lines[0] if lines else "").replace("|", "\\|")


def table(dependencies: list[Dependency]) -> str:
    rows = [
        "| Component | Version | Licence | Type | Project |",
        "| --- | --- | --- | --- | --- |",
        *(
            f"| {cell(d.name)} | {cell(d.version)} | {cell(d.licence)} | {d.kind} "
            f"| {cell(d.project)} |"
            for d in dependencies
        ),
    ]
    return "\n".join(rows)


def problems(dependencies: list[Dependency], exceptions: dict[str, str]) -> list[str]:
    found = []
    for d in dependencies:
        if d.kind in ("denied", "unknown") and normalise(d.name) not in exceptions:
            what = "is not allowed" if d.kind == "denied" else "could not be identified"
            found.append(
                f"{d.name} {d.version}: licence {d.licence!r} {what}; see docs/licensing.md, "
                f"then add an override or an exception with its reason in pyproject.toml"
            )
    return found


def stale_settings(dependencies: list[Dependency], settings: dict[str, Any]) -> list[str]:
    """Overrides, exceptions and project links for packages no longer depended on."""
    present = {normalise(d.name) for d in dependencies}
    return [
        f"[tool.heliograph.licences.{name}] names {key}, which is not a runtime dependency"
        for name in ("overrides", "exceptions", "projects")
        for key in section(settings, name)
        if key not in present
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="regenerate the notices table")
    args = parser.parse_args()

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    settings = pyproject.get("tool", {}).get("heliograph", {}).get("licences", {})
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    dependencies, found = collect(runtime_packages(lock), settings)
    found += problems(dependencies, section(settings, "exceptions"))
    found += stale_settings(dependencies, settings)

    text = NOTICES.read_text(encoding="utf-8")
    before, start, rest = text.partition(START)
    _, end, after = rest.partition(END)
    if not start or not end:
        sys.exit(f"{NOTICES.name} needs both {START} and {END}")
    generated = f"{before}{START}\n{table(dependencies)}\n{END}{after}"

    if args.write:
        NOTICES.write_text(generated, encoding="utf-8")
    elif generated != text:
        found.append(
            f"{NOTICES.name} is out of date; run: python3 scripts/licence_report.py --write"
        )

    for problem in found:
        print(problem, file=sys.stderr)
    if found:
        sys.exit(1)
    copyleft = ", ".join(d.name for d in dependencies if d.kind == "copyleft") or "none"
    print(f"{len(dependencies)} runtime dependencies checked; copyleft: {copyleft}")


if __name__ == "__main__":
    main()
