"""Causal refit of the Monday1618 entry score: the three bar features read the bar before the touch (owner 2026-10-27).

Owner: "用不看触及K线的特征重新拟合评分模型，然后在框架里重测 A 和 ABC" and "注意不要影响了之前的". The frozen model
(exp-monday-limit-reclaim-recent-20261025-v1/score_model.json) stays untouched; everything here is written to this
experiment's directory. The fit repeats monday_limit_reclaim_recent exactly: v2 near-edge 60m fills (Codex O-H-L-C
ledger and the monotone O-L-H-C replay, worse path, 10bp slippage on non-target exits), win = net > 0, standardized
logistic regression (sklearn defaults) on fills before 2025-01-01, the same ten features. Only vol_ratio, liquidity and
toward24 change: computed through the bar before the touch (monday_limit_score_leak_audit.bar_features, 'causal').
Control: the same procedure with the touch-bar values ('ledger') must reproduce the frozen coefficients.
Scores for every signal event (as the leak audit): `causal` = refit model on features through the bar before the touch
(known while an order rests), `stored` = refit model on features through the touch bar (known at its close). The
column names match what monday_limit_layered reads. Validation on fills from 2025: AUC with a label-permutation p,
top-decile mean net, against the frozen model on leaky and on causal features.
"""
from __future__ import annotations
import argparse
import json
import subprocess
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation.monday_limit_reclaim_patterns import SPLIT, SNAP
from yoyo.evaluation.monday_limit_be_scalein_compare import clean
from yoyo.evaluation import monday_limit_reclaim_allexits as ax
from yoyo.evaluation import monday_limit_reclaim_optimize as opt
from yoyo.evaluation.monday_limit_score_leak_audit import bar_features

BAR = ('vol_ratio', 'liquidity', 'toward24')


def with_bar(s, tag):
    """Model features with the three bar features taken from one bar ('ledger' = touch bar, 'causal' = bar before)."""
    g = s.copy()
    g['log_vol_ratio'] = np.log(g[f'vol_ratio_{tag}'].clip(lower=.05)); g['log_liquidity'] = np.log10(g[f'liquidity_{tag}'].clip(lower=1e4))
    g['toward24'] = g[f'toward24_{tag}']
    return g


def frozen_score(spec, g):
    X = g[spec['features']].to_numpy(float)
    z = ((X-np.array(spec['mean']))/np.array(spec['scale'])*np.array(spec['coef'])).sum(1)+spec['intercept']
    return 1/(1+np.exp(-z))


def auc(y, p):
    from sklearn.metrics import roc_auc_score
    return float(roc_auc_score(y, p))


