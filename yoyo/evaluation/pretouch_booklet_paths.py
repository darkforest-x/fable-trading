"""Strategy-booklet capital paths for the eight paper accounts on the frozen pre-touch score (owner 2026-10-31).

Owner: "能把触价前评分的几个策略的小册都写好吗". The in-app booklets show the lookahead score (the paper-account
rule). This exports one continuous path per account for the pre-touch score (A_rest_causal: the frozen model on the
5m bar before the touch, a limit resting at the 1.618) and, on the same rows, for the touch-bar score
(leaky_rest_touchbar), from the saved fills of exp-monday-limit-paper-accounts-20261028-v1. Admission is
monday_limit_paper_accounts.account_plans; plan sizes must equal that run's summary. Allocators, seeds and start
dates are those of strategy_booklet_paths. No new fills, scores or parameters.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.range_framework import ROOT
from yoyo.evaluation.strategy_booklet_paths import Trace, dated
from yoyo.evaluation.strategy_booklet_metrics import project
from yoyo.evaluation.m1_ranked_booklet import committed

A = ROOT/'experiments/active'


def rows(cfg, workers):
    from yoyo.evaluation.monday_limit_paper_accounts import load_symbol, symbols, scores, account_plans
    from yoyo.evaluation.monday_limit_be_scalein_compare import BAD_EVENTS
    src = ROOT/cfg['source_run']
    acfg = json.loads((src/'config.json').read_text())
    slip = acfg['slippage_bp']
    with ProcessPoolExecutor(workers) as ex:
        got = list(ex.map(load_symbol, [(str(src), s, slip['market_entry'], slip['non_target_exit']) for s in symbols(acfg)], chunksize=4))
    t = pd.concat([g[1] for g in got if len(g[1])], ignore_index=True)
    t = t[~t.event.isin(BAD_EVENTS)].copy()
    pre, full = scores(acfg)
    t['pre'] = t.event.map(pre); t['full'] = t.event.map(full)
    t['order'] = t.event.map(pd.read_csv(ROOT/acfg['features'], usecols=['event', 'l2_order_score']).set_index('event').l2_order_score)
    t['family'] = t.variant.str.split('_').str[0]
    t['kind'] = t.variant.str.split('_', n=1).str[1]
    plans = account_plans(t, acfg, {k: (['rest'], c) for k, c in cfg['arms'].items()})
    saved = json.loads((src/'summary.json').read_text())['results']['checks']['plan_rows']
    bad = {k: (len(v), saved.get(k)) for k, (_, v) in plans.items() if len(v) != saved.get(k)}
    if bad:
        raise RuntimeError(f'Plan rows differ from {src.name}: {bad}')
    return plans, [f'{g[0]}: {i}' for g in got for i in g[2]]


def allocate(x, p, trace):
    from yoyo.evaluation import monday_limit_return_opt as capital
    from yoyo.evaluation.monday_limit_spike_hw import slot_replay
    from yoyo.evaluation.monday_limit_langlang import path_run
    from yoyo.evaluation.monday_limit_reclaim_optimize import replay_w
    if p['allocator'] == 'fixed':
        x = x.sort_values('entry_ns', kind='stable').reset_index(drop=True).assign(entry=lambda y: y.entry_ns, exit=lambda y: y.exit_ns)
        return replay_w(x, np.full(len(x), p['fraction']), p['leverage'], trace=trace)[0]
    if p['allocator'] == 'linear':
        return capital.linear(x, x.k.to_numpy(), p['leverage'], p['seed'], trace=trace)
    if p['allocator'] == 'frac':
        return capital.frac(x, p['slots'], p['leverage'], p['seed'], trace=trace)
    if p['allocator'] == 'slots':
        x = x.sort_values(['symbol', 'entry_ns'], kind='stable').reset_index(drop=True)
        return slot_replay(x, p['slots'], p['leverage'], p['liq_pct'], p['seed'], trace=trace)[0]
    return path_run(x, 'allin', p['leverage'], p['seed'], trace=trace)


def run(config, workers):
    exp = config.resolve().parent
    cfg = json.loads(config.read_text())
    committed('yoyo/evaluation/pretouch_booklet_paths.py', 'yoyo/evaluation/monday_limit_paper_accounts.py',
              str(config.resolve().relative_to(ROOT)))
    plans, issues = rows(cfg, workers)
    manifest = dict(accounts={}, issues=issues[:20])
    for key, (_, x) in plans.items():
        account, arm = key.split('|')
        track = cfg['tracks'][account]
        p = cfg['paths'][track]
        x = dated(x.assign(symbol=x.event.str.split(':').str[0], tid=x.trade_id))
        x = x[x.entry_t >= pd.Timestamp(p['start'], tz='UTC')].reset_index(drop=True)
        trace = Trace(pd.Timestamp(p['start'], tz='UTC').value)
        result = allocate(x, p, trace)
        frame = pd.DataFrame(trace.rows)
        error = float(np.abs(frame.equity-(1000+frame.pnl.cumsum()+frame.cashflow.cumsum())).max())
        if error > 1e-5*max(1., frame.equity.max()):
            raise ValueError(f'{key}: cash-flow reconciliation {error}')
        name = f'{track}-{arm}'
        frame.to_csv(exp/f'{name}-ledger.csv.gz', index=False, compression='gzip')
        meta = dict(track=track, arm=arm, start=p['start'], end=str(x.exit_t.max()), seed=p.get('seed'), candidates=len(x),
                    funded=int((frame.notional > 0).sum()), allocator=p, allocator_result=result, reconciliation_error=error)
        data = project(frame, {**meta, 'note': ''})
        (exp/f'{name}-history.json').write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':'), allow_nan=False,
                                                           default=lambda v: v.item()))
        manifest['accounts'][name] = {**meta, 'summary': data['summary']}
        print(f"{name}: {meta['funded']} funded, end {data['summary']['end']:.2f}, pnl {data['summary']['pnl']:.2f}, "
              f"dd {data['summary']['max_drawdown_pct']:.1f}", flush=True)
    manifest.update(source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                    training_eligible=False, production_eligible=False)
    (exp/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=lambda v: v.item() if hasattr(v, 'item') else str(v)))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, required=True); ap.add_argument('--workers', type=int, default=6)
    a = ap.parse_args()
    run(a.config, a.workers)
