"""Descriptive entry-hour profile of the frozen Task62 V3 trade ledgers.

Source: exp-eth5m-stoch-target-be-20261019-v3; owner asked on 2026-10-20
which time of day has the highest win rate. Only entry_time determines the
Asia/Shanghai hour; exit outcomes are labels, never selection features.
All original policies, costs, splits and censoring are reused unchanged.
The primary policy is the previous full-history net-points winner, fixed
before this analysis. Hour rankings are descriptive, not forward evidence.
"""
import argparse
import csv
import gzip
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
import subprocess
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

EXP = Path('experiments/active/exp-eth5m-stoch-target-be-20261019-v3')
PRIMARY = 'tp20_price_5r'
TOL = 1e-8


def measures(rows):
    """Closed-trade denominators include stops and break-even outcomes."""
    z = rows[rows.closed]
    return dict(orders=len(rows), closed=len(z), censored=len(rows)-len(z),
        net_wins=int(z.net_points.gt(TOL).sum()),
        win_rate=float(z.net_points.gt(TOL).mean()) if len(z) else None,
        target_hits=int(z.target_hit.sum()),
        target_rate=float(z.target_hit.mean()) if len(z) else None,
        protected_stops=int(z.protected_stop.sum()),
        protected_net_wins=int((z.protected_stop & z.net_points.gt(TOL)).sum()),
        gross_points=float(z.gross_points.sum()), net_points=float(z.net_points.sum()),
        net_r=float(z.net_r.sum()))


def load(name):
    d = pd.read_csv(EXP/'results'/f'{name}_trades.csv.gz')
    assert not d.duplicated(['pair_id', 'policy']).any()
    stamp = pd.to_datetime(d.entry_time, utc=True).dt.tz_convert('Asia/Shanghai')
    assert (stamp == pd.to_datetime(d.entry_local, utc=True)).all()
    d['hour'] = stamp.dt.hour
    d['local_year'] = stamp.dt.year
    assert (d.closed == d.reason.ne('boundary')).all()
    return d


def subsets(frame):
    yield 'all', frame
    for seg, z in frame.groupby('segment'):
        yield seg, z
    for year, z in frame.groupby('local_year'):
        yield str(year), z


