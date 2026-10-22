"""Monday 1618 validity: which first touches return into the Monday range?

Owner Notion task 区间1.618测试 (2026-10-22/23): judge whether this week's 1618
is valid. The owner's TradingView positions (32 BTC, 8 XAU) are hindsight
winners, so they only motivate candidate features. Validity is labelled
objectively on a multi-symbol OKX swap universe; features are chosen on the
earlier period, frozen, then judged on the later period.

Touch: first weekly-side touch of the outer 1618 (short H+0.618R, long
L-0.618R, R=Monday NY high-low) from Tuesday00:00 NY until next Monday.
Label: completed15m bars reach the Monday near edge (short H, long L) before
the2.0 extension (short H+R, long L-R). Target ordering inside the touch bar
is unknown, so that bar can only fail; any later bar hitting both = fail.
Unresolved after14 days = fail. Trade view: limit at1618, SL2.0, TP edge, so
+1.618R/-1R, breakeven validity1/2.618=38.2percent, fee0 (owner2026-10-22).

Features use only bars closed before the touch bar opens, plus UTC daily bars
completed before the touch day. Each name states columns and windows:
range_atr     Monday R / prior-day ATR14 (daily OHLC).
hours         hours from Monday NY close to touch-bar open.
move6h        approach: price travel over the prior24 15m closes into the
              level, in R (close[t-25] -> level, side-adjusted).
trend_d       side*(prior daily close - daily EMA20)/ATR14d; >0 = trade with trend.
slope4h       side*(4h EMA50 change over6 completed4h bars)/ATR14d.
beyond_pw     side-adjusted (level - previous NY week's near extreme)/R;
              >0 = level outside last week's range (short: above its high).
beyond_30d    same versus the30 completed days before Monday.
opp_touched   opposite Monday edge traded after Monday close, before touch.
vol_ratio     volume of prior16 bars / mean16-bar volume over prior7 days.
rsi1h_ext     1h RSI14 at last completed hour, extension side (short RSI,
              long 100-RSI).
dow           NY weekday of touch (1=Tue..6=Sun).
mon_body      side*(Monday close-open)/R; >0 = Monday candle points the trade way.
"""
from __future__ import annotations
import argparse
import glob
import json
import subprocess
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump

EXP = ROOT/'experiments/active/exp-monday-1618-validity-20261023-v1'
FEATURES = ['range_atr','hours','move6h','trend_d','slope4h','beyond_pw','beyond_30d',
            'opp_touched','vol_ratio','rsi1h_ext','dow','mon_body']
STABLE = {'USDC','DAI','TUSD','FDUSD','USDE','PYUSD'}
TZ = 'America/New_York'


def load(path):
    f = pd.read_csv(path)
    f.index = pd.to_datetime(f.ts, unit='ms', utc=True)
    f = f[['open','high','low','close','volume']].astype(float)
    return f[~f.index.duplicated()].sort_index()


def universe(cfg):
    """Swap files with enough history; fresh BTC/XAU inputs replace stale copies."""
    files = {}
    for path in sorted(glob.glob(str(ROOT/'data/kline_fetched/okx_*_USDT_SWAP_15m_*.csv'))):
        name = Path(path).name.split('_')[1]
        if name in STABLE or int(path.rsplit('_', 1)[1][:-4]) < cfg['min_bars']:
            continue
        files[name] = path
    for name, path in cfg.get('fresh_inputs', {}).items():
        files[name] = str(ROOT/path)
    return files


def rsi(close, n=14):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    down = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100/(1 + up/down)


