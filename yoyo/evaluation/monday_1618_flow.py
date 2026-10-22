"""Non-price context: do positioning and flow judge Monday1618 validity?

Owner Notion task 区间1.618测试, 2026-10-23 ("想办法"): price-only features,
post-touch states, BTC out-of-sample and breadth/LightGBM all failed. This
study adds Binance USD-M positioning and flow, frozen before any outcome is
read. Universe: 12 Binance majors listed before2022 (config), 15m vision
klines 2022-09..2026-09. Touches and labels come unchanged from
monday_1618_validity.touches (Monday edge before2.0 extension, conservative
15m ordering, touch bar can only fail).

Every feature is read strictly before the touch bar opens (t):
  metrics rows (5m) with create_time <= t-5min; funding with calc_time <= t;
  taker volume from15m bars that closed by t. Side-adjusted so that positive
  means pressure/positioning INTO the1618 (with the move that reached it).
oi_chg_4h, oi_chg_24h   open-interest log change over4h / 24h.
oi_since_monday         OI log change from Monday NY close to touch.
top_ls_into_24h         -side*log change of top-trader position long/short.
global_ls_into          -side*log(global account long/short ratio).
taker_into_1h, _4h      -side*(taker buy-sell)/volume over prior4/16 bars.
funding_into            -side*last settled funding rate (crowd paying).
Primary hypothesis: larger oi_since_monday (new positions built into the
level) -> lower validity. Model: logistic C=0.5 on the8 features, trained on
touches before split only; success = later AUC>=0.55 AND early-threshold top
30percent validity>=38.2 and mean R>0 AND week-stratified p<0.01.
"""
from __future__ import annotations
import argparse
import glob
import io
import json
import subprocess
import zipfile
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation import monday_1618_validity as v

EXP = ROOT/'experiments/active/exp-monday-1618-flow-20261023-v1'
FLOW = ['oi_chg_4h', 'oi_chg_24h', 'oi_since_monday', 'top_ls_into_24h', 'global_ls_into',
        'taker_into_1h', 'taker_into_4h', 'funding_into']
KCOLS = ['open_time','open','high','low','close','volume','close_time','quote_volume','count',
         'taker_buy_volume','taker_buy_quote_volume','ignore']


def read_zips(pattern, **kw):
    frames = []
    for path in sorted(glob.glob(pattern)):
        with zipfile.ZipFile(path) as z:
            raw = z.read(z.namelist()[0]).decode()
        frames.append(pd.read_csv(io.StringIO(raw), **kw))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def klines(root, symbol):
    k = read_zips(f'{root}/klines15m/{symbol}/*.zip', header=None, names=KCOLS)
    k = k[pd.to_numeric(k.open_time, errors='coerce').notna()].astype({c: float for c in KCOLS})
    ts = k.open_time.where(k.open_time < 1e14, k.open_time/1000)
    f = pd.DataFrame(dict(open=k.open.values, high=k.high.values, low=k.low.values, close=k.close.values,
        volume=k.volume.values, taker_buy=k.taker_buy_volume.values), index=pd.to_datetime(ts.values, unit='ms', utc=True))
    return f[~f.index.duplicated()].sort_index()


def metrics(root, symbol):
    m = read_zips(f'{root}/metrics/{symbol}/*.zip')
    m.index = pd.to_datetime(m.create_time, utc=True)
    m = m[['sum_open_interest','sum_toptrader_long_short_ratio','count_long_short_ratio']].astype(float)
    return m[~m.index.duplicated()].sort_index()


def funding(root, symbol):
    r = read_zips(f'{root}/funding/{symbol}/*.zip')
    r.index = pd.to_datetime(r.calc_time.astype('int64'), unit='ms', utc=True)
    return r.last_funding_rate.astype(float).sort_index()


def asof(series, times):
    """Last value at or before each time; NaN before the first observation."""
    i = series.index.searchsorted(times, side='right')-1
    out = np.full(len(times), np.nan)
    ok = i >= 0
    out[ok] = series.to_numpy()[i[ok]]
    return out


