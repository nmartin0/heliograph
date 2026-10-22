# SPDX-FileCopyrightText: 2026 Nicholas Martin
# SPDX-License-Identifier: LicenseRef-Proprietary

"""The kit itself: each check fails on a plug-in broken in just that way.

A check that a broken plug-in passes proves nothing about the plug-ins that
pass it (docs/testing.md, "The kit is tested itself").
"""

import math
import subprocess
import sys
from collections import deque
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any, ClassVar

import pytest
from pydantic import field_serializer

from heliograph.plugins.conformance import Conformance, Reference, Setting
from heliograph.plugins.indicators import Sma
from heliograph.plugins.manifest import Manifest, Params, Plugin, param
from heliograph.testkit import CheckFailedError, cases, report, run
from heliograph.testkit.checks import (
    BEGINNING,
    FUTURE,
    _finite,
    _fresh,
    _peeking_runs,
    outputs,
    series,
)


class WindowParams(Params):
    period: int = param(3, description="Window", ge=2)


def manifest(name: str, params: type[Params] = WindowParams) -> Manifest:
    return Manifest(kind="indicators", name=name, version="1", summary=name, params=params)


REFERENCE = Conformance(
    references=[Reference(inputs=[[1.0], [2.0], [3.0]], outputs=[None, None, 2.0], source="hand")]
)


class Mean(Plugin[WindowParams]):
    """A correct mean of the last ``period`` samples: the base the broken ones bend."""

    manifest = manifest("mean")
    conformance = REFERENCE
    arity = 1

    def __init__(self, params: WindowParams) -> None:
        super().__init__(params)
        self.window: deque[float] = deque(maxlen=params.period)

    def update(self, inputs: Sequence[float | None]) -> float | None:
        (value,) = inputs
        if value is None:
            return None
        self.window.append(value)
        if len(self.window) < self.params.period:
            return None
        return sum(self.window) / len(self.window)


class Peeking(Mean):
    """Averages the current sample with the next one, which it finds in a cache that
    every instance shares: run on more data first, it knows the future."""

    manifest = manifest("peeking")
    seen: ClassVar[dict[int, float]] = {}

    def __init__(self, params: WindowParams) -> None:
        super().__init__(params)
        self.position = 0

    def update(self, inputs: Sequence[float | None]) -> float | None:
        (value,) = inputs
        if value is None:
            return None
        Peeking.seen[self.position] = value
        ahead = Peeking.seen.get(self.position + 1, value)
        self.position += 1
        return (value + ahead) / 2


class WriteOnce(Mean):
    """Keeps the first series it ever sees, by position, and averages each sample with
    the next one kept: once any run has filled the cache, it reads that run's future."""

    manifest = manifest("write_once")
    seen: ClassVar[dict[int, float]] = {}

    def __init__(self, params: WindowParams) -> None:
        super().__init__(params)
        self.position = 0

    def update(self, inputs: Sequence[float | None]) -> float | None:
        (value,) = inputs
        if value is None:
            return None
        WriteOnce.seen.setdefault(self.position, value)
        ahead = WriteOnce.seen.get(self.position + 1, value)
        self.position += 1
        return (value + ahead) / 2


class PeekFar(WriteOnce):
    """Reads 300 updates ahead, further than a short continuation shows."""

    manifest = manifest("peek_far")

    def update(self, inputs: Sequence[float | None]) -> float | None:
        (value,) = inputs
        if value is None:
            return None
        PeekFar.seen[self.position] = value
        ahead = PeekFar.seen.get(self.position + 300, value)
        self.position += 1
        return (value + ahead) / 2


class PeekLongWarmup(Peeking):
    """Peeks, but gives nothing for 450 updates: comparing only Nones proves nothing."""

    manifest = manifest("peek_long_warmup")

    def update(self, inputs: Sequence[float | None]) -> float | None:
        result = super().update(inputs)
        return result if self.position > 450 else None


