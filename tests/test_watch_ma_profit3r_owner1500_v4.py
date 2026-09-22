"""Operational handoff guards; no GPU or SSH access."""
from copy import deepcopy
import pytest
from scripts.research import watch_ma_profit3r_owner1500_v4 as w


def completed():
    return {'receipt': {'status': 'completed', 'train_requested': True, 'audit': {'manifest_sha256': w.MANIFEST},
                        'arms': {a: {'status': 'completed', 'best': w.RUN+f'/arm_{a}/weights/best.pt',
                                     'last': w.RUN+f'/arm_{a}/weights/last.pt', 'results_csv': w.RUN+f'/arm_{a}/results.csv'} for a in ('A', 'B')}},
            'progress': {a: {'epochs': list(range(1, 41)), 'mtime_ns': 123} for a in ('A', 'B')}}


def test_training_requires_actual_40_epochs_and_bound_paths():
    state = completed()
    assert w.ready(state)
    state['progress']['B']['epochs'].pop()
    with pytest.raises(RuntimeError, match='exactly epochs'): w.ready(state)
    state = completed()
    state['receipt']['arms']['A']['best'] = 'C:/other/best.pt'
    with pytest.raises(RuntimeError, match='invalid actual'): w.ready(state)
    state = completed()
    state['receipt']['status'] = 'preflight_passed'
    assert not w.ready(state)
    state['receipt']['status'] = 'failed'
    with pytest.raises(RuntimeError, match='training failed'): w.ready(state)


def test_completed_evaluation_requires_all_bindings():
    meta = {'status': 'completed', 'arm': 'A', 'model_sha256': 'model', 'manifest_sha256': w.MANIFEST,
            'ledger_sha256': w.LEDGER, 'runner_sha256': w.RUNNER, 'metrics_code_sha256': w.METRICS,
            'splits': ['val', 'test'], 'confidence': .001, 'nms_iou': .70, 'imgsz': 1280, 'device': 0,
            'augment': False, 'agnostic_nms': False, 'max_det': 300, 'read_only_evaluation': True,
            'artifacts': dict.fromkeys(w.EVAL_NAMES, 'digest')}
    w.validate_evaluation(meta, 'A', 'model')
    for key, bad in [('status', 'running'), ('arm', 'B'), ('model_sha256', 'other'), ('metrics_code_sha256', 'changed'), ('splits', ['test']), ('artifacts', {})]:
        changed = deepcopy(meta); changed[key] = bad
        with pytest.raises(RuntimeError, match='incorrectly bound'): w.validate_evaluation(changed, 'A', 'model')


def test_copy_rejects_existing_drift_and_only_promotes_verified_bytes(tmp_path, monkeypatch):
    dest = tmp_path / 'best.pt'
    dest.write_bytes(b'good'); digest = w.sha(dest)
    w.copy_file('remote', dest, digest)
    with pytest.raises(RuntimeError, match='existing local'): w.copy_file('remote', dest, 'wrong')
    dest.unlink()
    def corrupt_copy(args, **kwargs):
        from pathlib import Path
        Path(args[-1]).write_bytes(b'corrupt')
    monkeypatch.setattr(w.subprocess, 'run', corrupt_copy)
    with pytest.raises(RuntimeError, match='download SHA mismatch'): w.copy_file('remote', dest, digest)
    assert not dest.exists()


def test_handoff_waits_for_b_and_then_runs_frozen_order(monkeypatch):
    pending = completed()
    pending['receipt']['status'] = 'preflight_passed'
    pending['receipt']['arms'].pop('B')
    pending['progress']['B']['epochs'] = [1]
    states = iter([pending, completed()]); calls = []
    monkeypatch.setattr(w.sys, 'argv', ['watcher'])
    monkeypatch.setattr(w, 'snapshot', lambda: next(states))
    monkeypatch.setattr(w, 'state_write', lambda *a, **k: None)
    monkeypatch.setattr(w.time, 'sleep', lambda seconds: calls.append('wait'))
    def sync(receipt):
        calls.append('sync')
        return {f'arm_{arm}/weights/best.pt': {'sha256': arm} for arm in ('A', 'B')}
    monkeypatch.setattr(w, 'training_files', sync)
    monkeypatch.setattr(w, 'evaluate', lambda arm, *args: calls.append('eval_'+arm) or arm)
    monkeypatch.setattr(w, 'controls', lambda arm, path: calls.append('controls_'+arm))
    w.main()
    assert calls == ['wait', 'sync', 'eval_A', 'controls_A', 'eval_B', 'controls_B']