def flow_features(rows, f, m, fr):
    t = pd.DatetimeIndex(pd.to_datetime(rows.touch_time))
    lag = t-pd.Timedelta(minutes=5)
    oi = m.sum_open_interest
    side = rows.side.to_numpy()
    mon_close = t - pd.to_timedelta(rows.hours.to_numpy(), unit='h')
    out = pd.DataFrame(index=rows.index)
    now = asof(oi, lag)
    out['oi_chg_4h'] = np.log(now/asof(oi, lag-pd.Timedelta(hours=4)))
    out['oi_chg_24h'] = np.log(now/asof(oi, lag-pd.Timedelta(hours=24)))
    out['oi_since_monday'] = np.log(now/asof(oi, mon_close-pd.Timedelta(minutes=5)))
    top = m.sum_toptrader_long_short_ratio
    out['top_ls_into_24h'] = -side*np.log(asof(top, lag)/asof(top, lag-pd.Timedelta(hours=24)))
    out['global_ls_into'] = -side*np.log(asof(m.count_long_short_ratio, lag))
    buy = f.taker_buy; vol = f.volume
    for name, n in (('taker_into_1h', 4), ('taker_into_4h', 16)):
        b = buy.rolling(n).sum().shift(1); vv = vol.rolling(n).sum().shift(1)
        imb = ((2*b-vv)/vv).to_numpy()[rows.touch_i.to_numpy()]
        out[name] = -side*imb
    out['funding_into'] = -side*asof(fr, t)
    return out.replace([np.inf, -np.inf], np.nan)


def run():
    cfg = json.loads((EXP/'config.json').read_text())
    if subprocess.run(['git','diff','--quiet','HEAD','--','yoyo/evaluation/monday_1618_flow.py'], cwd=ROOT).returncode:
        raise RuntimeError('Commit builder first')
    from sklearn.metrics import roc_auc_score
    root = EXP/'inputs'; parts = []; coverage = {}
    for symbol in cfg['symbols']:
        f = klines(root, symbol)
        rows = pd.DataFrame(v.touches(f, symbol.replace('USDT', ''), cfg))
        if rows.empty:
            continue
        feats = flow_features(rows, f, metrics(root, symbol), funding(root, symbol))
        parts.append(pd.concat([rows, feats], axis=1))
        coverage[symbol] = dict(first=str(f.index[0]), last=str(f.index[-1]), bars=len(f), touches=len(rows),
                                flow_missing=int(feats.isna().any(axis=1).sum()))
    data = pd.concat(parts, ignore_index=True)
    data['segment'] = (pd.to_datetime(data.touch_time) >= pd.Timestamp(cfg['split'])).astype(int)
    data.to_csv(EXP/'touches_flow.csv.gz', index=False)
    early, late = data[data.segment.eq(0)], data[data.segment.eq(1)].reset_index(drop=True)
    uni = {}
    for name in FLOW:
        row = {}
        for label, frame in (('early', early), ('late', late)):
            x = frame[[name, 'valid']].dropna()
            row[f'{label}_auc'] = float(roc_auc_score(x.valid, x[name])) if x.valid.nunique() == 2 else None
            row[f'{label}_quintiles'] = v.quintiles(frame, name)
        uni[name] = row
    model = v.fit(early, FLOW)
    se, sl = model.predict_proba(early[FLOW])[:, 1], model.predict_proba(late[FLOW])[:, 1]
    cut = float(np.quantile(se, 1-cfg['top_fraction']))
    p, _ = v.stratified_p(late, sl, cfg['top_fraction'], cfg['seed'])
    sel = v.rate(late[sl >= cut]); later_auc = float(roc_auc_score(late.valid, sl))
    # Primary univariate hypothesis, pre-registered direction: lower OI build -> valid.
    x = late[['oi_since_monday', 'valid']].dropna()
    rng = np.random.default_rng(cfg['seed']); obs = roc_auc_score(x.valid, -x.oi_since_monday)
    perm = [roc_auc_score(rng.permutation(x.valid.to_numpy()), -x.oi_since_monday) for _ in range(2000)]
    primary = dict(feature='oi_since_monday', direction='lower OI build -> valid', late_auc=float(obs),
                   p_one_sided=float((np.sum(np.array(perm) >= obs)+1)/2001), n=len(x))
    passed = bool(later_auc >= .55 and sel['valid_pct'] >= 100/2.618 and sel['mean_r'] > 0 and p < .01)
    summary = dict(config=cfg, coverage=coverage, touches=len(data), base=dict(early=v.rate(early), late=v.rate(late)),
        univariate=uni, primary=primary, model=dict(coefficients=dict(zip(FLOW, map(float, model[-1].coef_[0]))),
        early_auc=float(roc_auc_score(early.valid, se)), later_auc=later_auc, early_threshold=cut,
        late_selected=sel, late_top_week_stratified_p=p), passed=passed,
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
        training_eligible=False, production_eligible=False)
    dump(EXP/'summary.json', summary)
    print(json.dumps(dict(touches=len(data), base=summary['base'], primary=primary, model=summary['model'], passed=passed,
        aucs={n: (round(u['early_auc'], 3), round(u['late_auc'], 3)) for n, u in uni.items()}), ensure_ascii=False, indent=1))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--config', type=Path, default=EXP/'config.json')
    args = parser.parse_args(); EXP = args.config.resolve().parent; run()
