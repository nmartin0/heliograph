# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The conformance checks, as plain functions: each raises CheckFailed or returns.

Every plug-in, whatever its kind:

- ``manifest``: it builds from the parameters the kit is given (its
  defaults, or ``Conformance.params``), and its parameter schema renders;
- ``params``: its parameters survive JSON and back, unchanged.

Every indicator, forecaster and gate (an ``Indicator`` in core/graph.py),
fed a fixed random walk
(independent walks, or the fields of random bars if it declares them):

- ``no_peeking``: in a fresh interpreter, its outputs for the first
  BEGINNING updates are the same whether it, or another instance, has seen
  what comes after; the outputs compared must hold a value;
- ``warm_up``: every output is None or a finite number, and it produces a
  value within the walk;
- ``gaps``: while some input has had no sample yet (the graph passes None
  for it), it gives no value;
- ``constant``: a constant input gives finite outputs;
- ``single_point``: one update alone does not fail;
- ``reference``: its outputs match the reference values it declares.

``run_check`` runs one in an empty temporary folder and reports anything
the plug-in raises as a CheckFailedError.
"""

import asyncio
import contextlib
import importlib
import inspect
import math
import multiprocessing
import random
import tempfile
from collections.abc import Awaitable, Callable, Sequence
from concurrent.futures import ProcessPoolExecutor
from typing import Any, cast

from heliograph.core.graph import Indicator
from heliograph.plugins.conformance import NONE, Conformance
from heliograph.plugins.manifest import Plugin

type PluginClass = type[Plugin[Any]]
type Check = Callable[[PluginClass], None]
type Updates = list[list[float | None]]


class CheckFailedError(AssertionError):
    """A plug-in does not meet a check; the message says how."""


def conformance(plugin: PluginClass) -> Conformance:
    """What ``plugin`` declares of itself, or the defaults."""
    declared = getattr(plugin, "conformance", NONE)
    if not isinstance(declared, Conformance):
        raise CheckFailedError(f"{plugin.__qualname__}.conformance is not a Conformance")
    return declared


def build(plugin: PluginClass) -> Plugin[Any]:
    """An instance, from the parameters the kit is given."""
    params = plugin.manifest.params.model_validate(dict(conformance(plugin).params))
    return plugin(params)


def run_check(check: Check, plugin: PluginClass) -> None:
    """Run ``check`` in an empty temporary folder, so a plug-in that writes files
    when built leaves nothing behind; whatever the plug-in raises fails the check."""
    with tempfile.TemporaryDirectory() as folder, contextlib.chdir(folder):
        try:
            check(plugin)
        except CheckFailedError:
            raise
        except Exception as exc:
            raise CheckFailedError(f"fails with {type(exc).__name__}: {exc}") from exc


# -- every plug-in ---------------------------------------------------------------------


def check_manifest(plugin: PluginClass) -> None:
    try:
        built = build(plugin)
    except Exception as exc:
        raise CheckFailedError(
            f"cannot be built from {dict(conformance(plugin).params) or 'its defaults'}: "
            f"{type(exc).__name__}: {exc} (give Conformance(params=...))"
        ) from exc
    try:
        if built.manifest is not plugin.manifest:
            raise CheckFailedError("an instance's manifest is not its class's")
        try:
            plugin.manifest.params_schema()
        except Exception as exc:
            raise CheckFailedError(f"its parameter schema does not render: {exc}") from exc
    finally:
        _close(built)


def _close(built: object) -> None:
    """Close what the kit built, if it can be closed (a file, a connection)."""
    close = getattr(built, "close", None)
    if callable(close):
        done = close()
        if inspect.isawaitable(done):
            asyncio.run(_wait(done))


async def _wait(done: Awaitable[object]) -> None:
    await done


def check_params(plugin: PluginClass) -> None:
    kind = plugin.manifest.params
    try:
        params = kind.model_validate(dict(conformance(plugin).params))
    except Exception as exc:
        raise CheckFailedError(f"its parameters cannot be made: {exc}") from exc
    again = kind.model_validate_json(params.model_dump_json())
    if again != params:
        raise CheckFailedError(
            f"its parameters change through JSON: {params!r} comes back as {again!r}"
        )


# -- indicators ------------------------------------------------------------------------

WALK = 160
BEGINNING = 400
FUTURE = 1000
"""no_peeking compares the outputs for BEGINNING updates followed by different
continuations of FUTURE updates: a peek further ahead than FUTURE, or a warm-up
longer than BEGINNING, is beyond it."""


def walk(arity: int, length: int = WALK, seed: int = 20261022) -> Updates:
    """``length`` updates of ``arity`` inputs: independent random walks from 100, in cents."""
    rng = random.Random(seed)
    levels = [100.0] * arity
    out: Updates = []
    for _ in range(length):
        levels = [round(max(1.0, level + rng.gauss(0, 1)), 2) for level in levels]
        out.append(list(levels))
    return out


def bars(fields: Sequence[str], length: int = WALK, seed: int = 20261022) -> Updates:
    """``length`` random bars whose fields agree (low <= open, close <= high), as
    the ``fields`` named, in order."""
    rng = random.Random(seed)
    close = 100.0
    out: Updates = []
    for _ in range(length):
        start = close
        close = round(max(2.0, close + rng.gauss(0, 1)), 2)
        bar = {
            "open": start,
            "close": close,
            "high": round(max(start, close) + abs(rng.gauss(0, 0.5)), 2),
            "low": round(max(1.0, min(start, close) - abs(rng.gauss(0, 0.5))), 2),
            "volume": round(1 + abs(rng.gauss(0, 10)), 2),
        }
        out.append([bar[name] for name in fields])
    return out


def indicator(plugin: PluginClass) -> Indicator:
    built = build(plugin)
    arity = getattr(built, "arity", None)
    if not callable(getattr(built, "update", None)) or not isinstance(arity, int) or arity < 1:
        raise CheckFailedError("is not an Indicator: it needs `arity` (1 or more) and `update`")
    fields = conformance(plugin).fields
    if fields and len(fields) != arity:
        raise CheckFailedError(f"declares {len(fields)} bar fields for its {arity} inputs")
    return cast(Indicator, built)


def series(plugin: PluginClass, length: int = WALK, seed: int = 20261022) -> Updates:
    """The inputs the kit feeds ``plugin``: bars if it names their fields, else walks."""
    fields = conformance(plugin).fields
    return bars(fields, length, seed) if fields else walk(indicator(plugin).arity, length, seed)


def outputs(plugin: PluginClass, updates: Sequence[Sequence[float | None]]) -> list[float | None]:
    """A fresh instance's outputs, one per update."""
    fresh = indicator(plugin)
    return [fresh.update(list(update)) for update in updates]


