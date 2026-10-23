"""Limit at the Monday1618, hold30 minutes, market exit (owner2026-10-23).

Owner: "测试一下直接挂单，然后持有30分钟退出". Rule frozen in config before
any outcome is read: resting limit at the first weekly-side1618 (short above
the Monday NY high, long below the low; touches from monday_1618_validity).
Fill in the first1m minute trading the level (gap through the level fills at
that minute's open). Primary: no stop, market exit at the open of the minute
30 minutes after the fill minute. Variant: same, but a2.0-extension stop
inside those30 minutes exits at the stop (the fill minute counts, stop first).
Real fees (owner2026-10-23, OKX Lv1): maker0.02% entry, taker0.05% exit or
stop. Null: 10 random minutes per touch in the same NY week (Tue..next Mon),
same side, entry at that minute's open, same exit rule and fees.
1m datasets: OKX BTC 2024-10..2026-10, Binance UM BTC/ETH/SOL/BNB/XRP/DOGE
2022-11..2026-09. 15m approximations (exit at the close of the bar after the
touch bar, i.e. 15-30 minutes, maker+taker fee): OKX177 swaps and Binance
alts. Pass: 1m mean net return >0 with week-cluster bootstrap95 lower bound
>0 in BOTH 1m datasets, and positive in both chronological halves.
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
from yoyo.evaluation import monday_1618_oos_1m as oos
from yoyo.evaluation.monday_1618_flow import klines

EXP = ROOT/'experiments/active/exp-monday-1618-hold30-20261023-v1'
A = ROOT/'experiments/active'


def okx_btc_minutes():
    raw = pd.read_csv(A/'exp-btc-monday-fib1618-okx-20261022-v1/candles.csv.gz')
    m = raw.set_index(pd.to_datetime(raw.ts, unit='ms', utc=True))[['open', 'high', 'low', 'close']].astype(float)
    m['volume'] = 0.
    return m[~m.index.duplicated()].sort_index()


def trades_1m(m, symbol, cfg, rng):
    """Exact-minute fills and30-minute exits for every first weekly1618 touch."""
    q = oos.quarter(m)
    o, h, l = m.open.to_numpy(), m.high.to_numpy(), m.low.to_numpy()
    idx = m.index; hold = cfg['hold_minutes']; fee = cfg['maker_fee']+cfg['taker_fee']
    ny = idx.tz_convert(v.TZ)
    week_key = (ny.normalize()-pd.to_timedelta(ny.dayofweek, unit='D')).tz_localize(None).asi8
    out, null = [], []
    for r in v.touches(q, symbol, cfg):
        i0 = idx.searchsorted(pd.Timestamp(r['touch_time']))
        side, level, stop = r['side'], r['level'], r['stop']
        hit = (l[i0:i0+15] <= level) if side == 1 else (h[i0:i0+15] >= level)
        if not hit.any():
            continue
        i = i0+int(np.argmax(hit)); j = i+hold
        if j >= len(m) or (idx[j]-idx[i]) != pd.Timedelta(minutes=hold):
            continue
        fill = o[i] if side*(level-o[i]) > 0 else level
        exit_px = o[j]
        seg_stop = (l[i:j] <= stop) if side == 1 else (h[i:j] >= stop)
        stop_px = exit_px if not seg_stop.any() else stop
        out.append(dict(symbol=symbol, week=r['week'], side=side, time=str(idx[i]),
            gross_bp=side*(exit_px-fill)/fill*1e4, gross_stop_bp=side*(stop_px-fill)/fill*1e4,
            stopped=bool(seg_stop.any()), fee_bp=fee*1e4))
        pool = np.flatnonzero(week_key == week_key[i])
        pool = pool[(pool+hold < len(m)) & (ny[pool].dayofweek >= 1)]
        for k in rng.choice(pool, min(cfg['null_per_touch'], len(pool)), replace=False):
            null.append(dict(symbol=symbol, week=r['week'], side=side, time=str(idx[k]),
                             gross_bp=side*(o[k+hold]-o[k])/o[k]*1e4, fee_bp=fee*1e4))
    return out, null


def trades_15m(f, rows, cfg):
    """Approximation: exit at the close of the bar after the touch bar (15-30 min)."""
    c, o = f.close.to_numpy(), f.open.to_numpy(); fee = cfg['maker_fee']+cfg['taker_fee']
    out = []
    for r in rows.itertuples():
        t = int(r.touch_i)
        if t+1 >= len(f):
            continue
        fill = o[t] if r.side*(r.level-o[t]) > 0 else r.level
        out.append(dict(symbol=r.symbol, week=r.week, side=r.side, time=r.touch_time,
                        gross_bp=r.side*(c[t+1]-fill)/fill*1e4, fee_bp=fee*1e4))
    return out


def stats(d, rng, col='gross_bp', draws=2000):
    if not len(d):
        return dict(n=0)
    net = d[col]-d.fee_bp
    g = net.groupby(d.week).agg(['sum', 'size'])
    s, n = g['sum'].to_numpy(), g['size'].to_numpy()
    boots = []
    for _ in range(draws):
        k = rng.integers(0, len(g), len(g))
        boots.append(s[k].sum()/n[k].sum())
    return dict(n=len(d), gross_mean_bp=float(d[col].mean()), net_mean_bp=float(net.mean()),
                net_median_bp=float(net.median()), win_pct_net=float(100*(net > 0).mean()),
                net_boot95=[float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))])


def run():
    cfg = json.loads((EXP/'config.json').read_text())
    if subprocess.run(['git','diff','--quiet','HEAD','--','yoyo/evaluation/monday_1618_hold30.py'], cwd=ROOT).returncode:
        raise RuntimeError('Commit builder first')
    if subprocess.run(['git','ls-files','--error-unmatch','yoyo/evaluation/monday_1618_hold30.py'], cwd=ROOT,
                      capture_output=True).returncode:
        raise RuntimeError('Builder is not tracked')
    rng = np.random.default_rng(cfg['seed'])
    sets, nulls = {}, {}
    t, n = trades_1m(okx_btc_minutes(), 'BTC', cfg, rng)
    sets['okx_btc_1m'], nulls['okx_btc_1m'] = pd.DataFrame(t), pd.DataFrame(n)
    scfg = json.loads((A/'exp-monday-1618-oos-1m-20261023-v1/config.json').read_text())
    tt, nn = [], []
    for sym, path in scfg['series'].items():
        a, b = trades_1m(oos.minutes(ROOT/path), sym, cfg, rng); tt += a; nn += b
    sets['binance_majors_1m'], nulls['binance_majors_1m'] = pd.DataFrame(tt), pd.DataFrame(nn)
    led = pd.read_csv(A/'exp-monday-1618-validity-20261023-v1/touches.csv.gz')
    vcfg = json.loads((A/'exp-monday-1618-validity-20261023-v1/config.json').read_text())
    rows = []
    for sym, path in v.universe(vcfg).items():
        f = v.load(path); f = f[f.index >= pd.Timestamp(vcfg['start'])]
        rows += trades_15m(f, led[led.symbol == sym], cfg)
    sets['okx_177_15m'] = pd.DataFrame(rows)
    alt = pd.read_csv(A/'exp-monday-1618-alt-hours-20261023-v1/touches_alt.csv.gz')
    root = A/'exp-monday-1618-alt-hours-20261023-v1/inputs'
    rows = []
    for sym, g in alt.groupby('symbol'):
        f = klines(root, f'{sym}USDT')
        rows += trades_15m(f, g, cfg)
    sets['binance_alts_15m'] = pd.DataFrame(rows)
    res = {}
    for name, d in sets.items():
        d.to_csv(EXP/f'trades_{name}.csv.gz', index=False)
        t = pd.to_datetime(d.time, utc=True)
        split = pd.Timestamp(cfg['split'][name])
        res[name] = dict(no_stop=stats(d, rng), earlier=stats(d[t < split], rng), later=stats(d[t >= split], rng),
                         by_side={str(s): stats(g, rng) for s, g in d.groupby('side')})
        if 'gross_stop_bp' in d:
            res[name]['with_2.0_stop'] = stats(d, rng, 'gross_stop_bp')
            res[name]['stopped_pct'] = float(100*d.stopped.mean())
            res[name]['by_symbol'] = {s: stats(g, rng) for s, g in d.groupby('symbol')}
        if name in nulls:
            res[name]['random_null'] = stats(nulls[name], rng)
    passed = bool(all(res[n]['no_stop']['net_boot95'][0] > 0 and res[n]['earlier']['net_mean_bp'] > 0
                      and res[n]['later']['net_mean_bp'] > 0 for n in ('okx_btc_1m', 'binance_majors_1m')))
    summary = dict(config=cfg, results=res, passed=passed,
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
        training_eligible=False, production_eligible=False)
    dump(EXP/'summary.json', summary)
    print(json.dumps(summary['results'], ensure_ascii=False, indent=1)[:6000])
    print('passed', passed)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--config', type=Path, default=EXP/'config.json')
    args = parser.parse_args(); EXP = args.config.resolve().parent; run()
