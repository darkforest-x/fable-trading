"""O-L-H-C intrabar replay of the Codex Monday1618 limit-reclaim signals (owner2026-10-25).

Owner: "去做吧" after shorts that fill and hit the target inside one 5m bar were
found to carry ~47 percent of recorded profit under the fixed O-H-L-C path.
Single variable: intrabar_path O-H-L-C -> O-L-H-C. Everything else is Codex's
frozen pipeline (exp-monday-limit-reclaim-20261024-v1/config.json, inputs via
monday_limit_reclaim_inputs.load_job, orders via monday_limit_reclaim.prepare,
NautilusTrader1.231.0 run_native with the same ExitPolicy and fees), limited
to alt signal orders with 60m confirmation and the near Monday edge target,
for the symbols in the 2026-10-24 snapshot. Positions are independent, so the
subset does not change any trade. O-L-H-C is adverse for a short's fill bar and
for a long's later bars, favourable for the opposite; per-trade min of the two
paths is reported downstream as the pessimistic bound.
v1 (2026-10-25) is invalid: without monotone segments the adapter replayed a
rising segment open-high-low-close, so an O-L-H-C short could still reach a
target below the close. Configs now set intrabar_path and monotone_segments;
outputs go next to the config (v2 = O-L-H-C, ohlc-check = O-H-L-C sample).
"""
from __future__ import annotations
import json
import subprocess
import time
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump

EXP = ROOT/'experiments/active/exp-monday-limit-reclaim-olhc-20261025-v1'
CODEX = ROOT/'experiments/active/exp-monday-limit-reclaim-20261024-v1'
SNAP = ROOT/'output/monday-partial-snapshot-20261024-2224/manifest.json'
KEEP = ['trade_id', 'side', 'entry', 'exit_price', 'reason', 'entry_time', 'exit_time', 'entry_i', 'exit_i',
        'reclaim_status', 'net_notional_bp', 'exit_path_phase']


def process(symbol, intrabar, monotone, root):
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
    e = e[(e.population == 'signal') & (e.reclaim_minutes == 60) & (e.exit_mode == 'near_monday_edge')]
    out = Path(root)/'symbols'/symbol
    out.mkdir(parents=True, exist_ok=True)
    if e.empty:
        pd.DataFrame(columns=KEEP).to_csv(out/'trades.csv.gz', index=False)
        return dict(symbol=symbol, orders=0)
    frame = p.copy(); frame.index = frame.index+pd.Timedelta(minutes=job['execution_minutes'])
    policy = ExitPolicy(trail_arm_r=1e12, opposite_hour_exit=False, round_trip_cost=0, target_from_entry=True,
                        intrabar_path=intrabar, reclaim_next_bar=True, breakeven_includes_cost=False,
                        fill_maker_fee=cfg['maker_fee'], fill_taker_fee=cfg['taker_fee'], monotone_segments=monotone)
    rows, fills, audit = run_native(frame, np.full(len(frame), np.nan), np.zeros(len(frame)), e, policy, tick=tick,
                                    symbol=symbol.removesuffix('USDT'), execution_minutes=job['execution_minutes'],
                                    entry_order_mode='limit', batch_bars=cfg['streaming_batch_bars'])
    rows = rows[[c for c in KEEP if c in rows]].copy()
    rows['entry_interval_open'] = f.index[rows.entry_i.astype(int)].astype(str).to_numpy()
    rows['exit_interval_open'] = f.index[rows.exit_i.astype(int)].astype(str).to_numpy()
    rows.to_csv(out/'trades.csv.gz', index=False)
    done = dict(symbol=symbol, intrabar=intrabar, monotone=monotone, orders=len(e), positions=audit['positions'], fills=audit['fills'],
                engine=audit.get('engine'), version=audit.get('version'), seconds=time.perf_counter()-t0)
    dump(out/'done.json', done)
    return done


def run(workers=3, symbols=None):
    cfg = json.loads((EXP/'config.json').read_text())
    for path in ('yoyo/evaluation/monday_limit_reclaim_olhc.py', str((EXP/'config.json').relative_to(ROOT))):
        if subprocess.run(['git','diff','--quiet','HEAD','--',path], cwd=ROOT).returncode or \
           subprocess.run(['git','ls-files','--error-unmatch',path], cwd=ROOT, capture_output=True).returncode:
            raise RuntimeError('Commit builder and config first')
    syms = symbols or cfg.get('symbols') or [r['symbol'] for r in json.loads(SNAP.read_text())['studies']['limit-reclaim']]
    todo = [s for s in syms if not (EXP/'symbols'/s/'done.json').exists()]
    with ProcessPoolExecutor(workers) as ex:
        futs = {ex.submit(process, s, cfg['intrabar_path'], cfg.get('monotone_segments', False), str(EXP)): s for s in todo}
        for k, fu in enumerate(as_completed(futs), 1):
            try:
                r = fu.result()
            except Exception as err:  # keep going; record the failure per symbol
                r = dict(symbol=futs[fu], error=repr(err))
                (EXP/'symbols'/futs[fu]).mkdir(parents=True, exist_ok=True); dump(EXP/'symbols'/futs[fu]/'error.json', r)
            print(json.dumps(dict(progress=k, total=len(todo), **r)), flush=True)


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(); ap.add_argument('--workers', type=int, default=3); ap.add_argument('--symbols', nargs='*')
    ap.add_argument('--config', type=Path, default=EXP/'config.json')
    a = ap.parse_args(); EXP = a.config.resolve().parent; run(a.workers, a.symbols)
