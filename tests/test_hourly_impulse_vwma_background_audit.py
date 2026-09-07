"""Synthetic-only independent V27 auditor tests; no actual CSV or price reads."""
import copy
from datetime import timedelta
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT/'experiments/active/exp-btcusdtp-1h-vwma-background-support-preholdout-20260907-v27/audit_saved.py'
spec = importlib.util.spec_from_file_location('vwma_background_auditor_tests', PATH)
v = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v)


def serial(row):
    return {k:x.isoformat() if hasattr(x, 'isoformat') else x for k, x in row.items()}


def reference_fixture():
    t = v.stamp('2024-01-01T00:00:00Z')
    source = [dict(open_time=(t+i*v.HOUR).isoformat(), open=100+i%7/10,
                   high=101+i%7/10, low=99+i%7/10, close=100+i%7/10, volume=1+i%3) for i in range(210)]
    ca, cb = v.a.recompute(source, 'SMA'), v.a.recompute(source, 'VWMA')
    fields = ('open_time', 'open', 'high', 'low', 'close', 'volume', 'segment_id')+v.a.FEATURES
    refs = tuple(k for k in ca[0] if k.startswith('reference_'))
    sma = [serial({k:r[k] for k in fields+refs}) for r in ca]
    vwma = [serial({k:r[k] for k in fields+refs}) for r in cb]
    old, new, history = [], [], []
    for a, b in zip(ca, cb):
        e = a['open_time']+v.HOUR
        low, high = v.quantile(history[-720:], 1/3), v.quantile(history[-720:], 2/3)
        frac = None if a['atr'] is None else a['atr']/a['close']
        bucket = None if None in (frac, low, high) else int(frac > low)+int(frac > high)
        history.append(frac)
        known = a['ma'] is not None and a['ma_slope_atr'] is not None
        row = {k:a[k] for k in fields}
        row.update(signal_time=a['open_time'], decision_time=e, signal_atr=a['atr'], entry_open=100.,
                   source_segment_id=700, entry_source_segment_id=700, known_entry_open=True,
                   entry_source_continuous=True, known_5m_available=e, known_5m_colour=1, known_5m_valid=True,
                   management_source_segment_id=900, month=e.strftime('%Y-%m'), utc_6h_bucket=e.hour//6,
                   known_hourly_colour=a['ma_side'], unsigned_hourly_slope_sign=None if not known else
                   (1 if a['ma_slope_atr'] > 0 else -1 if a['ma_slope_atr'] < 0 else 0), known_hourly_valid=known,
                   atr_fraction=frac, atr_tercile_low=low, atr_tercile_high=high, vol_bucket=bucket,
                   raw_strict_body_cross=False, current_or_prior_cross_excluded=False,
                   actual_mother_decision_excluded=False, matching_support=known and bucket is not None,
                   candidate_eligible=known and bucket is not None)
        out = dict(row)
        for k in v.HOURLY_OLD:
            out['inherited_sma_'+k] = row[k]
        for k in ('known_5m_colour', 'known_5m_available', 'known_5m_valid', 'management_source_segment_id'):
            out['exit_sma5_'+k] = row[k]
        for k in v.DEPENDENT+refs:
            out[k] = b[k]
        out.update(known_hourly_colour=b['ma_side'], unsigned_hourly_slope_sign=None if b['ma_slope_atr'] is None else
                   (1 if b['ma_slope_atr'] > 0 else -1 if b['ma_slope_atr'] < 0 else 0),
                   known_hourly_valid=known and b['reference_known'] and b['ma_slope_atr'] is not None,
                   entry_reference_kind='VWMA', entry_reference_length=40)
        old.append(serial(row)); new.append(serial(out))
    return old, sma, vwma, new


