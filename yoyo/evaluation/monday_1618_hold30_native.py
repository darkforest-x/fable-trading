"""NautilusTrader replay of the Monday1618 limit + 30-minute exit (owner2026-10-23).

The owner asked whether the framework ran it. This executes the same frozen
rule from exp-monday-1618-hold30 natively (NautilusTrader1.231.0 HEDGING,
nautilus_independent.run_native, 1m bars indexed by close time):
  resting limit at the tick-rounded1618 (short rounds up, long down, so the
  fill condition is unchanged), submitted at the close of the minute before
  the first touch (the level was never traded earlier that week, so this
  equals resting from Monday close), one-minute expiry;
  terminal market close at the close of the29th minute after the fill minute
  (fills at the next open, i.e. 30 minutes after the fill minute opened);
  primary without a stop (an unreachable 50percent stop), variant with the
  2.0-extension stop. Engine fees are zero; real fees are applied per fill
  afterwards: maker0.02% entry, taker0.05% exit or stop.
Results are reconciled trade by trade with the bar computation when its
trades file exists; later runs (hold50 onward) are framework-only.
Config "variants" may instead select the wick stop (owner2026-10-23
"止损改成影线低点"): no stop until the15m touch bar closes, then one native
stop at that bar's low (long) / high (short), the extreme the trade has
already survived. wick_ok marks bars whose body stayed inside the level.
"""
from __future__ import annotations
import argparse
import json
import math
import subprocess
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation import monday_1618_validity as v
from yoyo.evaluation import monday_1618_oos_1m as oos
from yoyo.evaluation.monday_1618_hold30 import okx_btc_minutes, stats

EXP = ROOT/'experiments/active/exp-monday-1618-hold30-20261023-v1'
A = ROOT/'experiments/active'


def infer_tick(m):
    p = m[['open', 'high', 'low', 'close']].to_numpy()
    for t in (1., .1, .01, .001, 1e-4, 1e-5, 1e-6, 1e-7):
        if (np.abs(p/t-np.rint(p/t)) < 1e-6).all():
            return t
    raise ValueError('No tick grid')


def entries(m, symbol, cfg, tick):
    q = oos.quarter(m)
    o, h, l, c = (m[k].to_numpy() for k in ('open', 'high', 'low', 'close')); idx = m.index; hold = cfg['hold_minutes']
    rows = []
    for r in v.touches(q, symbol, cfg):
        i0 = idx.searchsorted(pd.Timestamp(r['touch_time']))
        side, level = r['side'], r['level']
        lim = math.ceil(level/tick-1e-9)*tick if side == -1 else math.floor(level/tick+1e-9)*tick
        hit = (l[i0:i0+15] <= lim) if side == 1 else (h[i0:i0+15] >= lim)
        if not hit.any():
            continue
        i = i0+int(np.argmax(hit))
        if i < 1 or i+hold >= len(m) or (idx[i+hold]-idx[i]) != pd.Timedelta(minutes=hold) \
                or idx[i0+14]-idx[i0] != pd.Timedelta(minutes=14):
            continue
        wick = l[i0:i0+15].min() if side == 1 else h[i0:i0+15].max()
        body = (min(o[i0], c[i0+14]), max(o[i0], c[i0+14]))
        wick_ok = bool(body[0] >= lim) if side == 1 else bool(body[1] <= lim)
        stop20 = r['stop']
        stop20 = math.ceil(stop20/tick-1e-9)*tick if side == -1 else math.floor(stop20/tick+1e-9)*tick
        far = lim*(1.5 if side == -1 else .5)
        far = math.ceil(far/tick)*tick if side == -1 else math.floor(far/tick)*tick
        rows.append(dict(symbol=symbol, week=r['week'], side=side, fill_i=i, entry_i=i-1, expire_i=i+1,
                         end_i=i+hold-1, entry=round(lim, 10), stop_far=round(far, 10), stop20=round(stop20, 10),
                         wick_i=i0+14, stop_wick=round(wick, 10), wick_ok=wick_ok, fill_time=str(idx[i])))
    return pd.DataFrame(rows)


def replay(m, e, tick, symbol, stop_col, update_col=None):
    from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native
    frame = m[['open', 'high', 'low', 'close']].copy()
    frame.index = frame.index+pd.Timedelta(minutes=1)  # adapter expects bar close time
    x = e.copy()
    x['trade_id'] = [f'{symbol}-{w}-{s}' for w, s in zip(x.week, x.side)]
    x['stop'] = x[stop_col]; x['risk'] = (x.side*(x.entry-x.stop)).round(10); x['target_price'] = np.nan
    x['stop_update_i'] = x.wick_i if update_col else np.nan
    x['stop_update'] = x[update_col] if update_col else np.nan
    policy = ExitPolicy(trail_arm_r=1e12, opposite_hour_exit=False, round_trip_cost=0., target_from_entry=True)
    rows, fills, audit = run_native(frame, np.full(len(frame), np.nan), np.zeros(len(frame)),
        x[['trade_id', 'entry_i', 'expire_i', 'end_i', 'side', 'entry', 'stop', 'risk', 'target_price',
           'stop_update_i', 'stop_update']],
        policy, tick=tick, symbol=symbol, execution_minutes=1, entry_order_mode='limit')
    return rows.merge(x[['trade_id', 'week', 'fill_time', 'wick_ok', 'stop_wick']], on='trade_id'), fills, audit


