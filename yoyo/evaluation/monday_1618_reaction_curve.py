"""Small reactions at the Monday1618 on1m bars (method9).

Owner Notion task 区间1.618测试. Full returns to the Monday edge match
driftless odds, but a level can still produce a reliable small bounce that a
scalper monetizes. From each first weekly1618 touch in the frozen Binance1m
ledger (exp-monday-1618-oos-1m: BTC/ETH/SOL/BNB/XRP/DOGE, 2022-11..2026-09),
measure P(price moves x*R back inside before the2.0 extension) for
x = 0.1/0.2/0.3, entry at the1618 fill, ordering on1m (touch minute can only
stop, same minute both = stop). Driftless odds = 0.382/(0.382+x). R per trade
= +x/0.382 or -1. Split 2024-10-01. Pass for a target: both halves above its
driftless odds, later one-sided binomial p<0.01 and later mean R>0; the three
targets are Holm-adjusted.
"""
from __future__ import annotations
import argparse
import json
import subprocess
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation import monday_1618_oos_1m as oos


EXP = ROOT/'experiments/active/exp-monday-1618-reaction-curve-20261023-v1'


def reactions(m, rows, targets, horizon_days):
    h, l = m.high.to_numpy(), m.low.to_numpy()
    out = []
    for r in rows.itertuples():
        i = m.index.searchsorted(pd.Timestamp(r.touch_minute))
        end = min(len(m), i+horizon_days*1440)
        side = int(r.side)
        sh = (l[i:end] <= r.stop) if side == 1 else (h[i:end] >= r.stop)
        s = int(np.argmax(sh)) if sh.any() else 10**9
        row = dict(symbol=r.symbol, week=r.week, side=side, touch_minute=r.touch_minute)
        for x in targets:
            tgt = r.level+side*x*r.R
            th = (h[i:end] >= tgt) if side == 1 else (l[i:end] <= tgt)
            th[0] = False
            e = int(np.argmax(th)) if th.any() else 10**9
            row[f'hit_{x}'] = bool(e < s)
            row[f'resolved_{x}'] = min(e, s) < 10**9
        out.append(row)
    return out


def stats(frame, x):
    from scipy.stats import binomtest
    n = len(frame)
    if not n:
        return dict(n=0)
    k = int(frame[f'hit_{x}'].sum()); odds = .382/(.382+x)
    r = np.where(frame[f'hit_{x}'], x/.382, -1.0)
    return dict(n=n, hit_pct=100*k/n, driftless_pct=100*odds, mean_r=float(r.mean()),
                p_one_sided=float(binomtest(k, n, odds, alternative='greater').pvalue),
                unresolved=int((~frame[f'resolved_{x}']).sum()))


def run():
    cfg = json.loads((EXP/'config.json').read_text())
    if subprocess.run(['git','diff','--quiet','HEAD','--','yoyo/evaluation/monday_1618_reaction_curve.py'], cwd=ROOT).returncode:
        raise RuntimeError('Commit builder first')
    source = ROOT/cfg['touch_ledger']
    ledger = pd.read_csv(source/'touches_1m.csv.gz'); scfg = json.loads((source/'config.json').read_text())
    rows = []
    for symbol, g in ledger.groupby('symbol'):
        m = oos.minutes(ROOT/scfg['series'][symbol])
        rows += reactions(m, g, cfg['targets_R'], cfg['horizon_days'])
    data = pd.DataFrame(rows)
    data['segment'] = (pd.to_datetime(data.touch_minute) >= pd.Timestamp(cfg['split'])).astype(int)
    data.to_csv(EXP/'reactions.csv.gz', index=False)
    table = {}
    for x in cfg['targets_R']:
        table[str(x)] = dict(earlier=stats(data[data.segment.eq(0)], x), later=stats(data[data.segment.eq(1)], x),
            all=stats(data, x), by_symbol={s: stats(g, x) for s, g in data.groupby('symbol')})
    p = np.array([table[str(x)]['later']['p_one_sided'] for x in cfg['targets_R']])
    order = np.argsort(p); adj = np.empty(len(p))
    adj[order] = np.minimum(1, np.maximum.accumulate(p[order]*(len(p)-np.arange(len(p)))))
    passed = {}
    for x, a in zip(cfg['targets_R'], adj):
        t = table[str(x)]
        passed[str(x)] = bool(t['earlier']['hit_pct'] > t['earlier']['driftless_pct'] and
            t['later']['hit_pct'] > t['later']['driftless_pct'] and a < .01 and t['later']['mean_r'] > 0)
        t['later']['holm_p'] = float(a)
    summary = dict(config=cfg, touches=len(data), table=table, passed=passed,
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
        training_eligible=False, production_eligible=False)
    dump(EXP/'summary.json', summary)
    print(json.dumps({x: {k: {kk: (round(vv, 3) if isinstance(vv, float) else vv) for kk, vv in t[k].items()}
        for k in ('earlier', 'later', 'all')} for x, t in table.items()} | dict(passed=passed), ensure_ascii=False, indent=1))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--config', type=Path, default=EXP/'config.json')
    args = parser.parse_args(); EXP = args.config.resolve().parent; run()
