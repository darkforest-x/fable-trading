"""Trade quality by market-wide spike size at fill time, to set the spike alert threshold (owner 2026-10-27).

Owner: "应该门槛设置多少 警报后 后面的单子准确率越高". For every trade, the cluster size the alert would have seen at
its fill: distinct symbols whose first weekly 1.618 touch on the same side falls in the 5 minutes up to the fill bar
(5m bar resolution, so touches in the same bar count together). Touches: Codex ledger signal fill bars
(exp-monday-limit-reclaim-20261024-v1). Trades (worse path, existing NautilusTrader fills): v2_be17 and v3_be17 of
exp-monday-limit-time-news-20261026-v1 and spike-HW v1 (exp-monday-limit-spike-hw-20261026-v1). Two universes: all
Binance alts, and only currently OKX-listed symbols for both the count and the trades (what the live OKX alert sees).
Per family and cluster bucket: trades, distinct spike episodes (side x fill bar), win rate, mean net, and per
threshold the trades at or above it versus below it. Episodes, not trades, are the independent unit.
"""
from __future__ import annotations
import json
import subprocess
from concurrent.futures import ProcessPoolExecutor
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation.monday_limit_reclaim_patterns import SRC
from yoyo.evaluation.monday_spike_alert_history import rows

EXP = ROOT/'experiments/active/exp-monday-spike-alert-quality-20261027-v1'
A = ROOT/'experiments/active'
BUCKETS = [(1, 1), (2, 4), (5, 7), (8, 14), (15, 24), (25, 49), (50, 10**6)]
THRESHOLDS = [8, 15, 25, 40]


def counts(touch, trades):
    """Distinct same-side symbols touching in (fill - 5 min, fill] for each trade (bar open times in ms)."""
    out = np.zeros(len(trades), int)
    for side, g in touch.groupby('side'):
        ms = g.ms.to_numpy(); sym = g.symbol.to_numpy(); o = np.argsort(ms); ms, sym = ms[o], sym[o]
        sel = (trades.side == side).to_numpy()
        for i in np.flatnonzero(sel):
            t = trades.ms.iat[i]
            lo, hi = np.searchsorted(ms, t-5*60_000, side='right'), np.searchsorted(ms, t, side='right')
            out[i] = len(set(sym[lo:hi]))
    return out


def stats(x):
    if not len(x):
        return dict(n=0)
    return dict(n=int(len(x)), episodes=int(x.episode.nunique()), win_pct=float(100*(x.net > 1).mean()), mean_bp=float(x.net.mean()),
                episode_win_pct=float(100*x.groupby('episode').net.apply(lambda v: (v > 1).mean()).mean()))


def run():
    p = 'yoyo/evaluation/monday_spike_alert_quality.py'
    if subprocess.run(['git','diff','--quiet','HEAD','--',p], cwd=ROOT).returncode or \
       subprocess.run(['git','ls-files','--error-unmatch',p], cwd=ROOT, capture_output=True).returncode:
        raise RuntimeError('Commit builder first')
    syms = sorted(d.name for d in SRC.iterdir() if (d/'trades.csv.gz').exists())
    with ProcessPoolExecutor(4) as ex:
        touch = pd.concat([d for d in ex.map(rows, syms, chunksize=8) if len(d)], ignore_index=True)
    touch['ms'] = pd.to_datetime(touch.entry_time, utc=True).astype('int64')//10**6-5*60_000
    listed = set(json.loads((A/'exp-monday-limit-okx-wick-check-20261026-v1/inputs/okx_swaps.json').read_text()))
    on_okx = lambda s: f"{s.removesuffix('USDT')}-USDT-SWAP" in listed
    touch['okx'] = touch.symbol.map(on_okx)
    tn = pd.read_csv(A/'exp-monday-limit-time-news-20261026-v1/trades.csv.gz', parse_dates=['t'])
    fam = {'v2': tn[tn.variant == 'v2_be17'], 'v3': tn[tn.variant == 'v3_be17']}
    hw = pd.read_csv(A/'exp-monday-limit-spike-hw-20261026-v1/trades.csv', parse_dates=['entry_t']).rename(columns={'entry_t': 't'})
    fam['spike_hw'] = hw
    res = dict(touches=int(len(touch)))
    for uni in ('binance', 'okx'):
        tt = touch if uni == 'binance' else touch[touch.okx]
        res[uni] = {}
        for name, x in fam.items():
            x = x.copy()
            x['symbol'] = x.event.str.split(':').str[0]
            if uni == 'okx':
                x = x[x.symbol.map(on_okx)]
            x['ms'] = x.t.astype('int64')//10**6-5*60_000
            x = x.reset_index(drop=True)
            x['cluster'] = counts(tt, x)
            x['episode'] = x.side.astype(str)+':'+x.ms.astype(str)
            r = dict(all=stats(x))
            r['buckets'] = {f'{lo}-{hi if hi < 10**6 else "+"}': stats(x[(x.cluster >= lo) & (x.cluster <= hi)]) for lo, hi in BUCKETS}
            r['thresholds'] = {str(T): dict(at_or_above=stats(x[x.cluster >= T]), below=stats(x[x.cluster < T])) for T in THRESHOLDS}
            r['late'] = {str(T): dict(at_or_above=stats(x[(x.cluster >= T) & (x.t >= pd.Timestamp('2025-01-01', tz='UTC'))]),
                                      below=stats(x[(x.cluster < T) & (x.t >= pd.Timestamp('2025-01-01', tz='UTC'))])) for T in THRESHOLDS}
            res[uni][name] = r
    EXP.mkdir(exist_ok=True)
    dump(EXP/'summary.json', dict(results=res, source_commit=subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip(),
                                 training_eligible=False, production_eligible=False))
    f = lambda s: f"n{s['n']:5d} ep{s.get('episodes', 0):4d} win {s.get('win_pct', 0):5.1f} mean {s.get('mean_bp', 0):6.0f}" if s.get('n') else '-'
    for uni in ('binance', 'okx'):
        for name in fam:
            r = res[uni][name]
            print(f'== {uni} {name} all', f(r['all']))
            for k, v in r['buckets'].items():
                print(f'   cluster {k:7s}', f(v))
            for k, v in r['thresholds'].items():
                print(f'   >= {k:3s}', f(v['at_or_above']), '| <', f(v['below']), '|| 2025+ >=', f(r['late'][k]['at_or_above']), '| <', f(r['late'][k]['below']))


if __name__ == '__main__':
    run()
