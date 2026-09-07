"""Independent standard-library audit of V29 saved-hour classifier support.

ChartPrime source: https://www.tradingview.com/script/AtJtdaDe-Trend-Classifier-ChartPrime/
AtJtdaDe.pine (MPL2.0) lines21-60 defines SMA(EMA(close,10),10) and
SMA(EMA(high-low,100),100). EMA seeds at the first observed hour in each
contiguous segment; recursion is alpha*x+(1-alpha)*previous. Full SMA windows
are required. The source's flat slope belongs to its short branch. Only the
current completed hour is used, never the offset=-1 plotting diamond.

No pandas, strategy, feature, allocation or statistical helper imports. This
recomputes saved-hour numeric state, own-clock contexts and support accounting;
it does not verify raw5 aggregation, source authenticity, Pine/live execution,
randomization replay, economic outcomes or inference. Hashes and recorded
chronology are receipt checks, not proof of an historical atomic snapshot.
Run only after this auditor is committed. --out is strictly create-only.
"""
from __future__ import annotations

import argparse
from collections import Counter, deque
import csv
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[3]
EXPERIMENT = Path(__file__).resolve().parent
V20 = Path('experiments/active/exp-btcusdtp-1h-confirmed-structure-preholdout-20260906-v20/results')
V24 = Path('experiments/active/exp-btcusdtp-1h-fixed-clock-preholdout-20260907-v24/results')
V4 = Path('experiments/active/exp-btcusdtp-1h-first-k2-preholdout-20260906-v4/results/original_mothers.csv.gz')
TRACE_SHA = 'be9b9e73108047ada00ffac0ed0c4d5b2a3c137000435872c2d33c4ec1cbf7dd'
PINE_SHA = '0e425fb43caeda0638bb671ee8e944f61844205833fb52f0dcdd8f8728ebbddd'
PINE = 'experiments/active/exp-chartprime-public-confluence-audit-20260906-v1/sources/AtJtdaDe.pine'
SOURCE_PINS = {PINE: PINE_SHA,
               'yoyo/evaluation/hourly_impulse_structure_event_support.py': '2d62af2f4b837bf714754d5dce13242b9a1e8a53a31014d23818417d11796024',
               'yoyo/evaluation/hourly_impulse_structure_event_research.py': 'd605300c116af22778e3d6a6f1402d16b0249701f18baf64fc7324ef9f765f64'}
FEATURE_PINS = {'yoyo/data/hourly_impulse.py': 'f1128f514456f0e1a7bf89b719b2333ebe3b12f66402d58c82764d355be97a3b',
                'yoyo/data/hourly_impulse_structure.py': '84124cbecc1d31c00e22aa4c80b41741461f2f1fc2f17ccab5b0b838e9743128'}
HOUR = timedelta(hours=1)
OHLC = ('open_time', 'open', 'high', 'low', 'close')
CONTEXT = ('classifier_available_at', 'classifier_segment', 'classifier_count',
           'classifier_center', 'classifier_step', 'classifier_previous_center',
           'classifier_direction', 'classifier_known', 'classifier_reason')
GATE = 'classifier_gate_state'
OUTPUTS = {n+'.csv.gz' for n in ('hourly_trace', 'case_context', 'control_context', 'counts', 'matched_support')}
FOLDS = {str(y)+'H'+str(h): (f'{y}-{1 if h == 1 else 7:02d}-01T00:00:00Z',
          f'{y if h == 1 else y+1}-{7 if h == 1 else 1:02d}-01T00:00:00Z')
         for y in (2023, 2024) for h in (1, 2)}
MONTHS = [f'{y}-{m:02d}' for y in (2023, 2024) for m in range(1, 13)]
THRESHOLDS = dict(minimum_events=80, minimum_per_fold=12,
                  minimum_active_months=12, minimum_months_per_fold=3)


def require(ok, label):
    if not ok:
        raise AssertionError(label)


def missing(v):
    return v is None or isinstance(v, str) and v in ('', 'nan', 'NaN', 'NaT', '<NA>') or isinstance(v, float) and math.isnan(v)


def number(v):
    require(not isinstance(v, bool) and not missing(v), 'Missing/boolean numeric value')
    x = float(v)
    require(math.isfinite(x), 'Nonfinite numeric value')
    return x


