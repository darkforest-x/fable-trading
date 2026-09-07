"""Independent V27 saved-reference / complete-background capacity audit.

Standard library only. The SHA-pinned V26 *independent auditor* supplies list
SMA/VWMA/RMA formulas and scalar/CSV utilities; no strategy, graph builder,
adapter, allocation helper or solver is imported. Saved hourly OHLCV, not raw5,
is the price boundary. Exact own-hour support and every admissible edge are
recomputed. A feasible allocation attaining each bipartite component's
min(mothers, candidates//3) upper bound independently proves total capacity.
An unselected mother is NOT declared impossible in every optimal allocation.

This is neither random allocation nor economic evidence. Inherited SMA5
columns are checked against their pinned source, not against raw bars; native
15m execution, source authenticity, Pine/live parity and returns are untested.
Hashes/recorded chronology verify bytes/receipts, not an observation of the
historical process. CLI requires committed auditor source and creates only a
new audit.json. Tests call pure functions using synthetic rows exclusively.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[3]
E = Path('experiments/active/exp-btcusdtp-1h-vwma-background-support-preholdout-20260907-v27')
V26 = Path('experiments/active/exp-btcusdtp-1h-vwma-reference-support-preholdout-20260907-v26')
V10 = Path('experiments/active/exp-btcusdtp-1h-matching-support-preholdout-20260906-v10/results')
V4 = Path('experiments/active/exp-btcusdtp-1h-first-k2-preholdout-20260906-v4/results/original_mothers.csv.gz')
BUILDER = '36a315eafb5df20ce1e3d4bd5c15f01b2aa287ee'
CONFIG_SHA = '6784ab472b225dee843d1ee132628e99b625e0bb5c5f10dda8000b5858d7fe64'
FORMULA_SHA = 'd482641d596f1553a54d6c8c91068b07e869a169c579efa17133ed8ca9c8b4aa'
FORMULA_PATH = V26/'audit_saved.py'
_path = ROOT/FORMULA_PATH
if hashlib.sha256(_path.read_bytes()).hexdigest() != FORMULA_SHA:
    raise ValueError('Independent V26 formula source SHA changed')
_spec = importlib.util.spec_from_file_location('_v27_independent_v26', _path)
a = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(a)
require, check, sha, stamp, rows = a.require, a.check, a.sha, a.stamp, a.rows
HOUR, UTC, FOLDS = a.HOUR, a.UTC, a.FOLDS
KEYS = ('month', 'utc_6h_bucket', 'vol_bucket')
DEPENDENT = ('ma', 'ma_side', 'ma_slope_atr', 'cross_count24')
INHERITED = ('entry_open', 'source_segment_id', 'entry_source_segment_id', 'known_entry_open',
             'entry_source_continuous', 'known_5m_available', 'known_5m_colour', 'known_5m_valid',
             'management_source_segment_id')
HOURLY_OLD = DEPENDENT+('known_hourly_colour', 'unsigned_hourly_slope_sign', 'known_hourly_valid')
BUCKETS = ('atr_fraction', 'atr_tercile_low', 'atr_tercile_high', 'vol_bucket')
STAGES = ('same_keys', 'within_fold_embargo', 'valid_support', 'after_cross_exclusion',
          'after_actual_exclusion', 'valid_transferred_stop')
GRAPH = ('original_mothers', 'matching_frame', 'mother_support', 'stage_counts', 'eligible_edges')
TABLES = GRAPH+('allocation', 'assignments', 'controls', 'component_capacity', 'fold_coverage')


def null(v):
    return v is None or str(v).lower() in ('', 'nan', 'none', 'nat', '<na>')


def num(v):
    return None if null(v) else a.number(v)


def flag(v):
    require(v is True or v is False or v in ('True', 'False'), 'Explicit boolean required')
    return v is True or v == 'True'


def equal(got, want, label):
    if null(want):
        require(null(got), label+': unknown became known')
    elif isinstance(want, datetime):
        require((got if isinstance(got, datetime) else stamp(got)) == want, label+': clock')
    elif isinstance(want, bool):
        require(flag(got) == want, label+': boolean')
    elif isinstance(want, str) and want in ('True', 'False'):
        require(flag(got) == flag(want), label+': boolean')
    elif isinstance(want, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})', want):
        require(stamp(got) == stamp(want), label+': clock')
    else:
        try:
            number = a.number(want)
        except ValueError:
            require(got == want, label+': value')
        else:
            check(got, number, label)


def compare(got, want, label, exact=True):
    require(set(got) == set(want) if exact else set(want).issubset(got), label+': schema')
    for k, v in want.items():
        equal(got[k], v, label+'/'+k)


def keyed(data, fields):
    out = {}
    for r in data:
        key = tuple(r[f] for f in fields)
        require(all(not null(v) for v in key) and key not in out, 'Null/duplicate identity '+str(fields))
        out[key] = r
    return out


def compare_rows(got, want, label, keys=('event_id',), ordered=False):
    require(len(got) == len(want), label+': count')
    x, y = keyed(got, keys), keyed(want, keys)
    require(x.keys() == y.keys(), label+': identities')
    if ordered:
        require(list(x) == list(y), label+': order')
    for k in y:
        compare(x[k], y[k], label+'/'+str(k))


def time_rows(data):
    times = [stamp(r['open_time'], hour=True) for r in data]
    require(times and all(x < y for x, y in zip(times, times[1:])), 'Hour order/duplicate/empty')
    require(times[-1] < stamp('2025-01-01T00:00:00Z'), 'Forbidden source phase')
    return times


def quantile(values, q):
    s = sorted(v for v in values if v is not None)
    if len(s) < 168:
        return None
    pos = (len(s)-1)*q
    lo = int(pos)
    return s[lo]+(s[min(lo+1, len(s)-1)]-s[lo])*(pos-lo)


def verify_reference(old, sma, vwma, new, mothers):
    """Independently verify the entire reference range and unchanged source fields."""
    ta, tb, tn, to = [time_rows(x) for x in (sma, vwma, new, old)]
    require(ta == tb == tn and to[0] == ta[0] and to[-1] >= ta[-1], 'Reference exact clock domain')
    h = [r for r, t in zip(old, to) if t <= ta[-1]]
    require([stamp(r['open_time'], hour=True) for r in h] == ta, 'Internal old-source hour missing')
    ca, cb = a.recompute(sma, 'SMA'), a.recompute(sma, 'VWMA')
    mother_times = {stamp(r['decision_time'], hour=True) for r in mothers}
    require(len(mother_times) == len(mothers), 'Duplicate mother decision')
    refs = tuple(k for k in cb[0] if k.startswith('reference_'))
    changed = set(DEPENDENT+refs+('known_hourly_colour', 'unsigned_hourly_slope_sign', 'known_hourly_valid',
                  'raw_strict_body_cross', 'current_or_prior_cross_excluded', 'actual_mother_decision_excluded',
                  'matching_support', 'candidate_eligible'))
    extra = {'inherited_sma_'+k for k in HOURLY_OLD} | {'exit_sma5_'+k for k in
             ('known_5m_colour', 'known_5m_available', 'known_5m_valid', 'management_source_segment_id')} | {
             'entry_reference_kind', 'entry_reference_length'} | set(refs)
    cross_times = {r['open_time']+HOUR for r in cb if r['ma'] is not None and
                   ((r['open'] < r['ma'] < r['close']) or (r['close'] < r['ma'] < r['open']))}
    history, last_segment = [], None
    for oldrow, ar, br, nr, sa, vb in zip(h, sma, vwma, new, ca, cb):
        for saved, derived, name in ((ar, sa, 'SMA'), (br, vb, 'VWMA')):
            for k in ('open_time', 'open', 'high', 'low', 'close', 'volume', 'segment_id')+a.FEATURES+refs:
                equal(saved[k], derived[k], name+'/'+k)
        for k in ('open_time', 'open', 'high', 'low', 'close', 'volume', 'segment_id')+a.FEATURES:
            equal(oldrow[k], sa[k], 'Original SMA/'+k)
        require(set(nr) == set(oldrow)|extra, 'New matching schema')
        for k in set(oldrow)-changed:
            equal(nr[k], oldrow[k], 'Unchanged/'+k)
        for k in HOURLY_OLD:
            equal(nr['inherited_sma_'+k], oldrow[k], 'SMA snapshot/'+k)
        for k in ('known_5m_colour', 'known_5m_available', 'known_5m_valid', 'management_source_segment_id'):
            equal(nr['exit_sma5_'+k], oldrow[k], 'SMA5 snapshot/'+k)
        for k in DEPENDENT+refs:
            equal(nr[k], vb[k], 'New reference/'+k)
        e = vb['open_time']+HOUR
        equal(nr['signal_time'], vb['open_time'], 'Own signal')
        equal(nr['decision_time'], e, 'Own availability')
        equal(nr['signal_atr'], vb['atr'], 'Own ATR')
        require(nr['month'] == e.strftime('%Y-%m'), 'Own month')
        equal(nr['utc_6h_bucket'], e.hour//6, 'Own six-hour bucket')
        if sa['segment_id'] != last_segment:
            history, last_segment = [], sa['segment_id']
        fraction = None if num(ar['atr']) is None else num(ar['atr'])/num(ar['close'])
        low, high = quantile(history[-720:], 1/3), quantile(history[-720:], 2/3)
        bucket = None if None in (fraction, low, high) else int(fraction > low)+int(fraction > high)
        for k, v in zip(BUCKETS, (fraction, low, high, bucket)):
            equal(nr[k], v, 'Causal bucket/'+k)
        history.append(fraction)
        sign = None if vb['ma_slope_atr'] is None else (1 if vb['ma_slope_atr'] > 0 else -1 if vb['ma_slope_atr'] < 0 else 0)
        known_hour = flag(oldrow['known_hourly_valid']) and vb['reference_known'] and vb['ma_side'] in (-1, 1) and sign is not None
        if flag(nr['known_entry_open']):
            require(num(nr['entry_open']) is not None and num(nr['entry_open']) > 0, 'Supported OPEN')
        if flag(nr['entry_source_continuous']):
            require(not null(nr['source_segment_id']), 'Unknown continuous source')
            equal(nr['entry_source_segment_id'], nr['source_segment_id'], 'Physical source continuity')
        if flag(nr['known_5m_valid']):
            require(num(nr['known_5m_colour']) in (-1, 1), 'SMA5 valid colour')
            equal(nr['known_5m_available'], e, 'SMA5 exact availability')
        support = bucket is not None and vb['atr'] is not None and vb['atr'] > 0 and known_hour and all(
            flag(nr[k]) for k in ('known_entry_open', 'entry_source_continuous', 'known_5m_valid'))
        banned = e in cross_times or e-HOUR in cross_times
        want = dict(known_hourly_colour=vb['ma_side'], unsigned_hourly_slope_sign=sign,
                    known_hourly_valid=known_hour, raw_strict_body_cross=e in cross_times,
                    current_or_prior_cross_excluded=banned, actual_mother_decision_excluded=e in mother_times,
                    matching_support=support, candidate_eligible=support and not banned and e not in mother_times,
                    entry_reference_kind='VWMA', entry_reference_length=40)
        compare(nr, want, 'Rebuilt eligibility', exact=False)
    lookup = {r['open_time']:r for r in cb}
    for m in mothers:
        t, e, d = stamp(m['signal_time'], True), stamp(m['decision_time'], True), a.number(m['direction'])
        require(d in (-1, 1) and e == t+HOUR and t in lookup, 'Mother own hour/direction')
        request = dict(event_id=m['event_id'], signal_time=t, decision_time=e, direction=int(d), fold=m['fold'])
        require(a.state(lookup[t], int(d))[0] == 'accepted', 'Mother is not own frozen VWMA entry')
        compare(m, a.entry_row(request, lookup[t]), 'Mother own features', exact=False)
    return dict(source_rows=len(new), old_right_tail_rows_excluded=len(old)-len(new),
                full_SMA_and_VWMA_features_recomputed=True, inherited_SMA5_source_parity=True,
                causal_ATR_buckets_recomputed=True, actual_mother_exclusion_recomputed=True)


def expected_graph(mothers, matching):
    """Independent direct enumeration; no original graph code or outcomes."""
    keyed(mothers, ('event_id',))
    lookup = {stamp(r['decision_time'], True):r for r in matching}
    require(len(lookup) == len(matching), 'Duplicate matching decision')
    actual = {stamp(r['decision_time'], True) for r in mothers}
    require(len(actual) == len(mothers), 'Duplicate mother clock')
    groups = defaultdict(list)
    for r in matching:
        groups[(r['month'], num(r['utc_6h_bucket']), num(r['vol_bucket']))].append(r)
    limits = {f:(stamp(s+'T00:00:00Z'), stamp(e+'T00:00:00Z')-72*HOUR) for f, s, e in FOLDS}
    support, stages, edges = [], [], []
    for m in mothers:
        event, fold, d, t = m['event_id'], m['fold'], a.number(m['direction']), stamp(m['decision_time'], True)
        require(fold in limits and d in (-1, 1), 'Mother fold/direction')
        require(t == stamp(m['signal_time'], True)+HOUR, 'Mother own clock')
        start, end = limits[fold]
        r = dict(event_id=event, fold=fold, decision_time=t, direction=int(d), support_reason='eligible',
                 mother_risk_atr=None, available_controls=None, **{k:None for k in KEYS})
        counts = dict(event_id=event, fold=fold, **{k:None for k in STAGES})
        own = lookup.get(t)
        atr, stop = num(m['signal_atr']), num(m['initial_stop'])
        if own is None:
            r['support_reason'] = 'missing_mother_hourly_decision'
        elif not start <= t < end:
            r['support_reason'] = 'outside_fold_embargo'
        else:
            r.update({k:own[k] for k in KEYS})
            if atr is None or stop is None or min(atr, stop) <= 0:
                r['support_reason'] = 'invalid_mother_risk'
            elif not flag(own['known_entry_open']) or not flag(own['entry_source_continuous']):
                r['support_reason'] = 'missing_or_gapped_mother_open'
            elif num(own['signal_atr']) is None or not math.isclose(atr, num(own['signal_atr']), rel_tol=1e-9, abs_tol=1e-12):
                r['support_reason'] = 'mother_atr_mismatch'
            else:
                risk = d*(num(own['entry_open'])-stop)/atr
                r['mother_risk_atr'] = risk
                if not math.isfinite(risk) or risk <= 0:
                    r['support_reason'] = 'invalid_mother_risk'
                elif not flag(own['matching_support']) or any(null(own[k]) for k in KEYS):
                    r['support_reason'] = 'missing_causal_matching_support'
                else:
                    pool = groups[(own['month'], num(own['utc_6h_bucket']), num(own['vol_bucket']))]
                    predicates = (lambda x:True, lambda x:start <= stamp(x['decision_time'], True) < end,
                                  lambda x:flag(x['matching_support']), lambda x:not flag(x['current_or_prior_cross_excluded']),
                                  lambda x:not flag(x['actual_mother_decision_excluded']) and stamp(x['decision_time'], True) not in actual,
                                  lambda x:math.isfinite(num(x['entry_open'])-d*risk*num(x['signal_atr'])) and num(x['entry_open'])-d*risk*num(x['signal_atr']) > 0)
                    for key, predicate in zip(STAGES, predicates):
                        pool = [c for c in pool if predicate(c)]
                        counts[key] = len(pool)
                    r['available_controls'] = len(pool)
                    for c in pool:
                        ct = stamp(c['decision_time'], True)
                        edges.append(dict(event_id=event, candidate_id=ct.isoformat(), candidate_time=ct,
                                          fold=fold, mother_risk_atr=risk, synthetic_stop=num(c['entry_open'])-d*risk*num(c['signal_atr'])))
        support.append(r); stages.append(counts)
    return support, stages, edges


def capacity(mothers, edges, allocation, certificates):
    """Verify feasibility and independent component upper bounds, without MILP."""
    ids = keyed(mothers, ('event_id',)); legal = keyed(edges, ('event_id', 'candidate_id'))
    links, reverse = {k[0]:set() for k in ids}, defaultdict(set)
    for event, candidate in legal:
        require((event,) in ids, 'Orphan edge')
        links[event].add(candidate); reverse[candidate].add(event)
    chosen = keyed(allocation, ('event_id', 'candidate_id'))
    require(chosen.keys() <= legal.keys(), 'Allocation contains illegal edge')
    counts = Counter(e for e, c in chosen)
    require(all(n == 3 for n in counts.values()), 'Partial triple')
    require(len({c for e, c in chosen}) == len(chosen), 'Global candidate reuse')
    visited, components, event_component = set(), [], {}
    for first in sorted(links):
        if first in visited:
            continue
        stack, members = [first], set()
        while stack:
            event = stack.pop()
            if event in members:
                continue
            members.add(event)
            for c in links[event]:
                stack.extend(reverse[c]-members)
        visited |= members
        candidates = set().union(*(links[e] for e in members))
        upper = min(len(members), len(candidates)//3)
        complete = sum(counts[e] == 3 for e in members)
        require(complete == upper, 'Allocation does not attain independent component upper bound; no proof')
        comp = len(components)
        folds = {ids[(e,)]['fold'] for e in members}
        require(len(folds) == 1, 'Component crosses fold')
        for event in members:
            event_component[event] = comp
        components.append(dict(component_id=comp, mother_ids=sorted(members), fold=folds.pop(),
                               mother_count=len(members), candidate_count=len(candidates),
                               edge_count=sum(len(links[e]) for e in members), matched_mothers=complete,
                               independent_upper_bound=upper))
    require(len(certificates) == len(components), 'Component certificate count')
    for cert, expected in zip(certificates, components):
        for k in ('component_id', 'fold', 'mother_count', 'candidate_count', 'edge_count', 'matched_mothers'):
            equal(cert[k], expected[k], 'Capacity certificate/'+k)
        require(cert['mother_ids'] == expected['mother_ids'], 'Component mother identities')
        for k in ('optimal', 'solution_verified'):
            require(cert[k] is True, 'Invalid solver receipt/'+k)
        check(cert['allocated_controls'], expected['matched_mothers']*3, 'Certificate allocated controls')
        check(cert['connected_component_upper_bound'], expected['independent_upper_bound'], 'Certificate graph bound')
        check(cert['complete_mother_upper_bound'], expected['independent_upper_bound'], 'Certificate optimum bound')
        require(cert['count_per_mother'] == 3, 'Certificate group size')
        if expected['independent_upper_bound']:
            require(cert['solver_called'] is True and cert['solver_status'] == 0, 'Solver did not report optimal')
            check(cert['solver_objective'], -expected['matched_mothers'], 'Solver objective')
            check(cert['solver_dual_bound'], -expected['matched_mothers'], 'Solver bound')
            check(cert['solver_mip_gap'], 0., 'Solver gap')
        else:
            require(cert['solver_called'] is False, 'Empty-capacity solver receipt')
    for (event, candidate), row in chosen.items():
        equal(row['component_id'], event_component[event], 'Allocation component')
        equal(row['fold'], ids[(event,)]['fold'], 'Allocation fold')
    return components, counts


def verify_tables(old_matching, sma, vwma, original_mothers, tables, summary, required=260):
    require(set(tables) == set(TABLES), 'Ten-table contract')
    mothers, matching = tables['original_mothers'], tables['matching_frame']
    compare_rows(mothers, original_mothers, 'All original mother fields', ordered=True)
    reference = verify_reference(old_matching, sma, vwma, matching, mothers)
    support, stages, edges = expected_graph(mothers, matching)
    compare_rows(tables['mother_support'], support, 'Mother support', ordered=True)
    compare_rows(tables['stage_counts'], stages, 'Stage counts', ordered=True)
    compare_rows(tables['eligible_edges'], edges, 'All admissible edges', ('event_id', 'candidate_id'))
    certs = summary['components']
    components, counts = capacity(mothers, edges, tables['allocation'], certs)
    for got, cert in zip(tables['component_capacity'], certs):
        want = {k:json.dumps(v, sort_keys=True) if isinstance(v, (list, dict)) else v for k, v in cert.items()}
        compare(got, want, 'Saved component certificate')
    require(len(tables['component_capacity']) == len(certs), 'Saved certificate count')
    assignments = []
    for r in support:
        n = counts[r['event_id']]
        status = ('matched' if n == 3 else r['support_reason'] if r['support_reason'] != 'eligible' else
                  'insufficient_background_controls' if r['available_controls'] < 3 else 'shared_capacity_unmatched')
        assignments.append(dict(r, assigned_controls=n, match_status=status))
    compare_rows(tables['assignments'], assignments, 'Assignments preserve unknown mothers', ordered=True)
    lookup = {stamp(r['decision_time'], True).isoformat():r for r in matching}
    ml = {r['event_id']:r for r in mothers}
    el = {(r['event_id'], r['candidate_id']):r for r in edges}
    grouped = defaultdict(list)
    for r in tables['allocation']:
        grouped[r['event_id']].append(r)
    wanted_controls = []
    for event, selected in sorted(grouped.items()):
        m = ml[event]; d = a.number(m['direction'])
        for ordinal, allocation in enumerate(sorted(selected, key=lambda r:r['candidate_id'])):
            cid = allocation['candidate_id']; c = lookup[cid]; edge = el[(event, cid)]
            out = dict(event_id=event+'::background_control'+str(ordinal), parent_event_id=event, matched_event_id=event,
                       source_mother_decision_time=stamp(m['decision_time']), signal_time=stamp(c['signal_time']),
                       decision_time=stamp(c['decision_time']), direction=d, initial_stop=edge['synthetic_stop'],
                       signal_atr=num(c['signal_atr']), transferred_risk_atr=edge['mother_risk_atr'], entry_open=num(c['entry_open']),
                       fold=m['fold'], candidate_id=cid, component_id=num(allocation['component_id']),
                       ma_slope_atr=d*num(c['ma_slope_atr']), signed_hourly_slope_sign=d*num(c['unsigned_hourly_slope_sign']),
                       extension_atr=d*(num(c['close'])-num(c['ma']))/num(c['signal_atr']),
                       close_location=num(c['long_close_location' if d == 1 else 'short_close_location']))
            out.update({'signal_'+k:num(c[k]) for k in a.OHLC})
            for k in ('ma', 'ma_side', 'body_ratio', 'range_atr', 'volume_ratio', 'cross_count24', 'efficiency24',
                      'vol_bucket', 'known_5m_colour', 'known_5m_available', 'known_hourly_colour', 'source_segment_id', 'month', 'utc_6h_bucket'):
                out[k] = c[k]
            wanted_controls.append(out)
    compare_rows(tables['controls'], wanted_controls, 'Control own features and transferred risk', ordered=True)
    fold_rows = []
    for fold, _, _ in FOLDS:
        group = [r for r in assignments if r['fold'] == fold]
        n = sum(r['assigned_controls'] == 3 for r in group)
        fold_rows.append(dict(fold=fold, mothers=len(group), matched_mothers=n, controls=3*n, coverage=n/len(group) if group else None))
    compare_rows(tables['fold_coverage'], fold_rows, 'Four-fold coverage', ('fold',), ordered=True)
    for got, want in zip(summary['folds'], fold_rows):
        compare(got, want, 'Summary fold')
    require(len(summary['folds']) == 4, 'Summary missing empty fold')
    n = len(counts); passed = n >= required
    want = dict(experiment_id=E.name, mothers=len(mothers), maximum_matched=n, controls=3*n,
                unmatched_mothers=len(mothers)-n, coverage=n/len(mothers) if mothers else None,
                required_complete_mothers=required, coverage_gate_passed=passed, count_per_mother=3,
                matching_edges=len(edges), unique_eligible_control_times=len({r['candidate_id'] for r in edges}),
                status='background_support_passed' if passed else 'background_support_insufficient', mother_rows_removed=0)
    compare(summary, want, 'Summary counts', exact=False)
    check(summary['status_counts'], dict(Counter(r['match_status'] for r in assignments)), 'Summary reasons')
    require(summary['matching_keys'] == list(KEYS), 'Summary exact matching keys')
    for k in ('optimal', 'solution_verified', 'graph_rebuilt_before_allocation', 'legacy_251_226_gate_discarded'):
        require(summary[k] is True, 'Summary required flag '+k)
    for k in ('fallback_used', 'control_time_reuse_allowed', 'outcomes_read_or_computed', 'raw_price_io', 'profitability_test',
              'holdout_consumed', 'training_eligible', 'production_eligible', 'label_study_authorized_by_this_result', 'seed_used'):
        require(summary[k] is False, 'Unsupported claim '+k)
    require(summary['allocation_role'] == 'capacity_witness_not_random_sample', 'Randomness claim')
    return dict(reference=reference, mothers=len(mothers), complete_mothers=n, controls=3*n, edges=len(edges),
                unique_candidate_times=len({r['candidate_id'] for r in edges}), independent_component_count=len(components),
                independent_capacity_upper_bound=sum(r['independent_upper_bound'] for r in components),
                all_component_bounds_attained=True, folds=fold_rows, status_counts=summary['status_counts'],
                unmatched=[r for r in assignments if r['assigned_controls'] == 0])


def verify(root=ROOT):
    root = Path(root); directory = root/E/'results'; auditor_path = E/'audit_saved.py'
    auditor_sha = sha(root/auditor_path); audit_commit = a.git(root, 'rev-parse', 'HEAD').decode().strip()
    require(hashlib.sha256(a.git(root, 'show', audit_commit+':'+str(auditor_path))).hexdigest() == auditor_sha,
            'Auditor must be committed before real audit')
    require(not (directory/'failure.json').exists(), 'Failed run is not support evidence')
    require(sha(root/E/'config.json') == CONFIG_SHA, 'Frozen config SHA')
    metadata = {n:sha(directory/n) for n in ('started.json', 'support_frozen.json', 'summary.json', 'reference_rebuilt.json')}
    config = a.load_json(root/E/'config.json')
    started, frozen, summary, reference = [a.load_json(directory/n) for n in metadata]
    require({r['builder_commit'] for r in (started, frozen, summary)} == {BUILDER}, 'Builder identity')
    require(started['sources'] == frozen['sources'] == summary['sources'], 'Source receipts differ')
    require(started['config_sha256'] == summary['config_sha256'] == CONFIG_SHA, 'Config linkage')
    require(started['inputs'] == config['inputs'] and len(config['inputs']) == 18, 'Input manifest')
    require(summary['support_frozen_sha256'] == metadata['support_frozen.json'], 'Checkpoint linkage')
    require(frozen['reference_rebuilt_sha256'] == metadata['reference_rebuilt.json'] and summary['reference_rebuild'] == reference,
            'Reference receipt linkage')
    require(frozen['input_receipt'] == summary['source_receipt'], 'Input receipt linkage')
    require(set(summary['output_hashes']) == {n+'.csv.gz' for n in TABLES}, 'Ten-output manifest')
    require(set(frozen['output_hashes']) == {n+'.csv.gz' for n in GRAPH}, 'Pre-capacity graph manifest')
    require(all(summary['output_hashes'][k] == v for k, v in frozen['output_hashes'].items()), 'Graph changed after freeze')
    require(stamp(started['at']) < stamp(frozen['generated_at']) <= stamp(summary['generated_at']), 'Freeze chronology')
    require(frozen['capacity_attempted'] is False and frozen['outcomes_read_or_computed'] is False, 'Pre-capacity scope')
    source_count = a.source_receipt(root, started)
    require(source_count == 12 and len(config['frozen_helpers']) == 6, 'Source cardinality')
    inputs = {**config['inputs'], **config['frozen_helpers'], str(FORMULA_PATH):FORMULA_SHA}
    def hashes():
        for path, digest in inputs.items():
            require(sha(root/path) == digest, 'Input/helper SHA '+path)
        for path, digest in summary['output_hashes'].items():
            require(sha(directory/path) == digest, 'Output SHA '+path)
        for path, digest in metadata.items():
            require(sha(directory/path) == digest, 'Metadata SHA '+path)
        for item in started['sources']:
            require(sha(root/item['path']) == item['sha256'], 'Current source drift '+item['path'])
        require(sha(root/auditor_path) == auditor_sha, 'Auditor drift')
        require(not (directory/'failure.json').exists(), 'Failure appeared during audit')
    hashes()
    pstart, pbase, pfreeze, psum, paudit = [a.load_json(root/V26/n) for n in
        ('results/started.json', 'results/baseline_reproduced.json', 'results/support_frozen.json', 'results/summary.json', 'audit.json')]
    require(stamp(pstart['at']) < stamp(pbase['at']) < stamp(pfreeze['at']) <= stamp(psum['generated_at']) < stamp(started['at']), 'V26 baseline-first lineage')
    require(pstart['sources'] == pfreeze['sources'] and psum['builder_commit'] == pstart['builder_commit'] == pfreeze['builder_commit'], 'V26 source identity')
    require(pstart['config_sha256'] == config['inputs'][str(V26/'config.json')], 'V26 config identity')
    for k in ('all_entry_columns_parity', 'all_feature_columns_parity', 'before_vwma_computation'):
        require(pbase[k] is True, 'V26 baseline receipt '+k)
    require(pbase['events'] == 251 and paudit['status'] == 'passed', 'V26 baseline/audit')
    require(paudit['summary_sha256'] == config['inputs'][str(V26/'results/summary.json')], 'V26 audit summary linkage')
    require(psum['support_frozen_sha256'] == config['inputs'][str(V26/'results/support_frozen.json')] and
            pfreeze['baseline_checkpoint_sha256'] == config['inputs'][str(V26/'results/baseline_reproduced.json')], 'V26 stage SHA linkage')
    require(psum['output_hashes'] == pfreeze['output_hashes'] and len(psum['output_hashes']) == 7, 'V26 outputs')
    for name, digest in psum['output_hashes'].items():
        require(config['inputs'][str(V26/'results'/name)] == digest, 'V26 output pin')
    vstart, vfrozen, vsum = [a.load_json(root/V10/n) for n in ('started.json', 'support_frozen.json', 'summary.json')]
    require(stamp(vstart['at']) <= stamp(vfrozen['generated_at']) <= stamp(vsum['generated_at']) < stamp(started['at']), 'V10 chronology')
    require(vstart['sources'] == vfrozen['source_receipts'] == vsum['source_receipts'], 'V10 source identities')
    require(vfrozen['source_receipt'] == vsum['source_receipt'], 'V10 cutoff receipt')
    require(vfrozen['source_receipt']['holdout_price_rows'] == 0 and
            stamp(vfrozen['source_receipt']['phase_price_last_open']) < stamp('2025-01-01T00:00:00Z'), 'V10 phase scope')
    require(vfrozen['source_receipt']['timestamp_preflight_before_price_hash'] is True, 'V10 timestamp preflight')
    for name, relative in (('matching_frame.csv.gz', V10/'matching_frame.csv.gz'), ('original_mothers.csv.gz', V4)):
        require(vfrozen['output_hashes'][name] == vsum['output_hashes'][name] == config['inputs'][str(relative)], 'V10 input lineage')
    parent_counts = [a.source_receipt(root, r) for r in (vstart, pstart)]
    old, sma, vwma, mothers = [rows(root/p) for p in (V10/'matching_frame.csv.gz', V26/'results/hourly_sma.csv.gz',
                                                    V26/'results/hourly_vwma.csv.gz', V26/'results/vwma_entries.csv.gz')]
    require(len(mothers) == config['mothers'] == 288, 'Frozen mother denominator')
    require(dict(Counter(r['fold'] for r in mothers)) == config['fold_counts'] == {'2023H1':68, '2023H2':74, '2024H1':66, '2024H2':80}, 'Frozen fold counts')
    tables = {n:rows(directory/(n+'.csv.gz')) for n in TABLES}
    result = verify_tables(old, sma, vwma, mothers, tables, summary)
    check(frozen['mothers'], result['mothers'], 'Frozen mother count')
    check(frozen['matching_edges'], result['edges'], 'Frozen edge count')
    for k in ('mothers', 'source_rows', 'old_right_tail_rows_excluded'):
        check(reference[k], result['mothers'] if k == 'mothers' else result['reference'][k], 'Reference receipt/'+k)
    for k in ('old_sma_all_feature_parity', 'new_mother_all_entry_field_parity', 'causal_atr_bucket_parity',
              'inherited_source_and_sma5_unchanged', 'current_prior_cross_rebuilt', 'actual_mother_exclusion_rebuilt'):
        require(reference[k] is True, 'Reference receipt flag/'+k)
    for k in ('allocation_performed', 'outcomes_used', 'raw_source_rebuilt'):
        require(reference[k] is False, 'Reference receipt scope/'+k)
    require(reference['matching_keys'] == list(KEYS) and
            reference['known_hourly_rule'] == 'inherited_known_and_VWMA_side_and_finite_slope', 'Reference gate description')
    common_old = old[:len(sma)]
    for label, source in (('old', common_old), ('new', tables['matching_frame'])):
        check(reference[label+'_candidate_count'], sum(flag(r['candidate_eligible']) for r in source), 'Reference candidate count')
        check(reference[label+'_actual_times'], sum(flag(r['actual_mother_decision_excluded']) for r in source), 'Reference actual exclusions')
    hashes()
    return dict(status='passed', at=datetime.now(UTC).isoformat(), **result, builder_commit=BUILDER,
                audit_commit=audit_commit, auditor_sha256=auditor_sha, formula_dependency_sha256=FORMULA_SHA,
                summary_sha256=metadata['summary.json'], support_frozen_sha256=metadata['support_frozen.json'],
                input_hashes_verified=18, output_hashes_verified=10, source_receipts_verified=source_count,
                parent_source_receipts_verified=parent_counts, input_output_source_hashes_rechecked_after_computation=True,
                saved_chronology=dict(started=started['at'], support_frozen=frozen['generated_at'], summary=summary['generated_at']),
                raw5_aggregation_verified=False, source_authenticity_verified=False, native15m_exit_verified=False,
                profitability_evaluated=False, random_assignment_verified=False, tradingview_live_parity_verified=False,
                capacity_scope='Total complete capacity only; unselected mothers are not proved individually impossible.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=ROOT/E/'audit.json')
    args = parser.parse_args()
    require(not args.out.exists(), 'Refusing to overwrite audit output')
    result = verify()
    with args.out.open('x', encoding='utf-8') as handle:
        json.dump(result, handle, indent=2, ensure_ascii=False, allow_nan=False, default=lambda v:v.isoformat())
        handle.write('\n')
    print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False, default=lambda v:v.isoformat()))


if __name__ == '__main__':
    main()
