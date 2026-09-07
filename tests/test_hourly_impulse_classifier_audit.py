"""Synthetic-only tests of the independent V29 stdlib auditor."""
import ast
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[1]/'experiments/active/exp-btcusdtp-1h-classifier-support-preholdout-20260907-v29/audit_saved.py'
spec = importlib.util.spec_from_file_location('classifier_independent_audit', PATH)
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)
T = datetime(2023, 1, 1, tzinfo=timezone.utc)


def bars(n=180):
    rows = []
    for i in range(n):
        c = 100+i*.6
        rows.append(dict(open_time=(T+i*a.HOUR).isoformat(), open=c-.1, high=c+.5, low=c-.5, close=c))
    return rows


def req(name, i, mother=None, slot=None, matched=True, direction=1):
    return dict(event_id=name, signal_time=(T+i*a.HOUR).isoformat(), decision_time=(T+(i+1)*a.HOUR).isoformat(),
                direction=direction, fold='2023H1', request_kind='control' if mother else 'case',
                mother_id=mother or name, control_slot=slot, matched_support=matched, mother_month='2023-01')


def fixture():
    source = bars()
    states = a.reconstruct(source)
    cases = [req('2023-01-06T01:00:00+00:00_L', 120), req('unmatched', 20, matched=False)]
    cases[0]['signal_close'] = source[120]['close']
    cases[1]['signal_close'] = source[20]['close']
    controls = [req('control'+str(s), i, cases[0]['event_id'], s) for s, i in enumerate((125, 130, 135))]
    requests = dict(case=cases, control=controls)
    assignments = [dict(event_id=r['event_id'], matched_support=r['matched_support'], assigned_controls=3 if r['matched_support'] else 0) for r in cases]
    allocation = [dict(control_event_id=r['event_id'], event_id=r['mother_id'], control_slot=r['control_slot'], candidate_time=r['decision_time'], candidate_id=r['decision_time']) for r in controls]
    cc, ct = [a.own_context(requests[p], states) for p in ('case', 'control')]
    counts, matched, summary = a.support(cc, ct, states)
    tables = dict(hourly_trace=states, case_context=cc, control_context=ct, counts=counts, matched_support=matched)
    return source, tables, requests, assignments, allocation, summary


def test_complete_synthetic_tables():
    result = a.verify_tables(*fixture())
    assert result['contexts_recomputed'] == 5
    assert result['count_rows'] == 62
    assert result['population']['case'] == dict(total=2, accepted=1, abstain=0, unknown=1, known=1)
    assert result['support_values'] == dict(events=1, minimum_fold_events=0, active_months=1, minimum_fold_months=0)


def test_formula_seed_full_windows_and_hand_calculation():
    source = bars(101)
    states = a.reconstruct(source)
    assert states[0]['classifier_center'] is None
    assert states[8]['classifier_center'] is None
    ema, history = 100., [100.]
    for i in range(1, 10):
        ema = (2/11)*(100+i*.6)+(9/11)*ema
        history.append(ema)
    assert states[9]['classifier_center'] == pytest.approx(sum(history)/10)
    assert states[9]['classifier_previous_center'] is None
    assert states[98]['classifier_direction'] is None
    assert states[99]['classifier_count'] == 100
    assert states[99]['classifier_step'] == 1
    assert states[99]['classifier_known'] is True
    assert states[99]['classifier_direction'] == 1


@pytest.mark.parametrize('center,previous,step,close,want', [
    (100, 99, 1, 101, 0), (100, 99, 1, 101.1, 1), (100, 101, 1, 99, 0),
    (100, 101, 1, 98.9, -1), (100, 100, 1, 98.9, -1), (100, 100, 1, 101.1, 0),
    (100, 99, 1, 98.9, 0), (100, 101, 1, 101.1, 0)])
def test_literal_strict_bands_and_flat_short(center, previous, step, close, want):
    assert a.classify(center, previous, step, close) == want


def test_flat_constant_history_neutral_known():
    source = bars(110)
    for r in source:
        r.update(open=123.456, close=123.456, high=124.456, low=122.456)
    states = a.reconstruct(source)
    assert all(r['classifier_center'] == pytest.approx(123.456) for r in states[9:])
    assert all(r['classifier_direction'] == 0 for r in states[99:])


def test_gap_resets_both_emas_and_full_windows():
    source = bars(220)
    del source[110]
    states = a.reconstruct(source)
    assert states[110]['classifier_segment'] == 2
    assert states[110]['classifier_count'] == 1
    assert states[208]['classifier_known'] is False
    assert states[209]['classifier_known'] is True
    fresh = a.reconstruct(source[110:])
    for x, y in zip(states[110:], fresh):
        assert {k: v for k, v in x.items() if k != 'classifier_segment'} == {k: v for k, v in y.items() if k != 'classifier_segment'}


@pytest.mark.parametrize('cut', [9, 10, 99, 100, 125, 150])
def test_prefix_and_future_quotes_do_not_modify_past(cut):
    source = bars()
    past = a.reconstruct(source[:cut])
    assert a.reconstruct(source)[:cut] == past
    for r in source[cut:]:
        r.update(open=50, close=1000, high=1200, low=1)
        r['future_return'] = 1000000
    assert a.reconstruct(source)[:cut] == past


