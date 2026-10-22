"""Out-of-sample1m check of the Monday1618 limit trade on Binance majors.

Owner Notion task 区间1.618测试. exp-monday-1618-validity (OKX swaps, 2025-06
to2026-08) found the universe at random-walk validity (39.1 vs38.2percent)
but pre-declared focus BTC at49percent (n=53). Hypothesis frozen before this
run: BTC first weekly1618 touch, limit at the level, SL2.0 extension, TP
Monday near edge holds more often than the driftless38.2percent and earns
mean R>0. Judged on Binance UM BTCUSDT1m 2022-11-01..2025-05-31, entirely
before the discovery window; 2026-08-14..2026-09-30 is reported as a second
later slice. ETH/SOL/BNB/XRP/DOGE are descriptive, not tested hypotheses.

Touches and features come from15m bars aggregated from the same1m file with
monday_1618_validity.touches (bar-open index, NY Monday). Resolution reruns on
1m from the first minute that trades the level: stop counts from that minute,
target from the next; a minute with both = stop. 14-day horizon mark.
"""
from __future__ import annotations
import argparse
import json
import subprocess
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation import monday_1618_validity as v

EXP = ROOT/'experiments/active/exp-monday-1618-oos-1m-20261023-v1'


def minutes(path):
    f = pd.read_csv(path, usecols=['ts','open','high','low','close','volume'])
    f.index = pd.to_datetime(f.ts, unit='ms', utc=True)
    return f[['open','high','low','close','volume']].astype(float).sort_index()


def quarter(m):
    g = m.resample('15min', closed='left', label='left')
    q = g.agg(dict(open='first', high='max', low='min', close='last', volume='sum'))
    return q[g.close.count().eq(15)]


def refine(m, row, horizon_days):
    """Exact-minute resolution of one15m touch. Returns (valid, r, minute)."""
    t0 = pd.Timestamp(row['touch_time'])
    i0 = m.index.searchsorted(t0)
    window = m.iloc[i0:i0+15]
    side = row['side']; level, stop, edge = row['level'], row['stop'], row['edge']
    hit = (window.low <= level) if side == 1 else (window.high >= level)
    if not hit.any():
        return None
    i = i0+int(np.argmax(hit.to_numpy()))
    o = m.open.iloc[i]
    fill = o if side*(level-o) > 0 else level
    end = min(len(m), i+horizon_days*1440)
    h, l = m.high.to_numpy()[i:end], m.low.to_numpy()[i:end]
    sh = (l <= stop) if side == 1 else (h >= stop)
    eh = (h >= edge) if side == 1 else (l <= edge)
    eh[0] = False
    s = int(np.argmax(sh)) if sh.any() else 10**9
    e = int(np.argmax(eh)) if eh.any() else 10**9
    risk = side*(fill-stop)
    if e < s:
        return True, side*(edge-fill)/risk, str(m.index[i])
    if s < 10**9:
        return False, -1.0, str(m.index[i])
    return False, side*(m.close.iloc[end-1]-fill)/risk, str(m.index[i])


def stats(frame, seed):
    n = len(frame)
    if not n:
        return dict(n=0)
    from scipy.stats import binomtest
    k = int(frame.valid.sum())
    rng = np.random.default_rng(seed)
    w = frame.groupby('week').r.sum().to_numpy()
    flips = rng.choice([-1, 1], size=(20000, len(w)))
    p_r = float((((flips*w).sum(1) >= w.sum()).sum()+1)/20001)
    return dict(n=n, valid=k, valid_pct=100*k/n, mean_r=float(frame.r.mean()), sum_r=float(frame.r.sum()),
        p_valid_vs_382=float(binomtest(k, n, 1/2.618, alternative='greater').pvalue), p_mean_r_week_flip=p_r,
        long=dict(n=int(frame.side.eq(1).sum()), valid_pct=float(100*frame[frame.side.eq(1)].valid.mean()) if frame.side.eq(1).any() else None),
        short=dict(n=int(frame.side.eq(-1).sum()), valid_pct=float(100*frame[frame.side.eq(-1)].valid.mean()) if frame.side.eq(-1).any() else None))


def run():
    cfg = json.loads((EXP/'config.json').read_text())
    if subprocess.run(['git','diff','--quiet','HEAD','--','yoyo/evaluation/monday_1618_oos_1m.py'], cwd=ROOT).returncode:
        raise RuntimeError('Commit builder first')
    rows = []
    for symbol, path in cfg['series'].items():
        m = minutes(path); q = quarter(m)
        for r in v.touches(q, symbol, cfg):
            got = refine(m, r, cfg['horizon_days'])
            if got is None:
                continue
            r.update(valid_15m=r['valid'], r_15m=r['r'], valid=got[0], r=got[1], touch_minute=got[2])
            rows.append(r)
    data = pd.DataFrame(rows)
    t = pd.to_datetime(data.touch_time)
    data['period'] = np.select([t < pd.Timestamp(cfg['oos_pre_end']), t < pd.Timestamp(cfg['discovery_end'])],
                               ['oos_pre', 'discovery'], 'oos_post')
    data.to_csv(EXP/'touches_1m.csv.gz', index=False)
    table = {s: {p: stats(g[g.period.eq(p)], cfg['seed']) for p in ('oos_pre', 'discovery', 'oos_post')}
             for s, g in data.groupby('symbol')}
    primary = table['BTC']['oos_pre']
    passed = bool(primary['p_valid_vs_382'] < .01 and primary['mean_r'] > 0)
    summary = dict(config=cfg, primary=dict(symbol='BTC', period='oos_pre', passed=passed, **primary),
        table=table, ordering_changes=int((data.valid != data.valid_15m).sum()),
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
        training_eligible=False, production_eligible=False)
    dump(EXP/'summary.json', summary)
    print(json.dumps(dict(primary=summary['primary'], table={s: {p: {k: x.get(k) for k in ('n','valid_pct','mean_r','p_valid_vs_382')}
        for p, x in d.items()} for s, d in table.items()}), ensure_ascii=False, indent=1))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--config', type=Path, default=EXP/'config.json')
    args = parser.parse_args(); EXP = args.config.resolve().parent; run()