def integer(v):
    x = number(v)
    require(x == int(x), 'Noninteger value')
    return int(x)


def flag(v):
    require(type(v) is bool or v in ('True', 'False'), 'Explicit boolean required')
    return v is True or v == 'True'


def clock(v, hour=True):
    require(isinstance(v, (str, datetime)), 'Explicit timestamp required')
    if isinstance(v, str):
        fraction = re.search(r'\.(\d+)(?:Z|[+-]\d\d:\d\d)$', v)
        require(not hour or not fraction or not any(c != '0' for c in fraction[1]), 'Subhour clock')
        # Python3.9 accepts microseconds only. Retain ns separately for receipts.
        parse = v[:fraction.start(1)]+fraction[1][:6].ljust(6, '0')+v[fraction.end(1):] if fraction else v
        t = datetime.fromisoformat(parse.replace('Z', '+00:00'))
    else:
        t = v
    require(t.tzinfo is not None and t.utcoffset() == timedelta(0), 'Explicit UTC required')
    require(not hour or (t.minute, t.second, t.microsecond) == (0, 0, 0), 'Native hourly clock required')
    return t.astimezone(timezone.utc)


def receipt_clock(v):
    """Exact nanosecond chronology key; never truncate legitimate receipts."""
    require(isinstance(v, str), 'Receipt timestamp string required')
    t = clock(v, hour=False)
    fraction = re.search(r'\.(\d+)(?:Z|[+-]\d\d:\d\d)$', v)
    digits = fraction[1] if fraction else ''
    require(len(digits) <= 9, 'Receipt precision beyond nanoseconds')
    return t.replace(microsecond=0), int(digits.ljust(9, '0')) if digits else 0


def equal(got, want, label):
    if missing(want):
        require(missing(got), label+': unknown became known')
    elif isinstance(want, bool):
        require(flag(got) == want, label)
    elif isinstance(want, datetime):
        require(clock(got) == want, label)
    elif isinstance(want, int):
        require(integer(got) == want, label)
    elif isinstance(want, float):
        require(math.isclose(number(got), want, rel_tol=1e-12, abs_tol=1e-12), label)
    else:
        require(got == want, label)


def indexed(rows, key='event_id'):
    out = {}
    for r in rows:
        require(key in r and isinstance(r[key], str) and not missing(r[key]) and r[key] not in out, 'Null/duplicate '+key)
        out[r[key]] = r
    return out


def compare_rows(got, expected, label):
    require(len(got) == len(expected), label+': row count')
    for i, (a, b) in enumerate(zip(got, expected)):
        require(set(a) == set(b), label+': schema')
        for k, v in b.items():
            equal(a[k], v, f'{label}/{i}/{k}')


def request_equal(a, b):
    """Identity text is never guessed to be a timestamp (IDs may look like one)."""
    require(set(b).issubset(a), 'Original request column dropped')
    for k, v in b.items():
        if k in ('signal_time', 'decision_time'):
            equal(a[k], clock(v), 'Request '+k)
        elif missing(v):
            equal(a[k], None, 'Request '+k)
        elif k in ('event_id', 'fold', 'request_kind', 'mother_id', 'mother_month'):
            equal(a[k], v, 'Request '+k)
        elif type(v) is bool or v in ('True', 'False'):
            equal(a[k], flag(v), 'Request '+k)
        else:
            try:
                x = number(v)
            except (AssertionError, ValueError, TypeError):
                equal(a[k], v, 'Request '+k)
            else:
                equal(a[k], x, 'Request '+k)


def classify(center, previous, step, close):
    """Literal source direction, including its flat-slope short branch."""
    return 1 if center > previous and close > center+step else -1 if center <= previous and close < center-step else 0


