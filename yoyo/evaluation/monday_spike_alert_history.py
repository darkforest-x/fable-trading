"""How often the market-wide spike alert would have fired in the backtest universe (owner 2026-10-27).

Rule as in yoyo/monitor/spike_alert.py: per side, >= 8 distinct symbols whose first weekly Monday 1.618 touch
falls in the last 5 one-minute candles, one alert per side per 30 minutes. Touch times: fill bars of the Codex
ledger signal rows (exp-monday-limit-reclaim-20261024-v1, 60m confirmation, near target; Binance 5m, so a touch
is known only to the 5m bar). Reported for the full Binance alt universe and for symbols with a current OKX USDT
swap (okx_swaps.json of exp-monday-limit-okx-wick-check-20261026-v1), per year, with each episode's size.
"""
from __future__ import annotations
import json
import subprocess
from concurrent.futures import ProcessPoolExecutor
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation.monday_limit_reclaim_patterns import SRC

EXP = ROOT/'experiments/active/exp-monday-spike-alert-history-20261027-v1'
THRESHOLD, WINDOW_MS, COOLDOWN_MS = 8, 5*60_000, 30*60_000


def rows(symbol):
    x = pd.read_csv(SRC/symbol/'trades.csv.gz', usecols=['population', 'reclaim_minutes', 'exit_mode', 'side', 'entry_time'])
    x = x[(x.population == 'signal') & (x.reclaim_minutes == 60) & (x.exit_mode == 'near_monday_edge')]
    return x[['side', 'entry_time']].assign(symbol=symbol)


def episodes(t):
    out = []
    for side, g in t.groupby('side'):
        ms = (pd.to_datetime(g.entry_time, utc=True).astype('int64')//10**6-5*60_000).to_numpy()  # fill bar open
        sym = g.symbol.to_numpy(); o = np.argsort(ms); ms, sym = ms[o], sym[o]
        last, lo = -10**15, 0
        for i in range(len(ms)):
            while ms[lo] <= ms[i]-WINDOW_MS:
                lo += 1
            n = len(set(sym[lo:i+1]))
            if n >= THRESHOLD and ms[i]-last >= COOLDOWN_MS:
                last = ms[i]
                j = i
                while j+1 < len(ms) and ms[j+1]-ms[i] < WINDOW_MS:
                    j += 1
                out.append(dict(side='lower' if side == 1 else 'upper', ms=int(ms[i]), n_at_alert=n, n_cluster=len(set(sym[lo:j+1]))))
    return pd.DataFrame(out)


def run():
    p = 'yoyo/evaluation/monday_spike_alert_history.py'
    if subprocess.run(['git','diff','--quiet','HEAD','--',p], cwd=ROOT).returncode or \
       subprocess.run(['git','ls-files','--error-unmatch',p], cwd=ROOT, capture_output=True).returncode:
        raise RuntimeError('Commit builder first')
    syms = sorted(d.name for d in SRC.iterdir() if (d/'trades.csv.gz').exists())
    with ProcessPoolExecutor(4) as ex:
        t = pd.concat([d for d in ex.map(rows, syms, chunksize=8) if len(d)], ignore_index=True)
    listed = set(json.loads((ROOT/'experiments/active/exp-monday-limit-okx-wick-check-20261026-v1/inputs/okx_swaps.json').read_text()))
    t['okx'] = t.symbol.map(lambda s: f"{s.removesuffix('USDT')}-USDT-SWAP" in listed)
    res = dict(rule=dict(threshold=THRESHOLD, window_minutes=5, cooldown_minutes=30), touches=int(len(t)))
    for name, x in (('binance_universe', t), ('okx_listed', t[t.okx])):
        e = episodes(x)
        e['bj'] = pd.to_datetime(e.ms, unit='ms', utc=True).dt.tz_convert('Asia/Shanghai')
        res[name] = dict(alerts=int(len(e)), per_year={str(y): int(n) for y, n in e.groupby(e.bj.dt.year).size().items()},
                         side=e.side.value_counts().to_dict(),
                         largest=[dict(time=f'{r.bj:%Y-%m-%d %H:%M}', side=r.side, cluster=int(r.n_cluster)) for r in e.nlargest(12, 'n_cluster').itertuples()],
                         last_2026=[dict(time=f'{r.bj:%Y-%m-%d %H:%M}', side=r.side, cluster=int(r.n_cluster)) for r in e[e.bj.dt.year == 2026].itertuples()])
    EXP.mkdir(exist_ok=True)
    dump(EXP/'summary.json', dict(results=res, source_commit=subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip()))
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    run()
