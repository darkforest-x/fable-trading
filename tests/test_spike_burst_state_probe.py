"""Integrity checks for the native full-state probe builder, not a Pine VM."""
import hashlib
from pathlib import Path
import textwrap

import pytest

from yoyo.evaluation.build_spike_burst_state_probe import (
    ASSERTIONS, BEGIN_HELPERS, BEGIN_STATE, END_HELPERS, END_STATE,
    build, extract, render,
)


SOURCE = Path(__file__).resolve().parents[1] / "yoyo/evaluation/pine/spike_burst_v1.pine"


def test_actual_state_and_helpers_are_preserved_exactly():
    source = SOURCE.read_text()
    probe, count = render(source)
    state = extract(source, BEGIN_STATE, END_STATE)
    assert textwrap.indent(state, "    ") in probe
    assert extract(source, BEGIN_HELPERS, END_HELPERS) in probe
    assert hashlib.sha256(state.encode()).hexdigest() in probe
    assert count == 35
    assert "passed == 35" in probe
    assert "float middle = 100.0 * u + md" in probe
    assert "f_fixture(9)" in probe
    assert "price-first burst emits before MD release" in probe


def test_builder_fails_if_state_boundary_missing_or_duplicated():
    source = SOURCE.read_text()
    with pytest.raises(ValueError, match="Expected one source block"):
        render(source.replace(BEGIN_STATE, ""))
    with pytest.raises(ValueError, match="Expected one source block"):
        render(source + BEGIN_STATE)


def test_state_mutation_flows_into_probe_without_python_mirror():
    source = SOURCE.read_text()
    modified = source.replace("pendingSide == 0 and math.max", "math.max")
    assert modified != source
    probe, _ = render(modified)
    assert "bool near = math.max" in probe
    assert "new ATR band cannot cancel frozen long window" in probe
    assert "f_fixture(0)" in probe and "f_fixture(6)" in probe


def test_probe_generation_is_deterministic_and_does_not_claim_execution(tmp_path):
    output = tmp_path / "probe.pine"
    expected, count = render(SOURCE.read_text())
    assert build(SOURCE, output) == count
    assert output.read_text() == expected
    assert "runtime.error(\"BURST STATE FAIL:" in expected
    assert "At least 29 closed chart bars required" in expected
    assert "int b = bar_index - 1" in expected
    assert "int step = bar_index - 1" in expected
    assert "if bar_index ==" not in ASSERTIONS
    assert "request.security" not in ASSERTIONS
