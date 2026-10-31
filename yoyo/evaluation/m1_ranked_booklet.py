"""Strategy-booklet capital paths for the 1m rank-mapped entry score (owner 2026-10-31).

Owner: "写个策略小册我看看 要和之前一样". The 1m retest (monday_limit_1m_account_label) reported medians
over many orderings. A booklet shows one continuous path instead. This exporter rebuilds the same account
plans from the same label-run fills (worse path, slippage, fees), for the m1_ranked arm and, for reference,
the frozen pre-touch arm. It runs the booklet allocators with the seeds and start dates of
strategy_booklet_paths, then projects each ledger with strategy_booklet_metrics.project. No new fills, no
refit, no parameter choice; scores are read from scores_arms.csv.gz.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.range_framework import ROOT
from yoyo.evaluation.strategy_booklet_paths import Trace, dated
from yoyo.evaluation.strategy_booklet_metrics import project

A = ROOT/'experiments/active'


def committed(*paths):
    for p in paths:
        if subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--', p], cwd=ROOT).returncode or \
                subprocess.run(['git', 'ls-files', '--error-unmatch', p], cwd=ROOT, capture_output=True).returncode:
            raise RuntimeError(f'Commit {p} before exporting')


def plans(cfg, workers):
    """Account rows per arm, exactly as monday_limit_1m_account_label.summarize admits them."""
    from yoyo.evaluation.monday_limit_1m_account_label import labels
    from yoyo.evaluation.monday_limit_paper_accounts import V3MODE
    from yoyo.evaluation.monday_limit_be_scalein_compare import BAD_EVENTS
    src = json.loads((ROOT/cfg['source_experiment']).read_text())
    acfg = json.loads((ROOT/src['accounts_config']).read_text())
    t, issues = labels(src, workers)
    t = t[~t.event.isin(BAD_EVENTS)].copy()
    sc = pd.read_csv((ROOT/cfg['source_experiment']).parent/'scores_arms.csv.gz').set_index('event')
    sc = sc.join(pd.read_csv(ROOT/src['frozen_scores']).set_index('event').causal.rename('frozen_full'), how='outer')
    t['order'] = t.event.map(pd.read_csv(ROOT/acfg['features'], usecols=['event', 'l2_order_score']).set_index('event').l2_order_score)
    sz, cut, hi = acfg['v3_sizing'], acfg['cut'], acfg['hi']
    out = {}
    for arm, (c3, cs) in cfg['arms'].items():
        s3, ss = t.event.map(sc[c3]), t.event.map(sc[cs])
        v3 = t[(t.kind == 'v3') & (s3 >= cut) & (t['mode'] == V3MODE(s3, hi))].copy()
        v3['k'] = np.minimum(sz['fraction']*(s3[v3.index]-sz['cut'])/sz['mean_excess'], sz['cap'])
        legs = t[(t.kind == 'v3_add') & t.base.isin(v3.trade_id) & (s3 >= hi)]
        legs = legs.assign(k=legs.base.map(v3.set_index('trade_id').k))
        out[arm] = dict(v3=v3, v3add20=pd.concat([v3, legs]),
                        spike=t[(t.kind == 'spike') & (ss >= hi) & (t.order >= acfg['min_order'])],
                        allin3=t[(t.kind == 'v3') & (s3 >= hi) & (t['mode'] == 'opposite_1618')])
    return out, issues


def allocate(track, x, p, trace):
    from yoyo.evaluation import monday_limit_return_opt as capital
    from yoyo.evaluation.monday_limit_spike_hw import slot_replay
    from yoyo.evaluation.monday_limit_langlang import path_run
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
    committed('yoyo/evaluation/m1_ranked_booklet.py', str(config.resolve().relative_to(ROOT)))
    rows, issues = plans(cfg, workers)
    manifest = dict(accounts={}, label_issues=issues[:20])
    for arm, accounts in rows.items():
        for track, x in accounts.items():
            p = cfg['paths'][track]
            x = dated(x.assign(symbol=x.event.str.split(':').str[0], tid=x.trade_id))
            x = x[x.entry_t >= pd.Timestamp(p['start'], tz='UTC')].reset_index(drop=True)
            trace = Trace(pd.Timestamp(p['start'], tz='UTC').value)
            result = allocate(track, x, p, trace)
            frame = pd.DataFrame(trace.rows)
            error = float(np.abs(frame.equity-(1000+frame.pnl.cumsum()+frame.cashflow.cumsum())).max())
            if error > 1e-5*max(1., frame.equity.max()):
                raise ValueError(f'{arm} {track}: cash-flow reconciliation {error}')
            frame.to_csv(exp/f'{track}-{arm}-ledger.csv.gz', index=False, compression='gzip')
            meta = dict(track=track, arm=arm, start=p['start'], end=str(x.exit_t.max()), seed=p['seed'], candidates=len(x),
                        funded=int((frame.notional > 0).sum()), allocator=p, allocator_result=result, reconciliation_error=error)
            data = project(frame, {**meta, 'note': ''})
            (exp/f'{track}-{arm}-history.json').write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':'), allow_nan=False,
                                                                       default=lambda v: v.item()))
            manifest['accounts'][f'{track}|{arm}'] = {**meta, 'summary': data['summary']}
            print(f"{track} {arm}: {meta['funded']} funded, end {data['summary']['end']:.2f}, dd {data['summary']['max_drawdown_pct']:.1f}", flush=True)
    manifest.update(source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                    training_eligible=False, production_eligible=False)
    (exp/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=lambda v: v.item() if hasattr(v, 'item') else str(v)))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, required=True); ap.add_argument('--workers', type=int, default=6)
    a = ap.parse_args()
    run(a.config, a.workers)