class SharesState(Mean):
    """Averages everything every instance has seen."""

    manifest = manifest("shares_state")
    total: ClassVar[list[float]] = []

    def update(self, inputs: Sequence[float | None]) -> float | None:
        (value,) = inputs
        if value is None:
            return None
        SharesState.total.append(value)
        return sum(SharesState.total) / len(SharesState.total)


class Spread(Mean):
    """The first input less the second, counting a second input with no sample yet as 0."""

    manifest = manifest("spread")
    arity = 2

    def update(self, inputs: Sequence[float | None]) -> float | None:
        a, b = inputs
        return None if a is None else a - (b or 0.0)


class TrueRange(Mean):
    """High, low and close, which must agree: it refuses a high below the low."""

    manifest = manifest("true_range")
    conformance = Conformance(
        fields=("high", "low", "close"),
        references=[
            Reference(
                inputs=[[11.0, 9.0, 10.0], [12.0, 10.5, 11.0]],
                outputs=[2.0, 2.0],
                source="hand",
            )
        ],
    )
    arity = 3

    def __init__(self, params: WindowParams) -> None:
        super().__init__(params)
        self.close: float | None = None

    def update(self, inputs: Sequence[float | None]) -> float | None:
        high, low, close = inputs
        if high is None or low is None or close is None:
            return None
        if high < low:
            raise ValueError(f"high {high} below low {low}")
        previous = close if self.close is None else self.close
        self.close = close
        return max(high, previous) - min(low, previous)


class ZScore(Mean):
    """Standardises the last sample by the window's spread: a constant input divides by zero."""

    manifest = manifest("z_score")

    def update(self, inputs: Sequence[float | None]) -> float | None:
        mean = super().update(inputs)
        if mean is None:
            return None
        spread = (sum((x - mean) ** 2 for x in self.window) / len(self.window)) ** 0.5
        last = self.window[-1]
        return float("nan") if spread == 0 else (last - mean) / spread


class FailsAlone(Mean):
    """Reads the sample before the current one, which the first update does not have."""

    manifest = manifest("fails_alone")

    def update(self, inputs: Sequence[float | None]) -> float | None:
        result = super().update(inputs)
        return self.window[-2] if len(self.window) else result


class OffByOne(Mean):
    """Divides by one more than it sums."""

    manifest = manifest("off_by_one")

    def update(self, inputs: Sequence[float | None]) -> float | None:
        mean = super().update(inputs)
        return None if mean is None else mean * len(self.window) / (len(self.window) + 1)


class NeverReady(Mean):
    """Waits for a thousand samples."""

    manifest = manifest("never_ready")
    conformance = Conformance(params={"period": 1000}, references=REFERENCE.references)


class NoReference(Mean):
    manifest = manifest("no_reference")
    conformance = Conformance()


class AlwaysNan(Mean):
    """Gives NaN from the first update."""

    manifest = manifest("always_nan")

    def update(self, inputs: Sequence[float | None]) -> float | None:
        return math.nan


class NotAnIndicator(Plugin[WindowParams]):
    manifest = manifest("not_an_indicator")


class TwoFieldsOneInput(Mean):
    manifest = manifest("two_fields_one_input")
    conformance = Conformance(fields=("high", "low"))


class NotItsManifest(Mean):
    manifest = manifest("not_its_manifest")

    def __init__(self, params: WindowParams) -> None:
        super().__init__(params)
        self.manifest = manifest("another")  # type: ignore[misc]


class LossyParams(WindowParams):
    @field_serializer("period")
    def _halved(self, period: int) -> int:
        return period // 2 + 1


class Lossy(Mean):
    """Its parameters do not survive JSON."""

    manifest = manifest("lossy", LossyParams)


class NeedsAPath(Params):
    path: str = param(..., description="Required")


class Unbuildable(Plugin[NeedsAPath]):
    """Needs a parameter it has no default for, and declares none for the kit."""

    manifest = manifest("unbuildable", NeedsAPath)