def run():
    cfg = json.loads((EXP/'config.json').read_text())
    for p in ('yoyo/evaluation/monday_1618_hold30_native.py', 'yoyo/evaluation/monday_1618_hold30.py'):
        if subprocess.run(['git','diff','--quiet','HEAD','--',p], cwd=ROOT).returncode or \
           subprocess.run(['git','ls-files','--error-unmatch',p], cwd=ROOT, capture_output=True).returncode:
            raise RuntimeError('Commit builders first')
    scfg = json.loads((A/'exp-monday-1618-oos-1m-20261023-v1/config.json').read_text())
    series = dict(okx_btc_1m={'BTC': None}, binance_majors_1m={s: ROOT/p for s, p in scfg['series'].items()})
    fee = (cfg['maker_fee']+cfg['taker_fee'])*1e4
    rng = np.random.default_rng(cfg['seed']); res = {}; audits = {}
    variants = cfg.get('variants', {'no_stop': {'stop': 'stop_far'}, 'stop_2.0': {'stop': 'stop20'}})
    primary = cfg.get('primary', 'no_stop')
    for name, syms in series.items():
        parts = {label: [] for label in variants}
        for sym, path in syms.items():
            m = okx_btc_minutes() if path is None else oos.minutes(path)
            tick = infer_tick(m)
            e = entries(m, sym, cfg, tick)
            for label, spec in variants.items():
                rows, fills, audit = replay(m, e, tick, sym, spec['stop'], spec.get('update'))
                audits[f'{name}|{sym}|{label}'] = {k: audit.get(k) for k in ('engine', 'version', 'positions', 'fills')}
                rows['symbol'] = sym
                parts[label].append(rows)
                fills.to_csv(EXP/f'native_fills_{name}_{sym}_{label}.csv.gz', index=False)
        res[name] = {}
        for label, ps in parts.items():
            d = pd.concat(ps, ignore_index=True)
            filled = d[d.exit_price.notna()].copy() if 'exit_price' in d else d
            filled['gross_bp'] = filled.side*(filled.exit_price-filled.entry)/filled.entry*1e4
            filled['fee_bp'] = fee
            filled['time'] = filled.fill_time
            filled.to_csv(EXP/f'native_trades_{name}_{label}.csv.gz', index=False)
            t = pd.to_datetime(filled.time, utc=True); split = pd.Timestamp(cfg['split'][name])
            res[name][label] = dict(all=stats(filled, rng), earlier=stats(filled[t < split], rng),
                                    later=stats(filled[t >= split], rng), reasons=filled.reason.value_counts().to_dict(),
                                    by_symbol={s: stats(g, rng) for s, g in filled.groupby('symbol')},
                                    by_wick_ok={str(k): stats(g, rng) for k, g in filled.groupby('wick_ok')},
                                    median_wick_risk_bp=float((filled.side*(filled.entry-filled.stop_wick)/filled.entry*1e4).median()))
        if not (EXP/f'trades_{name}.csv.gz').exists():
            continue  # framework-only run (owner 2026-10-23): no bar computation to reconcile
        quick = pd.read_csv(EXP/f'trades_{name}.csv.gz')
        nat = pd.read_csv(EXP/f'native_trades_{name}_no_stop.csv.gz')
        j = nat.merge(quick, on=['symbol', 'week', 'side'], suffixes=('_native', '_bars'))
        res[name]['reconciliation'] = dict(native=len(nat), bars=len(quick), matched=len(j),
            mean_abs_diff_bp=float((j.gross_bp_native-j.gross_bp_bars).abs().mean()),
            max_abs_diff_bp=float((j.gross_bp_native-j.gross_bp_bars).abs().max()))
    passed = bool(all(res[n][primary]['all']['net_boot95'][0] > 0 and res[n][primary]['earlier']['net_mean_bp'] > 0
                      and res[n][primary]['later']['net_mean_bp'] > 0 for n in res))
    dump(EXP/'native_summary.json', dict(config=cfg, results=res, audits=audits, passed=passed,
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
        training_eligible=False, production_eligible=False))
    print(json.dumps(dict(results=res, passed=passed), ensure_ascii=False, indent=1)[:7000])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--config', type=Path, default=EXP/'config.json')
    args = parser.parse_args(); EXP = args.config.resolve().parent; run()
