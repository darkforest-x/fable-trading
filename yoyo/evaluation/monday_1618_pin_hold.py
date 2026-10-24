"""1m pin at the Monday1618 that holds for an hour: does it reach the Monday edge?

Owner2026-10-24 (OKX BTC 1m chart): a 1m candle wicks through the long1618
and closes back above, then price stays away for an hour. Question: how many
first weekly touches look like this, and how many reach the Monday near edge.
Ledgers reused: Binance majors touches_1m (exp-monday-1618-oos-1m). No OKX1m
ledger existed, so OKX BTC1m touches are rebuilt with the same
monday_1618_validity.touches and monday_1618_oos_1m.refine functions.
Conditions use only minutes at or after the first touch minute i:
  pin: minute i closes back inside the level (long close > level);
  held: minutes i+1..i+60 never trade the level again;
  held_pin_extreme (sensitivity): minutes i+1..i+60 stay beyond the pin extreme.
Outcome is the existing label: Monday near edge before the2.0 extension (1m,
14 days, touch minute can only stop). Null: random-walk barrier odds from the
close of minute i+60, side*(c-stop)/side*(edge-stop), averaged over the same
events, plus held touches without a pin. Descriptive; no orders or fees.
"""
from __future__ import annotations
import json
import subprocess
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation import monday_1618_validity as v
from yoyo.evaluation import monday_1618_oos_1m as oos
from yoyo.evaluation.monday_1618_hold30 import okx_btc_minutes

EXP = ROOT/'experiments/active/exp-monday-1618-pin-hold-20261024-v1'
A = ROOT/'experiments/active'


def events(m, rows, hold):
    """Pin/held flags and the barrier odds left after `hold` minutes."""
    o, h, l, c = (m[k].to_numpy() for k in ('open', 'high', 'low', 'close'))
    out = []
    for r in rows.itertuples():
        i = m.index.get_loc(pd.Timestamp(r.touch_minute))
        if i+hold >= len(m) or m.index[i+hold]-m.index[i] != pd.Timedelta(minutes=hold):
            continue
        s, lvl = r.side, r.level
        nxt = slice(i+1, i+hold+1)
        extreme = l[i] if s == 1 else h[i]
        out.append(dict(symbol=r.symbol, week=r.week, side=s, touch_minute=str(m.index[i]), valid=bool(r.valid),
            pin=bool(s*(c[i]-lvl) > 0),
            held=bool((l[nxt] > lvl).all() if s == 1 else (h[nxt] < lvl).all()),
            held_pin_extreme=bool((l[nxt] >= extreme).all() if s == 1 else (h[nxt] <= extreme).all()),
            geo=float(np.clip(s*(c[i+hold]-r.stop)/(s*(r.edge-r.stop)), 0, 1)),
            move_bp=float(s*(c[i+hold]-lvl)/lvl*1e4)))
    return pd.DataFrame(out)


def rate(d, rng, draws=2000):
    if not len(d):
        return dict(n=0)
    g = d.groupby('week').valid.agg(['sum', 'size'])
    s, n = g['sum'].to_numpy(), g['size'].to_numpy()
    boots = [s[k].sum()/n[k].sum() for k in (rng.integers(0, len(g), len(g)) for _ in range(draws))]
    return dict(n=len(d), reached_edge_pct=float(100*d.valid.mean()), random_walk_pct=float(100*d.geo.mean()),
                excess_pp=float(100*(d.valid.mean()-d.geo.mean())),
                boot95=[float(100*np.percentile(boots, 2.5)), float(100*np.percentile(boots, 97.5))],
                median_move_bp=float(d.move_bp.median()))


def run():
    cfg = json.loads((EXP/'config.json').read_text())
    p = 'yoyo/evaluation/monday_1618_pin_hold.py'
    if subprocess.run(['git','diff','--quiet','HEAD','--',p], cwd=ROOT).returncode or \
       subprocess.run(['git','ls-files','--error-unmatch',p], cwd=ROOT, capture_output=True).returncode:
        raise RuntimeError('Commit builder first')
    scfg = json.loads((A/'exp-monday-1618-oos-1m-20261023-v1/config.json').read_text())
    m = okx_btc_minutes()
    rows = []
    for r in v.touches(oos.quarter(m), 'BTC', scfg):
        got = oos.refine(m, r, scfg['horizon_days'])
        if got is not None:
            rows.append(dict(r, symbol='BTC', valid=got[0], touch_minute=got[2]))
    sets = {'okx_btc_1m': events(m, pd.DataFrame(rows), cfg['hold_minutes'])}
    led = pd.read_csv(A/'exp-monday-1618-oos-1m-20261023-v1/touches_1m.csv.gz')
    parts = [events(oos.minutes(ROOT/path), led[led.symbol == sym], cfg['hold_minutes'])
             for sym, path in scfg['series'].items()]
    sets['binance_majors_1m'] = pd.concat(parts, ignore_index=True)
    rng = np.random.default_rng(cfg['seed']); res = {}
    for name, d in sets.items():
        d.to_csv(EXP/f'events_{name}.csv.gz', index=False)
        groups = dict(all=d, pin=d[d.pin], held=d[d.held], pin_held=d[d.pin & d.held],
                      held_no_pin=d[~d.pin & d.held], pin_not_held=d[d.pin & ~d.held],
                      pin_held_extreme=d[d.pin & d.held_pin_extreme])
        res[name] = {k: rate(g, rng) for k, g in groups.items()}
        if name == 'binance_majors_1m':
            res[name]['pin_held_by_symbol'] = {s: rate(g, rng) for s, g in groups['pin_held'].groupby('symbol')}
    dump(EXP/'summary.json', dict(config=cfg, results=res,
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
        training_eligible=False, production_eligible=False))
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    run()