@pytest.mark.parametrize(
    ("plugin", "check", "reason"),
    [
        (Peeking, "no_peeking", "it sees the future, or instances share state"),
        (Spread, "gaps", "gave a value while only input 0 of 2 had a sample"),
        (SharesState, "no_peeking", "it sees the future, or instances share state"),
        (PeekFar, "no_peeking", "it sees the future"),
        (PeekLongWarmup, "no_peeking", "gives no value in its first 400 updates"),
        (ZScore, "constant", "on a constant input: output 2 is nan"),
        (FailsAlone, "single_point", "one update alone fails: IndexError"),
        (FailsAlone, "warm_up", "fails with IndexError"),
        (OffByOne, "reference", r"reference 0 \(hand\): output 2 is 1.5, not 2.0"),
        (NeverReady, "warm_up", "produced no value in 160 updates"),
        (NoReference, "reference", "declares no reference values"),
        (Lossy, "params", "its parameters change through JSON"),
        (Unbuildable, "manifest", "cannot be built from its defaults"),
        (Unbuildable, "params", "its parameters cannot be made"),
        (AlwaysNan, "single_point", "after one update: output 0 is nan"),
        (NotAnIndicator, "warm_up", "is not an Indicator"),
        (TwoFieldsOneInput, "warm_up", "declares 2 bar fields for its 1 inputs"),
        (NotItsManifest, "manifest", "an instance's manifest is not its class's"),
    ],
)
def test_each_check_fails_on_a_plugin_broken_in_its_way(
    plugin: type[Plugin[Any]], check: str, reason: str
) -> None:
    with pytest.raises(CheckFailedError, match=reason):
        run(plugin, check)


@pytest.mark.parametrize("warmed", [False, True])
@pytest.mark.parametrize("peeker", [Peeking, WriteOnce])
def test_phase_2_gate_the_kit_fails_on_a_deliberately_peeking_indicator(
    peeker: type[Mean], warmed: bool
) -> None:
    """Whether its shared cache starts empty or full of an earlier, longer run's data."""
    peeker.seen.clear()  # type: ignore[attr-defined]
    if warmed:
        for check in ("warm_up", "constant", "gaps"):
            run(peeker, check)
        outputs(peeker, series(peeker, BEGINNING + FUTURE, seed=9))
    with pytest.raises(CheckFailedError, match="it sees the future"):
        run(peeker, "no_peeking")


def test_a_correct_indicator_passes_every_check() -> None:
    for case in cases([Mean]):
        plugin, check = case.values
        run(plugin, check)


def test_an_indicator_reading_related_fields_is_fed_bars_that_agree() -> None:
    for case in cases([TrueRange]):
        run(*case.values)


def test_peeking_is_checked_only_on_a_plugin_that_can_be_imported_afresh() -> None:
    class Local(Mean):
        manifest = manifest("local")

    with pytest.raises(CheckFailedError, match="defined inside a function"):
        run(Local, "no_peeking")


def test_a_nan_is_not_mistaken_for_peeking() -> None:
    """NaN is not equal to itself; the same NaN in every run is not a difference."""
    run(AlwaysNan, "no_peeking")


@pytest.mark.parametrize(
    ("plugin", "found"),
    [
        (Mean, ""),
        (AlwaysNan, ""),
        (Peeking, "it sees the future"),
        (PeekLongWarmup, "gives no value in its first 400 updates"),
    ],
)
def test_the_peeking_runs_find_what_they_find_in_this_process_too(
    plugin: type[Mean], found: str
) -> None:
    """The fresh interpreter only clears the caches; the runs are the same code."""
    if hasattr(plugin, "seen"):
        plugin.seen.clear()
    assert found in _peeking_runs(plugin)
    assert bool(found) == bool(_peeking_runs(plugin))


@pytest.mark.parametrize(
    ("plugin", "found"),
    [
        (FailsAlone, "fails with IndexError"),
        (TwoFieldsOneInput, "declares 2 bar fields for its 1 inputs"),
    ],
)
def test_what_fails_in_the_fresh_interpreter_comes_back_as_a_message(
    plugin: type[Mean], found: str
) -> None:
    assert found in _fresh(plugin.__module__, plugin.__qualname__, "_peeking_runs")


CLOSED: list[str] = []


