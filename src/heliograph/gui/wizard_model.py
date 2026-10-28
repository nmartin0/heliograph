# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The Wizard's model: a run configuration seen as a graph, and edited as one (D42).

The configuration stays the truth. ``Wizard`` holds it as the dictionary TOML gave
(mode, mounts, streams, components, strategies) and *derives* the picture from it:
streams, components and strategies are nodes; an input a component reads, a stage a
strategy uses and the price a strategy decides at are edges. Editing changes the
dictionary and nothing else, so everything the picture does not show (mounts, the
mode, a component's other keys) is carried through untouched.

Nothing here knows a kind of plug-in. What a strategy takes, and which kinds are nodes
others can read, comes from the engine (``/api/pipeline``) as ``Rules``; the engine's
own validation has the last word on whether the result is a good configuration.
"""

import copy
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import tomli_w

INSTRUMENT_FIELDS = ("symbol", "venue", "asset_class", "quote_currency")
MOUNT_KINDS = ("trades", "bars")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")


def instant(text: str) -> datetime | None:
    """A moment written as ISO 8601 text with a UTC offset (``2026-01-01T00:00:00Z``);
    None for no text. Raises ValueError, saying why, for anything else."""
    text = text.strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(f"{text!r} is not a date and time like 2026-01-01T00:00:00Z") from None
    if moment.utcoffset() is None:
        raise ValueError(f"{text} needs a UTC offset (Z or +hh:mm)")
    return moment


BAR_FIELDS = ("open", "high", "low", "close", "volume")
DEFAULT_FIELD = "close"
COLUMN, ROW = 280, 110
"""The distance between layers of the picture and between nodes in one layer."""

_REF = re.compile(r"(?P<stream>[A-Za-z_][A-Za-z0-9_-]*@[0-9a-z]+)\.(?P<field>[a-z]+)")

STREAM, COMPONENT, STRATEGY = "stream", "component", "strategy"
PRICE = "price"
INPUT = "input"


@dataclass(frozen=True)
class Rules:
    """What the engine says a strategy takes and which components read inputs."""

    roles: tuple[tuple[str, str, bool], ...]
    """For each field of a strategy that names components: field, kind, takes many."""
    graph: frozenset[str]
    """Kinds whose components are nodes that others read."""
    reads: Mapping[str, str]
    """Per kind, what its components say of ``inputs``: required, optional or none."""

    @classmethod
    def of(cls, pipeline: Mapping[str, Any]) -> "Rules":
        roles = pipeline["roles"]
        return cls(
            roles=tuple((r["role"], r["kind"], bool(r["many"])) for r in roles),
            graph=frozenset(pipeline["graph"]),
            reads={r["kind"]: r["inputs"] for r in roles}
            | dict.fromkeys(pipeline["graph"], "required"),
        )

    def role_of(self, kind: str) -> tuple[str, bool] | None:
        """The field of a strategy that takes components of ``kind``, and whether it is a list."""
        for role, role_kind, many in self.roles:
            if role_kind == kind:
                return role, many
        return None

    def takes_inputs(self, kind: str) -> bool:
        return self.reads.get(kind, "none") != "none"


@dataclass(frozen=True)
class Node:
    id: str
    type: str
    label: str
    sub: str
    x: int
    y: int


@dataclass(frozen=True)
class Edge:
    id: str
    source: str
    target: str
    role: str
    label: str = ""


def stream_id(mount: str, bars: str) -> str:
    return f"{STREAM}:{mount}@{bars}"


def component_id(name: str) -> str:
    return f"{COMPONENT}:{name}"


def strategy_id(name: str) -> str:
    return f"{STRATEGY}:{name}"


class Wizard:
    """A configuration being assembled."""

    def __init__(self, config: Mapping[str, Any], rules: Rules) -> None:
        self.config: dict[str, Any] = copy.deepcopy(dict(config))
        self.config.setdefault("components", [])
        self.config.setdefault("streams", [])
        self.config.setdefault("strategies", [])
        self.rules = rules

    # ---- reading

    def _named(self, name: str) -> dict[str, Any] | None:
        for component in self.config["components"]:
            if component.get("name") == name:
                return component  # type: ignore[no-any-return]
        return None

    def _strategy(self, name: str) -> dict[str, Any] | None:
        for strategy in self.config["strategies"]:
            if strategy.get("name") == name:
                return strategy  # type: ignore[no-any-return]
        return None

    def _shown(self) -> list[dict[str, Any]]:
        """The components the picture draws: the named ones of kinds a strategy uses or reads."""
        kinds = {kind for _, kind, _ in self.rules.roles} | self.rules.graph
        return [c for c in self.config["components"] if c.get("name") and c["kind"] in kinds]

    def stream_ids(self) -> set[str]:
        return {stream_id(s["mount"], s["bars"]) for s in self.config["streams"]}

    def edges(self) -> list[Edge]:
        known = self.stream_ids()
        shown = {c["name"] for c in self._shown()}
        edges = []
        for component in self._shown():
            target = component_id(component["name"])
            for ref in component.get("inputs", ()):
                source = self._source(ref, known, shown)
                if source is not None:
                    field = _REF.fullmatch(ref)
                    label = field["field"] if field else ""
                    edges.append(Edge(f"{INPUT}:{target}:{ref}", source, target, INPUT, label))
        for strategy in self.config["strategies"]:
            target = strategy_id(strategy["name"])
            ref = strategy.get("price", "")
            source = self._source(ref, known, set())
            price = _REF.fullmatch(ref)
            if source is not None and price is not None:
                edges.append(Edge(f"{PRICE}:{target}", source, target, PRICE, price["field"]))
            for role, _, many in self.rules.roles:
                value = strategy.get(role)
                names = list(value or ()) if many else ([value] if value else [])
                for name in names:
                    if name in shown:
                        source = component_id(name)
                        edges.append(Edge(f"{role}:{target}:{name}", source, target, role))
        return edges

    @staticmethod
    def _source(ref: str, streams: set[str], components: set[str]) -> str | None:
        match = _REF.fullmatch(ref)
        if match is not None:
            found = f"{STREAM}:{match['stream']}"
            return found if found in streams else None
        return component_id(ref) if ref in components else None

    def nodes(self) -> list[Node]:
        shown = self._shown()
        depth = self._depths(shown)
        top = max((depth[c["name"]] for c in shown if c["kind"] in self.rules.graph), default=0)
        stage = top + 1
        columns: dict[int, list[tuple[str, str, str, str]]] = {}
        for stream in self.config["streams"]:
            name = f"{stream['mount']}@{stream['bars']}"
            columns.setdefault(0, []).append(
                (stream_id(stream["mount"], stream["bars"]), STREAM, name, self._where(stream))
            )
        for component in shown:
            column = depth[component["name"]] if component["kind"] in self.rules.graph else stage
            sub = f"{component['kind']} · {component['plugin']}"
            columns.setdefault(column, []).append(
                (component_id(component["name"]), COMPONENT, component["name"], sub)
            )
        for strategy in self.config["strategies"]:
            sub = f"decides at {strategy.get('price', '?')}"
            columns.setdefault(stage + 1, []).append(
                (strategy_id(strategy["name"]), STRATEGY, strategy["name"], sub)
            )
        return [
            Node(identity, kind, label, sub, column * COLUMN, row * ROW)
            for column, members in sorted(columns.items())
            for row, (identity, kind, label, sub) in enumerate(members)
        ]

    def _where(self, stream: Mapping[str, Any]) -> str:
        """What a stream's bars come from: the source and the instrument of its mount."""
        mount = self.config.get("mounts", {}).get(stream["mount"])
        if not mount:
            return "bars"
        symbol = mount.get("instrument", {}).get("symbol")
        return f"{mount.get('source', '?')} · {symbol}" if symbol else str(mount.get("source", "?"))

    def _depths(self, shown: list[dict[str, Any]]) -> dict[str, int]:
        """How many nodes deep each graph node reads: one if it reads only streams."""
        by_name = {c["name"]: c for c in shown if c["kind"] in self.rules.graph}
        depths: dict[str, int] = {}

        def depth(name: str, path: frozenset[str]) -> int:
            if name in depths:
                return depths[name]
            reads = [
                depth(ref, path | {name})
                for ref in by_name[name].get("inputs", ())
                if ref in by_name and ref not in path and ref != name
            ]
            depths[name] = 1 + max(reads, default=0)
            return depths[name]

        for name in by_name:
            depth(name, frozenset())
        for component in shown:
            depths.setdefault(component["name"], 0)
        return depths

    def flow(self) -> dict[str, list[dict[str, Any]]]:
        """The picture as the front end draws it."""
        return {
            "nodes": [
                {"id": n.id, "type": n.type, "label": n.label, "sub": n.sub, "x": n.x, "y": n.y}
                for n in self.nodes()
            ],
            "edges": [
                {
                    "id": e.id,
                    "source": e.source,
                    "target": e.target,
                    "role": e.role,
                    "label": e.label,
                }
                for e in self.edges()
            ],
        }

    # ---- writing the configuration

    def text(self) -> str:
        """The configuration as TOML, components in an order that reads before it is read."""
        config = copy.deepcopy(self.config)
        config["components"] = self._in_reading_order()
        return tomli_w.dumps(config)

    def _in_reading_order(self) -> list[dict[str, Any]]:
        pending = list(self.config["components"])
        names = {c.get("name") for c in pending if c.get("name")}
        done: set[str] = set()
        ordered: list[dict[str, Any]] = []
        while pending:
            for component in pending:
                waits = [r for r in component.get("inputs", ()) if r in names and r not in done]
                if not waits:
                    break
            else:
                ordered.extend(pending)  # a cycle: leave it as it is for the engine to name
                break
            pending.remove(component)
            ordered.append(component)
            if component.get("name"):
                done.add(component["name"])
        return ordered

    # ---- editing

    def _free(self, base: str) -> str:
        taken = {c.get("name") for c in self.config["components"]}
        taken |= {s["name"] for s in self.config["strategies"]}
        name, n = base, 1
        while name in taken:
            n += 1
            name = f"{base}{n}"
        return name

    def add_component(self, kind: str, plugin: str, params: Mapping[str, Any] | None = None) -> str:
        """Add a component of a plug-in, named after it; its id is returned."""
        component: dict[str, Any] = {"kind": kind, "plugin": plugin, "name": self._free(plugin)}
        if self.rules.takes_inputs(kind):
            component["inputs"] = []
        component["params"] = copy.deepcopy(dict(params or {}))
        self.config["components"].append(component)
        return component_id(component["name"])

    def add_strategy(self, name: str) -> str:
        """Add a strategy that decides at the first stream's close, if there is a stream."""
        strategy: dict[str, Any] = {"name": self._free(name)}
        streams = self.config["streams"]
        if streams:
            strategy["price"] = f"{streams[0]['mount']}@{streams[0]['bars']}.{DEFAULT_FIELD}"
        self.config["strategies"].append(strategy)
        return strategy_id(strategy["name"])

    def set_params(self, name: str, params: Mapping[str, Any]) -> None:
        component = self._named(name)
        if component is not None:
            component["params"] = copy.deepcopy(dict(params))

    def params(self, name: str) -> dict[str, Any]:
        component = self._named(name)
        return copy.deepcopy(dict(component.get("params", {}))) if component else {}

    def kind_of(self, name: str) -> str | None:
        component = self._named(name)
        return None if component is None else str(component["kind"])

    def plugin_of(self, name: str) -> str | None:
        component = self._named(name)
        return None if component is None else str(component["plugin"])

    def remove(self, node: str) -> list[str]:
        """Remove a component or a strategy, and every edge that ran to or from it; or a
        stream that nothing reads (and its mount, if no other stream is of it)."""
        kind, _, name = node.partition(":")
        if kind == STREAM:
            return self._remove_stream(name)
        if kind == STRATEGY:
            self.config["strategies"] = [s for s in self.config["strategies"] if s["name"] != name]
            return []
        if kind != COMPONENT or self._named(name) is None:
            return ["only a component or a strategy can be removed"]
        self.config["components"] = [c for c in self.config["components"] if c.get("name") != name]
        for other in self.config["components"]:
            if "inputs" in other:
                other["inputs"] = [r for r in other["inputs"] if r != name]
        for strategy in self.config["strategies"]:
            for role, _, many in self.rules.roles:
                if many and role in strategy:
                    strategy[role] = [n for n in strategy[role] if n != name]
                elif strategy.get(role) == name:
                    del strategy[role]
        return []

    def _remove_stream(self, name: str) -> list[str]:
        prefix = f"{name}."
        readers = [
            c["name"]
            for c in self.config["components"]
            if any(r.startswith(prefix) for r in c.get("inputs", ()))
        ]
        readers += [
            s["name"] for s in self.config["strategies"] if s.get("price", "").startswith(prefix)
        ]
        if readers:
            return [f"{name} is read by {', '.join(readers)}; remove those links first"]
        mount, _, bars = name.partition("@")
        before = len(self.config["streams"])
        self.config["streams"] = [
            s for s in self.config["streams"] if (s["mount"], s["bars"]) != (mount, bars)
        ]
        if len(self.config["streams"]) == before:
            return [f"there is no stream {name}"]
        if not any(s["mount"] == mount for s in self.config["streams"]):
            self.config.get("mounts", {}).pop(mount, None)
        return []

    def add_mount(
        self,
        name: str,
        source: str,
        params: Mapping[str, Any],
        instrument: Mapping[str, str],
        kind: str,
        bar_size: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[str]:
        """Add a data source under ``name``, or say what is missing (and change nothing).
        What the engine checks of the whole (the symbol's form, a span's order) it checks
        when the configuration is kept or run."""
        problems = []
        if not _NAME.fullmatch(name):
            problems.append("a name is letters, digits, _ and -, starting with a letter or _")
        elif name in self.config.get("mounts", {}):
            problems.append(f"there is already data named {name}")
        if not source:
            problems.append("choose a data source")
        missing = [k for k in INSTRUMENT_FIELDS if not (instrument.get(k) or "").strip()]
        if missing:
            problems.append(f"the instrument needs {', '.join(missing)}")
        if kind not in MOUNT_KINDS:
            problems.append(f"data is of {' or '.join(MOUNT_KINDS)}")
        elif (kind == "bars") != bool(bar_size):
            problems.append("a bar size is given for bars, and only for bars")
        if problems:
            return problems
        mount: dict[str, Any] = {"source": source, "kind": kind}
        mount["params"] = copy.deepcopy(dict(params))
        mount["instrument"] = {k: instrument[k].strip() for k in INSTRUMENT_FIELDS}
        if bar_size:
            mount["bar_size"] = bar_size
        if start is not None:
            mount["start"] = start
        if end is not None:
            mount["end"] = end
        self.config.setdefault("mounts", {})[name] = mount
        return []

    def add_data(
        self,
        name: str,
        source: str,
        params: Mapping[str, Any],
        instrument: Mapping[str, str],
        kind: str,
        bar_size: str | None,
        start: datetime | None,
        end: datetime | None,
        bars: str,
    ) -> list[str]:
        """Add data and the bars to build from it together, or neither."""
        problems = self.add_mount(name, source, params, instrument, kind, bar_size, start, end)
        if problems:
            return problems
        problems = self.add_stream(name, bars)
        if problems:
            del self.config["mounts"][name]
        return problems

    def add_stream(self, mount: str, bars: str) -> list[str]:
        """Add bars of ``bars`` (such as ``1h``) built from the data named ``mount``."""
        bars = bars.strip()
        if mount not in self.config.get("mounts", {}):
            return [f"there is no data named {mount}"]
        if not bars:
            return ["give the size of the bars, such as 1h"]
        if any((s["mount"], s["bars"]) == (mount, bars) for s in self.config["streams"]):
            return [f"there already are {bars} bars of {mount}"]
        self.config["streams"].append({"mount": mount, "bars": bars})
        return []

    def connect(self, source: str, target: str, field: str = DEFAULT_FIELD) -> list[str]:
        """Join two nodes, or say why they cannot be joined (and change nothing)."""
        s_type, _, s_name = source.partition(":")
        t_type, _, t_name = target.partition(":")
        if s_type == STRATEGY:
            return ["a strategy feeds nothing"]
        if t_type == STREAM:
            return ["a stream reads nothing"]
        if t_type == STRATEGY and s_type == COMPONENT:
            return self._feed(s_name, t_name)
        ref = self._ref(s_type, s_name, field)
        if isinstance(ref, list):
            return ref
        if t_type == COMPONENT:
            return self._read(s_type, s_name, ref, t_name)
        strategy = self._strategy(t_name)
        if strategy is None:
            return [f"there is no strategy {t_name}"]
        strategy["price"] = ref
        return []

    def _ref(self, s_type: str, name: str, field: str) -> str | list[str]:
        if s_type == STREAM:
            if field not in BAR_FIELDS:
                return [f"a bar has {', '.join(BAR_FIELDS)}, not {field!r}"]
            return f"{name}.{field}"
        if self.kind_of(name) not in self.rules.graph:
            return ["only an indicator, forecaster or gate can be read"]
        return name

    def _read(self, s_type: str, s_name: str, ref: str, target: str) -> list[str]:
        component = self._named(target)
        if component is None or not self.rules.takes_inputs(component["kind"]):
            return [f"{target} reads no inputs"]
        if ref in component.get("inputs", ()):
            return [f"{target} already reads {ref}"]
        if s_type == COMPONENT and self._depends(s_name, target):
            return [f"{s_name} reads {target}, so {target} cannot read it back"]
        component.setdefault("inputs", []).append(ref)
        return []

    def _depends(self, name: str, on: str, seen: frozenset[str] = frozenset()) -> bool:
        """Whether ``name`` reads ``on``, directly or through other nodes (or is it)."""
        if name == on:
            return True
        component = self._named(name)
        if component is None or name in seen:
            return False
        return any(self._depends(r, on, seen | {name}) for r in component.get("inputs", ()))

    def _feed(self, name: str, target: str) -> list[str]:
        """Make the component ``name`` a stage of the strategy, by the role its kind has."""
        strategy = self._strategy(target)
        if strategy is None:
            return [f"there is no strategy {target}"]
        role = self.rules.role_of(self.kind_of(name) or "")
        if role is None:
            return [f"{name} is not a stage of a strategy"]
        field, many = role
        for other in self.config["strategies"]:
            if other is not strategy and name in _names(other.get(field)):
                return [f"{name} is already strategy {other['name']}'s; a stage serves one"]
        if many:
            names = list(strategy.get(field, ()))
            if name in names:
                return [f"{target} already uses {name}"]
            strategy[field] = [*names, name]
        else:
            strategy[field] = name
        return []

    def disconnect(self, edge: str) -> None:
        """Remove an edge that the picture draws."""
        found = next((e for e in self.edges() if e.id == edge), None)
        if found is None:
            return
        _, _, target = found.target.partition(":")
        if found.role == INPUT:
            component = self._named(target)
            ref = edge.removeprefix(f"{INPUT}:{found.target}:")
            if component is not None:
                component["inputs"] = [r for r in component["inputs"] if r != ref]
            return
        strategy = self._strategy(target)
        if strategy is None:
            return
        if found.role == PRICE:
            strategy["price"] = ""
            return
        name = found.source.partition(":")[2]
        many = next(m for r, _, m in self.rules.roles if r == found.role)
        if many:
            strategy[found.role] = [n for n in strategy[found.role] if n != name]
        else:
            del strategy[found.role]


def _names(value: Any) -> list[str]:
    if not value:
        return []
    return [value] if isinstance(value, str) else list(value)