def _same(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is b
    return a == b or (math.isnan(a) and math.isnan(b))


def check_no_peeking(plugin: PluginClass) -> None:
    """The first BEGINNING outputs must not depend on what follows them.

    The same beginning is run with two different continuations, in turn and
    again, and once alone. An indicator that sees the future (or shares state
    between instances) answers the beginning differently depending on what it
    saw before. The runs happen in a fresh interpreter, so a cache filled by
    other checks, or earlier runs, cannot make them agree.
    """
    failure = _in_fresh_process(plugin, "_peeking_runs")
    if failure:
        raise CheckFailedError(failure)


def _peeking_runs(plugin: PluginClass) -> str:
    """The no_peeking runs; what is wrong, or "" if nothing is."""
    beginning = series(plugin, BEGINNING, seed=1)
    futures = [series(plugin, FUTURE, seed=2), series(plugin, FUTURE, seed=3)]
    runs = [
        ("followed by another future", beginning + futures[1]),
        ("followed by the first again", beginning + futures[0]),
        ("followed by the other again", beginning + futures[1]),
        ("alone", beginning),
    ]
    first = outputs(plugin, beginning + futures[0])[:BEGINNING]
    if all(value is None for value in first):
        return (
            f"gives no value in its first {BEGINNING} updates, so whether it sees the "
            "future cannot be checked"
        )
    for label, updates in runs:
        got = outputs(plugin, updates)[:BEGINNING]
        for index, (a, b) in enumerate(zip(first, got, strict=True)):
            if not _same(a, b):
                return (
                    f"its output at update {index} of the same {BEGINNING} updates is {a} "
                    f"in the first run and {b} {label}: it sees the future, or instances "
                    "share state"
                )
    return ""


def _in_fresh_process(plugin: PluginClass, name: str) -> str:
    """Run this module's function ``name`` on ``plugin`` in a new interpreter."""
    if "<locals>" in plugin.__qualname__:
        raise CheckFailedError(
            "must be importable by module and name to be checked in a fresh process "
            f"({plugin.__module__}.{plugin.__qualname__} is defined inside a function)"
        )
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=1, mp_context=context) as pool:
        return pool.submit(_fresh, plugin.__module__, plugin.__qualname__, name).result()


