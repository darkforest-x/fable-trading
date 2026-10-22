"""Volume-profile and swing confluence at the Monday1618 (method7).

Owner Notion task 区间1.618测试. A1618 that coincides with a level where
the market previously accepted heavy volume, or with a prior swing extreme,
may hold more often than an isolated projection. Reads the frozen OKX touch
ledger of exp-monday-1618-validity and the same15m bars; labels unchanged.

All inputs are15m bars that closed before the Monday that defines the
level, or before the touch bar for the30-day window. Profiles spread each
bar's volume uniformly over its high-low range into bins of R/20.
pw_poc_dist   |level - previous NY week POC| / R.
pw_va_dist    distance from level to the nearer of previous-week VAH/VAL
              (70percent value area), / R.
vol_density   volume traded within +-0.05R of the level over the30 days
              before the touch, divided by that window's volume per R/10
              band (1 = average density).
swing_dist    |level - nearest4h fractal swing high (short) / low (long)|
              among swings confirmed in the30 days before the touch, / R;
              a fractal needs2 completed4h bars on each side.
Model: logistic C=0.5 on the4 features trained before split; same success
rule as stage1 (later AUC>=0.55, early-threshold top30percent validity
>=38.2 and mean R>0, week-stratified p<0.01).
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

EXP = ROOT/'experiments/active/exp-monday-1618-confluence-20261023-v1'
FEATS = ['pw_poc_dist', 'pw_va_dist', 'vol_density', 'swing_dist']


def profile(h, l, vol, lo, hi, bins):
    """Volume per bin on [lo,hi): each bar spread uniformly over its range."""
    edges = np.linspace(lo, hi, bins+1)
    out = np.zeros(bins)
    for a, b, q in zip(l, h, vol):
        if q <= 0 or not np.isfinite(q):
            continue
        if b > a:
            overlap = np.clip(np.minimum(b, edges[1:])-np.maximum(a, edges[:-1]), 0, None)
            out += q*overlap/(b-a)
        else:
            out[min(bins-1, max(0, int(np.searchsorted(edges, a, side='right'))-1))] += q
    return edges, out


def value_area(edges, vol, share=.7):
    mids = (edges[:-1]+edges[1:])/2
    poc = int(np.argmax(vol)); lo = hi = poc; total = vol.sum(); acc = vol[poc]
    while acc < share*total and (lo > 0 or hi < len(vol)-1):
        left = vol[lo-1] if lo > 0 else -1; right = vol[hi+1] if hi < len(vol)-1 else -1
        if right >= left:
            hi += 1; acc += vol[hi]
        else:
            lo -= 1; acc += vol[lo]
    return mids[poc], edges[hi+1], edges[lo]


def swings(f, days=30):
    """4h fractal highs/lows with confirmation time (2 bars after the pivot closes)."""
    h4 = f.resample('4h').agg(dict(high='max', low='min')).dropna()
    hi, lo = h4.high.to_numpy(), h4.low.to_numpy()
    rows = []
    for i in range(2, len(h4)-2):
        confirm = h4.index[i+2]+pd.Timedelta(hours=4)
        if hi[i] > max(hi[i-2:i].max(), hi[i+1:i+3].max()):
            rows.append((confirm, 'high', hi[i]))
        if lo[i] < min(lo[i-2:i].min(), lo[i+1:i+3].min()):
            rows.append((confirm, 'low', lo[i]))
    return pd.DataFrame(rows, columns=['confirm', 'kind', 'price'])


def features(f, rows):
    h, l, vol = f.high.to_numpy(), f.low.to_numpy(), f.volume.to_numpy()
    local = f.index.tz_convert(v.TZ)
    week = (local.normalize()-pd.to_timedelta(local.dayofweek, unit='D')).tz_localize(None)
    weekstr = np.array([str(w.date()) for w in week])
    sw = swings(f)
    out = []
    for r in rows.itertuples():
        t = int(r.touch_i); R = r.R
        prev = str((pd.Timestamp(r.week)-pd.Timedelta(days=7)).date())
        idx = np.flatnonzero(weekstr == prev)
        poc_d = va_d = np.nan
        if len(idx) > 96*5:
            lo_, hi_ = l[idx].min(), h[idx].max()
            edges, prof = profile(h[idx], l[idx], vol[idx], lo_, hi_, max(5, int(np.ceil((hi_-lo_)/(R/20)))))
            if prof.sum() > 0:
                poc, vah, val = value_area(edges, prof)
                poc_d = abs(r.level-poc)/R; va_d = min(abs(r.level-vah), abs(r.level-val))/R
        a = max(0, t-30*96)
        hh, ll, vv = h[a:t], l[a:t], vol[a:t]
        total = np.nansum(vv)
        dens = np.nan
        if total > 0:
            band = .05*R
            width = np.maximum(hh-ll, 1e-12)
            inside = np.clip(np.minimum(hh, r.level+band)-np.maximum(ll, r.level-band), 0, None)
            share = np.nansum(vv*inside/width)/total
            span = (np.nanmax(hh)-np.nanmin(ll))/R
            dens = share/(.1/span) if span > 0 else np.nan
        t0 = f.index[t]
        kind = 'high' if r.side == -1 else 'low'
        s = sw[(sw.kind == kind) & (sw.confirm <= t0) & (sw.confirm > t0-pd.Timedelta(days=30))]
        sd = float(np.min(np.abs(s.price.to_numpy()-r.level))/R) if len(s) else np.nan
        out.append(dict(pw_poc_dist=poc_d, pw_va_dist=va_d, vol_density=dens, swing_dist=sd))
    return pd.DataFrame(out, index=rows.index)


def run():
    cfg = json.loads((EXP/'config.json').read_text())
    if subprocess.run(['git','diff','--quiet','HEAD','--','yoyo/evaluation/monday_1618_confluence.py'], cwd=ROOT).returncode:
        raise RuntimeError('Commit builder first')
    from sklearn.metrics import roc_auc_score
    source = ROOT/cfg['touch_ledger']
    data = pd.read_csv(source/'touches.csv.gz'); vcfg = json.loads((source/'config.json').read_text())
    files = v.universe(vcfg); parts = []
    for symbol, rows in data.groupby('symbol'):
        f = v.load(files[symbol]); f = f[f.index >= pd.Timestamp(vcfg['start'])]
        parts.append(features(f, rows))
    data = data.join(pd.concat(parts))
    data[['symbol','week','side','touch_time']+FEATS].to_csv(EXP/'confluence_features.csv.gz', index=False)
    early, late = data[data.segment.eq(0)], data[data.segment.eq(1)].reset_index(drop=True)
    uni = {}
    for name in FEATS:
        row = {}
        for label, frame in (('early', early), ('late', late)):
            x = frame[[name, 'valid']].dropna()
            row[f'{label}_auc'] = float(roc_auc_score(x.valid, x[name]))
            row[f'{label}_quintiles'] = v.quintiles(frame, name)
        uni[name] = row
    model = v.fit(early, FEATS)
    se, sl = model.predict_proba(early[FEATS])[:, 1], model.predict_proba(late[FEATS])[:, 1]
    cut = float(np.quantile(se, 1-cfg['top_fraction']))
    p, _ = v.stratified_p(late, sl, cfg['top_fraction'], cfg['seed'])
    sel = v.rate(late[sl >= cut]); later_auc = float(roc_auc_score(late.valid, sl))
    passed = bool(later_auc >= .55 and sel['valid_pct'] >= 100/2.618 and sel['mean_r'] > 0 and p < .01)
    summary = dict(config=cfg, touches=len(data), univariate=uni, model=dict(
        coefficients=dict(zip(FEATS, map(float, model[-1].coef_[0]))), early_auc=float(roc_auc_score(early.valid, se)),
        later_auc=later_auc, early_threshold=cut, late_selected=sel, late_all=v.rate(late), late_top_week_stratified_p=p),
        passed=passed, source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
        training_eligible=False, production_eligible=False)
    dump(EXP/'summary.json', summary)
    print(json.dumps(dict(model=summary['model'], passed=passed,
        aucs={n: (round(u['early_auc'], 3), round(u['late_auc'], 3)) for n, u in uni.items()}), ensure_ascii=False, indent=1))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--config', type=Path, default=EXP/'config.json')
    args = parser.parse_args(); EXP = args.config.resolve().parent; run()