def audit_hourly(hourly):
    """Independent stdlib CSV/datetime reducer, not the pandas grouping path."""
    counts = defaultdict(lambda: [0, 0, 0, 0, 0.0])
    with gzip.open(EXP/'results'/'signal_trades.csv.gz', 'rt') as f:
        for r in csv.DictReader(f):
            h = datetime.fromisoformat(r['entry_time']).astimezone(ZoneInfo('Asia/Shanghai')).hour
            z = counts[(r['policy'], h)]
            z[0] += 1
            if r['closed'] == 'True':
                z[1] += 1
                z[2] += float(r['net_points']) > TOL
                z[3] += r['target_hit'] == 'True'
                z[4] += float(r['net_points'])
    for r in hourly[hourly.period.eq('all')].itertuples():
        z = counts[(r.policy, r.hour_start)]
        assert z[:4] == [r.orders, r.closed, r.net_wins, r.target_hits]
        assert abs(z[4]-r.net_points) < 1e-7
    return len(counts)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', default='entry_hours_20261020')
    args = parser.parse_args()
    out = EXP/args.out
    out.mkdir(exist_ok=False)
    signal, control = load('signal'), load('random')
    paired = signal.merge(control, on=['pair_id', 'policy'], suffixes=('', '_random'),
                          validate='one_to_one')
    assert (paired.segment == paired.segment_random).all()
    assert (paired.week == paired.week_random).all()
    assert (paired.side == paired.side_random).all()
    paired = paired[paired.closed & paired.closed_random].copy()
    tables = {}
    for width in (1, 4):
        records = []
        for period, s in subsets(signal):
            if period == 'all':
                q = paired
            elif period.isdigit():
                q = paired[paired.local_year.eq(int(period))]
            else:
                q = paired[paired.segment.eq(period)]
            for (policy, hour), a in s.groupby(['policy', (s.hour//width)*width]):
                b = q[q.policy.eq(policy) & ((q.hour//width)*width).eq(hour)]
                same = b[((b.hour_random//width)*width).eq(hour)]
                records.append(dict(period=period, policy=policy, hour_start=int(hour),
                    hour_end=int(hour)+width, **measures(a),
                    paired_n=len(b), paired_signal_wins=int(b.net_points.gt(TOL).sum()),
                    paired_random_wins=int(b.net_points_random.gt(TOL).sum()),
                    paired_random_win_rate=float(b.net_points_random.gt(TOL).mean()) if len(b) else None,
                    paired_signal_net_points=float(b.net_points.sum()),
                    paired_random_net_points=float(b.net_points_random.sum()),
                    paired_excess_points=float((b.net_points-b.net_points_random).sum()),
                    same_clock_pairs=len(same),
                    same_clock_signal_wins=int(same.net_points.gt(TOL).sum()),
                    same_clock_random_wins=int(same.net_points_random.gt(TOL).sum())))
        tables[width] = pd.DataFrame(records)
        tables[width].to_csv(out/f'entry_{width}h.csv', index=False)
    prior = pd.read_csv(EXP/'results'/'summary.csv')
    for r in prior.itertuples():
        a = tables[1][tables[1].period.eq(r.segment) & tables[1].policy.eq(r.policy)]
        assert a.orders.sum() == r.orders and a.closed.sum() == r.closed
        assert a.target_hits.sum() == r.target_hits
        assert abs(a.net_points.sum()-r.net_points) < 1e-7
        assert abs(a.net_wins.sum()/a.closed.sum()-r.win_rate) < 1e-12
    checks = audit_hourly(tables[1])
    # Validate the primary policy's 2024-selected hour on the frozen later segment.
    primary = tables[1][tables[1].policy.eq(PRIMARY)]
    dev = primary[primary.period.eq('development')].sort_values(
        ['win_rate', 'closed', 'hour_start'], ascending=[False, False, True]).iloc[0]
    selected = primary[primary.hour_start.eq(dev.hour_start)]
    selected.to_csv(out/'development_selected_hour.csv', index=False)
    # Compare grouping by the signal bar's open time, ten minutes before entry.
    s = signal[signal.policy.eq(PRIMARY)].copy()
    s['signal_hour'] = (pd.to_datetime(s.entry_time, utc=True)-pd.Timedelta(minutes=10)).dt.tz_convert('Asia/Shanghai').dt.hour
    signal_clock = pd.DataFrame([dict(signal_hour=int(h), **measures(g)) for h, g in s.groupby('signal_hour')])
    signal_clock.to_csv(out/'primary_signal_hour_sensitivity.csv', index=False)
    receipt = dict(source_experiment=EXP.name, primary_policy=PRIMARY,
        builder_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        timezone='Asia/Shanghai', grouping='Actual entry time; fixed half-open 1h and 4h bins',
        observed_entry_start=signal.entry_local.min(), observed_entry_end=signal.entry_local.max(),
        source_signals=int(signal.pair_id.nunique()), policies=int(signal.policy.nunique()),
        definition='Net win: closed and net_points > 1e-8; all closed BE/stop trades stay in denominator; target_hit is separate',
        controls='Original same-symbol/side/UTC-week/volatility-bucket matches, grouped by SIGNAL entry clock. Control clock may differ; same-clock subset counts supplied. This is not a clock-conditioned causal null.',
        checks=dict(independent_policy_hour_groups=checks, original_summary_groups=len(prior)),
        development_selected_hour=int(dev.hour_start),
        limitations=['All-history rankings are hindsight across 24 hours; yearly stability is descriptive, not untouched or forward validation.',
            'Fee-covering BE tick rounding can create tiny positive outcomes counted as net wins; target rates and protected wins are reported separately.',
            'Independent overlapping trades are not account returns; 20bp cost remains frozen, no funding or extra slippage.',
            'AUC/top-decile metrics do not apply to this descriptive clock grouping; no classifier is trained.'],
        reproduce=f'.venv/bin/python -m yoyo.evaluation.eth_stoch_intraday --out {args.out}_reproduction',
        training_eligible=False, production_eligible=False)
    (out/'receipt.json').write_text(json.dumps(receipt, indent=2, ensure_ascii=False)+'\n')
    for width in (1, 4):
        a = tables[width]
        for policy in (PRIMARY, 'tp10_none', 'tp20_none'):
            z = a[a.policy.eq(policy) & a.period.eq('all')].sort_values('win_rate', ascending=False).head(5)
            print(width, policy, z[['hour_start','hour_end','closed','net_wins','win_rate','target_hits','net_points','paired_n','paired_random_win_rate']].to_string(index=False))
    print('Primary yearly/segment leaders')
    print(primary.sort_values('win_rate', ascending=False).groupby('period').head(1)[
        ['period','hour_start','closed','net_wins','win_rate','net_points']].to_string(index=False))
    print('Independent hourly groups audited:', checks)


if __name__ == '__main__':
    main()
