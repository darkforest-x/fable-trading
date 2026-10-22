"""Stage4: market breadth plus a nonlinear model on the frozen1618 touch ledger.

Owner Notion task 区间1.618测试. Stage1 logistic on12 touch-time features
did not generalize (later AUC0.52). This last pre-registered attempt adds two
market-context features and a small LightGBM; same earlier/later split, same
success rule, so a pass cannot come from reusing the stage1 selection.

breadth24h   share of universe symbols whose same-side first1618 touch
             happened in the24h strictly before this touch (ledger times only).
btc_pos      BTC's side-adjusted position versus its own Monday range at the
             last BTC15m close before the touch: side*(close-near edge)/R_btc,
             e.g. a short touch while BTC is far above its Monday high is
             negative. Reads BTC15m bars that closed before the touch bar.
Model: LightGBM fixed (num_leaves8, 300 trees, lr.03, min_data_in_leaf100,
feature_fraction.8, seed) on all14 features, trained on the earlier period
only. Success: later AUC>=0.55 AND later top30percent (earlier threshold)
validity>=38.2 and mean R>0 AND week-stratified p<0.01.
"""
from __future__ import annotations
import argparse
import json
import subprocess
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation import monday_1618_validity as v

EXP = ROOT/'experiments/active/exp-monday-1618-breadth-gbm-20261023-v1'


def breadth(touches, symbols):
    t = pd.to_datetime(touches.touch_time).astype('int64').to_numpy()
    out = np.zeros(len(touches))
    for side in (1, -1):
        idx = np.flatnonzero(touches.side.to_numpy() == side)
        order = idx[np.argsort(t[idx])]
        times = t[order]
        lo = np.searchsorted(times, times-24*3600*10**9, side='left')
        hi = np.searchsorted(times, times, side='left')
        out[order] = (hi-lo)/symbols
    return out


def btc_position(touches, btc):
    """BTC Monday range per NY week from the same15m bars, read before the touch."""
    local = btc.index.tz_convert(v.TZ)
    week = (local.normalize()-pd.to_timedelta(local.dayofweek, unit='D')).tz_localize(None)
    ranges = {}
    for wk, g in btc.groupby(week):
        mon = g[g.index.tz_convert(v.TZ).dayofweek == 0]
        if len(mon) == 96:
            ranges[str(wk.date())] = (mon.high.max(), mon.low.min())
    out = []
    closes = btc.close
    for r in touches.itertuples():
        rng = ranges.get(r.week)
        t = pd.Timestamp(r.touch_time)
        i = closes.index.searchsorted(t)-1
        if rng is None or i < 0:
            out.append(np.nan); continue
        H, L = rng; c = closes.iloc[i]
        edge = L if r.side == 1 else H
        out.append(r.side*(c-edge)/(H-L))
    return np.array(out)


def run():
    cfg = json.loads((EXP/'config.json').read_text())
    if subprocess.run(['git','diff','--quiet','HEAD','--','yoyo/evaluation/monday_1618_breadth_gbm.py'], cwd=ROOT).returncode:
        raise RuntimeError('Commit builder first')
    import lightgbm as lgb
    from sklearn.metrics import roc_auc_score
    source = ROOT/cfg['touch_ledger']
    data = pd.read_csv(source/'touches.csv.gz')
    vcfg = json.loads((source/'config.json').read_text())
    files = v.universe(vcfg)
    btc = v.load(files['BTC']); btc = btc[btc.index >= pd.Timestamp(vcfg['start'])]
    data['breadth24h'] = breadth(data, data.symbol.nunique())
    data['btc_pos'] = btc_position(data, btc)
    cols = v.FEATURES+['breadth24h', 'btc_pos']
    early, late = data[data.segment.eq(0)], data[data.segment.eq(1)].reset_index(drop=True)
    model = lgb.LGBMClassifier(num_leaves=8, n_estimators=300, learning_rate=.03, min_child_samples=100,
        colsample_bytree=.8, random_state=cfg['seed'], verbose=-1)
    model.fit(early[cols], early.valid.astype(int))
    se, sl = model.predict_proba(early[cols])[:, 1], model.predict_proba(late[cols])[:, 1]
    cut = float(np.quantile(se, 1-cfg['top_fraction']))
    p, late_cut = v.stratified_p(late, sl, cfg['top_fraction'], cfg['seed'])
    selected = late[sl >= cut]
    uni = {}
    for name in ('breadth24h', 'btc_pos'):
        for label, frame in (('early', early), ('late', late)):
            x = frame[[name, 'valid']].dropna()
            uni[f'{name}_{label}_auc'] = float(roc_auc_score(x.valid, x[name]))
            uni[f'{name}_{label}_quintiles'] = v.quintiles(frame, name)
    later_auc = float(roc_auc_score(late.valid, sl))
    sel = v.rate(selected)
    passed = bool(later_auc >= .55 and sel['valid_pct'] >= 100/2.618 and sel['mean_r'] > 0 and p < .01)
    summary = dict(config=cfg, features=cols, early_auc=float(roc_auc_score(early.valid, se)), later_auc=later_auc,
        early_threshold=cut, late_selected=sel, late_all=v.rate(late), late_top_week_stratified_p=p,
        importance=dict(zip(cols, map(int, model.booster_.feature_importance()))), univariate=uni, passed=passed,
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
        training_eligible=False, production_eligible=False)
    data[['symbol','week','side','touch_time','breadth24h','btc_pos']].to_csv(EXP/'context_features.csv.gz', index=False)
    dump(EXP/'summary.json', summary)
    print(json.dumps({k: summary[k] for k in ('early_auc','later_auc','late_selected','late_all','late_top_week_stratified_p','passed','importance')}
        | {k: v_ for k, v_ in uni.items() if k.endswith('auc')}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--config', type=Path, default=EXP/'config.json')
    args = parser.parse_args(); EXP = args.config.resolve().parent; run()