def reconstruct(rows):
    """Sequential OHLC-only reconstruction; gap starts new recursive history."""
    output, last, segment = [], None, 0
    for r in rows:
        t = clock(r['open_time'])
        require(last is None or t > last, 'Trace chronological unique hours')
        o, h, l, c = [number(r[k]) for k in OHLC[1:]]
        require(0 < l <= min(o, c) <= max(o, c) <= h, 'Invalid OHLC geometry')
        if last is None or t != last+HOUR:
            segment += 1
            count, ema_c, ema_r, previous = 0, c, h-l, None
            centers, ranges = deque(maxlen=10), deque(maxlen=100)
        else:
            # Preserve exactly constant inputs, as a recursive mean should.
            if ema_c != c:
                ema_c = (2/11)*c+(1-2/11)*ema_c
            if ema_r != h-l:
                ema_r = (2/101)*(h-l)+(1-2/101)*ema_r
        centers.append(ema_c)
        ranges.append(ema_r)
        count += 1
        center = math.fsum(centers)/10 if count >= 10 else None
        step = math.fsum(ranges)/100 if count >= 100 else None
        known = center is not None and previous is not None and step is not None
        d = classify(center, previous, step, c) if known else None
        output.append(dict(open_time=t, open=o, high=h, low=l, close=c,
                           classifier_available_at=t+HOUR, classifier_segment=segment,
                           classifier_count=count, classifier_center=center, classifier_step=step,
                           classifier_previous_center=previous, classifier_direction=d,
                           classifier_known=known, classifier_reason='known' if known else 'warmup'))
        last, previous = t, center
    return output


def own_context(requests, states):
    indexed(requests)
    lookup = {r['open_time']: r for r in states}
    out = []
    for r in requests:
        require(not any(k.startswith('classifier_') for k in r), 'Stacked classifier context')
        t, e, d = clock(r['signal_time']), clock(r['decision_time']), integer(r['direction'])
        require(e == t+HOUR and d in (-1, 1) and r['fold'] in FOLDS, 'Own clock/direction/fold')
        start, end = map(clock, FOLDS[r['fold']])
        require(start <= e < end-timedelta(hours=72), 'Own fold/72h embargo')
        if t in lookup:
            bar = lookup[t]
            own = {k: bar[k] for k in CONTEXT}
            if 'signal_close' in r:
                equal(r['signal_close'], bar['close'], 'Own signal close')
        else:
            own = {k: None for k in CONTEXT}
            own.update(classifier_available_at=e, classifier_count=0, classifier_known=False,
                       classifier_reason='missing_signal_hour')
        own[GATE] = ('accepted' if own['classifier_direction'] == d else 'abstain') if own['classifier_known'] else 'unknown'
        out.append(dict(r, **own))
    return out


def membership(cases, controls, assignments, allocation):
    cm, cr, am, al = indexed(cases), indexed(controls), indexed(assignments), indexed(allocation, 'control_event_id')
    require(not cm.keys() & cr.keys() and am.keys() == cm.keys() and al.keys() == cr.keys(), 'Frozen membership identities')
    used, case_times = set(), {clock(r['decision_time']) for r in cases}
    require(len(case_times) == len(cases), 'Duplicate case time')
    for c in controls:
        a, e = al[c['event_id']], clock(c['decision_time'])
        require(c['mother_id'] in cm and c['mother_id'] == a['event_id'], 'Control parent')
        m = cm[c['mother_id']]
        require(e not in used and e not in case_times, 'Global control time reuse/exclusion')
        used.add(e)
        require(e == clock(a['candidate_time']) == clock(a['candidate_id']), 'Control own candidate time')
        require(integer(c['control_slot']) == integer(a['control_slot']), 'Control slot')
        require(c['request_kind'] == 'control' and flag(c['matched_support']), 'Control membership flag')
        require(c['fold'] == m['fold'] and integer(c['direction']) == integer(m['direction']) and
                c['mother_month'] == m['mother_month'] == e.strftime('%Y-%m'), 'Control fold/direction/month')
    for m in cases:
        group = [c for c in controls if c['mother_id'] == m['event_id']]
        a, match = am[m['event_id']], flag(am[m['event_id']]['matched_support'])
        require(m['mother_id'] == m['event_id'] and missing(m['control_slot']) and m['request_kind'] == 'case', 'Case linkage')
        require(m['mother_month'] == clock(m['decision_time']).strftime('%Y-%m'), 'Case month')
        require(flag(m['matched_support']) == match and len(group) == integer(a['assigned_controls']) == (3 if match else 0), 'Complete three or zero')
        require(not group or {integer(c['control_slot']) for c in group} == {0, 1, 2}, 'Distinct triple slots')


