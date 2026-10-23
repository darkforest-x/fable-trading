"""Implied volatility regime and Monday1618 validity on BTC/ETH (method16).

Owner2026-10-23 chose this new dimension. Deribit DVOL (30-day implied vol
index, public API, hourly) is read strictly before the touch bar opens: an
hourly bar counts once open+1h <= touch bar open. Touches and labels:
monday_1618_validity.touches on Binance USD-M15m vision klines of BTCUSDT and
ETHUSDT (conservative15m ordering; Monday edge before2.0 extension).
dvol_pct90    percentile of the latest DVOL within the prior90 days
dvol          latest DVOL level
dvol_chg_24h  log change of DVOL over the prior24h
rv_iv         realized vol of the prior96 15m log returns (annualized, %)
              divided by DVOL
Primary hypothesis frozen in config: calmer implied-vol regime (lower
dvol_pct90) -> higher validity: AUC of -dvol_pct90 above0.5 in the earlier
period and later one-sided permutation p<0.01. Model: logistic on all four
trained before split; success as stage1 (later AUC>=0.55, early-threshold
top30percent validity>=38.2 and mean R>0, week-stratified p<0.01).
"""
from __future__ import annotations
import argparse
import json
import subprocess
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation import monday_1618_validity as v
from yoyo.evaluation.monday_1618_flow import klines
from yoyo.data.binance_um_vision_fetch import BASE, fetch

EXP = ROOT/'experiments/active/exp-monday-1618-dvol-20261023-v1'
FEATS = ['dvol_pct90', 'dvol', 'dvol_chg_24h', 'rv_iv']
API = 'https://www.deribit.com/api/v2/public/get_volatility_index_data'