@pytest.mark.parametrize('field,value', [('open', 0), ('high', -1), ('low', float('nan')),
    ('close', float('inf')), ('close', True), ('high', 50), ('low', 200)])
def test_invalid_ohlc_rejected(field, value):
    source = bars()
    source[100][field] = value
    with pytest.raises((AssertionError, ValueError)):
        a.reconstruct(source)


@pytest.mark.parametrize('stamp', ['2023-01-01T00:00:00', '2023-01-01T00:00:00+01:00',
    '2023-01-01T00:00:00.000000001Z', '2023-01-01T00:01:00Z', 123, None])
def test_bad_hour_timestamps(stamp):
    with pytest.raises((AssertionError, ValueError)):
        a.clock(stamp)


def test_nanosecond_receipts_not_truncated():
    assert a.receipt_clock('2023-01-01T00:00:00.000000001Z') < a.receipt_clock('2023-01-01T00:00:00.000000002Z')
    assert a.receipt_clock('2023-01-01T00:00:00.123456789Z')[1] == 123456789
    with pytest.raises(AssertionError):
        a.receipt_clock('2023-01-01T00:00:00.1234567891Z')


@pytest.mark.parametrize('mode', ['duplicate', 'reverse'])
def test_trace_unique_order(mode):
    source = bars()
    if mode == 'duplicate':
        source[100]['open_time'] = source[99]['open_time']
    else:
        source.reverse()
    with pytest.raises(AssertionError):
        a.reconstruct(source)


def test_missing_exact_hour_unknown_not_asof_and_own_control_direction():
    source = bars()
    states = a.reconstruct(source)
    r = req('own', 120, direction=-1)
    assert a.own_context([r], states)[0][a.GATE] == 'abstain'
    del states[120]
    row = a.own_context([r], states)[0]
    assert row[a.GATE] == 'unknown'
    assert row['classifier_reason'] == 'missing_signal_hour'
    assert row['classifier_direction'] is None


@pytest.mark.parametrize('field,value', [('direction', True), ('direction', 0), ('fold', 'bad'),
    ('decision_time', '2023-07-01T00:00:00Z'), ('signal_close', 1), ('classifier_direction', 1)])
def test_request_clock_identity_and_stacking_reject(field, value):
    source, _, requests, *_ = fixture()
    r = requests['case'][0]
    r[field] = value
    with pytest.raises((AssertionError, ValueError)):
        a.own_context([r], a.reconstruct(source))


@pytest.mark.parametrize('which,field,value', [
    ('hourly_trace', 'classifier_center', 10), ('hourly_trace', 'classifier_direction', -1),
    ('hourly_trace', 'classifier_step', 0), ('hourly_trace', 'classifier_previous_center', 0),
    ('hourly_trace', 'classifier_segment', 99), ('hourly_trace', 'classifier_count', 99),
    ('hourly_trace', 'close', 3), ('case_context', 'classifier_gate_state', 'abstain'),
    ('case_context', 'classifier_available_at', '2023-01-01T00:00:00Z'),
    ('case_context', 'direction', -1), ('case_context', 'fold', '2024H1'),
    ('control_context', 'classifier_direction', -1), ('control_context', 'mother_id', 'other'),
    ('counts', 'accepted', 100), ('matched_support', 'complete_known', False)])
def test_saved_tampering_rejected(which, field, value):
    data = fixture()
    rows = data[1][which]
    rows[120 if which == 'hourly_trace' else 0][field] = value
    with pytest.raises(AssertionError):
        a.verify_tables(*data)


@pytest.mark.parametrize('table', ['hourly_trace', 'case_context', 'control_context', 'counts', 'matched_support'])
def test_all_rows_retained(table):
    data = fixture()
    data[1][table].pop()
    with pytest.raises(AssertionError):
        a.verify_tables(*data)


@pytest.mark.parametrize('change', ['reuse', 'parent', 'slot', 'candidate', 'month', 'direction', 'assignment', 'orphan', 'caseid'])
def test_frozen_triple_membership(change):
    _, _, reqs, assignments, allocation, _ = fixture()
    c = reqs['control']
    if change == 'reuse': c[1]['decision_time'] = c[0]['decision_time']
    elif change == 'parent': c[0]['mother_id'] = 'unmatched'
    elif change == 'slot': c[0]['control_slot'] = 1
    elif change == 'candidate': allocation[0]['candidate_id'] = c[1]['decision_time']
    elif change == 'month': c[0]['mother_month'] = '2024-01'
    elif change == 'direction': c[0]['direction'] = -1
    elif change == 'assignment': assignments[0]['assigned_controls'] = 2
    elif change == 'orphan': allocation[0]['control_event_id'] = 'orphan'
    elif change == 'caseid': reqs['case'][1]['event_id'] = reqs['case'][0]['event_id']
    with pytest.raises(AssertionError):
        a.membership(reqs['case'], c, assignments, allocation)


