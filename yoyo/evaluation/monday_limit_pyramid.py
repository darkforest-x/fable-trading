"""Pyramiding (滚仓) for the high-payoff Monday1618 limit-reclaim version, native replay (owner 2026-10-27).

Owner: "你知道滚仓吗，这个可不可行 ... 反正你想尽一切办法去做吧". Codex's frozen signal orders (alts, 60m confirmation,
weeks from since_week), frozen entry score >= min_score. Parent: limit at the Monday 1.618, -30 percent backstop until
the reclaim, breakeven at entry +17bp after it. Pyramid legs (adapter entry column pyramid_parent) are equal-size stop
entries armed at the successful reclaim; each fill moves the stop of all filled legs to the previous key level.
Levels for a long (mirrored for a short): L / H Monday low / high, W = H - L,
  near = L, mid = L + 0.5W, far = H, opp1618 = H + 0.618W, opp2618 = H + 1.618W.
Variants:
  base_opp1618  parent only, target opp1618 (v3's high-score target);
  pyr3_opp1618  + legs at near / mid / far, ratchets entry / near / mid, target opp1618;
  base_opp2618  parent only, target opp2618;
  pyr4_opp2618  + legs at near / mid / far / opp1618, ratchets entry / near / mid / far, target opp2618.
Triggers and targets round away from the entry, ratchets toward it (tick). Events whose opp2618 is not positive skip
the 2618 variants. One native run per symbol and intrabar path, monotone segments.
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
from yoyo.evaluation.monday_limit_backstop import CODEX, stops

KEEP = ['trade_id', 'side', 'entry', 'stop', 'target_price', 'exit_price', 'reason', 'entry_time', 'exit_time', 'entry_i',
        'exit_i', 'reclaim_status', 'net_notional_bp', 'pyramid_parent', 'pyramid_step', 'trigger', 'ratchet']


def away(x, side, tick):
    return math.ceil(x/tick-1e-7)*tick if side > 0 else math.floor(x/tick+1e-7)*tick


def toward(x, side, tick):
    return math.floor(x/tick+1e-7)*tick if side > 0 else math.ceil(x/tick-1e-7)*tick


def legs_for(r, variant, tick):
    side, L, H = int(r.side), float(r.monday_low), float(r.monday_high)
    W = H-L
    near, mid, far = (L, L+.5*W, H) if side > 0 else (H, H-.5*W, L)
    opp1618, opp2618 = (H+.618*W, H+1.618*W) if side > 0 else (L-.618*W, L-1.618*W)
    if variant.endswith('2618'):
        target = away(opp2618, side, tick)
        steps = [(near, r.entry), (mid, near), (far, mid), (opp1618, far)] if variant.startswith('pyr') else []
    else:
        target = float(r.target_price)
        steps = [(near, r.entry), (mid, near), (far, mid)] if variant.startswith('pyr') else []
    return target, [(away(tr, side, tick), toward(ra, side, tick)) for tr, ra in steps]


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
    e = e[(e.population == 'signal') & (e.reclaim_minutes == 60) & (e.week >= cfg['since_week']) & (e.exit_mode == 'opposite_1618')].copy()
    sc = pd.read_csv(Path(exp)/'scores.csv.gz').set_index('event').score
    e['event'] = e.trade_id.str.split(':').str[:3].str.join(':')
    e['score'] = e.event.map(sc)
    e = e[e.score >= threshold]
    out = Path(exp)/intrabar.lower()/'symbols'/symbol
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for r in e.itertuples():
        stop = float(stops(pd.DataFrame([r._asdict()]), 'P', cfg['backstop_pct'], tick)[0])
        for variant in cfg['variants']:
            target, steps = legs_for(r, variant, tick)
            if not target > 0 or int(r.side)*(target-r.entry) < tick:
                continue
            pid = f'{r.trade_id}:v_{variant}'
            base = {k: getattr(r, k) for k in ('side', 'entry_i', 'expire_i', 'end_i', 'reclaim_minutes', 'score', 'event')}
            rows.append(dict(base, trade_id=pid, entry=r.entry, risk=r.entry, stop=stop, target_price=target,
                             be_offset_bp=cfg['be_offset_bp'], pyramid_parent=None, pyramid_step=0, ratchet=np.nan))
            for k, (trigger, ratchet) in enumerate(steps, 1):
                if int(r.side)*(target-trigger) < tick:
                    break
                rows.append(dict(base, trade_id=f'{pid}:pyr{k}', entry=trigger, risk=trigger, stop=stop, target_price=target,
                                 be_offset_bp=cfg['be_offset_bp'], pyramid_parent=pid, pyramid_step=k, ratchet=ratchet))
    if not rows:
        pd.DataFrame(columns=KEEP+['variant', 'score', 'event']).to_csv(out/'trades.csv.gz', index=False)
        dump(out/'done.json', dict(symbol=symbol, intrabar=intrabar, orders=0, positions=0))
        return dict(symbol=symbol, orders=0)
    x = pd.DataFrame(rows)
    frame = p.copy(); frame.index = frame.index+pd.Timedelta(minutes=job['execution_minutes'])
    policy = ExitPolicy(trail_arm_r=1e12, opposite_hour_exit=False, round_trip_cost=0, target_from_entry=True,
                        intrabar_path=intrabar, reclaim_next_bar=True, breakeven_includes_cost=False,
                        fill_maker_fee=ccfg['maker_fee'], fill_taker_fee=ccfg['taker_fee'], monotone_segments=True,
                        reclaim_backstop=True)
    res, _, audit = run_native(frame, np.full(len(frame), np.nan), np.zeros(len(frame)), x.drop(columns=['event', 'score']), policy,
                               tick=tick, symbol=symbol.removesuffix('USDT'), execution_minutes=job['execution_minutes'],
                               entry_order_mode='limit', batch_bars=ccfg['streaming_batch_bars'])
    # trigger is set only when a pyramid leg fills; keep a fixed schema per symbol
    res = res.reindex(columns=KEEP).merge(x[['trade_id', 'score', 'event']], on='trade_id')
    res['variant'] = res.trade_id.str.split(':v_').str[-1].str.split(':pyr').str[0]
    res.to_csv(out/'trades.csv.gz', index=False)
    opp = pd.DataFrame(audit.get('opportunities', []))
    done = dict(symbol=symbol, intrabar=intrabar, orders=len(x), positions=audit['positions'], fills=audit['fills'],
                opportunity_status=opp.status.value_counts().to_dict() if len(opp) else {},
                engine=audit.get('engine'), version=audit.get('version'), seconds=time.perf_counter()-t0)
    dump(out/'done.json', done)
    return done


def run(config, workers, intrabar):
    exp = config.resolve().parent
    cfg = json.loads(config.read_text())
    for path in ('yoyo/evaluation/monday_limit_pyramid.py', 'yoyo/layers/l3_backtest/nautilus_independent.py', str(config.resolve().relative_to(ROOT))):
        if subprocess.run(['git','diff','--quiet','HEAD','--',path], cwd=ROOT).returncode or \
           subprocess.run(['git','ls-files','--error-unmatch',path], cwd=ROOT, capture_output=True).returncode:
            raise RuntimeError('Commit builder, adapter and config first')
    threshold = max(json.loads((exp/'scores.json').read_text())['threshold'], cfg['min_score'])
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
