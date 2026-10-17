"""Independently recompute every rank and exclusion statistic using only stdlib.

Read stream-level ledgers rather than the builder's combined files. Verify
closed eligibility, costs, uniqueness, chronological boundaries, symbol totals,
all eight bottom-twenty lists and matched denominators. This audits arithmetic
and partitioning, not native TradingView parity or market executability.
"""
import csv
import gzip
import json
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent / 'results_v1'


def stamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def read(path):
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt', encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def stats(rows, controls):
    values = [float(r['net_r']) for r in rows]
    n = len(values)
    positive = math.fsum(x for x in values if x > 0)
    negative = -math.fsum(x for x in values if x < 0)
    paired = [r for r in rows if r['trade_key'] in controls]
    target = math.fsum(float(r['net_r']) for r in paired)
    random = math.fsum(float(controls[r['trade_key']]['control_net_r']) for r in paired)
    m = len(paired)
    return dict(trades=n, wins=sum(x > 0 for x in values), win_rate=sum(x > 0 for x in values)/n,
        sum_net_r=math.fsum(values), mean_net_r=math.fsum(values)/n,
        pf_r=positive/negative if negative else ('inf' if positive else None),
        positive_net_r=positive, negative_net_r_abs=negative, matched=m, matched_coverage=m/n,
        matched_target_sum_net_r=target, matched_random_sum_net_r=random,
        matched_target_mean_net_r=target/m if m else None, random_mean_net_r=random/m if m else None,
        matched_excess_mean_net_r=(target-random)/m if m else None)


def rank(rows):
    grouped = defaultdict(list)
    for r in rows:
        grouped[r['symbol']].append(r)
    return sorted(grouped, key=lambda s:(math.fsum(float(r['net_r']) for r in grouped[s]), s))[:20]


def main():
    result = json.loads((ROOT/'result.json').read_text())
    receipt = json.loads((ROOT/'receipt.json').read_text())
    assert receipt['complete'] and not receipt['errors'] and not receipt['subset']
    assert len(receipt['receipts']) == 638
    rows, controls, keys = [], {}, set()
    for folder in sorted((ROOT/'streams').iterdir()):
        for r in read(folder/'trades.csv.gz'):
            assert r['trade_key'] not in keys
            keys.add(r['trade_key'])
            if r['status'] != 'closed':
                continue
            r['entry'] = stamp(r['entry_time'])
            r['exit'] = stamp(r['exit_time'])
            assert stamp(result['config']['analysis_start']) <= r['entry'] < stamp(result['config']['end'])
            assert r['entry'] <= r['exit']
            assert math.isclose(float(r['gross_return'])-float(r['net_return']), .002, abs_tol=1e-12)
            assert math.isclose(float(r['net_r']),float(r['net_return'])*float(r['entry_price'])/float(r['initial_risk']),abs_tol=1e-10)
            rows.append(r)
        for c in read(folder/'controls.csv.gz'):
            eligible = c['control_status']=='closed' and stamp(c['control_anchor_close']).weekday()!=6
            assert eligible == (c['eligible']=='True')
            if not eligible:
                continue
            assert c['trade_key'] not in controls
            c['entry'] = stamp(c['control_entry_time'])
            controls[c['trade_key']] = c
    assert len(keys)-len(rows) == result['censored']
    checks = 0
    def equal(actual, expected):
        nonlocal checks
        for key, value in expected.items():
            if isinstance(value,(int,float)):
                assert math.isclose(actual[key],value,rel_tol=0,abs_tol=1e-9),(key,actual[key],value)
            else:
                assert actual[key] == value
            checks += 1
    cut = stamp(result['config']['split'])
    for g in result['groups']:
        part = [r for r in rows if int(r['timeframe_min'])==g['timeframe_min'] and int(r['side'])==g['side']]
        early = [r for r in part if r['entry']<cut and r['exit']<cut]
        later = [r for r in part if r['entry']>=cut]
        assert len(part)-len(early)-len(later) == g['chronology']['cross_boundary_excluded']
        for selected, population, expected, ranks, paired in [
            (part,part,g['full_history'],g['full_history_bottom20'],controls),
            (early,later,g['chronology']['later'],g['chronology']['early_bottom20'],
             {k:c for k,c in controls.items() if c['entry']>=cut})]:
            worst = rank(selected)
            assert worst == [r['symbol'] for r in ranks]
            for state, sub in [('before',population),('removed',[r for r in population if r['symbol'] in worst]),
                    ('after',[r for r in population if r['symbol'] not in worst])]:
                equal(expected[state],stats(sub,paired))
    ranked = read(ROOT/'symbol_rankings.csv')
    for r in ranked:
        part = [x for x in rows if x['symbol']==r['symbol'] and x['side']==r['side'] and x['timeframe_min']==r['timeframe_min']]
        for k,v in stats(part,controls).items():
            if k not in r:
                continue
            if v == 'inf':
                assert r[k]=='inf'
            elif v is None:
                assert r[k]==''
            else:
                assert math.isclose(float(r[k]),v,rel_tol=0,abs_tol=1e-9),(r['symbol'],k)
            checks += 1
    # Independent occupancy sensitivity: filtering one side must not silently
    # hide newly available confirmations on the other side of the same stream.
    lists = {(g['timeframe_min'],g['side']):{r['symbol'] for r in g['full_history_bottom20']}
             for g in result['groups']}
    serial_rows = []
    for folder in sorted((ROOT/'streams').iterdir()):
        candidates = [r for r in read(folder/'candidates.csv.gz') if r['exit_i']]
        candidates.sort(key=lambda r:(int(r['signal_i']),int(r['anchor_i'])))
        flat = -1
        for r in candidates:
            if r['symbol'] in lists[int(r['timeframe_min']),int(r['side'])] or int(r['signal_i'])<flat:
                continue
            flat = math.inf if r['status']=='censored_boundary' else int(float(r['exit_i']))
            if r['status']=='closed' and stamp(r['entry_time'])>=stamp(result['config']['analysis_start']):
                serial_rows.append(r)
    filtered = [r for r in rows if r['symbol'] not in lists[int(r['timeframe_min']),int(r['side'])]]
    old_keys, new_keys = {r['trade_key'] for r in filtered}, {r['trade_key'] for r in serial_rows}
    sensitivity = dict(filtered_original_closed=len(filtered), replayed_closed=len(serial_rows),
        added_keys=sorted(new_keys-old_keys), displaced_keys=sorted(old_keys-new_keys),
        replayed_sum_net_r=math.fsum(float(r['net_r']) for r in serial_rows),
        filtered_sum_net_r=math.fsum(float(r['net_r']) for r in filtered))
    out = dict(status='passed', streams=1276, closed=len(rows), symbol_side_rows=len(ranked),
        bottom20_lists=8, comparison_sets=24, scalar_checks=checks,
        full_history_serial_sensitivity=sensitivity,
        methods='stdlib csv/gzip/math from per-stream ledgers; no analysis module imports')
    (ROOT/'independent_verification.json').write_text(json.dumps(out,indent=2))
    print(json.dumps(out))


if __name__=='__main__':
    main()
