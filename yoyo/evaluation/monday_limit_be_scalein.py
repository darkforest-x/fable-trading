"""Fee-covering breakeven and scale-in legs for the Monday1618 limit-reclaim rule, native replay (owner2026-10-26).

Owner: "Extend the framework and test both: breakeven price at entry / covering fees +0.07% / covering fees plus
slippage +0.17%; for scores >= 0.6, add an equal-size position at -5% / -10% drawdown, with exits shared with the
original order. Run full history on the worst path." Codex's frozen signal orders (alts, 60m confirmation, all
weeks), frozen entry score from scores.csv.gz next to the config. Families:
  v2: score >= top-40 threshold, near Monday edge target, no backstop ('far' stop at 1/50 or 50x of entry);
  v3: score >= 0.5, range 0.618 target for 0.5-0.6 and opposite 1618 from 0.6, -30 percent backstop.
Each family runs breakeven offsets 0 / 7 / 17 bp (ExitPolicy reclaim with entry column be_offset_bp; a reclaim
close that has not cleared the offset price exits at that close, reason 12). For v3 events with score >= 0.6,
scale-in legs rest at 5 or 10 percent beyond the v3 offset-0 entry from the same submission (entry column
parent): they share the parent's confirmation clock, reclaim test (parent entry), breakeven price, backstop and
target, and are cancelled when the parent closes unfilled. Offset-0 rows must equal the earlier ledgers (parity).
One native run per symbol and intrabar path, monotone segments.
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
from yoyo.evaluation.monday_limit_backstop import CODEX, stops

KEEP = ['trade_id', 'side', 'entry', 'stop', 'target_price', 'exit_price', 'reason', 'entry_time', 'exit_time', 'entry_i',
        'exit_i', 'reclaim_status', 'net_notional_bp']


def v3_mode(score):
    return np.where(score >= .6, 'opposite_1618', 'range_0.618')


def process(symbol, intrabar, exp, cfg, threshold):
    from yoyo.evaluation.monday_limit_reclaim import prepare
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native
    ccfg = json.loads((CODEX/'config.json').read_text())
    job = json.loads((CODEX/'symbols'/symbol/'identity.json').read_text())['job']
    if job['group'] != 'crypto_5m':
        return dict(symbol=symbol, skipped=job['group'])
    t0 = time.perf_counter()
    f, quality = load_job(job)
    p, e, _, _, tick = prepare(f, job, ccfg, quality)
    e = e[(e.population == 'signal') & (e.reclaim_minutes == 60) & (e.week >= cfg['since_week'])].copy()
    sc = pd.read_csv(Path(exp)/'scores.csv.gz').set_index('event').score
    e['event'] = e.trade_id.str.split(':').str[:3].str.join(':')
    e['score'] = e.event.map(sc)
    e = e[e.score >= threshold]
    out = Path(exp)/intrabar.lower()/'symbols'/symbol
    out.mkdir(parents=True, exist_ok=True)
    parts = []
    v2 = e[e.exit_mode == 'near_monday_edge']
    v3 = e[(e.score >= .5) & (e.exit_mode == v3_mode(e.score))]
    for off in cfg['be_offsets_bp']:
        for fam, x, kind, value in (('v2', v2, 'far', 0), ('v3', v3, 'P', 30)):
            y = x.copy()
            y['stop'] = stops(y, kind, value, tick)
            y['be_offset_bp'] = float(off)
            y['trade_id'] = y.trade_id+f':v_{fam}_be{off}'
            parts.append(y)
    hi = v3[v3.score >= .6]
    if len(hi):
        par = hi.copy()
        par['stop'] = stops(par, 'P', 30, tick)
        par['trade_id'] = par.trade_id+':v_v3_be0'
        for dd in cfg['scale_in_pct']:
            c = par.copy()
            raw = c.entry*(1-c.side*dd/100)
            c['entry'] = np.where(c.side > 0, np.floor(raw/tick+1e-7)*tick, np.ceil(raw/tick-1e-7)*tick)
            c['parent'] = par.trade_id
            c['reclaim_ref'] = par.entry
            c['be_offset_bp'] = 0.
            c['trade_id'] = par.trade_id.str.replace(':v_v3_be0', f':v_v3_add{dd}', regex=False)
            c = c[c.side*(c.entry-c.stop) > 0]
            parts.append(c)
    e = pd.concat(parts, ignore_index=True)
    if e.empty:
        pd.DataFrame(columns=KEEP+['variant', 'score', 'parent']).to_csv(out/'trades.csv.gz', index=False)
        dump(out/'done.json', dict(symbol=symbol, intrabar=intrabar, orders=0, positions=0))
        return dict(symbol=symbol, orders=0)
    if 'parent' not in e:
        e['parent'] = None
    e['parent'] = e.parent.where(e.parent.notna(), None)
    if 'reclaim_ref' not in e:
        e['reclaim_ref'] = np.nan
    e['risk'] = e.entry
    frame = p.copy(); frame.index = frame.index+pd.Timedelta(minutes=job['execution_minutes'])
    policy = ExitPolicy(trail_arm_r=1e12, opposite_hour_exit=False, round_trip_cost=0, target_from_entry=True,
                        intrabar_path=intrabar, reclaim_next_bar=True, breakeven_includes_cost=False,
                        fill_maker_fee=ccfg['maker_fee'], fill_taker_fee=ccfg['taker_fee'], monotone_segments=True,
                        reclaim_backstop=True)
    rows, _, audit = run_native(frame, np.full(len(frame), np.nan), np.zeros(len(frame)), e.drop(columns=['event', 'score']), policy,
                                tick=tick, symbol=symbol.removesuffix('USDT'), execution_minutes=job['execution_minutes'],
                                entry_order_mode='limit', batch_bars=ccfg['streaming_batch_bars'])
    rows = rows[[c for c in KEEP if c in rows]].merge(e[['trade_id', 'score', 'parent']], on='trade_id')
    rows['variant'] = rows.trade_id.str.rsplit(':v_', n=1).str[-1]
    rows.to_csv(out/'trades.csv.gz', index=False)
    done = dict(symbol=symbol, intrabar=intrabar, orders=len(e), positions=audit['positions'], fills=audit['fills'],
                cancelled=sum(o['status'] == 'parent_closed' for o in audit.get('opportunities', [])),
                engine=audit.get('engine'), version=audit.get('version'), seconds=time.perf_counter()-t0)
    dump(out/'done.json', done)
    return done


def run(config, workers, intrabar):
    exp = config.resolve().parent
    cfg = json.loads(config.read_text())
    for path in ('yoyo/evaluation/monday_limit_be_scalein.py', str(config.resolve().relative_to(ROOT))):
        if subprocess.run(['git','diff','--quiet','HEAD','--',path], cwd=ROOT).returncode or \
           subprocess.run(['git','ls-files','--error-unmatch',path], cwd=ROOT, capture_output=True).returncode:
            raise RuntimeError('Commit builder and config first')
    threshold = json.loads((exp/'scores.json').read_text())['threshold']
    root = exp/intrabar.lower()
    todo = [s for s in cfg['symbols'] if not (root/'symbols'/s/'done.json').exists()]
    with ProcessPoolExecutor(workers) as ex:
        futs = {ex.submit(process, s, intrabar, str(exp), cfg, threshold): s for s in todo}
        for k, fu in enumerate(as_completed(futs), 1):
            try:
                r = fu.result()
            except Exception as err:  # keep going; record the failure per symbol
                r = dict(symbol=futs[fu], error=repr(err))
                (root/'symbols'/futs[fu]).mkdir(parents=True, exist_ok=True); dump(root/'symbols'/futs[fu]/'error.json', r)
            print(json.dumps(dict(progress=k, total=len(todo), **r)), flush=True)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, required=True); ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--intrabar', choices=['OHLC', 'OLHC'], required=True)
    a = ap.parse_args(); run(a.config, a.workers, a.intrabar)