@pytest.mark.parametrize('key', ['population', 'support_values', 'support_gates', 'coverage',
    'accepted_case_control_states', 'trace_validation', 'support_pass', 'status', 'outcomes_read_or_computed', 'economic_acceptance'])
def test_summary_no_false_support_or_economic_claim(key):
    data = fixture()
    data[-1][key] = 'tampered'
    with pytest.raises(AssertionError, match='Summary'):
        a.verify_tables(*data)


def test_complete_known_not_requires_all_control_acceptances():
    source, tables, requests, assignments, allocation, summary = fixture()
    states = a.reconstruct(source)
    cc, ct = [a.own_context(requests[p], states) for p in ('case', 'control')]
    ct[0][a.GATE] = 'abstain'
    _, matched, result = a.support(cc, ct, states)
    assert matched[0]['complete_known'] is True
    assert matched[0]['control_accepted'] == 2
    assert result['accepted_case_control_states']['abstain'] == 1
    ct[0][a.GATE] = 'unknown'
    _, matched, _ = a.support(cc, ct, states)
    assert matched[0]['complete_known'] is False


def test_zero_and_empty_categories_present():
    states = a.reconstruct(bars())
    counts, matched, summary = a.support([], [], states)
    assert len(counts) == 62
    assert not matched
    assert all(r['total'] == 0 and r['accepted_rate'] is None for r in counts)
    assert not summary['support_pass']


def test_request_and_source_not_mutated():
    data = fixture()
    before = deepcopy(data)
    a.verify_tables(*data)
    assert data == before


def test_create_only_receipt(tmp_path):
    path = tmp_path/'audit.json'
    a.write_receipt(path, {'status': 'passed'})
    first = path.read_bytes()
    with pytest.raises(FileExistsError):
        a.write_receipt(path, {'status': 'replaced'})
    assert path.read_bytes() == first


def test_auditor_imports_only_stdlib():
    tree = ast.parse(PATH.read_text())
    imports = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    imports += [alias.name for n in ast.walk(tree) if isinstance(n, ast.Import) for alias in n.names]
    assert not any(s.split('.')[0] in {'pandas', 'numpy', 'yoyo', 'scipy', 'importlib'} for s in imports)


def test_verify_fail_closed_when_auditor_not_committed(monkeypatch, tmp_path):
    monkeypatch.setattr(a.subprocess, 'check_output', lambda *args, **kwargs: 'commit' if kwargs.get('text') else b'wrong bytes')
    monkeypatch.setattr(a, 'read_json', lambda p: pytest.fail('Read study before auditor source guard'))
    with pytest.raises(AssertionError, match='Commit auditor'):
        a.verify(root=PATH.parents[3], experiment=tmp_path)


def valid_config():
    return dict(support=a.THRESHOLDS.copy(), source_pins=a.SOURCE_PINS.copy(), feature_sources=a.FEATURE_PINS.copy(),
                parameters=dict(center_length=10, range_length=100, band_multiple=1, ema_adjust=False,
                    ema_seed='first_value_per_hourly_segment', short_slope='less_than_or_equal', band_boundary='strict',
                    visual_offset_used=False, warmup_unknown=True), population=dict(cases=251, controls=744, matched=248, unmatched=3),
                folds=[[f, *b] for f, b in a.FOLDS.items()], embargo_hours=72, new_allocation=False,
                reference_is_original_SMA251=True, outcomes_read_or_computed=False, economic_acceptance=False,
                holdout_consumed=False, training_eligible=False, production_eligible=False,
                gate='completed_K1_Trend_Classifier_label_equals_direction', experiment_id=a.EXPERIMENT.name,
                trace_rows=18222, trace_start='2022-11-30T16:00:00Z', trace_end='2024-12-28T22:00:00Z',
                phase_end_exclusive='2025-01-01T00:00:00Z')


def test_config_fixed_contract():
    a.verify_config(valid_config())


@pytest.mark.parametrize('key,value', [('source_pins', {}), ('feature_sources', {}), ('support', {}),
    ('parameters', {}), ('population', {}), ('folds', []), ('embargo_hours', 71), ('new_allocation', True),
    ('reference_is_original_SMA251', False), ('outcomes_read_or_computed', True), ('economic_acceptance', True),
    ('holdout_consumed', True), ('training_eligible', True), ('production_eligible', True), ('trace_rows', 1),
    ('trace_start', '2022-01-01T00:00:00Z'), ('trace_end', '2025-12-01T00:00:00Z'),
    ('phase_end_exclusive', '2026-01-01T00:00:00Z'), ('gate', 'different'), ('experiment_id', 'v28')])
def test_config_drift_rejected(key, value):
    config = valid_config()
    config[key] = value
    with pytest.raises(AssertionError):
        a.verify_config(config)


def test_boolean_scope_cannot_be_coerced_to_integer_zero():
    data = fixture()
    data[-1]['outcomes_read_or_computed'] = 0
    with pytest.raises(AssertionError, match='Summary'):
        a.verify_tables(*data)
