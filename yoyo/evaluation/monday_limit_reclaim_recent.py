"""Last five months of the Monday1618 limit-reclaim v2 rule, and the frozen entry score (owner2026-10-25).

Owner: "回测一下近5个月的". Uses the same inputs and worst-path convention as
monday_limit_reclaim_optimize (Codex O-H-L-C ledger and the monotone O-L-H-C v2
replay, alts, 60m confirmation, near Monday edge target, 10bp slippage on
non-target exits). The entry score is refitted exactly as there (logistic
regression on fills before 2025-01-01, win = worst net > 0) and frozen to
score_model.json (scaler means/scales, coefficients, intercept, top-40 percent
threshold from training scores) for the paper-trading monitor. Window: fills
from five months before the last bar of the Codex inputs. Reports all trades
versus the top-40 percent score filter, by month, and 1000 USDT replays (10
percent per trade, gross <= 1x). Read-only arithmetic on framework fills.
"""
from __future__ import annotations
import json
import subprocess
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation.monday_limit_reclaim_patterns import SPLIT
from yoyo.evaluation import monday_limit_reclaim_optimize as opt

EXP = ROOT/'experiments/active/exp-monday-limit-reclaim-recent-20261025-v1'


def run():
    p = 'yoyo/evaluation/monday_limit_reclaim_recent.py'
    if subprocess.run(['git','diff','--quiet','HEAD','--',p], cwd=ROOT).returncode or \
       subprocess.run(['git','ls-files','--error-unmatch',p], cwd=ROOT, capture_output=True).returncode:
        raise RuntimeError('Commit builder first')
    cfg = json.loads((EXP/'config.json').read_text())
    o, l, extra = opt.tp100_from_ledgers()
    for d in (o, l):
        d['net_s'] = d.net_notional_bp-np.where(d.reason == 4, 0., opt.SLIP)
        d['entry_ns'] = pd.to_datetime(d.entry_time, utc=True).astype('int64'); d['exit_ns'] = pd.to_datetime(d.exit_time, utc=True).astype('int64')
    m = o.merge(l[['trade_id', 'net_s', 'entry_ns', 'exit_ns', 'reason']], on='trade_id', suffixes=('_o', '_l'))
    worse = m.net_s_l < m.net_s_o
    m['net'] = np.where(worse, m.net_s_l, m.net_s_o)
    m['entry'] = np.where(worse, m.entry_ns_l, m.entry_ns_o); m['exit'] = np.where(worse, m.exit_ns_l, m.exit_ns_o)
    m = m.set_index('trade_id')
    meta = extra.set_index('trade_id').reindex(m.index)
    ev = pd.read_csv(opt.A/'exp-monday-limit-reclaim-patterns3-20261024-v1/events_60m.csv.gz')
    ev['trade_id'] = ev.symbol+':'+ev.week+':'+np.where(ev.side_n == 1, '+1', '-1')+':m60:near_monday_edge:c0'
    s = m.join(ev.set_index('trade_id')[['vol_ratio', 'approach_min', 'opp_first', 'monday_close_side', 'liquidity', 'toward24', 'cluster24']], how='inner')
    s = s.join(meta[['bucket', 'range_pct']])
    s['log_vol_ratio'] = np.log(s.vol_ratio.clip(lower=.05)); s['log_approach'] = np.log1p(s.approach_min.clip(lower=0))
    s['opp_first'] = (s.opp_first == '是').astype(int); s['log_liquidity'] = np.log10(s.liquidity.clip(lower=1e4))
    s = s.dropna(subset=opt.FEATS)
    s['t'] = pd.to_datetime(s.entry_time, utc=True); s['y'] = (s.net > 0).astype(int)
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    tr = s[s.t < SPLIT]
    model = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000)).fit(tr[opt.FEATS], tr.y)
    tr_score = model.predict_proba(tr[opt.FEATS])[:, 1]
    cut = float(np.quantile(tr_score, .6))
    sc, lr = model[0], model[-1]
    frozen = dict(features=opt.FEATS, mean=sc.mean_.tolist(), scale=sc.scale_.tolist(), coef=lr.coef_[0].tolist(),
                  intercept=float(lr.intercept_[0]), top40_threshold=cut, trained_before=str(SPLIT), train_n=len(tr),
                  feature_notes=dict(bucket='floor(log2(mean 14-bar TR / close of the 5m bar before the touch)*4)',
                                     log_vol_ratio='log of last-hour quote volume / median hourly quote volume of the prior 7 days',
                                     log_approach='log1p(minutes from the first post-Monday cross of the near Monday edge to the touch bar)',
                                     opp_first='1 if the opposite 1.618 of the same symbol-week filled earlier',
                                     monday_close_side='Monday close position, 0 = edge away from this touch, 1 = edge this touch extends',
                                     log_liquidity='log10 mean daily quote volume of the prior 7 days (USDT)',
                                     toward24='percent move toward the level over the prior 24h',
                                     cluster24='other symbols filling the same side in the prior 24h',
                                     side='1 long, -1 short', range_pct='Monday range / level, percent'))
    EXP.mkdir(exist_ok=True)
    dump(EXP/'score_model.json', frozen)
    s['score'] = model.predict_proba(s[opt.FEATS])[:, 1]
    last = pd.to_datetime(s.exit_time, utc=True).max()
    start = last-pd.DateOffset(months=5)
    rec = s[s.t >= start].copy().reset_index()
    rec['top40'] = rec.score >= cut
    out = dict(window=[str(start), str(last)])
    for name, g in (('all', rec), ('top40', rec[rec.top40])):
        r, _ = opt.replay_w(g.reset_index(drop=True), np.full(len(g), .10), 1.)
        out[name] = dict(n=len(g), win_pct=float(100*(g.net > 0).mean()), mean_bp=float(g.net.mean()),
                         median_bp=float(g.net.median()), replay_k10_g1=r,
                         by_month={str(k): dict(n=len(x), win_pct=float(100*(x.net > 0).mean()), mean_bp=float(x.net.mean()))
                                   for k, x in g.groupby(g.t.dt.strftime('%Y-%m'))},
                         by_side={str(k): dict(n=len(x), mean_bp=float(x.net.mean())) for k, x in g.groupby('side')})
    dump(EXP/'summary.json', dict(config=cfg, results=out, score_model='score_model.json',
         source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(), training_eligible=False, production_eligible=False))
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    run()