def tally(rows):
    c = Counter(r[GATE] for r in rows)
    require(set(c) <= {'accepted', 'abstain', 'unknown'}, 'Unknown gate state')
    return dict(total=len(rows), accepted=c['accepted'], abstain=c['abstain'], unknown=c['unknown'], known=len(rows)-c['unknown'])


def support(cases, controls, states):
    counts, matched = [], []
    for pop, rows in (('case', cases), ('control', controls)):
        for dim, keys in (('all', ['all']), ('fold', list(FOLDS)), ('direction', ['1', '-1']), ('month', MONTHS)):
            for key in keys:
                group = [r for r in rows if dim == 'all' or (r['fold'] if dim == 'fold' else str(integer(r['direction']))
                         if dim == 'direction' else clock(r['decision_time']).strftime('%Y-%m')) == key]
                c = tally(group)
                counts.append(dict(population=pop, dimension=dim, key=key, **c,
                                   accepted_rate=c['accepted']/c['total'] if c['total'] else None))
    for m in cases:
        group = sorted([c for c in controls if c['mother_id'] == m['event_id']], key=lambda c: integer(c['control_slot']))
        c = tally(group)
        require(len(group) in (0, 3), 'Matched triple count')
        complete = bool(m['classifier_known'] and len(group) == 3 and not c['unknown'])
        matched.append(dict(event_id=m['event_id'], fold=m['fold'], case_state=m[GATE], matched_support=bool(group),
                            control_ids='|'.join(c['event_id'] for c in group), control_total=len(group),
                            **{'control_'+s: c[s] for s in ('accepted', 'abstain', 'unknown')}, complete_known=complete,
                            accepted_case_complete_known=complete and m[GATE] == 'accepted'))
    accepted = [r for r in cases if r[GATE] == 'accepted']
    values = dict(events=len(accepted), minimum_fold_events=min(sum(r['fold'] == f for r in accepted) for f in FOLDS),
                  active_months=len({clock(r['decision_time']).strftime('%Y-%m') for r in accepted}),
                  minimum_fold_months=min(len({clock(r['decision_time']).strftime('%Y-%m') for r in accepted if r['fold'] == f}) for f in FOLDS))
    gates = {k: values[v] >= THRESHOLDS[k] for k, v in zip(THRESHOLDS, ('events', 'minimum_fold_events', 'active_months', 'minimum_fold_months'))}
    ratio = lambda n, d: dict(numerator=n, denominator=d, rate=n/d if d else None)
    population = dict(case=tally(cases), control=tally(controls))
    complete = sum(m['complete_known'] for m in matched)
    coverage = dict(case_known=ratio(population['case']['known'], len(cases)), control_known=ratio(population['control']['known'], len(controls)),
                    complete_known_triples_all_cases=ratio(complete, len(cases)),
                    complete_known_triples_accepted_cases=ratio(sum(m['accepted_case_complete_known'] for m in matched), len(accepted)))
    summary = dict(population=population, support_values=values, support_gates=gates, support_pass=all(gates.values()),
                   coverage=coverage, accepted_case_control_states=tally([c for c in controls if c['mother_id'] in {m['event_id'] for m in accepted}]),
                   status='support_pass_requires_separate_outcome_preregistration' if all(gates.values()) else 'insufficient_support_no_outcomes',
                   outcomes_read_or_computed=False, economic_acceptance=False,
                   trace_validation=dict(rows=len(states), segments=len({r['classifier_segment'] for r in states}),
                                         raw5_aggregation_verified=False, pine_builtin_parity=False))
    return counts, matched, summary