def fetch_dvol(currency, start, end, path):
    if path.exists():
        return pd.read_csv(path, index_col=0, parse_dates=True)
    rows, s = [], int(pd.Timestamp(start).value//10**6)
    stop = int(pd.Timestamp(end).value//10**6)
    while s < stop:
        e = min(stop, s+30*86400*1000)
        url = f'{API}?currency={currency}&start_timestamp={s}&end_timestamp={e}&resolution=3600'
        with urllib.request.urlopen(url, timeout=60) as r:
            data = json.loads(r.read())['result']['data']
        rows += data
        s = e; time.sleep(.2)
    d = pd.DataFrame(rows, columns=['ts', 'open', 'high', 'low', 'close']).drop_duplicates('ts')
    d.index = pd.to_datetime(d.ts, unit='ms', utc=True)
    d = d[['close']].sort_index()
    d.to_csv(path)
    return d


def features(rows, f, dvol):
    t = pd.DatetimeIndex(pd.to_datetime(rows.touch_time, utc=True))
    avail = dvol.close.copy(); avail.index = avail.index+pd.Timedelta(hours=1)
    idx = avail.index.searchsorted(t, side='right')-1
    vals = avail.to_numpy(); times = avail.index
    out = []
    lr = np.log(f.close).diff()
    for r, i, tt in zip(rows.itertuples(), idx, t):
        if i < 0 or tt-times[i] > pd.Timedelta(hours=3):
            out.append(dict.fromkeys(FEATS, np.nan)); continue
        now = vals[i]
        lo = times.searchsorted(tt-pd.Timedelta(days=90))
        hist = vals[lo:i+1]
        j = times.searchsorted(tt-pd.Timedelta(hours=24), side='right')-1
        k = int(r.touch_i)
        rv = float(lr.iloc[max(0, k-96):k].std()*np.sqrt(96*365)*100)
        out.append(dict(dvol_pct90=float((hist <= now).mean()) if len(hist) > 24*30 else np.nan, dvol=float(now),
                        dvol_chg_24h=float(np.log(now/vals[j])) if j >= 0 else np.nan, rv_iv=rv/now))
    return pd.DataFrame(out, index=rows.index)


def run():
    cfg = json.loads((EXP/'config.json').read_text())
    if subprocess.run(['git','diff','--quiet','HEAD','--','yoyo/evaluation/monday_1618_dvol.py'], cwd=ROOT).returncode:
        raise RuntimeError('Commit builder first')
    from sklearn.metrics import roc_auc_score
    out = EXP/'inputs'; out.mkdir(parents=True, exist_ok=True)
    months = pd.period_range(cfg['fetch_start_month'], cfg['fetch_end_month'], freq='M')
    jobs = [('klines15m', s, f'{BASE}/monthly/klines/{s}/15m/{s}-15m-{m}.zip') for s in cfg['symbols'] for m in months]
    with ThreadPoolExecutor(16) as pool:
        status = list(pool.map(lambda j: fetch(out, *j), jobs))
    receipt = dict(kline_jobs=len(jobs), counts=pd.Series([x[2] for x in status]).value_counts().to_dict())
    parts = []
    for s in cfg['symbols']:
        cur = s.replace('USDT', '')
        dv = fetch_dvol(cur, cfg['dvol_start'], cfg['end'], out/f'dvol_{cur}.csv')
        receipt[f'dvol_{cur}'] = dict(rows=len(dv), first=str(dv.index.min()), last=str(dv.index.max()))
        f = klines(out, s)
        rows = pd.DataFrame(v.touches(f, cur, cfg))
        rows = rows[pd.to_datetime(rows.touch_time, utc=True) < pd.Timestamp(cfg['end'])]
        parts.append(rows.join(features(rows, f, dv)))
    dump(out/'fetch_receipt.json', receipt)
    data = pd.concat(parts, ignore_index=True).dropna(subset=FEATS)
    data['segment'] = (pd.to_datetime(data.touch_time, utc=True) >= pd.Timestamp(cfg['split'])).astype(int)
    data[['symbol','week','side','touch_time','valid','r','segment']+FEATS].to_csv(EXP/'touches_dvol.csv.gz', index=False)
    early, late = data[data.segment.eq(0)], data[data.segment.eq(1)].reset_index(drop=True)
    uni = {n: {f'{lab}_auc': float(roc_auc_score(fr.valid, fr[n])) for lab, fr in (('early', early), ('late', late))} |
              {f'{lab}_quintiles': v.quintiles(fr, n) for lab, fr in (('early', early), ('late', late))} for n in FEATS}
    rng = np.random.default_rng(cfg['seed'])
    obs = roc_auc_score(late.valid, -late.dvol_pct90)
    perm = [roc_auc_score(rng.permutation(late.valid.to_numpy()), -late.dvol_pct90) for _ in range(5000)]
    primary = dict(early_auc=float(roc_auc_score(early.valid, -early.dvol_pct90)), later_auc=float(obs),
                   later_p_one_sided=float((np.sum(np.array(perm) >= obs)+1)/5001))
    model = v.fit(early, FEATS)
    se, sl = model.predict_proba(early[FEATS])[:, 1], model.predict_proba(late[FEATS])[:, 1]
    cut = float(np.quantile(se, 1-cfg['top_fraction']))
    p, _ = v.stratified_p(late, sl, cfg['top_fraction'], cfg['seed'])
    sel = v.rate(late[sl >= cut]); later_auc = float(roc_auc_score(late.valid, sl))
    passed = dict(primary=bool(primary['early_auc'] > .5 and primary['later_p_one_sided'] < .01),
                  model=bool(later_auc >= .55 and sel['valid_pct'] >= 100/2.618 and sel['mean_r'] > 0 and p < .01))
    summary = dict(config=cfg, receipt=receipt, touches=len(data), base=dict(early=v.rate(early), late=v.rate(late)),
        by_symbol={s: v.rate(g) for s, g in data.groupby('symbol')}, univariate=uni, primary=primary,
        model=dict(coefficients=dict(zip(FEATS, map(float, model[-1].coef_[0]))), later_auc=later_auc,
                   late_selected=sel, late_top_week_stratified_p=p), passed=passed,
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
        training_eligible=False, production_eligible=False)
    dump(EXP/'summary.json', summary)
    print(json.dumps(dict(receipt=receipt, touches=len(data), base=summary['base'], primary=primary,
        model=summary['model'], passed=passed, aucs={n: (round(u['early_auc'], 3), round(u['late_auc'], 3)) for n, u in uni.items()}),
        ensure_ascii=False, indent=1))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--config', type=Path, default=EXP/'config.json')
    args = parser.parse_args(); EXP = args.config.resolve().parent; run()
