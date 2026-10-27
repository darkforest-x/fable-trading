"""Paper strategies v3add20, spike and allin3 with live-executable scores: frozen model vs causal refit (owner 2026-10-27).

Owner: "原来有很多策略啊 v3add20 spike 这两个也跑" and "还有allin3". Each strategy rests its limit on the score of the
5m close before the touch (layer A of monday_limit_layered), under two models, against the leaky backtest it was built on:
  leaky   frozen score reading the touch bar (the original backtests, unchanged ledgers);
  frozen  frozen model on features through the bar before the touch (what the paper engine computes today);
  refit   exp-monday-limit-score-causal-refit-20261027-v1 (trained before 2026; 2026 to date is out of sample).
Strategies (rules as their paper accounts, breakeven at the level for all three):
  v3add20  v3 (score >= 0.5; range 0.618 below 0.6, opposite 1618 from 0.6; -30 percent backstop) plus an equal-size add
           leg 5 percent beyond the level for score >= 0.6 sharing the parent's exits; margin = realized equity / 20 at 3x
           (monday_limit_return_opt.frac);
  spike    score >= 0.6 and 15m EMA order score >= 3 (causal: completed 15m bars before the touch), near Monday edge
           target, -18 percent backstop; 10 slots at 5x, 19 percent liquidation (monday_limit_spike_hw.slot_replay);
  allin3   score >= 0.6, opposite 1618 target, -30 percent backstop; one position at a time, all-in 3x, blow-up below
           100 USDT and a fresh 1000 (monday_limit_langlang.path_run 'allin', monthly starts as the pyramid comparison).
Native run (this file, both intrabar paths): for events where either causal score is >= 0.6, the v3 parent
(opposite 1618, P30), its add leg (v3_add5) and the spike order (near edge, P18). v3 rows with score 0.5-0.6 come from
the layered runs' `rest` rows (same orders). Leaky rows come from exp-monday-limit-be-scalein-20261026-v1 (v3_be0,
v3_add5) and exp-monday-limit-v3-grid-20261025-v1 (near_P18). Parity: every native row sharing a trade id with those
ledgers or the layered runs must have equal net. Summary is read-only arithmetic on fills: worse path per row, 10bp
slippage on non-target exits, the two bad source bars dropped, MAE as monday_limit_spike_opt.legs.
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
A = ROOT/'experiments/active'


def scores(cfg):
    fz = pd.read_csv(ROOT/cfg['frozen_scores']).set_index('event')
    rf = pd.read_csv(ROOT/cfg['refit_scores']).set_index('event')
    return pd.DataFrame(dict(leaky=fz.stored, frozen=fz.causal)).join(rf.causal.rename('refit'), how='outer')


def symbols(cfg):
    s = scores(cfg)
    hi = s[(s.frozen >= cfg['hi']) | (s.refit >= cfg['hi'])]
    return sorted({e.split(':')[0] for e in hi.index if e.split(':')[1] >= cfg['since_week']})


def process(symbol, intrabar, exp, cfg):
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
    sc = scores(cfg)
    e['event'] = e.trade_id.str.split(':').str[:3].str.join(':')
    e = e[e.event.isin(sc.index)].copy()
    e['pre_frozen'] = e.event.map(sc.frozen); e['pre_refit'] = e.event.map(sc.refit)
    e = e[(e.pre_frozen >= cfg['hi']) | (e.pre_refit >= cfg['hi'])]
    e['be_offset_bp'] = 0.
    par = e[e.exit_mode == 'opposite_1618'].copy()
    par['stop'] = stops(par, 'P', 30, tick)
    par['trade_id'] = par.trade_id+':v_v3_be0'
    child = par.copy()
    raw = child.entry*(1-child.side*cfg['add_pct']/100)
    child['entry'] = np.where(child.side > 0, np.floor(raw/tick+1e-7)*tick, np.ceil(raw/tick-1e-7)*tick)
    child['parent'] = par.trade_id
    child['reclaim_ref'] = par.entry
    child['trade_id'] = par.trade_id.str.replace(':v_v3_be0', f":v_v3_add{cfg['add_pct']}", regex=False)
    child = child[child.side*(child.entry-child.stop) > 0]
    spk = e[e.exit_mode == 'near_monday_edge'].copy()
    spk['stop'] = stops(spk, 'P', 18, tick)
    spk['trade_id'] = spk.trade_id+':v_spike'
    x = pd.concat([par, child, spk], ignore_index=True)
    out = Path(exp)/intrabar.lower()/'symbols'/symbol
    out.mkdir(parents=True, exist_ok=True)
    if x.empty:
        pd.DataFrame(columns=KEEP+['variant', 'score', 'pre_frozen', 'pre_refit']).to_csv(out/'trades.csv.gz', index=False)
        dump(out/'done.json', dict(symbol=symbol, intrabar=intrabar, orders=0, positions=0))
        return dict(symbol=symbol, orders=0)
    x['parent'] = x.parent.where(x.parent.notna(), None) if 'parent' in x else None
    if 'reclaim_ref' not in x:
        x['reclaim_ref'] = np.nan
    x['risk'] = x.entry
    frame = p.copy(); frame.index = frame.index+pd.Timedelta(minutes=job['execution_minutes'])
    policy = ExitPolicy(trail_arm_r=1e12, opposite_hour_exit=False, round_trip_cost=0, target_from_entry=True,
                        intrabar_path=intrabar, reclaim_next_bar=True, breakeven_includes_cost=False,
                        fill_maker_fee=ccfg['maker_fee'], fill_taker_fee=ccfg['taker_fee'], monotone_segments=True,
                        reclaim_backstop=True)
    meta = ['event', 'pre_frozen', 'pre_refit', 'population', 'exit_mode']
    rows, _, audit = run_native(frame, np.full(len(frame), np.nan), np.zeros(len(frame)), x.drop(columns=meta), policy,
                                tick=tick, symbol=symbol.removesuffix('USDT'), execution_minutes=job['execution_minutes'],
                                entry_order_mode='limit', batch_bars=ccfg['streaming_batch_bars'])
    rows = rows[[k for k in KEEP if k in rows]].merge(x[['trade_id', 'pre_frozen', 'pre_refit']], on='trade_id')
    rows['score'] = rows.pre_refit  # monday_limit_spike_opt.legs reads a score column; both scores are kept
    rows['variant'] = rows.trade_id.str.rsplit(':v_', n=1).str[-1]
    rows.to_csv(out/'trades.csv.gz', index=False)
    done = dict(symbol=symbol, intrabar=intrabar, orders=len(x), positions=audit['positions'], fills=audit['fills'],
                cancelled=sum(o['status'] == 'parent_closed' for o in audit.get('opportunities', [])),
                engine=audit.get('engine'), version=audit.get('version'), seconds=time.perf_counter()-t0)
    dump(out/'done.json', done)
    return done


def committed(*paths):
    for path in paths:
        if subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--', path], cwd=ROOT).returncode or \
           subprocess.run(['git', 'ls-files', '--error-unmatch', path], cwd=ROOT, capture_output=True).returncode:
            raise RuntimeError('Commit builder and config first')


def run(config, workers, intrabar):
    exp = config.resolve().parent
    cfg = json.loads(config.read_text())
    committed('yoyo/evaluation/monday_limit_strategies_causal.py', 'yoyo/layers/l3_backtest/nautilus_independent.py',
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
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, required=True); ap.add_argument('--workers', type=int, default=3)
    ap.add_argument('--intrabar', choices=['OHLC', 'OLHC']); ap.add_argument('--summary', action='store_true')
    a = ap.parse_args()
    run(a.config, a.workers, a.intrabar)