def _fresh(module: str, qualname: str, name: str) -> str:
    """In the new interpreter: find the plug-in and run ``name`` on it."""
    plugin: Any = importlib.import_module(module)
    for part in qualname.split("."):
        plugin = getattr(plugin, part)
    try:
        return cast(str, globals()[name](plugin))
    except CheckFailedError as exc:
        return str(exc)
    except Exception as exc:
        return f"fails with {type(exc).__name__}: {exc}"


def _finite(where: str, values: Sequence[float | None]) -> None:
    """Every value is None or a finite number, as the graph accepts them."""
    for index, value in enumerate(values):
        if value is None:
            continue
        try:
            finite = not isinstance(value, bool) and math.isfinite(value)
        except (TypeError, ValueError, OverflowError):
            finite = False
        if not finite:
            raise CheckFailedError(f"{where}: output {index} is {value!r}, not a finite number")


def check_warm_up(plugin: PluginClass) -> None:
    produced = outputs(plugin, series(plugin))
    _finite("on a random walk", produced)
    if all(value is None for value in produced):
        raise CheckFailedError(f"produced no value in {WALK} updates: is its warm-up that long?")


def check_gaps(plugin: PluginClass) -> None:
    """Until every input has had a sample, the graph passes None for the others.

    A one-input indicator is only ever updated with a sample, so has no gap.
    """
    arity = indicator(plugin).arity
    data = series(plugin)
    lead = 5
    for sampled in range(arity if arity > 1 else 0):
        leading: Updates = [
            [value if number == sampled else None for number, value in enumerate(update)]
            for update in data[:lead]
        ]
        got = outputs(plugin, leading + data)
        if any(value is not None for value in got[:lead]):
            raise CheckFailedError(
                f"gave a value while only input {sampled} of {arity} had a sample"
            )


def check_constant(plugin: PluginClass) -> None:
    flat = series(plugin, 1)[0]
    _finite("on a constant input", outputs(plugin, [flat] * WALK))


def check_single_point(plugin: PluginClass) -> None:
    try:
        _finite("after one update", outputs(plugin, series(plugin, 1)))
    except CheckFailedError:
        raise
    except Exception as exc:
        raise CheckFailedError(f"one update alone fails: {type(exc).__name__}: {exc}") from exc


def check_reference(plugin: PluginClass) -> None:
    references = conformance(plugin).references
    if not references:
        raise CheckFailedError(
            "declares no reference values: give Conformance(references=...), "
            "or hold the check as todo or off with a reason"
        )
    for number, reference in enumerate(references):
        got = outputs(plugin, reference.inputs)
        for index, (have, want) in enumerate(zip(got, reference.outputs, strict=True)):
            same = have is None and want is None
            if have is not None and want is not None:
                same = math.isclose(have, want, rel_tol=1e-9, abs_tol=1e-12)
            if not same:
                raise CheckFailedError(
                    f"reference {number} ({reference.source}): output {index} is {have}, not {want}"
                )


# -- which checks each kind gets ----------------------------------------------------------

EVERY: dict[str, Check] = {"manifest": check_manifest, "params": check_params}
INDICATOR_CHECKS: dict[str, Check] = {
    "no_peeking": check_no_peeking,
    "warm_up": check_warm_up,
    "gaps": check_gaps,
    "constant": check_constant,
    "single_point": check_single_point,
    "reference": check_reference,
}
# Forecasters and gates are graph nodes too (core/pipeline.py): the same contract.
BY_KIND: dict[str, dict[str, Check]] = {
    "indicators": INDICATOR_CHECKS,
    "forecasters": INDICATOR_CHECKS,
    "gates": INDICATOR_CHECKS,
}


def checks_for(plugin: PluginClass) -> dict[str, Check]:
    """Every check a plug-in of its kind must meet, by name."""
    return {**EVERY, **BY_KIND.get(plugin.manifest.kind, {})}
