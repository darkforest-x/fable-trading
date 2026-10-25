"""Closer targets for the Codex Monday1618 limit-reclaim signals (owner2026-10-25 策略优化).

Owner sub-page "策略优化": better take-profit, partial take-profit. Single
variable: the target. Codex's frozen pipeline (load_job, prepare, ExitPolicy,
fees, reclaim and breakeven rules) builds the alt signal orders with 60m
confirmation and the near Monday edge target; each order is copied with its
target at a fraction (config target_fractions) of the entry-to-edge distance,
rounded toward the entry on the tick grid. Run natively under the configured
intrabar path with monotone segments. Positions are independent, so a 50/50
partial take-profit equals the mean of the 0.5 and 1.0 legs (same entry and
stop rules), computed downstream.
"""
from __future__ import annotations
import argparse
import json
import math
import subprocess
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump

CODEX = ROOT/'experiments/active/exp-monday-limit-reclaim-20261024-v1'
KEEP = ['trade_id', 'side', 'entry', 'exit_price', 'reason', 'entry_time', 'exit_time', 'entry_i', 'exit_i',
        'reclaim_status', 'net_notional_bp', 'target_price']


def process(symbol, intrabar, fractions, root):
    from yoyo.evaluation.monday_limit_reclaim import prepare
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native
    cfg = json.loads((CODEX/'config.json').read_text())
    job = json.loads((CODEX/'symbols'/symbol/'identity.json').read_text())['job']
    t0 = time.perf_counter()
    f, quality = load_job(job)
    p, e, _, _, tick = prepare(f, job, cfg, quality)
    e = e[(e.population == 'signal') & (e.reclaim_minutes == 60) & (e.exit_mode == 'near_monday_edge')]
    out = Path(root)/'symbols'/symbol
    out.mkdir(parents=True, exist_ok=True)
    legs = []
    for frac in fractions:
        x = e.copy()
        raw = x.entry+frac*(x.target_price-x.entry)
        x['target_price'] = [(math.floor(v/tick+1e-7) if s == 1 else math.ceil(v/tick-1e-7))*tick for v, s in zip(raw, x.side)]
        ok = x.side*(x.target_price-x.entry) >= tick*(1-1e-7)
        x = x[ok & x.target_price.notna()]
        x['trade_id'] = x.trade_id+f':tp{int(round(frac*100))}'
        legs.append(x)
    e = pd.concat(legs, ignore_index=True)
    if e.empty:
        pd.DataFrame(columns=KEEP).to_csv(out/'trades.csv.gz', index=False)
        dump(out/'done.json', dict(symbol=symbol, orders=0)); return dict(symbol=symbol, orders=0)
    frame = p.copy(); frame.index = frame.index+pd.Timedelta(minutes=job['execution_minutes'])
    policy = ExitPolicy(trail_arm_r=1e12, opposite_hour_exit=False, round_trip_cost=0, target_from_entry=True,
                        intrabar_path=intrabar, reclaim_next_bar=True, breakeven_includes_cost=False,
                        fill_maker_fee=cfg['maker_fee'], fill_taker_fee=cfg['taker_fee'], monotone_segments=True)
    rows, _, audit = run_native(frame, np.full(len(frame), np.nan), np.zeros(len(frame)), e, policy, tick=tick,
                                symbol=symbol.removesuffix('USDT'), execution_minutes=job['execution_minutes'],
                                entry_order_mode='limit', batch_bars=cfg['streaming_batch_bars'])
    rows = rows[[c for c in KEEP if c in rows]].copy()
    rows.to_csv(out/'trades.csv.gz', index=False)
    done = dict(symbol=symbol, intrabar=intrabar, fractions=list(fractions), orders=len(e), positions=audit['positions'],
                fills=audit['fills'], engine=audit.get('engine'), version=audit.get('version'), seconds=time.perf_counter()-t0)
    dump(out/'done.json', done)
    return done


def run(config, workers):
    exp = config.resolve().parent
    cfg = json.loads(config.read_text())
    for path in ('yoyo/evaluation/monday_limit_reclaim_exit_variants.py', str(config.resolve().relative_to(ROOT))):
        if subprocess.run(['git','diff','--quiet','HEAD','--',path], cwd=ROOT).returncode or \
           subprocess.run(['git','ls-files','--error-unmatch',path], cwd=ROOT, capture_output=True).returncode:
            raise RuntimeError('Commit builder and config first')
    syms = sorted(d.name for d in (CODEX/'symbols').iterdir()
                  if json.loads((d/'identity.json').read_text())['job']['group'] == 'crypto_5m')
    todo = [s for s in syms if not (exp/'symbols'/s/'done.json').exists()]
    with ProcessPoolExecutor(workers) as ex:
        futs = {ex.submit(process, s, cfg['intrabar_path'], cfg['target_fractions'], str(exp)): s for s in todo}
        for k, fu in enumerate(as_completed(futs), 1):
            try:
                r = fu.result()
            except Exception as err:
                r = dict(symbol=futs[fu], error=repr(err))
                (exp/'symbols'/futs[fu]).mkdir(parents=True, exist_ok=True); dump(exp/'symbols'/futs[fu]/'error.json', r)
            print(json.dumps(dict(progress=k, total=len(todo), **r)), flush=True)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, required=True); ap.add_argument('--workers', type=int, default=4)
    a = ap.parse_args(); run(a.config, a.workers)