def context(f):
    """Causal per-bar context, aligned to each 15m bar OPEN (prior closes only)."""
    day = f.resample('1D').agg(dict(open='first', high='max', low='min', close='last')).dropna()
    tr = pd.concat([day.high-day.low, (day.high-day.close.shift()).abs(),
                    (day.low-day.close.shift()).abs()], axis=1).max(axis=1)
    daily = pd.DataFrame(dict(atr=tr.ewm(alpha=1/14, adjust=False).mean(),
        ema=day.close.ewm(span=20, adjust=False).mean(), close=day.close,
        hi30=day.high.rolling(30).max(), lo30=day.low.rolling(30).min()))
    # A completed day becomes visible at the next UTC midnight.
    daily.index = daily.index + pd.Timedelta(days=1)
    h4 = f.close.resample('4h').last().dropna()
    e4 = h4.ewm(span=50, adjust=False).mean()
    slope = (e4 - e4.shift(6)).rename('slope')
    slope.index = slope.index + pd.Timedelta(hours=4)
    h1 = f.close.resample('1h').last().dropna()
    r1 = rsi(h1).rename('rsi')
    r1.index = r1.index + pd.Timedelta(hours=1)
    out = daily.reindex(f.index, method='ffill')
    out['slope'] = slope.reindex(f.index, method='ffill')
    out['rsi'] = r1.reindex(f.index, method='ffill')
    vol = f.volume.rolling(16).sum().shift(1)
    out['vol_ratio'] = vol/(f.volume.rolling(672).sum().shift(1)/42)
    out['close_25'] = f.close.shift(25)
    return out


def touches(f, symbol, cfg):
    """All weekly-side first touches with labels, R outcome and features."""
    ctx = context(f)
    local = f.index.tz_convert(TZ)
    week = (local.normalize() - pd.to_timedelta(local.dayofweek, unit='D')).tz_localize(None)
    o, h, l, c = (f[k].to_numpy() for k in ('open','high','low','close'))
    times = f.index
    horizon = cfg['horizon_days']*96
    weeks = pd.Series(np.arange(len(f)), index=week)
    out = []
    starts = sorted(set(week))
    for k, wk in enumerate(starts):
        pos = weeks.loc[[wk]].to_numpy()
        mon = pos[local[pos].dayofweek == 0]
        if len(mon) != 96 or mon[-1]-mon[0] != 95:
            continue
        rest = pos[pos > mon[-1]]
        if not len(rest):
            continue
        H, L = h[mon].max(), l[mon].min(); R = H-L
        if R <= 0:
            continue
        prev = weeks.loc[[starts[k-1]]].to_numpy() if k and starts[k-1] == wk-pd.Timedelta(days=7) else None
        for side in (1, -1):
            level = L-.618*R if side == 1 else H+.618*R
            stop = L-R if side == 1 else H+R
            edge = L if side == 1 else H
            hit = (l[rest] <= level) if side == 1 else (h[rest] >= level)
            if not hit.any():
                continue
            t = int(rest[np.argmax(hit)])
            if t < 1000:
                continue
            end = min(len(f), t+horizon)
            stop_hit = (l[t:end] <= stop) if side == 1 else (h[t:end] >= stop)
            edge_hit = (h[t+1:end] >= edge) if side == 1 else (l[t+1:end] <= edge)
            s = int(np.argmax(stop_hit)) if stop_hit.any() else 10**9
            e = int(np.argmax(edge_hit))+1 if edge_hit.any() else 10**9
            resolved = min(s, e) < 10**9
            valid = bool(e < s)
            # Gap through the limit fills at the open, which is the better price.
            fill = o[t] if side*(level-o[t]) > 0 else level
            risk = abs(stop-fill)
            if valid:
                r = side*(edge-fill)/risk
            elif s < 10**9:
                r = -1.0
            else:
                r = side*(c[end-1]-fill)/risk
            x = ctx.iloc[t]
            atr = x.atr
            if not np.isfinite(atr) or atr <= 0:
                continue
            near_pw = np.nan
            if prev is not None:
                near_pw = l[prev].min() if side == 1 else h[prev].max()
            opp = h[mon[-1]+1:t] >= H if side == 1 else l[mon[-1]+1:t] <= L
            out.append(dict(symbol=symbol, week=str(wk.date()), side=side, touch_i=t,
                touch_time=str(times[t]), level=level, stop=stop, edge=edge, R=R, fill=fill,
                valid=valid, resolved=resolved, r=r, bars_to_resolve=min(s, e) if resolved else -1,
                range_atr=R/atr, hours=(times[t]-times[mon[-1]]).total_seconds()/3600-.25,
                move6h=side*(x.close_25-level)/R,
                trend_d=side*(x.close-x.ema)/atr, slope4h=side*x.slope/atr,
                beyond_pw=side*(near_pw-level)/R if np.isfinite(near_pw) else np.nan,
                beyond_30d=side*((x.lo30 if side == 1 else x.hi30)-level)/R,
                opp_touched=float(opp.any()), vol_ratio=x.vol_ratio,
                rsi1h_ext=x.rsi if side == -1 else 100-x.rsi,
                dow=int(times[t].tz_convert(TZ).dayofweek), mon_body=side*(c[mon[-1]]-o[mon[0]])/R))
    return out


