"""Synthetic V20 prefix, support-stop and freeze-before-outcomes contracts."""
import ast
import json
from copy import deepcopy
from pathlib import Path

import pandas as pd
import pytest

from yoyo.evaluation import hourly_impulse_structure_research as subject


def test_frozen_config_file_matches_code():
    path = subject.EXPERIMENT/"config.json"
    assert json.loads(path.read_text()) == subject.frozen_config()
    base = json.loads((subject.ROOT/subject.BASE_CONFIG).read_text())
    subject.verify_config(subject.frozen_config(), base)


@pytest.mark.parametrize("key", ["gate", "support", "outcome_read_rule", "fixed_execution", "phase_end_exclusive", "matching_coverage_required"])
def test_no_posthoc_contract_change(key):
    base = json.loads((subject.ROOT/subject.BASE_CONFIG).read_text())
    config = deepcopy(subject.frozen_config())
    config[key] = None
    with pytest.raises(ValueError):
        subject.verify_config(config, base)


def test_support_failure_touches_no_outcome_path(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("Outcome/hash/file access despite failed support")
    monkeypatch.setattr(subject, "digest", forbidden)
    monkeypatch.setattr(subject, "read_parent_frame", forbidden)
    with pytest.raises(ValueError, match="support"):
        subject.read_outcomes_after_freeze(tmp_path, {"support_pass": False}, pd.DataFrame())


def test_support_pass_requires_actual_freeze(tmp_path):
    with pytest.raises(FileNotFoundError):
        subject.read_outcomes_after_freeze(tmp_path, {"support_pass": True, "support_gates": {"a": True}}, pd.DataFrame())


def test_support_alias_never_creates_second_gate():
    context = pd.DataFrame({"event_id": ["a", "b"], "structure_gate_state": ["accepted", "unknown"]})
    expected = context.copy(deep=True)
    view = subject.support_view(context)
    assert view[subject.support.GATE_COLUMN].tolist() == ["accepted", "unknown"]
    pd.testing.assert_frame_equal(context, expected)
    with pytest.raises(ValueError):
        subject.support_view(context.assign(**{subject.support.GATE_COLUMN: "accepted"}))


def test_entrypoint_pins_prefix_and_never_calls_simulator():
    tree = ast.parse(Path(subject.__file__).read_text())
    calls = {n.func.id if isinstance(n.func, ast.Name) else n.func.attr for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, (ast.Name, ast.Attribute))}
    assert not calls & {"Study", "simulate_events", "simulate_requests", "replay_arm", "simulate_dual"}
    assert subject.PHASE_END == "2025-01-01"
    run = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run")
    reads = [n for n in ast.walk(run) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "load_source"]
    assert len(reads) == 1
    assert ast.unparse(reads[0].args[1]) == "utc(PHASE_END)"
    assert not any("failed_reduce" in p for p in subject.SOURCES)


def test_outcome_hash_loop_after_feature_freeze_check():
    source = Path(subject.__file__).read_text()
    function = source.split("def read_outcomes_after_freeze", 1)[1].split("def run", 1)[0]
    assert function.index('context_frozen.json') < function.index('OUTCOME_INPUTS.items()')
    assert function.index('output_hashes') < function.index('OUTCOME_INPUTS.items()')