def verify_tables(source, tables, requests, assignments, allocation, summary):
    """Pure list-of-dictionaries audit; synthetic test populations may be small."""
    states = reconstruct(source)
    compare_rows(tables['hourly_trace'], states, 'Classifier trace')
    membership(requests['case'], requests['control'], assignments, allocation)
    own = {p: own_context(requests[p], states) for p in ('case', 'control')}
    for p in own:
        saved = tables[p+'_context']
        require(len(saved) == len(own[p]), 'Context row count')
        indexed(saved)
        for r, expected, original in zip(saved, own[p], requests[p]):
            require(set(r) == set(expected), 'Context schema')
            request_equal(r, original)
            for k in (*CONTEXT, GATE):
                equal(r[k], expected[k], p+'/'+k)
    counts, matched, expected = support(own['case'], own['control'], states)
    compare_rows(tables['counts'], counts, 'Counts')
    compare_rows(tables['matched_support'], matched, 'Matched support')
    for key, value in expected.items():
        require(json.dumps(summary[key], sort_keys=True, allow_nan=False) == json.dumps(value, sort_keys=True, allow_nan=False), 'Summary '+key)
    return dict(trace_rows_recomputed=len(states), contexts_recomputed=sum(map(len, own.values())),
                count_rows=len(counts), mother_rows=len(matched), population=expected['population'],
                support_values=expected['support_values'], coverage=expected['coverage'])


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text())


def read_csv(path, columns=None):
    with (gzip.open if str(path).endswith('.gz') else open)(path, 'rt', newline='') as f:
        reader = csv.DictReader(f)
        require(reader.fieldnames is not None and len(set(reader.fieldnames)) == len(reader.fieldnames), 'CSV schema')
        require(columns is None or set(columns) <= set(reader.fieldnames), 'CSV required columns')
        return [{k: r[k] for k in columns} if columns else r for r in reader]


def verify_sources(root, receipt):
    commit = receipt['builder_commit']
    t = subprocess.check_output(['git', 'show', '-s', '--format=%cI', commit], cwd=root, text=True).strip()
    commit_t = datetime.fromisoformat(t).astimezone(timezone.utc)
    require((commit_t.replace(microsecond=0), commit_t.microsecond*1000) <= receipt_clock(receipt['at']), 'Source committed after run')
    require(receipt['sources'] and len({s['path'] for s in receipt['sources']}) == len(receipt['sources']), 'Source receipt identities')
    for s in receipt['sources']:
        blob = subprocess.check_output(['git', 'show', commit+':'+s['path']], cwd=root)
        require(hashlib.sha256(blob).hexdigest() == s['sha256'], 'Source commit SHA: '+s['path'])
    return len(receipt['sources'])


def verify_config(config):
    require(config['support'] == THRESHOLDS, 'Frozen support thresholds')
    require(config['source_pins'] == SOURCE_PINS and config['feature_sources'] == FEATURE_PINS, 'Original source pins')
    require(config['parameters'] == dict(center_length=10, range_length=100, band_multiple=1, ema_adjust=False,
            ema_seed='first_value_per_hourly_segment', short_slope='less_than_or_equal', band_boundary='strict',
            visual_offset_used=False, warmup_unknown=True), 'Frozen classifier parameters')
    require(config['population'] == dict(cases=251, controls=744, matched=248, unmatched=3), 'Frozen population')
    require(config['folds'] == [[f, *b] for f, b in FOLDS.items()] and config['embargo_hours'] == 72, 'Frozen fold/embargo')
    require(config['new_allocation'] is False and config['reference_is_original_SMA251'] is True, 'No new samples/reference')
    require(config['gate'] == 'completed_K1_Trend_Classifier_label_equals_direction' and config['experiment_id'] == EXPERIMENT.name, 'Experiment and gate identity')
    require(config['trace_rows'] == 18222 and clock(config['trace_start']) == clock('2022-11-30T16:00:00Z') and
            clock(config['trace_end']) == clock('2024-12-28T22:00:00Z') and clock(config['phase_end_exclusive']) == clock('2025-01-01T00:00:00Z'), 'Frozen trace phase')
    require(all(config[k] is False for k in ('outcomes_read_or_computed', 'economic_acceptance', 'holdout_consumed', 'training_eligible', 'production_eligible')), 'Config non-economic scope')