def random_geometry(f, symbol, rows, cfg, rng):
    """Same week/side/R geometry from random Tue-Sun bar opens: is1618 special?"""
    h, l, o, c = (f[k].to_numpy() for k in ('high','low','open','close'))
    local = f.index.tz_convert(TZ)
    week = (local.normalize() - pd.to_timedelta(local.dayofweek, unit='D')).tz_localize(None)
    weekstr = np.array([str(w.date()) for w in week])
    ok = (local.dayofweek >= 1) & (np.arange(len(f)) > 1000)
    out = []
    for r in rows:
        pool = np.flatnonzero(ok & (weekstr == r['week']))
        pool = pool[pool+cfg['horizon_days']*96 < len(f)] if r['resolved'] else pool
        if not len(pool):
            continue
        for t in rng.choice(pool, min(cfg['controls_per_touch'], len(pool)), replace=False):
            side = r['side']; entry = o[t]; risk = .382*r['R']
            stop = entry-side*risk; edge = entry+side*.618*r['R']
            end = min(len(f), t+cfg['horizon_days']*96)
            sh = (l[t:end] <= stop) if side == 1 else (h[t:end] >= stop)
            eh = (h[t:end] >= edge) if side == 1 else (l[t:end] <= edge)
            s = int(np.argmax(sh)) if sh.any() else 10**9
            e = int(np.argmax(eh)) if eh.any() else 10**9
            # Same-bar ordering is unknown: conservative stop first.
            out.append(dict(symbol=symbol, week=r['week'], side=side, valid=bool(e < s), resolved=min(s, e) < 10**9,
                r=1.618 if e < s else (-1.0 if s < 10**9 else side*(c[end-1]-entry)/risk)))
    return out


def build(cfg):
    rng = np.random.default_rng(cfg['seed'])
    rows, controls, coverage = [], [], {}
    for symbol, path in universe(cfg).items():
        f = load(path)
        f = f[f.index >= pd.Timestamp(cfg['start'])]
        got = touches(f, symbol, cfg)
        rows += got
        controls += random_geometry(f, symbol, got, cfg, rng)
        coverage[symbol] = dict(first=str(f.index[0]), last=str(f.index[-1]), bars=len(f), touches=len(got))
    data = pd.DataFrame(rows)
    data['segment'] = (pd.to_datetime(data.touch_time) >= pd.Timestamp(cfg['split'])).astype(int)
    return data, pd.DataFrame(controls), coverage


def rate(frame):
    n = len(frame)
    return dict(n=n, valid_pct=float(100*frame.valid.mean()) if n else None,
                mean_r=float(frame.r.mean()) if n else None, unresolved=int((~frame.resolved).sum()) if n else 0)


def quintiles(frame, name):
    x = frame[[name, 'valid', 'r', 'resolved']].dropna()
    if name in ('opp_touched', 'dow'):
        groups = x.groupby(name)
    else:
        groups = x.groupby(pd.qcut(x[name], 5, duplicates='drop'), observed=True)
    return [dict(bin=str(k), **rate(g)) for k, g in groups]


