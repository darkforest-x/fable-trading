"""Layered live entries for the Monday1618 v3 rule: rest on the previous-close score, confirm at the touch close, enter late at that close (owner 2026-10-27).

Owner: the spike paper score is only known when the 5m touch bar closes, too late to rest a live limit. Proposed
layers: rest on a pre-score, confirm with the real score at the touch close and keep or exit; for a level touched
without a resting order, fall back to a market entry at that close with the stop unchanged; combine them and find
the cases that keep return and cut drawdown. Scores (frozen model) from
exp-monday-limit-score-leak-audit-20261027-v1/scores_causal.csv.gz:
  pre   `causal`, features through the bar before the touch: known while the order rests;
  full  `stored`, the same model reading the touch bar: known at the touch bar's close.
v3 exits throughout: range 0.618 target below 0.6 and opposite 1618 from 0.6, -30 percent backstop from the level,
60m reclaim with breakeven at the level. Variants (independent trades; one limit run and one market run per symbol
and intrabar path):
  rest      resting limit for pre >= 0.5, target by pre, held as v3 (layer A);
  rest_chk  the same order for events with full < 0.5, closed at market at its fill bar's close (reason 14) unless
            that bar already took the target or the backstop (layer B; for full >= 0.5 it equals rest);
  late      pre < 0.5 and full >= 0.5: no resting order; market entry at the touch bar's close when that close lies
            between the backstop and the target; backstop, target and reclaim test at the resting plan's level-based
            prices, target by full (layer C).
The owner's 1m spike entry needs 1m bars through each trade and is not run here. `summarize` is read-only
arithmetic on these fills: worse path per trade, 10bp slippage on non-target exits and on late market entries,
the two bad source bars dropped, parity of rest with the earlier v3_be0 ledger, and frac N20 3x replays.
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
from yoyo.evaluation.monday_limit_be_scalein import v3_mode

KEEP = ['trade_id', 'side', 'entry', 'stop', 'target_price', 'exit_price', 'reason', 'entry_time', 'exit_time', 'entry_i',
        'exit_i', 'reclaim_status', 'net_notional_bp']
A = ROOT/'experiments/active'


def process(symbol, intrabar, exp, cfg):
    from yoyo.evaluation.monday_limit_reclaim import prepare
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native
    ccfg = json.loads((CODEX/'config.json').read_text())
    job = json.loads((CODEX/'symbols'/symbol/'identity.json').read_text())['job']
    if job['group'] != 'crypto_5m':
        return dict(symbol=symbol, skipped=job['group'])
    t0 = time.perf_counter()
    cut = cfg['cut']
    f, quality = load_job(job)
    p, e, _, _, tick = prepare(f, job, ccfg, quality)
    e = e[(e.population == 'signal') & (e.reclaim_minutes == 60) & (e.week >= cfg['since_week'])].copy()
    sc = pd.read_csv(ROOT/cfg['scores']).set_index('event')
    e['event'] = e.trade_id.str.split(':').str[:3].str.join(':')
    e = e[e.event.isin(sc.index)].copy()
    e['pre'] = e.event.map(sc.causal); e['full'] = e.event.map(sc.stored)
    e['stop'] = stops(e, 'P', 30, tick)
    e['be_offset_bp'] = 0.
    rest = e[(e.pre >= cut) & (e.exit_mode == v3_mode(e.pre))].copy()
    rest['fill_close_exit'] = False
    rest['trade_id'] = rest.trade_id+':v_rest'
    chk = e[(e.pre >= cut) & (e.full < cut) & (e.exit_mode == v3_mode(e.pre))].copy()
    chk['fill_close_exit'] = True
    chk['trade_id'] = chk.trade_id+':v_rest_chk'
    late = e[(e.pre < cut) & (e.full >= cut) & (e.exit_mode == v3_mode(e.full))].copy()
    close = p.close.to_numpy()
    late['reclaim_ref'] = late.entry
    late['entry_i'] = late.touch_i_hint.astype(int)
    late['entry'] = close[late.entry_i.to_numpy()]
    late['risk'] = late.entry
    late['trade_id'] = late.trade_id+':v_late'
    tp, s, c = late.target_price.to_numpy(float), late.side.to_numpy(float), late.entry.to_numpy(float)
    past_target = np.isfinite(tp) & (s*(tp-c) < tick*(1-1e-7))
    past_stop = s*(c-late.stop.to_numpy(float)) <= 0
    short = late.entry_i.to_numpy() >= late.end_i.to_numpy()
    skips = dict(candidates=int(len(late)), past_target=int(past_target.sum()), past_backstop=int((past_stop & ~past_target).sum()),
                 no_room=int((short & ~past_target & ~past_stop).sum()))
    late = late[~(past_target | past_stop | short)]
    out = Path(exp)/intrabar.lower()/'symbols'/symbol
    out.mkdir(parents=True, exist_ok=True)
    frame = p.copy(); frame.index = frame.index+pd.Timedelta(minutes=job['execution_minutes'])
    policy = ExitPolicy(trail_arm_r=1e12, opposite_hour_exit=False, round_trip_cost=0, target_from_entry=True,
                        intrabar_path=intrabar, reclaim_next_bar=True, breakeven_includes_cost=False,
                        fill_maker_fee=ccfg['maker_fee'], fill_taker_fee=ccfg['taker_fee'], monotone_segments=True,
                        reclaim_backstop=True)
    parts, done = [], dict(symbol=symbol, intrabar=intrabar, late=skips)
    meta = ['event', 'pre', 'full', 'population', 'exit_mode']
    for mode, x in (('limit', pd.concat([rest, chk], ignore_index=True)), ('market', late.drop(columns=['fill_close_exit'], errors='ignore'))):
        if x.empty:
            done[mode] = dict(orders=0, positions=0)
            continue
        rows, _, audit = run_native(frame, np.full(len(frame), np.nan), np.zeros(len(frame)), x.drop(columns=meta), policy,
                                    tick=tick, symbol=symbol.removesuffix('USDT'), execution_minutes=job['execution_minutes'],
                                    entry_order_mode=mode, batch_bars=ccfg['streaming_batch_bars'])
        parts.append(rows[[k for k in KEEP if k in rows]].merge(x[['trade_id', 'pre', 'full']], on='trade_id'))
        done[mode] = dict(orders=len(x), positions=audit['positions'], fills=audit['fills'],
                          engine=audit.get('engine'), version=audit.get('version'))
    t = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=KEEP+['pre', 'full'])
    t['variant'] = t.trade_id.str.rsplit(':v_', n=1).str[-1]
    t.to_csv(out/'trades.csv.gz', index=False)
    done['seconds'] = time.perf_counter()-t0
    dump(out/'done.json', done)
    return done


def committed(*paths):
    for path in paths:
        if subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--', path], cwd=ROOT).returncode or \
           subprocess.run(['git', 'ls-files', '--error-unmatch', path], cwd=ROOT, capture_output=True).returncode:
            raise RuntimeError('Commit builder and config first')


def symbols(cfg):
    sc = pd.read_csv(ROOT/cfg['scores'])
    sc = sc[(sc.stored >= cfg['cut']) | (sc.causal >= cfg['cut'])]
    return sorted({e.split(':')[0] for e in sc.event if e.split(':')[1] >= cfg['since_week']})


def run(config, workers, intrabar):
    exp = config.resolve().parent
    cfg = json.loads(config.read_text())
    committed('yoyo/evaluation/monday_limit_layered.py', 'yoyo/layers/l3_backtest/nautilus_independent.py',
              str(config.resolve().relative_to(ROOT)))
    root = exp/intrabar.lower()
    todo = [s for s in symbols(cfg) if not (root/'symbols'/s/'done.json').exists()]
    with ProcessPoolExecutor(workers) as ex:
        futs = {ex.submit(process, s, intrabar, str(exp), cfg): s for s in todo}
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