class ClosesSync(Mean):
    manifest = manifest("closes_sync")

    def close(self) -> None:
        CLOSED.append("sync")


class ClosesAsync(Mean):
    manifest = manifest("closes_async")

    async def close(self) -> None:
        CLOSED.append("async")


def test_the_kit_closes_what_it_builds() -> None:
    CLOSED.clear()
    run(ClosesSync, "manifest")
    run(ClosesAsync, "manifest")
    assert CLOSED == ["sync", "async"]


def test_outputs_the_graph_accepts_are_accepted() -> None:
    _finite("here", [None, 1, 2.5, Decimal("3.25")])  # type: ignore[list-item]
    with pytest.raises(CheckFailedError, match="not a finite number"):
        _finite("here", [10**400])


# -- softening -------------------------------------------------------------------------


def held(name: str, **checks: Setting) -> type[Mean]:
    class Held(OffByOne):
        manifest = manifest(name)
        conformance = Conformance(references=REFERENCE.references, checks=checks)

    return Held


HELD = """
from collections.abc import Sequence

import pytest

from heliograph.plugins.conformance import Conformance, Reference, Setting
from heliograph.plugins.manifest import Manifest, NoParams, Plugin
from heliograph.testkit import cases, run

HAND = [Reference(inputs=[[1.0], [2.0]], outputs=[1.0, 2.0], source="hand")]


class Echo(Plugin[NoParams]):
    manifest = Manifest(kind="indicators", name="echo", version="1", summary="echo")
    conformance = Conformance(
        references=HAND, checks={"reference": Setting(level="todo", reason="being fixed")}
    )
    arity = 1

    def update(self, inputs: Sequence[float | None]) -> float | None:
        return inputs[0]


class Ratio(Echo):
    manifest = Manifest(kind="indicators", name="ratio", version="1", summary="ratio")
    conformance = Conformance(
        references=HAND, checks={"constant": Setting(level="todo", reason="being fixed")}
    )
    last = 0.0

    def update(self, inputs: Sequence[float | None]) -> float | None:
        (value,) = inputs
        assert value is not None
        change, self.last = value / (value - self.last), value
        return change


@pytest.mark.parametrize(("plugin", "check"), cases([Echo, Ratio]))
def test_conformance(plugin, check):
    run(plugin, check)
"""


@pytest.mark.parametrize(
    ("case", "outcome"),
    [
        ("indicators/echo:reference", "1 failed"),  # passes, though held todo: XPASS(strict)
        ("indicators/ratio:constant", "1 xfailed"),  # divides by zero, held todo
    ],
)
def test_a_todo_check_is_an_expected_failure_that_fails_once_it_passes(
    tmp_path: Path, case: str, outcome: str
) -> None:
    (tmp_path / "test_held.py").write_text(HELD)
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:randomly",
            "-p",
            "no:cacheprovider",
            f"test_held.py::test_conformance[{case}]",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert outcome in done.stdout, done.stdout + done.stderr


def test_the_plugin_wide_default_does_not_hold_the_checks_every_plugin_meets() -> None:
    class Unready(Mean):
        manifest = manifest("unready")
        conformance = Conformance(default=Setting(level="todo", reason="all being written"))

    held_as = {c.values[1]: [m.name for m in c.marks] for c in cases([Unready])}
    assert held_as["manifest"] == held_as["params"] == []
    assert held_as["reference"] == ["xfail"]


def test_a_plugin_whose_conformance_cannot_be_read_gets_only_a_failing_settings_case() -> None:
    class Garbled(Mean):
        manifest = manifest("garbled")
        conformance = "declared"  # type: ignore[assignment]

    (case,) = cases([Garbled])
    with pytest.raises(CheckFailedError, match="is not a Conformance"):
        run(*case.values)


def test_an_off_check_is_not_run_and_is_reported() -> None:
    off = held("off", reference=Setting(level="off", reason="no source yet"))
    assert "reference" not in [c.values[1] for c in cases([off])]
    assert report([off]) == ["indicators/off: reference off: no source yet"]


