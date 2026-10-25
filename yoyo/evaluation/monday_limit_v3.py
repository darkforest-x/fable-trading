"""Framework replay of score-dependent targets and backstops for the Monday1618 limit-reclaim rule (v3 candidate, owner2026-10-25).

Owner asked to settle a v3 plan: entry score threshold (0.226, optionally 0.5),
near Monday edge target below score 0.6 and opposite 1618 from 0.6, a backstop
per score band, linear sizing in score. The pieces were measured separately;
this runs the whole rule natively once so their interaction is in the fills.
Codex's frozen signal orders (alts, 60m confirmation, weeks from since_week,
fees from the Codex config) with frozen entry score at or above the top-40
threshold. Each variant maps score bands (lower edge -> target mode, backstop
kind, backstop value; kinds as in monday_limit_backstop.stops) to one
independent HEDGING position per event; variant "current" (near target,
'far' backstop) must reproduce the existing ledgers (parity). Scores come from
scores.csv.gz next to the config, written once from the frozen model.
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


def write_scores(exp):
    from yoyo.evaluation import monday_limit_reclaim_allexits as ax
    score, cut = ax.scores()
    pd.DataFrame(dict(event=score.index, score=score.to_numpy())).to_csv(exp/'scores.csv.gz', index=False)
    dump(exp/'scores.json', dict(threshold=cut, n=len(score), model='experiments/active/exp-monday-limit-reclaim-recent-20261025-v1/score_model.json'))


def process(symbol, intrabar, exp, variants, since, threshold):
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
    e = e[(e.population == 'signal') & (e.reclaim_minutes == 60) & (e.week >= since)].copy()
    sc = pd.read_csv(Path(exp)/'scores.csv.gz').set_index('event').score
    e['event'] = e.trade_id.str.split(':').str[:3].str.join(':')
    e['score'] = e.event.map(sc)
    e = e[e.score >= threshold]
    out = Path(exp)/intrabar.lower()/'symbols'/symbol
    out.mkdir(parents=True, exist_ok=True)
    parts = []
    for name, bands in variants.items():
        lows = np.array([b[0] for b in bands])
        which = np.searchsorted(lows, e.score.to_numpy(), side='right')-1
        for k, (low, mode, kind, value) in enumerate(bands):
            x = e[(which == k) & (e.exit_mode == mode).to_numpy()].copy()
            if x.empty:
                continue
            x['stop'] = stops(x, kind, value, tick)
            x['trade_id'] = x.trade_id+':v_'+name
            parts.append(x)
    if not parts:
        pd.DataFrame(columns=KEEP+['variant', 'score']).to_csv(out/'trades.csv.gz', index=False)
        dump(out/'done.json', dict(symbol=symbol, intrabar=intrabar, orders=0, positions=0))
        return dict(symbol=symbol, orders=0)
    e = pd.concat(parts, ignore_index=True)
    frame = p.copy(); frame.index = frame.index+pd.Timedelta(minutes=job['execution_minutes'])
    policy = ExitPolicy(trail_arm_r=1e12, opposite_hour_exit=False, round_trip_cost=0, target_from_entry=True,
                        intrabar_path=intrabar, reclaim_next_bar=True, breakeven_includes_cost=False,
                        fill_maker_fee=cfg['maker_fee'], fill_taker_fee=cfg['taker_fee'], monotone_segments=True,
                        reclaim_backstop=True)
    rows, _, audit = run_native(frame, np.full(len(frame), np.nan), np.zeros(len(frame)), e.drop(columns=['event', 'score']), policy,
                                tick=tick, symbol=symbol.removesuffix('USDT'), execution_minutes=job['execution_minutes'],
                                entry_order_mode='limit', batch_bars=cfg['streaming_batch_bars'])
    rows = rows[[c for c in KEEP if c in rows]].merge(e[['trade_id', 'score']], on='trade_id')
    rows['variant'] = rows.trade_id.str.rsplit(':v_', n=1).str[-1]
    rows.to_csv(out/'trades.csv.gz', index=False)
    done = dict(symbol=symbol, intrabar=intrabar, orders=len(e), positions=audit['positions'], fills=audit['fills'],
                engine=audit.get('engine'), version=audit.get('version'), seconds=time.perf_counter()-t0)
    dump(out/'done.json', done)
    return done


def run(config, workers, intrabar):
    exp = config.resolve().parent
    cfg = json.loads(config.read_text())
    for path in ('yoyo/evaluation/monday_limit_v3.py', str(config.resolve().relative_to(ROOT))):
        if subprocess.run(['git','diff','--quiet','HEAD','--',path], cwd=ROOT).returncode or \
           subprocess.run(['git','ls-files','--error-unmatch',path], cwd=ROOT, capture_output=True).returncode:
            raise RuntimeError('Commit builder and config first')
    if not (exp/'scores.csv.gz').exists():
        write_scores(exp)
    threshold = json.loads((exp/'scores.json').read_text())['threshold']
    root = exp/intrabar.lower()
    todo = [s for s in cfg['symbols'] if not (root/'symbols'/s/'done.json').exists()]
    with ProcessPoolExecutor(workers) as ex:
        futs = {ex.submit(process, s, intrabar, str(exp), cfg['variants'], cfg['since_week'], threshold): s for s in todo}
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