def run(config):
    exp = config.resolve().parent
    for p in ('yoyo/evaluation/monday_limit_score_causal_refit.py', str(config.resolve().relative_to(ROOT))):
        if subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--', p], cwd=ROOT).returncode or \
           subprocess.run(['git', 'ls-files', '--error-unmatch', p], cwd=ROOT, capture_output=True).returncode:
            raise RuntimeError('Commit builder and config first')
    cfg = json.loads(config.read_text())
    old = json.loads((ROOT/cfg['frozen_model']).read_text())
    syms = [r['symbol'] for r in json.loads(SNAP.read_text())['studies']['limit-reclaim']]
    with ProcessPoolExecutor(cfg['workers']) as ex:
        x = pd.concat([d for d in ex.map(bar_features, syms, chunksize=4) if len(d)], ignore_index=True).set_index('event')
    # Training frame exactly as monday_limit_reclaim_recent.run.
    o, l, extra = opt.tp100_from_ledgers()
    for d in (o, l):
        d['net_s'] = d.net_notional_bp-np.where(d.reason == 4, 0., opt.SLIP)
    m = o.merge(l[['trade_id', 'net_s']], on='trade_id', suffixes=('_o', '_l'))
    m['net'] = np.where(m.net_s_l < m.net_s_o, m.net_s_l, m.net_s_o)
    m = m.set_index('trade_id')
    meta = extra.set_index('trade_id').reindex(m.index)
    ev = pd.read_csv(ROOT/cfg['events'])
    ev['trade_id'] = ev.symbol+':'+ev.week+':'+np.where(ev.side_n == 1, '+1', '-1')+':m60:near_monday_edge:c0'
    s = m.join(ev.set_index('trade_id')[['vol_ratio', 'approach_min', 'opp_first', 'monday_close_side', 'liquidity', 'toward24', 'cluster24']], how='inner')
    s = s.join(meta[['bucket', 'range_pct']])
    s['log_approach'] = np.log1p(s.approach_min.clip(lower=0)); s['opp_first'] = (s.opp_first == '是').astype(int)
    s['event'] = s.index.str.split(':').str[:3].str.join(':')
    s = s.join(x, on='event')
    feature_parity = {k: float((s[k]-s[f'{k}_ledger']).abs().max()) for k in BAR}
    s['t'] = pd.to_datetime(s.entry_time, utc=True); s['y'] = (s.net > 0).astype(int)
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    fits, frames = {}, {}
    for tag in ('ledger', 'causal'):
        g = with_bar(s, tag).dropna(subset=opt.FEATS)
        tr = g[g.t < SPLIT]
        fits[tag] = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000)).fit(tr[opt.FEATS], tr.y)
        frames[tag] = g
    lg = fits['ledger']
    control = dict(train_n=int((frames['ledger'].t < SPLIT).sum()), frozen_train_n=old['train_n'],
                   coef_max_abs_diff=float(np.abs(lg[-1].coef_[0]-np.array(old['coef'])).max()),
                   intercept_diff=float(lg[-1].intercept_[0]-old['intercept']),
                   mean_max_abs_diff=float(np.abs(lg[0].mean_-np.array(old['mean'])).max()), feature_parity=feature_parity)
    model = fits['causal']
    g = frames['causal']
    tr, va = g[g.t < SPLIT], g[g.t >= SPLIT]
    sc, lr = model[0], model[-1]
    cut40 = float(np.quantile(model.predict_proba(tr[opt.FEATS])[:, 1], .6))
    notes = dict(old['feature_notes'])
    for k, txt in (('log_vol_ratio', 'log of quote volume of the hour ending at the close of the 5m bar before the touch / median hourly quote volume of the prior 7 days'),
                   ('log_liquidity', 'log10 mean daily quote volume of the 7 days ending at the close of the bar before the touch (USDT)'),
                   ('toward24', 'percent move toward the level over the 24h ending at the close of the bar before the touch')):
        notes[k] = txt
    dump(exp/'score_model_causal.json', dict(features=opt.FEATS, mean=sc.mean_.tolist(), scale=sc.scale_.tolist(),
         coef=lr.coef_[0].tolist(), intercept=float(lr.intercept_[0]), top40_threshold=cut40, trained_before=str(SPLIT),
         train_n=int(len(tr)), feature_notes=notes, frozen_model_untouched=cfg['frozen_model']))
    # Validation on 2025+ fills: refit causal vs frozen model on causal and on leaky features.
    rng = np.random.default_rng(cfg['seed'])
    p_new = model.predict_proba(va[opt.FEATS])[:, 1]
    p_old_causal = frozen_score(old, va)
    p_old_leaky = frozen_score(old, frames['ledger'].loc[va.index])
    a_new = auc(va.y, p_new)
    perm = np.array([auc(rng.permutation(va.y.to_numpy()), p_new) for _ in range(cfg['permutations'])])

    def top(p, q):
        k = p >= np.quantile(p, q)
        return dict(n=int(k.sum()), mean_bp=float(va.net[k].mean()), win_pct=float(100*(va.net[k] > 0).mean()))
    val = dict(n=int(len(va)), base_mean_bp=float(va.net.mean()), base_win_pct=float(100*va.y.mean()),
               refit_causal=dict(auc=a_new, perm_p=float((1+(perm >= a_new).sum())/(1+len(perm))), top10=top(p_new, .9),
                                 ge_05=int((p_new >= .5).sum()), coef=dict(zip(opt.FEATS, lr.coef_[0].round(4).tolist()))),
               frozen_causal=dict(auc=auc(va.y, p_old_causal), top10=top(p_old_causal, .9), ge_05=int((p_old_causal >= .5).sum())),
               frozen_leaky=dict(auc=auc(va.y, p_old_leaky), top10=top(p_old_leaky, .9), ge_05=int((p_old_leaky >= .5).sum())))
    # Scores for every signal event (same frame as the leak audit).
    ae = pd.read_csv(ROOT/cfg['events'])
    ae['event'] = ae.symbol+':'+ae.week+':'+np.where(ae.side_n == 1, '+1', '-1')
    v2 = sorted(d.name for d in ax.V2.iterdir() if (d/'done.json').exists())
    with ProcessPoolExecutor(cfg['workers']) as ex:
        ex2 = pd.concat(list(ex.map(opt.ledger_extra, v2, chunksize=8)), ignore_index=True)
    ex2['event'] = ex2.trade_id.str.split(':').str[:3].str.join(':')
    e = ae.set_index('event').join(ex2.set_index('event')[['bucket', 'range_pct']], how='inner').join(x, how='inner')
    e['side'] = e.side_n.astype(float); e['log_approach'] = np.log1p(e.approach_min.clip(lower=0))
    e['opp_first'] = (e.opp_first == '是').astype(int)
    out = pd.DataFrame({'stored': model.predict_proba(with_bar(e, 'ledger')[opt.FEATS])[:, 1],
                        'causal': model.predict_proba(with_bar(e, 'causal')[opt.FEATS])[:, 1]}, index=e.index)
    prev = pd.read_csv(ROOT/cfg['previous_scores']).set_index('event')
    out = out.join(prev[['stored', 'causal']].rename(columns={'stored': 'frozen_stored', 'causal': 'frozen_causal'}), how='left')
    out.reset_index().to_csv(exp/'scores_refit.csv.gz', index=False)
    bands = {str(c): dict(refit_causal=int((out.causal >= c).sum()), refit_touch=int((out.stored >= c).sum()),
                          frozen_causal=int((out.frozen_causal >= c).sum()), frozen_touch=int((out.frozen_stored >= c).sum()),
                          refit_causal_and_frozen_causal=int(((out.causal >= c) & (out.frozen_causal >= c)).sum()))
             for c in cfg['cuts']}
    res = clean(dict(control=control, events=int(len(out)), validation_2025=val, bands=bands, top40_threshold=cut40,
                     corr_refit_vs_frozen_causal=float(out[['causal', 'frozen_causal']].corr().iloc[0, 1])))
    dump(exp/'summary.json', dict(config=cfg, results=res, source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                                 training_eligible=False, production_eligible=False))
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, required=True)
    run(ap.parse_args().config)