def test_a_check_setting_is_narrower_than_the_plugins_default() -> None:
    class Defaulted(Mean):
        manifest = manifest("defaulted")
        conformance = Conformance(
            references=REFERENCE.references,
            default=Setting(level="off", reason="all off"),
            checks={"reference": Setting()},
        )

    assert [c.values[1] for c in cases([Defaulted])] == [
        "settings",
        "manifest",
        "params",
        "reference",
    ]


STRONG = """
def test_strong():
    pass


class TestStrong:
    def test_method(self):
        pass

    # def test_commented(
    \"\"\"def test_in_docstring(\"\"\"
"""


@pytest.mark.parametrize(
    ("by", "exists"),
    [
        ("test_strong.py::test_strong", True),
        ("test_strong.py::test_strong[1-2]", True),
        ("test_strong.py::TestStrong::test_method", True),
        ("test_strong.py::test_method", False),
        ("test_strong.py::TestStrong::test_commented", False),
        ("test_strong.py::TestStrong::test_in_docstring", False),
        ("test_strong.py::test_stron", False),
        ("../outside/test_strong.py::test_strong", False),
    ],
)
def test_a_replacing_test_is_found_as_pytest_names_it(
    tmp_path: Path, by: str, exists: bool
) -> None:
    (tmp_path / "root").mkdir()
    (tmp_path / "outside").mkdir()
    for folder in ("root", "outside"):
        (tmp_path / folder / "test_strong.py").write_text(STRONG)
    replaced = held("by", reference=Setting(level="replaced", reason="stronger", by=by))
    if exists:
        run(replaced, "settings", root=tmp_path / "root")
    else:
        with pytest.raises(CheckFailedError, match="does not exist"):
            run(replaced, "settings", root=tmp_path / "root")


def test_replacing_tests_are_found_from_the_project_root_wherever_pytest_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "pyproject.toml").write_text("")
    (tmp_path / "tests" / "deep").mkdir(parents=True)
    (tmp_path / "tests" / "test_strong.py").write_text(STRONG)
    monkeypatch.chdir(tmp_path / "tests" / "deep")
    replaced = held(
        "from_root",
        reference=Setting(
            level="replaced", reason="stronger", by="tests/test_strong.py::test_strong"
        ),
    )
    run(replaced, "settings")


def test_a_replaced_check_passes_only_when_its_test_exists(tmp_path: Path) -> None:
    (tmp_path / "test_strong.py").write_text("def test_strong():\n    pass\n")
    strong = held(
        "strong",
        reference=Setting(
            level="replaced", reason="a stronger test", by="test_strong.py::test_strong"
        ),
    )
    run(strong, "reference", root=tmp_path)
    run(strong, "settings", root=tmp_path)
    gone = held(
        "gone",
        reference=Setting(
            level="replaced", reason="a stronger test", by="test_strong.py::test_gone"
        ),
    )
    with pytest.raises(CheckFailedError, match="test_gone, does not exist"):
        run(gone, "settings", root=tmp_path)


def test_a_setting_for_a_check_the_kind_does_not_have_is_refused() -> None:
    misspelt = held("misspelt", refrence=Setting(level="off", reason="typo"))
    with pytest.raises(CheckFailedError, match="holds checks its kind does not have: refrence"):
        run(misspelt, "settings")


@pytest.mark.parametrize(
    ("setting", "reason"),
    [
        (lambda: Setting(level="todo"), "needs a written reason"),
        (lambda: Setting(level="replaced", reason="x"), "names its test in `by`"),
        (lambda: Setting(by="x::y"), "names its test in `by`"),
        (lambda: Setting(level="skip", reason="x"), "no conformance level"),  # type: ignore[arg-type]
        (lambda: Reference(inputs=[[1.0]], outputs=[], source="x"), "one output per update"),
        (lambda: Reference(inputs=[], outputs=[], source=" "), "says where its values come from"),
    ],
)
def test_a_softening_without_its_reason_is_refused(setting: Any, reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        setting()


def test_the_shipped_averages_pass_with_their_hand_worked_references() -> None:
    for case in cases([Sma]):
        run(*case.values)
