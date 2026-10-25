"""Backstop (initial stop) grid for the Monday1618 limit-reclaim v2 rule (owner2026-10-25).

Owner: "肯定要设置兜底止损啊 ... 如何设置初始止损最保证收益最高". The current rule has
no stop between the fill and the 60m confirmation close; the worst trade lost
91 percent (OM, 2025-04-14). Rule tested: Codex's frozen signal orders (alts,
60m confirmation, near Monday edge target, fees from the Codex config), plus a
resting stop-market backstop from the fill (ExitPolicy.reclaim_backstop): a
reclaim moves it to entry as before, a failed reclaim cancels it before the
market exit. Backstop distance per config: k R beyond the entry (R = 0.382 of
the Monday range = target distance / 1.618) or p percent of the entry, floored
(long) / ceiled (short) to the tick, never below one tick. Config "far" puts the
stop at 1/50 (long) or 50x (short) of the entry; it must reproduce the existing
ledgers trade for trade (parity). Only weeks from 2024-12-30 are submitted
(2025 selection, 2026 test). Every config is an independent HEDGING position
in one native run per symbol and intrabar path (monotone segments).
"""
from __future__ import annotations
import argparse
import json
import subprocess
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump

CODEX = ROOT/'experiments/active/exp-monday-limit-reclaim-20261024-v1'
KEEP = ['trade_id', 'side', 'entry', 'stop', 'exit_price', 'reason', 'entry_time', 'exit_time', 'entry_i', 'exit_i',
        'reclaim_status', 'net_notional_bp']


def stops(e, kind, value, tick):
    """Backstop price per order; R = target distance / 1.618."""
    side, entry = e.side.to_numpy(float), e.entry.to_numpy(float)
    if kind == 'far':
        raw = np.where(side > 0, entry/50, entry*50)
    elif kind == 'R':
        raw = entry-side*value*np.abs(e.target_price.to_numpy(float)-entry)/1.618
    else:
        raw = entry*(1-side*value/100)
    out = np.where(side > 0, np.floor(raw/tick+1e-7)*tick, np.ceil(raw/tick-1e-7)*tick)
    return np.maximum(out, tick)


def process(symbol, intrabar, root, grid, since):
    from yoyo.evaluation.monday_limit_reclaim import prepare
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native
    cfg = json.loads((CODEX/'config.json').read_text())
    job = json.loads((CODEX/'symbols'/symbol/'identity.json').read_text())['job']
    if job['group'] != 'crypto_5m':
        return dict(symbol=symbol, skipped=job['group'])
    t0 = time.perf_counter()
    f, quality = load_job(job)
    p, e, _, _, tick = prepare(f, job, cfg, quality)
    e = e[(e.population == 'signal') & (e.reclaim_minutes == 60) & (e.exit_mode == 'near_monday_edge') & (e.week >= since)]
    out = Path(root)/'symbols'/symbol
    out.mkdir(parents=True, exist_ok=True)
    if e.empty:
        pd.DataFrame(columns=KEEP+['config']).to_csv(out/'trades.csv.gz', index=False)
        dump(out/'done.json', dict(symbol=symbol, intrabar=intrabar, orders=0, positions=0))
        return dict(symbol=symbol, orders=0)
    parts = []
    for name, kind, value in grid:
        x = e.copy()
        x['stop'] = stops(x, kind, value, tick)
        x['trade_id'] = x.trade_id+':bs_'+name
        parts.append(x)
    e = pd.concat(parts, ignore_index=True)
    frame = p.copy(); frame.index = frame.index+pd.Timedelta(minutes=job['execution_minutes'])
    policy = ExitPolicy(trail_arm_r=1e12, opposite_hour_exit=False, round_trip_cost=0, target_from_entry=True,
                        intrabar_path=intrabar, reclaim_next_bar=True, breakeven_includes_cost=False,
                        fill_maker_fee=cfg['maker_fee'], fill_taker_fee=cfg['taker_fee'], monotone_segments=True,
                        reclaim_backstop=True)
    rows, fills, audit = run_native(frame, np.full(len(frame), np.nan), np.zeros(len(frame)), e, policy, tick=tick,
                                    symbol=symbol.removesuffix('USDT'), execution_minutes=job['execution_minutes'],
                                    entry_order_mode='limit', batch_bars=cfg['streaming_batch_bars'])
    rows = rows[[c for c in KEEP if c in rows]].copy()
    rows['config'] = rows.trade_id.str.rsplit(':bs_', n=1).str[-1]
    rows.to_csv(out/'trades.csv.gz', index=False)
    done = dict(symbol=symbol, intrabar=intrabar, orders=len(e), positions=audit['positions'], fills=audit['fills'],
                engine=audit.get('engine'), version=audit.get('version'), seconds=time.perf_counter()-t0)
    dump(out/'done.json', done)
    return done


def run(config, workers):
    exp = config.resolve().parent
    cfg = json.loads(config.read_text())
    for path in ('yoyo/evaluation/monday_limit_backstop.py', str(config.resolve().relative_to(ROOT))):
        if subprocess.run(['git','diff','--quiet','HEAD','--',path], cwd=ROOT).returncode or \
           subprocess.run(['git','ls-files','--error-unmatch',path], cwd=ROOT, capture_output=True).returncode:
            raise RuntimeError('Commit builder and config first')
    syms = cfg['symbols']
    todo = [s for s in syms if not (exp/'symbols'/s/'done.json').exists()]
    grid = [tuple(g) for g in cfg['grid']]
    with ProcessPoolExecutor(workers) as ex:
        futs = {ex.submit(process, s, cfg['intrabar_path'], str(exp), grid, cfg['since_week']): s for s in todo}
        for k, fu in enumerate(as_completed(futs), 1):
            try:
                r = fu.result()
            except Exception as err:  # keep going; record the failure per symbol
                r = dict(symbol=futs[fu], error=repr(err))
                (exp/'symbols'/futs[fu]).mkdir(parents=True, exist_ok=True); dump(exp/'symbols'/futs[fu]/'error.json', r)
            print(json.dumps(dict(progress=k, total=len(todo), **r)), flush=True)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, required=True); ap.add_argument('--workers', type=int, default=4)
    a = ap.parse_args(); run(a.config, a.workers)
