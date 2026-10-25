"""Monday1618 limit with a 5m wick stop and the near Monday edge target (owner2026-10-25).

Owner (MUBARAK 5m example, stop just below the touch wick, about 20R to target):
"为什么选择60min监控而不是5min 如果监控5min你看我这单能赚20r". Rule tested:
Codex's frozen signal orders (alts, first weekly-side 1618 resting limit,
near Monday edge target, same fees and monitoring window), but no reclaim rule
and no breakeven: the stop is the low (long) / high (short) of the 5m bar that
fills, placed at that bar's close (stop_update; price cannot pass it inside the
bar); exit at the target, the stop, or the end of the window. Run natively under
the configured intrabar path with monotone segments; R = entry-to-wick distance.
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
KEEP = ['trade_id', 'side', 'entry', 'exit_price', 'reason', 'entry_time', 'exit_time', 'entry_i', 'exit_i', 'net_notional_bp']


def process(symbol, intrabar, root):
    from yoyo.evaluation.monday_limit_reclaim import prepare
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native
    cfg = json.loads((CODEX/'config.json').read_text())
    job = json.loads((CODEX/'symbols'/symbol/'identity.json').read_text())['job']
    t0 = time.perf_counter()
    f, quality = load_job(job)
    p, e, _, _, tick = prepare(f, job, cfg, quality)
    e = e[(e.population == 'signal') & (e.reclaim_minutes == 60) & (e.exit_mode == 'near_monday_edge')].copy()
    out = Path(root)/'symbols'/symbol
    out.mkdir(parents=True, exist_ok=True)
    lo, hi = p.low.to_numpy(), p.high.to_numpy()
    k = e.touch_i_hint.astype(int).to_numpy()
    e['stop_update_i'] = k
    e['stop_update'] = np.where(e.side == 1, lo[k], hi[k])
    e['stop'] = [(math.floor(v*.5/tick) if s == 1 else math.ceil(v*1.5/tick))*tick for v, s in zip(e.entry, e.side)]
    e['risk'] = e.side*(e.entry-e.stop)
    e = e[(e.stop_update_i > e.entry_i) & (e.stop_update_i < e.end_i)]
    if e.empty:
        pd.DataFrame(columns=KEEP+['wick_stop']).to_csv(out/'trades.csv.gz', index=False)
        dump(out/'done.json', dict(symbol=symbol, orders=0)); return dict(symbol=symbol, orders=0)
    frame = p.copy(); frame.index = frame.index+pd.Timedelta(minutes=job['execution_minutes'])
    policy = ExitPolicy(trail_arm_r=1e12, opposite_hour_exit=False, round_trip_cost=0, target_from_entry=True,
                        intrabar_path=intrabar, monotone_segments=True, breakeven_includes_cost=False,
                        fill_maker_fee=cfg['maker_fee'], fill_taker_fee=cfg['taker_fee'])
    cols = ['trade_id', 'entry_i', 'expire_i', 'end_i', 'side', 'entry', 'stop', 'risk', 'target_price', 'stop_update_i', 'stop_update']
    rows, _, audit = run_native(frame, np.full(len(frame), np.nan), np.zeros(len(frame)), e[cols], policy, tick=tick,
                                symbol=symbol.removesuffix('USDT'), execution_minutes=job['execution_minutes'],
                                entry_order_mode='limit', batch_bars=cfg['streaming_batch_bars'])
    rows = rows[[c for c in KEEP if c in rows]].merge(e[['trade_id', 'stop_update', 'target_price']].rename(columns={'stop_update': 'wick_stop'}), on='trade_id')
    rows.to_csv(out/'trades.csv.gz', index=False)
    done = dict(symbol=symbol, intrabar=intrabar, orders=len(e), positions=audit['positions'], fills=audit['fills'],
                engine=audit.get('engine'), version=audit.get('version'), seconds=time.perf_counter()-t0)
    dump(out/'done.json', done)
    return done


def run(config, workers):
    exp = config.resolve().parent
    cfg = json.loads(config.read_text())
    for path in ('yoyo/evaluation/monday_limit_wick_stop.py', str(config.resolve().relative_to(ROOT))):
        if subprocess.run(['git','diff','--quiet','HEAD','--',path], cwd=ROOT).returncode or \
           subprocess.run(['git','ls-files','--error-unmatch',path], cwd=ROOT, capture_output=True).returncode:
            raise RuntimeError('Commit builder and config first')
    syms = sorted(d.name for d in (CODEX/'symbols').iterdir()
                  if json.loads((d/'identity.json').read_text())['job']['group'] == 'crypto_5m')
    todo = [s for s in syms if not (exp/'symbols'/s/'done.json').exists()]
    with ProcessPoolExecutor(workers) as ex:
        futs = {ex.submit(process, s, cfg['intrabar_path'], str(exp)): s for s in todo}
        for n, fu in enumerate(as_completed(futs), 1):
            try:
                r = fu.result()
            except Exception as err:
                r = dict(symbol=futs[fu], error=repr(err))
                (exp/'symbols'/futs[fu]).mkdir(parents=True, exist_ok=True); dump(exp/'symbols'/futs[fu]/'error.json', r)
            print(json.dumps(dict(progress=n, total=len(todo), **r)), flush=True)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, required=True); ap.add_argument('--workers', type=int, default=2)
    a = ap.parse_args(); run(a.config, a.workers)