def stratified_p(frame, score, top, seed, draws=2000):
    """Week-stratified permutation: shuffle scores among the same week's touches."""
    rng = np.random.default_rng(seed)
    cut = np.quantile(score, 1-top)
    observed = frame.valid.to_numpy()[score >= cut].mean()
    groups = [np.flatnonzero(frame.week.to_numpy() == w) for w in frame.week.unique()]
    v = frame.valid.to_numpy(); count = 0
    for _ in range(draws):
        s = score.copy()
        for g in groups:
            s[g] = rng.permutation(s[g])
        count += v[s >= cut].mean() >= observed
    return float((count+1)/(draws+1)), float(cut)


def fit(train, cols):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.impute import SimpleImputer
    model = make_pipeline(SimpleImputer(strategy='median'), StandardScaler(), LogisticRegression(C=.5, max_iter=2000))
    model.fit(train[cols], train.valid.astype(int))
    return model


def run():
    cfg = json.loads((EXP/'config.json').read_text())
    if subprocess.run(['git','diff','--quiet','HEAD','--','yoyo/evaluation/monday_1618_validity.py'], cwd=ROOT).returncode:
        raise RuntimeError('Commit builder first')
    data, controls, coverage = build(cfg)
    data.to_csv(EXP/'touches.csv.gz', index=False); controls.to_csv(EXP/'random_geometry.csv.gz', index=False)
    from sklearn.metrics import roc_auc_score
    early, late = data[data.segment.eq(0)], data[data.segment.eq(1)]
    # Univariate screen on the earlier period only; selection is frozen here.
    screen = {}
    for name in FEATURES:
        x = early[[name, 'valid']].dropna()
        auc = roc_auc_score(x.valid, x[name]) if x.valid.nunique() == 2 else .5
        screen[name] = dict(auc=float(auc), quintiles=quintiles(early, name))
    chosen = [n for n, s in screen.items() if abs(s['auc']-.5) >= cfg['min_screen_auc_gap']]
    model = fit(early, chosen or FEATURES)
    cols = chosen or FEATURES
    late_score = model.predict_proba(late[cols])[:, 1]
    early_score = model.predict_proba(early[cols])[:, 1]
    p, cut = stratified_p(late.reset_index(drop=True), late_score, cfg['top_fraction'], cfg['seed'])
    early_cut = float(np.quantile(early_score, 1-cfg['top_fraction']))
    selected_late = late[late_score >= early_cut]
    validation = dict(chosen=cols, coefficients=dict(zip(cols, map(float, model[-1].coef_[0]))),
        early_auc=float(roc_auc_score(early.valid, early_score)), late_auc=float(roc_auc_score(late.valid, late_score)),
        early_threshold=early_cut, late_selected_by_early_threshold=rate(selected_late),
        late_top_fraction=rate(late[late_score >= cut]), late_all=rate(late),
        late_top_week_stratified_p=p, late_univariate_auc={n: float(roc_auc_score(late[[n,'valid']].dropna().valid,
            late[[n,'valid']].dropna()[n])) for n in FEATURES})
    by_symbol = {s: rate(g) for s, g in data[data.symbol.isin(cfg['focus'])].groupby('symbol')}
    summary = dict(config=cfg, symbols=len(coverage), touches=len(data), base=dict(all=rate(data), early=rate(early), late=rate(late)),
        random_geometry=dict(all=rate(controls)), screen=screen, validation=validation, focus=by_symbol,
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
        training_eligible=False, production_eligible=False)
    dump(EXP/'coverage.json', coverage); dump(EXP/'summary.json', summary)
    print(json.dumps(dict(base=summary['base'], random=summary['random_geometry'], validation=validation), ensure_ascii=False, indent=1))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--config', type=Path, default=EXP/'config.json')
    args = parser.parse_args(); EXP = args.config.resolve().parent; run()
