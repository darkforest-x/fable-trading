"""Fit the Monday1618 entry score with pre-touch 1m features; train before 2026, test 2026 to date (owner 2026-10-28).

Owner: "做 1 分钟模型 ... 用触及前一分钟为止的特征（含最近几分钟的放量和速度）训练，2026 年前训练、2026 年至今测试，
再在框架里跑策略". Training rows, label and estimator repeat monday_limit_score_causal_refit (v2 near-edge 60m fills,
worse path, 10bp slippage on non-target exits, win = net > 0, standardized logistic regression with sklearn defaults):
  base   the ten frozen features with vol_ratio / liquidity / toward24 from the 5m bar before the touch (the causal
         refit's main model, refitted here on the rows that have 1m features, the control);
  m1     base plus the thirteen 1m features of exp-monday-limit-1m-features-20261028-v1 (completed 1m bars before the
         touch minute).
Only events whose 1m features are ok enter training and test; the others score NaN and are never selected. Test on
fills from 2026-01-01: AUC with a label-permutation p, top-decile mean net, the frozen model on causal features on the
same rows. Scores for every event: `causal` = m1 at the minute before the touch (resting decision), `stored` = the
frozen touch-bar score (the leaky reference, unchanged), plus base and frozen causal columns; m1's top-40 threshold
on its training scores is written for the v2-style accounts.
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
from yoyo.evaluation.monday_limit_reclaim_patterns import SNAP
from yoyo.evaluation.monday_limit_be_scalein_compare import clean
from yoyo.evaluation import monday_limit_reclaim_allexits as ax
from yoyo.evaluation import monday_limit_reclaim_optimize as opt
from yoyo.evaluation.monday_limit_score_leak_audit import bar_features
from yoyo.evaluation.monday_limit_score_causal_refit import with_bar, frozen_score, spec_of, auc
from yoyo.evaluation.monday_limit_1m_features import FEATS as M1


def run(config):
    exp = config.resolve().parent
    for p in ('yoyo/evaluation/monday_limit_score_1m_fit.py', str(config.resolve().relative_to(ROOT))):
        if subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--', p], cwd=ROOT).returncode or \
           subprocess.run(['git', 'ls-files', '--error-unmatch', p], cwd=ROOT, capture_output=True).returncode:
            raise RuntimeError('Commit builder and config first')
    cfg = json.loads(config.read_text())
    old = json.loads((ROOT/cfg['frozen_model']).read_text())
    m1 = pd.read_csv(ROOT/cfg['features_1m']).set_index('event')
    ok = m1[m1.status == 'ok'][M1]
    syms = [r['symbol'] for r in json.loads(SNAP.read_text())['studies']['limit-reclaim']]
    with ProcessPoolExecutor(cfg['workers']) as ex:
        x = pd.concat([d for d in ex.map(bar_features, syms, chunksize=4) if len(d)], ignore_index=True).set_index('event')
    # Training frame as monday_limit_reclaim_recent / the causal refit.
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
    s['t'] = pd.to_datetime(s.entry_time, utc=True); s['y'] = (s.net > 0).astype(int)
    g = with_bar(s, 'causal').join(ok, on='event', how='inner').dropna(subset=opt.FEATS+M1)
    split = pd.Timestamp(cfg['split'], tz='UTC')
    tr, va = g[g.t < split], g[g.t >= split]
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    sets = {'base': list(opt.FEATS), 'm1': list(opt.FEATS)+M1}
    rng = np.random.default_rng(cfg['seed'])
    models, test = {}, dict(train_n=int(len(tr)), test_n=int(len(va)), rows_with_1m=int(len(g)), base_mean_bp=float(va.net.mean()),
                            base_win_pct=float(100*va.y.mean()))

    def top(p, q=.9):
        k = p >= np.quantile(p, q)
        return dict(n=int(k.sum()), mean_bp=float(va.net[k].mean()), win_pct=float(100*(va.net[k] > 0).mean()))
    for name, cols in sets.items():
        model = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000)).fit(tr[cols], tr.y)
        models[name] = model
        p = model.predict_proba(va[cols])[:, 1]
        a = auc(va.y, p)
        perm = np.array([auc(rng.permutation(va.y.to_numpy()), p) for _ in range(cfg['permutations'])])
        test[name] = dict(auc=a, perm_p=float((1+(perm >= a).sum())/(1+len(perm))), top10=top(p), ge_05=int((p >= .5).sum()),
                          ge_06=int((p >= .6).sum()), coef=dict(zip(cols, model[-1].coef_[0].round(4).tolist())))
    pf = frozen_score(old, va)
    test['frozen_causal'] = dict(auc=auc(va.y, pf), top10=top(pf), ge_05=int((pf >= .5).sum()))
    # AUC gain of m1 over base against shuffled 1m features (rows permuted jointly), refit each time.
    gains = []
    for _ in range(cfg['feature_shuffles']):
        sh = tr.copy(); sh[M1] = sh[M1].to_numpy()[rng.permutation(len(sh))]
        vs = va.copy(); vs[M1] = vs[M1].to_numpy()[rng.permutation(len(vs))]
        mm = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000)).fit(sh[sets['m1']], sh.y)
        gains.append(auc(vs.y, mm.predict_proba(vs[sets['m1']])[:, 1])-test['base']['auc'])
    test['m1_gain_over_base'] = test['m1']['auc']-test['base']['auc']
    test['shuffled_1m_gain_q'] = {f'q{int(100*q)}': float(np.quantile(gains, q)) for q in (.5, .95, .99)}
    cut40 = float(np.quantile(models['m1'].predict_proba(tr[sets['m1']])[:, 1], .6))
    spec = dict(spec_of(models['m1'], cut40), features=sets['m1'])
    dump(exp/'score_model_1m.json', dict(spec, trained_before=str(split), train_n=int(len(tr)),
         feature_source=cfg['features_1m'], frozen_model_untouched=cfg['frozen_model']))
    # Scores for every signal event (frame as the leak audit), NaN without 1m features.
    ae = pd.read_csv(ROOT/cfg['events'])
    ae['event'] = ae.symbol+':'+ae.week+':'+np.where(ae.side_n == 1, '+1', '-1')
    v2 = sorted(d.name for d in ax.V2.iterdir() if (d/'done.json').exists())
    with ProcessPoolExecutor(cfg['workers']) as ex:
        ex2 = pd.concat(list(ex.map(opt.ledger_extra, v2, chunksize=8)), ignore_index=True)
    ex2['event'] = ex2.trade_id.str.split(':').str[:3].str.join(':')
    e = ae.set_index('event').join(ex2.set_index('event')[['bucket', 'range_pct']], how='inner').join(x, how='inner')
    e['side'] = e.side_n.astype(float); e['log_approach'] = np.log1p(e.approach_min.clip(lower=0))
    e['opp_first'] = (e.opp_first == '是').astype(int)
    e = with_bar(e, 'causal').join(ok, how='left')
    out = pd.DataFrame(index=e.index)
    out['causal'] = frozen_score(spec, e)
    out['base'] = frozen_score(spec_of(models['base'], 0.), e)
    prev = pd.read_csv(ROOT/cfg['previous_scores']).set_index('event')
    out = out.join(prev[['stored', 'causal']].rename(columns={'causal': 'frozen_causal'}), how='left')
    out.reset_index().to_csv(exp/'scores_1m.csv.gz', index=False)
    bands = {str(c): dict(m1=int((out.causal >= c).sum()), frozen_causal=int((out.frozen_causal >= c).sum()),
                          frozen_touch=int((out.stored >= c).sum()), m1_and_frozen_causal=int(((out.causal >= c) & (out.frozen_causal >= c)).sum()))
             for c in (cut40, .5, .6)}
    res = clean(dict(test=test, top40_threshold=cut40, events=int(len(out)), scored=int(out.causal.notna().sum()), bands=bands))
    dump(exp/'summary.json', dict(config=cfg, results=res, source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                                 training_eligible=False, production_eligible=False))
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, required=True)
    run(ap.parse_args().config)