def verify(root=ROOT, experiment=EXPERIMENT):
    """Saved-only CLI entry; exact source/input/output guards bracket all reads."""
    root, experiment = Path(root), Path(experiment)
    script = Path(__file__).resolve()
    auditor_sha, auditor_commit = sha(script), subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    blob = subprocess.check_output(['git', 'show', auditor_commit+':'+script.relative_to(root).as_posix()], cwd=root)
    require(hashlib.sha256(blob).hexdigest() == auditor_sha, 'Commit auditor before actual reads')
    directory = experiment/'results'
    require(not (directory/'failure.json').exists(), 'Failed study is not support evidence')
    config, started, frozen, summary = [read_json(p) for p in (experiment/'config.json', directory/'started.json', directory/'support_frozen.json', directory/'summary.json')]
    verify_config(config)
    require(sha(experiment/'config.json') == started['config_sha256'], 'Config SHA')
    source_count = verify_sources(root, started)
    require(started['sources'] == frozen['sources'] and started['builder_commit'] == frozen['builder_commit'] == summary['builder_commit'], 'Run source identity')
    require(receipt_clock(started['at']) <= receipt_clock(frozen['at']) <= receipt_clock(summary['generated_at']), 'Run chronology')
    require(summary['support_frozen_sha256'] == sha(directory/'support_frozen.json'), 'Support freeze SHA')
    require(started['inputs'] == config['inputs'] == frozen['input_receipt']['inputs'], 'Input receipt identity')
    require(started['source_pins'] == config['source_pins'] == frozen['input_receipt']['classifier_sources'], 'Classifier source receipt identity')
    allowed = {str(V20/n) for n in ('started.json', 'context_frozen.json', 'hourly_trace.csv.gz', 'failure.json', 'outcomes_started.json', 'outcomes_resumed_1.json')}
    allowed |= {str(V24/n) for n in ('started.json', 'sampling_frozen.json', 'case_requests.csv.gz', 'control_requests.csv.gz', 'random_assignments.csv.gz', 'random_allocation.csv.gz')}
    allowed.add(str(V4))
    require(set(config['inputs']) == allowed, 'Unexpected input (no outcome tables allowed)')
    require(config['inputs'][str(V20/'hourly_trace.csv.gz')] == TRACE_SHA, 'V20 source trace pin')
    require(set(frozen['output_hashes']) == OUTPUTS and frozen['output_hashes'] == summary['output_hashes'], 'Output manifest')
    guards = {root/p: s for p, s in config['inputs'].items()}
    guards.update({root/p: s for p, s in {**SOURCE_PINS, **FEATURE_PINS}.items()})
    guards.update({root/s['path']: s['sha256'] for s in started['sources']})
    guards.update({directory/p: s for p, s in frozen['output_hashes'].items()})
    guards.update({script: auditor_sha, experiment/'config.json': started['config_sha256'],
                   directory/'summary.json': sha(directory/'summary.json'), directory/'started.json': sha(directory/'started.json'),
                   directory/'support_frozen.json': summary['support_frozen_sha256']})
    def check_hashes():
        for p, s in guards.items():
            require(sha(p) == s, 'Frozen file SHA changed: '+str(p))
    check_hashes()
    old, old_freeze, first, failure, resumed = [read_json(root/V20/n) for n in ('started.json', 'context_frozen.json', 'outcomes_started.json', 'failure.json', 'outcomes_resumed_1.json')]
    times = [receipt_clock(r['at']) for r in (old, old_freeze, first, failure, resumed)]
    require(all(a < b for a, b in zip(times, times[1:])), 'V20 recorded freeze/recovery chronology')
    require(old_freeze['outcomes_read'] is False and old_freeze['output_hashes']['hourly_trace.csv.gz'] == TRACE_SHA, 'V20 pre-outcome feature freeze')
    require(failure['status'] == 'failed_not_evidence' and failure['error_type'] == 'AssertionError', 'V20 historical failure identity')
    source_receipt = old_freeze['source_receipt']
    require(source_receipt['holdout_price_rows'] == 0 and clock(source_receipt['phase_price_last_open'], False) < clock('2025-01-01T00:00:00Z'), 'V20 pre2025/holdout source scope')
    require(all(r['context_frozen_sha256'] == sha(root/V20/'context_frozen.json') for r in (first, resumed)), 'V20 same-feature recovery')
    sampling_start, sampling = [read_json(root/V24/n) for n in ('started.json', 'sampling_frozen.json')]
    parent_counts = [verify_sources(root, r) for r in (old, sampling_start)]
    require(receipt_clock(sampling_start['at']) < receipt_clock(sampling['at']) < receipt_clock(started['at']), 'V24 sampling chronology')
    require(sampling_start['sources'] == sampling['sources'] and sampling['before_any_label'] is True and sampling['before_any_raw_read'] is True and sampling['sampling']['outcomes_used'] is False, 'Frozen pre-label sampling')
    contract = dict(mothers=251, matched_mothers=248, controls=744, unmatched_mothers=3, seed=20260907,
                    streams=1, bit_generator='PCG64', no_reuse=True, fallback_used=False, outcomes_used=False,
                    selection_frozen_before_labels=True)
    require(all(sampling['sampling'].get(k) == v for k, v in contract.items()), 'V24 frozen sampling contract')
    receipt = frozen['input_receipt']
    require(receipt['parent_commits'] == [old['builder_commit'], sampling_start['builder_commit']] and
            receipt['parent_sources_verified'] == parent_counts and receipt['v20_failure_after_feature_freeze'] is True and
            receipt['v20_recovery_same_feature_sha'] is True and receipt['raw5_read'] is False and receipt['outcomes_read'] is False, 'Parent lineage receipt')
    for p in ('case_requests.csv.gz', 'control_requests.csv.gz', 'random_assignments.csv.gz', 'random_allocation.csv.gz'):
        require(sampling['output_hashes'][p] == config['inputs'][str(V24/p)], 'Original sampling file SHA')
    require(frozen['timestamp_preflight_before_prices'] is True and frozen['outcomes_read_or_computed'] is False and frozen['raw5_read'] is False and frozen['holdout_consumed'] is False, 'Support scope')
    clocks = [clock(r['open_time']) for r in read_csv(root/V20/'hourly_trace.csv.gz', ['open_time'])]
    require(len(clocks) == 18222 and len(set(clocks)) == len(clocks) and clocks == sorted(clocks), 'Full trace timestamp preflight')
    require(clocks[0] == clock('2022-11-30T16:00:00Z') and clocks[-1] == clock('2024-12-28T22:00:00Z'), 'Source phase')
    source = read_csv(root/V20/'hourly_trace.csv.gz', OHLC)
    requests = {p: read_csv(root/V24/(p+'_requests.csv.gz')) for p in ('case', 'control')}
    require(len(requests['case']) == 251 and len(requests['control']) == 744, 'Full original population')
    original, cm = indexed(read_csv(root/V4, ('event_id', 'signal_time', 'decision_time', 'direction', 'fold'))), indexed(requests['case'])
    require(original.keys() == cm.keys(), 'Original 251 mothers')
    for identity, r in original.items():
        request_equal(cm[identity], r)
    require(Counter(r['fold'] for r in requests['case']) == dict(zip(FOLDS, (55, 66, 55, 75))), 'Original fold counts')
    assignments, allocation = [read_csv(root/V24/p) for p in ('random_assignments.csv.gz', 'random_allocation.csv.gz')]
    require(sum(flag(r['matched_support']) for r in assignments) == 248 and len(allocation) == 744, '248 triples / 3 unmatched')
    tables = {p[:-7]: read_csv(directory/p) for p in OUTPUTS}
    receipt = verify_tables(source, tables, requests, assignments, allocation, summary)
    check_hashes()
    return dict(status='passed', summary_sha256=guards[directory/'summary.json'], auditor_commit=auditor_commit,
                auditor_sha256=auditor_sha, builder_commit=started['builder_commit'], sources_verified=source_count,
                parent_sources_verified=parent_counts, input_hashes_verified=len(config['inputs']), output_hashes_verified=len(OUTPUTS),
                **receipt, raw5_aggregation_recomputed=False, pine_live_parity_verified=False, randomization_replayed=False,
                outcomes_read_or_computed=False, inference_recomputed=False,
                limitation='Independent saved-hour formula/support audit, not raw5 aggregation, historical source authenticity, Pine/live execution, randomization replay or economic evidence.')


def write_receipt(path, result):
    with Path(path).open('x') as f:
        json.dump(result, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write('\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    if args.out is not None:
        require(not args.out.exists(), 'Refusing receipt overwrite')
    result = verify()
    if args.out is not None:
        write_receipt(args.out, result)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
