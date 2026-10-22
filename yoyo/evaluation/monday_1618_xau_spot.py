"""XAUUSD spot2011-2026: does gold's Monday1618 hold or break (method10)?

Owner Notion task 区间1.618测试. The owner trades XAUUSDT.P 1618 reversals;
OKX perps only cover2025-06 onward (29percent validity, n=48). HistData M1
spot gives15 years. Timestamps are New York local wall time (DST-aware,
verified: summer Sunday18:01 -> 22:00 UTC, daily break17-18 NY). Monday =
NY Monday bars (92 x15m because of the break; >=80 required). Touches and
features via monday_1618_validity.touches on15m bars aggregated here from
M1; labels re-resolved on M1 from the first minute trading the level (stop
from that minute, Monday edge from the next; same minute both = stop).
Two pre-registered directions, Holm over both:
  hold  validity >38.2percent (limit reversal, SL2.0, TP Monday edge)
  break validity <38.2percent (mirror trade: 2.0 before the Monday edge)
Pass for a direction: both 2011-2018 and 2019-2026 on that side, later
one-sided Holm p<0.01, later mean R of that direction's trade >0. fee0.
The touch minute can only stop the hold trade, which slightly favours break.
"""
from __future__ import annotations
import argparse
import glob
import io
import json
import subprocess
import zipfile
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation import monday_1618_validity as v
from yoyo.evaluation import monday_1618_oos_1m as oos

EXP = ROOT/'experiments/active/exp-monday-1618-xau-spot-20261023-v1'


def minutes(pattern):
    parts = []
    for path in sorted(glob.glob(pattern)):
        with zipfile.ZipFile(path) as z:
            name = next(n for n in z.namelist() if n.endswith('.csv'))
            raw = z.read(name).decode()
        parts.append(pd.read_csv(io.StringIO(raw), sep=';', header=None,
                                 names=['t', 'open', 'high', 'low', 'close', 'volume']))
    m = pd.concat(parts, ignore_index=True)
    local = pd.to_datetime(m.t, format='%Y%m%d %H%M%S')
    # Wall clock bar open in New York; the weekly DST jump falls while closed.
    idx = local.dt.tz_localize(v.TZ, ambiguous='NaT', nonexistent='NaT').dt.tz_convert('UTC')
    m = m.assign(ts=idx).dropna(subset=['ts']).set_index('ts')[['open', 'high', 'low', 'close']].astype(float)
    m = m[~m.index.duplicated()].sort_index()
    m['volume'] = np.nan
    return m


def quarter(m):
    g = m.resample('15min', closed='left', label='left')
    q = g.agg(dict(open='first', high='max', low='min', close='last'))
    q = q[g.close.count().gt(0)]
    q['volume'] = np.nan
    return q


def stats(frame, direction, seed):
    from scipy.stats import binomtest
    n = len(frame)
    if not n:
        return dict(n=0)
    k = int(frame.valid.sum()); odds = 1/2.618
    alt = 'greater' if direction == 'hold' else 'less'
    # Break trade mirrors the barriers: risk0.618R to the edge, reward0.382R
    # to2.0, so +0.618 R when2.0 comes first and -1 when the edge does.
    r = frame.r if direction == 'hold' else np.where(frame.valid, -1.0, np.where(frame.resolved, .382/.618, -frame.r*.382/.618))
    return dict(n=n, valid_pct=100*k/n, mean_r=float(np.mean(r)),
                p_one_sided=float(binomtest(k, n, odds, alternative=alt).pvalue))


def run():
    cfg = json.loads((EXP/'config.json').read_text())
    if subprocess.run(['git','diff','--quiet','HEAD','--','yoyo/evaluation/monday_1618_xau_spot.py',
                       'yoyo/evaluation/monday_1618_validity.py'], cwd=ROOT).returncode:
        raise RuntimeError('Commit builder first')
    m = minutes(str(ROOT/cfg['pattern']))
    q = quarter(m)
    rows = []
    for r in v.touches(q, 'XAU', cfg):
        got = oos.refine(m, r, cfg['horizon_days'])
        if got is None:
            continue
        r.update(valid_15m=r['valid'], r_15m=r['r'], valid=got[0], r=got[1], touch_minute=got[2])
        rows.append(r)
    data = pd.DataFrame(rows)
    data['resolved'] = data.r.abs().round(6).isin([1.0, round(.618/.382, 6)]) | data.valid
    data['segment'] = (pd.to_datetime(data.touch_time) >= pd.Timestamp(cfg['split'])).astype(int)
    data.to_csv(EXP/'touches_xau.csv.gz', index=False)
    table = {d: dict(earlier=stats(data[data.segment.eq(0)], d, cfg['seed']), later=stats(data[data.segment.eq(1)], d, cfg['seed']),
                     all=stats(data, d, cfg['seed'])) for d in ('hold', 'break')}
    p = np.array([table[d]['later']['p_one_sided'] for d in ('hold', 'break')])
    adj = np.minimum(1, np.sort(p)*np.array([2, 1]))
    adj = np.maximum.accumulate(adj)[np.argsort(np.argsort(p))]
    passed = {}
    for d, a in zip(('hold', 'break'), adj):
        t = table[d]; sign = 1 if d == 'hold' else -1
        passed[d] = bool(sign*(t['earlier']['valid_pct']-100/2.618) > 0 and sign*(t['later']['valid_pct']-100/2.618) > 0
                         and a < .01 and t['later']['mean_r'] > 0)
        t['later']['holm_p'] = float(a)
    by_year = {str(y): dict(n=len(g), valid_pct=float(100*g.valid.mean())) for y, g in data.groupby(pd.to_datetime(data.touch_time).dt.year)}
    summary = dict(config=cfg, minutes=len(m), touches=len(data), table=table, passed=passed, by_year=by_year,
        ordering_changes=int((data.valid != data.valid_15m).sum()),
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
        training_eligible=False, production_eligible=False)
    dump(EXP/'summary.json', summary)
    print(json.dumps(dict(table=table, passed=passed, by_year=by_year, touches=len(data)), ensure_ascii=False, indent=1))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--config', type=Path, default=EXP/'config.json')
    args = parser.parse_args(); EXP = args.config.resolve().parent; run()