def graph_fixture():
    t = v.stamp('2024-01-10T00:00:00Z')
    matching = []
    for i in range(6):
        e = t+i*v.HOUR
        matching.append(dict(open_time=(e-v.HOUR).isoformat(), signal_time=(e-v.HOUR).isoformat(), decision_time=e.isoformat(),
                             month='2024-01', utc_6h_bucket=0, vol_bucket=1, entry_open=100., signal_atr=2.,
                             known_entry_open=True, entry_source_continuous=True, matching_support=True,
                             current_or_prior_cross_excluded=False, actual_mother_decision_excluded=i == 0,
                             open=100., high=102., low=98., close=101., ma=100., ma_side=1, ma_slope_atr=.1,
                             body_ratio=.75, range_atr=2., volume_ratio=1., cross_count24=2., efficiency24=.5,
                             long_close_location=.75, short_close_location=.25, unsigned_hourly_slope_sign=1,
                             known_5m_colour=-1, known_5m_available=e.isoformat(), known_hourly_colour=1, source_segment_id=77))
    mother = dict(event_id='mother', signal_time=(t-v.HOUR).isoformat(), decision_time=t.isoformat(),
                  direction=1, fold='2024H1', initial_stop=96., signal_atr=2.)
    return [mother], matching


def certificates(mothers, edges, allocation):
    # Test fixtures use a single connected component, plus optional isolated mothers.
    edge_ids = {r['event_id'] for r in edges}
    parts = [sorted(edge_ids)] if edge_ids else []
    parts += [[m['event_id']] for m in mothers if m['event_id'] not in edge_ids]
    parts.sort(key=lambda x:x[0])
    out = []
    for i, ids in enumerate(parts):
        local = [r for r in edges if r['event_id'] in ids]
        cs = {r['candidate_id'] for r in local}
        selected = {r['event_id'] for r in allocation if r['event_id'] in ids}
        n, upper = len(selected), min(len(ids), len(cs)//3)
        out.append(dict(component_id=i, mother_ids=ids, fold=next(m['fold'] for m in mothers if m['event_id'] in ids),
                        mother_count=len(ids), candidate_count=len(cs), edge_count=len(local), matched_mothers=n,
                        optimal=True, solution_verified=True, allocated_controls=n*3,
                        connected_component_upper_bound=upper, complete_mother_upper_bound=upper, count_per_mother=3,
                        solver_called=upper > 0, solver_status=0 if upper else None,
                        solver_objective=-n if upper else None, solver_dual_bound=-n if upper else None,
                        solver_mip_gap=0. if upper else None))
    return out


def allocation_for(mothers, edges):
    selected = edges[:3]
    return [dict(event_id=r['event_id'], candidate_id=r['candidate_id'], component_id=0, fold=r['fold']) for r in selected]


def test_reference_full_formula_and_distinct_source_domains():
    f = reference_fixture()
    result = v.verify_reference(*f, [])
    assert result['source_rows'] == 210 and result['causal_ATR_buckets_recomputed']
    assert f[3][0]['segment_id'] == 0 and f[3][0]['source_segment_id'] == 700
    assert f[3][0]['management_source_segment_id'] == 900


@pytest.mark.parametrize('field,value', [('ma', 123), ('ma_side', -3), ('ma_slope_atr', .8), ('cross_count24', 99),
    ('atr_tercile_low', .5), ('atr_fraction', .3), ('vol_bucket', 2), ('known_5m_colour', -1),
    ('known_5m_available', '2024-01-01T00:00:00Z'), ('entry_open', 88), ('entry_source_segment_id', 701),
    ('source_segment_id', 701), ('management_source_segment_id', 901), ('known_hourly_colour', -3),
    ('unsigned_hourly_slope_sign', 8), ('known_hourly_valid', False), ('candidate_eligible', False),
    ('current_or_prior_cross_excluded', True), ('actual_mother_decision_excluded', True),
    ('inherited_sma_ma', 1), ('exit_sma5_known_5m_colour', -1), ('entry_reference_kind', 'SMA')])
def test_reference_drift_rejected(field, value):
    f = reference_fixture(); f[3][200][field] = value
    with pytest.raises(ValueError):
        v.verify_reference(*f, [])


@pytest.mark.parametrize('where', [0, 1, 2, 3])
def test_missing_hour_in_one_source_rejected(where):
    f = list(reference_fixture()); f[where].pop(80)
    with pytest.raises(ValueError):
        v.verify_reference(*f, [])


def test_old_right_tail_allowed():
    f = list(reference_fixture())
    tail = dict(f[0][-1]); tail['open_time'] = (v.stamp(tail['open_time'])+v.HOUR).isoformat()
    f[0].append(tail)
    assert v.verify_reference(*f, [])['old_right_tail_rows_excluded'] == 1


def test_graph_exact_keys_exclusions_and_risk_transfer():
    m, h = graph_fixture()
    s, stages, edges = v.expected_graph(m, h)
    assert s[0]['mother_risk_atr'] == 2 and s[0]['available_controls'] == 5
    assert stages[0]['same_keys'] == 6 and stages[0]['after_actual_exclusion'] == 5
    assert all(r['synthetic_stop'] == 96 and r['candidate_id'] != m[0]['decision_time'] for r in edges)
    h[1]['vol_bucket'] = 2; h[2]['current_or_prior_cross_excluded'] = True
    h[3]['actual_mother_decision_excluded'] = True; h[4]['matching_support'] = False
    assert len(v.expected_graph(m, h)[2]) == 1


@pytest.mark.parametrize('change,reason', [('missing', 'missing_mother_hourly_decision'),
    ('open', 'missing_or_gapped_mother_open'), ('atr', 'mother_atr_mismatch'), ('stop', 'invalid_mother_risk'),
    ('support', 'missing_causal_matching_support'), ('boundary', 'outside_fold_embargo')])
def test_unknown_mothers_are_not_zero_support(change, reason):
    m, h = graph_fixture()
    if change == 'missing':
        h = h[1:]
    elif change == 'open':
        h[0]['known_entry_open'] = False
    elif change == 'atr':
        m[0]['signal_atr'] = 3
    elif change == 'stop':
        m[0]['initial_stop'] = 101
    elif change == 'support':
        h[0]['matching_support'] = False
    else:
        m[0]['fold'] = '2023H2'
    s, counts, edges = v.expected_graph(m, h)
    assert s[0]['support_reason'] == reason and s[0]['available_controls'] is None
    assert all(counts[0][k] is None for k in v.STAGES) and edges == []


def test_short_transfers_direction_but_not_control_colour():
    m, h = graph_fixture(); m[0].update(direction=-1, initial_stop=104)
    edges = v.expected_graph(m, h)[2]
    assert len(edges) == 5 and all(r['synthetic_stop'] == 104 for r in edges)
    # Own SMA5 colour=-1, own hourly colour=+1: neither is an exact matching key.


def test_72h_candidate_boundary_excluded_and_positive_stop_required():
    m, h = graph_fixture()
    e = v.stamp('2024-06-28T00:00:00Z')
    for i, r in enumerate(h):
        r.update(decision_time=(e+i*v.HOUR).isoformat(), signal_time=(e+(i-1)*v.HOUR).isoformat(), month='2024-06')
    m[0].update(decision_time=e.isoformat(), signal_time=(e-v.HOUR).isoformat())
    assert v.expected_graph(m, h)[0][0]['support_reason'] == 'outside_fold_embargo'
    m, h = graph_fixture(); h[1]['entry_open'] = 1
    assert len(v.expected_graph(m, h)[2]) == 4


def test_capacity_feasible_equals_independent_bound():
    m, h = graph_fixture(); edges = v.expected_graph(m, h)[2]
    allocation = allocation_for(m, edges); certs = certificates(m, edges, allocation)
    parts, counts = v.capacity(m, edges, allocation, certs)
    assert parts[0]['independent_upper_bound'] == 1 and counts['mother'] == 3


@pytest.mark.parametrize('bad', ['partial', 'duplicate', 'orphan', 'wrongfold', 'component', 'upper', 'gap', 'status', 'dual', 'omitted'])
def test_capacity_adversarial_rejections(bad):
    m, h = graph_fixture(); edges = v.expected_graph(m, h)[2]
    allocation = allocation_for(m, edges); certs = certificates(m, edges, allocation)
    if bad == 'partial': allocation.pop()
    elif bad == 'duplicate': allocation.append(dict(allocation[0]))
    elif bad == 'orphan': allocation[0]['candidate_id'] = 'foreign'
    elif bad == 'wrongfold': allocation[0]['fold'] = '2023H1'
    elif bad == 'component': allocation[0]['component_id'] = 3
    elif bad == 'upper': certs[0]['complete_mother_upper_bound'] = 2
    elif bad == 'gap': certs[0]['solver_mip_gap'] = .1
    elif bad == 'status': certs[0]['solver_status'] = 1
    elif bad == 'dual': certs[0]['solver_dual_bound'] = -2
    else: allocation = []
    with pytest.raises(ValueError): v.capacity(m, edges, allocation, certs)


def test_opposite_direction_cannot_reuse_same_candidate_time():
    m, h = graph_fixture(); second = dict(m[0], event_id='other', direction=-1, initial_stop=104)
    m.append(second)
    base = v.expected_graph(m[:1], h)[2][:3]
    edges = base+[dict(r, event_id='other') for r in base]
    allocation = [dict(event_id=r['event_id'], candidate_id=r['candidate_id'], fold=r['fold'], component_id=0) for r in edges]
    with pytest.raises(ValueError, match='reuse'):
        v.capacity(m, edges, allocation, certificates(m, edges, allocation))


def test_zero_edge_mother_retained_in_capacity():
    m, h = graph_fixture(); m.append(dict(m[0], event_id='zero'))
    edges = v.expected_graph(m[:1], h)[2]
    allocation = allocation_for(m, edges)
    parts, counts = v.capacity(m, edges, allocation, certificates(m, edges, allocation))
    assert len(parts) == 2 and parts[1]['mother_ids'] == ['zero'] and parts[1]['independent_upper_bound'] == 0
    assert counts['zero'] == 0


@pytest.mark.parametrize('mutator', [lambda r:r.pop(), lambda r:r.append(dict(r[0])),
                                  lambda r:r[0].update(synthetic_stop=99)])
def test_full_edge_set_comparison_not_only_chosen(mutator):
    m, h = graph_fixture(); expected = v.expected_graph(m, h)[2]
    got = copy.deepcopy(expected); mutator(got)
    with pytest.raises(ValueError):
        v.compare_rows(got, expected, 'All edges', ('event_id', 'candidate_id'))


@pytest.mark.parametrize('bad', ['2024-01-01T00:00:00.000000001Z', '2024-01-01T00:00:00', '2024-01-01T00:05:00Z'])
def test_hour_clock_failclosed(bad):
    with pytest.raises(ValueError): v.time_rows([{'open_time':bad}])


def test_csv_timestamp_and_certificate_json_are_distinct():
    v.equal('2024-01-01 00:00:00+00:00', '2024-01-01T00:00:00+00:00', 'Clock')
    v.equal('["2024-01-01T00:00:00+00:00_L"]', '["2024-01-01T00:00:00+00:00_L"]', 'JSON')
    with pytest.raises(ValueError): v.equal(0, None, 'Unknown')


def test_uncommitted_auditor_blocks_before_any_results_read(monkeypatch, tmp_path):
    monkeypatch.setattr(v, 'sha', lambda p:'a')
    monkeypatch.setattr(v.a, 'git', lambda *args:b'wrong source')
    monkeypatch.setattr(v.a, 'load_json', lambda p:pytest.fail('Must not read study metadata'))
    monkeypatch.setattr(v, 'rows', lambda p:pytest.fail('Must not read study CSV'))
    with pytest.raises(ValueError, match='committed'):
        v.verify(tmp_path)


def test_cli_refuses_existing_output_before_verification(monkeypatch, tmp_path):
    out = tmp_path/'audit.json'; out.write_text('original', encoding='utf-8')
    monkeypatch.setattr('sys.argv', ['audit_saved.py', '--out', str(out)])
    monkeypatch.setattr(v, 'verify', lambda:pytest.fail('Must not audit before overwrite guard'))
    with pytest.raises(ValueError, match='overwrite'): v.main()
    assert out.read_text() == 'original'


def bundle():
    mothers, frame = graph_fixture()
    support, stages, edges = v.expected_graph(mothers, frame)
    allocated = allocation_for(mothers, edges)
    certs = certificates(mothers, edges, allocated)
    assignments = [dict(support[0], assigned_controls=3, match_status='matched')]
    controls = []
    for i, r in enumerate(frame[1:4]):
        c = dict(event_id='mother::background_control'+str(i), parent_event_id='mother', matched_event_id='mother',
                 source_mother_decision_time=mothers[0]['decision_time'], signal_time=r['signal_time'],
                 decision_time=r['decision_time'], direction=1, initial_stop=96., signal_atr=2., transferred_risk_atr=2.,
                 entry_open=100., fold='2024H1', candidate_id=r['decision_time'], component_id=0,
                 ma_slope_atr=.1, signed_hourly_slope_sign=1, extension_atr=.5, close_location=.75)
        c.update({'signal_'+k:r[k] for k in v.a.OHLC})
        for k in ('ma', 'ma_side', 'body_ratio', 'range_atr', 'volume_ratio', 'cross_count24', 'efficiency24',
                  'vol_bucket', 'known_5m_colour', 'known_5m_available', 'known_hourly_colour', 'source_segment_id', 'month', 'utc_6h_bucket'):
            c[k] = r[k]
        controls.append(c)
    folds = [dict(fold=f, mothers=int(f == '2024H1'), matched_mothers=int(f == '2024H1'),
                  controls=3 if f == '2024H1' else 0, coverage=1. if f == '2024H1' else None) for f, s, e in v.FOLDS]
    tables = dict(original_mothers=mothers, matching_frame=frame, mother_support=support, stage_counts=stages,
                  eligible_edges=edges, allocation=allocated, assignments=assignments, controls=controls,
                  component_capacity=[{k:json.dumps(x, sort_keys=True) if isinstance(x, (list, dict)) else x for k, x in c.items()} for c in certs],
                  fold_coverage=folds)
    summary = dict(experiment_id=v.E.name, mothers=1, maximum_matched=1, controls=3, unmatched_mothers=0,
                   coverage=1., required_complete_mothers=1, coverage_gate_passed=True, count_per_mother=3,
                   matching_edges=5, unique_eligible_control_times=5, status='background_support_passed', mother_rows_removed=0,
                   status_counts={'matched':1}, matching_keys=list(v.KEYS), components=certs, folds=folds,
                   allocation_role='capacity_witness_not_random_sample')
    for k in ('optimal', 'solution_verified', 'graph_rebuilt_before_allocation', 'legacy_251_226_gate_discarded'):
        summary[k] = True
    for k in ('fallback_used', 'control_time_reuse_allowed', 'outcomes_read_or_computed', 'raw_price_io', 'profitability_test',
              'holdout_consumed', 'training_eligible', 'production_eligible', 'label_study_authorized_by_this_result', 'seed_used'):
        summary[k] = False
    return mothers, tables, summary


def test_complete_ten_table_integration_and_empty_folds(monkeypatch):
    mothers, tables, summary = bundle()
    # Formula test is separate; this fixture isolates whole graph-to-summary wiring.
    monkeypatch.setattr(v, 'verify_reference', lambda *args:{'synthetic_only':True})
    result = v.verify_tables([], [], [], mothers, tables, summary, required=1)
    assert result['complete_mothers'] == 1 and result['independent_capacity_upper_bound'] == 1
    assert len(result['folds']) == 4 and result['folds'][0]['mothers'] == 0


@pytest.mark.parametrize('bad', ['stop', 'ownprice', 'colour', 'parent', 'fold', 'sign', 'missingcontrol',
                                 'unknownzero', 'missingfold', 'denominator', 'randomness', 'outcomes', 'stage', 'edge'])
def test_ten_table_adversarial_drift(monkeypatch, bad):
    mothers, tables, summary = bundle()
    monkeypatch.setattr(v, 'verify_reference', lambda *args:{'synthetic_only':True})
    if bad == 'stop': tables['controls'][0]['initial_stop'] = 95
    elif bad == 'ownprice': tables['controls'][0]['entry_open'] = 105
    elif bad == 'colour': tables['controls'][0]['known_5m_colour'] = 1
    elif bad == 'parent': tables['controls'][0]['parent_event_id'] = 'foreign'
    elif bad == 'fold': tables['controls'][0]['fold'] = '2023H1'
    elif bad == 'sign': tables['controls'][0]['signed_hourly_slope_sign'] = -1
    elif bad == 'missingcontrol': tables['controls'].pop()
    elif bad == 'unknownzero': tables['fold_coverage'][0]['coverage'] = 0
    elif bad == 'missingfold': summary['folds'] = summary['folds'][1:]
    elif bad == 'denominator': summary['mothers'] = 251
    elif bad == 'randomness': summary['seed_used'] = True
    elif bad == 'outcomes': summary['profitability_test'] = True
    elif bad == 'stage': tables['stage_counts'][0]['same_keys'] = 5
    elif bad == 'edge': tables['eligible_edges'].pop()
    with pytest.raises(ValueError):
        v.verify_tables([], [], [], mothers, tables, summary, required=1)
