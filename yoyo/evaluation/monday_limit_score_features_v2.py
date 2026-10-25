"""Entry-score feature table v2 for the Monday1618 limit-reclaim rule (owner2026-10-25).

Owner asked to improve the entry score with market context, volatility
expansion, the project's MA-structure features and per-coin history, then to
compare logistic regression with LightGBM under a walk-forward split. One row
per alt signal fill (60m confirmation; Codex ledger, all history since 2020).
Every feature uses bars strictly before the touch bar (index touch - 1, the
submission bar) or earlier events that had already exited; windows below are in
5m bars unless stated.
  A  base (10): the frozen score's inputs from events_60m (patterns3) and the
     Codex ledger: bucket, log_vol_ratio, log_approach, opp_first,
     monday_close_side, log_liquidity, toward24, cluster24, side, range_pct.
  B  market (alt index from all 626 symbols' completed 1h bars before the touch
     hour): mkt_toward_1h / mkt_toward_24h = side-aligned median 1h / 24h return
     (positive = market moving toward the level), mkt_breadth_toward = share of
     symbols whose last 1h return moved > 2 percent toward the level, mkt_disp_1h
     = cross-sectional std of 1h returns, liq_rank = percentile of this symbol's
     7-day quote volume among symbols, cluster1h = other fills on the same side in
     the prior 60 minutes.
  C  volatility expansion: atr14 / atr288, atr14 / atr2016 (mean true range),
     rng12_vs_288 = high-low of the last 12 bars / high-low of the last 288.
  D  MA structure: the 28 FEATURE_COLUMNS of yoyo.layers.l2_judgment.features on
     completed 15m bars before the touch (yoyo.data.indicators.add_indicators),
     short rows side-aligned (align_short_feature_rows); prefixed l2_.
  E  per-coin history: prior_fills, prior_reclaim_rate, prior_win_rate over this
     symbol's earlier fills whose worst-path exit was before this touch;
     coin_age_days since the symbol's first bar.
  F  range structure: range_rel = Monday range percent / median high-low percent
     of the 8 prior weeks; hours_since_monday = hours from the NY Monday close to
     the touch; dow = NY weekday of the touch; dist7d_R = side-aligned distance
     from the level to the prior 7-day extreme in R (positive = the level is a
     new 7-day extreme).
Labels (no feature reads them): worst-path near-target net with 10bp slippage on
non-target exits (Codex O-H-L-C and monotone O-L-H-C v2), win, reclaim (no failed
reclaim on the worse path), and worst-path target hits for range 0.5 / 0.618 /
far edge / opposite 1618 (exp-monday-limit-reclaim-olhc-allexits-20261025-v1).
Read-only; nothing is re-executed.
"""
from __future__ import annotations
import json
import subprocess
from concurrent.futures import ProcessPoolExecutor
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation.monday_limit_reclaim_patterns import SRC
from yoyo.evaluation import monday_limit_reclaim_allexits as ax

EXP = ROOT/'experiments/active/exp-monday-limit-score-features-v2-20261025-v1'
EVENTS = ROOT/'experiments/active/exp-monday-limit-reclaim-patterns3-20261024-v1/events_60m.csv.gz'
LCOLS = ['trade_id', 'population', 'reclaim_minutes', 'exit_mode', 'closed', 'side', 'entry', 'level', 'monday_high', 'monday_low',
         'target_price', 'entry_i', 'bucket', 'entry_time', 'exit_time']


def per_symbol(symbol):
    """Bar features per event plus this symbol's 1h close / quote-volume series for the market pass."""
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    from yoyo.data.indicators import add_indicators
    from yoyo.layers.l2_judgment.features import add_features, extract_feature_rows, align_short_feature_rows, FEATURE_COLUMNS
    job = json.loads((SRC/symbol/'identity.json').read_text())['job']
    led = pd.read_csv(SRC/symbol/'trades.csv.gz', usecols=LCOLS)
    led = led[(led.population == 'signal') & (led.reclaim_minutes == 60) & (led.exit_mode == 'near_monday_edge') & led.closed]
    f, _ = load_job(job)
    hourly = pd.DataFrame(dict(close=f.close.resample('1h').last(), qv=(f.close*f.volume).resample('1h').sum())).dropna()
    hourly.index = hourly.index+pd.Timedelta(hours=1)  # label by close time
    if led.empty:
        return pd.DataFrame(), hourly.assign(symbol=symbol)
    h, l, c = f.high.to_numpy(), f.low.to_numpy(), f.close.to_numpy()
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum(h-l, np.maximum(abs(h-pc), abs(l-pc)))
    ctr = np.r_[0., np.cumsum(tr)]
    mean_tr = lambda a, b: (ctr[b]-ctr[a])/max(b-a, 1)
    f15 = f.resample('15min').agg(dict(open='first', high='max', low='min', close='last', volume='sum')).dropna()
    feat15 = add_features(add_indicators(f15))
    rows = []
    for r in led.itertuples():
        k = int(r.entry_i)-1
        if k < 300:
            continue
        side, level = int(r.side), float(r.entry)
        R = abs(r.target_price-level)/1.618
        touch = f.index[k+1]
        a14, a288, a2016 = mean_tr(k-13, k+1), mean_tr(max(0, k-287), k+1), mean_tr(max(0, k-2015), k+1)
        hi12, lo12 = h[k-11:k+1].max(), l[k-11:k+1].min(); hi288, lo288 = h[max(0, k-287):k+1].max(), l[max(0, k-287):k+1].min()
        lo7, hi7 = l[max(0, k-2015):k+1].min(), h[max(0, k-2015):k+1].max()
        mon = pd.Timestamp(r.trade_id.split(':')[1], tz='America/New_York').tz_convert('UTC')
        weekly = []
        for j in range(1, 9):
            a, b = f.index.searchsorted(mon-pd.Timedelta(days=7*j)), f.index.searchsorted(mon-pd.Timedelta(days=7*(j-1)))
            if b-a > 1000:
                weekly.append((h[a:b].max()-l[a:b].min())/c[b-1])
        row = dict(trade_id=r.trade_id, event=':'.join(r.trade_id.split(':')[:3]), symbol=symbol, side=side, touch=touch,
                   atr_exp_1d=a14/a288 if a288 else np.nan, atr_exp_7d=a14/a2016 if a2016 else np.nan,
                   rng12_vs_288=(hi12-lo12)/(hi288-lo288) if hi288 > lo288 else np.nan,
                   range_rel=((r.monday_high-r.monday_low)/level)/np.median(weekly) if len(weekly) >= 3 else np.nan,
                   hours_since_monday=(touch-(mon+pd.Timedelta(hours=24)))/pd.Timedelta(hours=1),
                   dow=int(touch.tz_convert('America/New_York').weekday()),
                   dist7d_R=((lo7-level) if side == 1 else (level-hi7))/R if R > 0 else np.nan,
                   coin_age_days=(touch-f.index[0])/pd.Timedelta(days=1),
                   exit_time=pd.Timestamp(r.exit_time))
        j15 = int(feat15.index.searchsorted(touch-pd.Timedelta(minutes=15), side='right'))-1
        if j15 >= 300:
            x = extract_feature_rows(feat15, [j15])
            if side == -1:
                x = align_short_feature_rows(x, feat15, [j15])
            row |= {f'l2_{k_}': float(v) for k_, v in x.iloc[0][FEATURE_COLUMNS].items()}
        rows.append(row)
    return pd.DataFrame(rows), hourly.assign(symbol=symbol)


def labels(symbol):
    g, _ = ax.load(symbol)
    g = g[g.m == 60]
    worse = g.net_l < g.net_o
    g = g.assign(net=np.where(worse, g.net_l, g.net_o), reason_w=np.where(worse, g.reason_l, g.reason_o))
    near = g[g.leg == 'near'].set_index('event')
    out = pd.DataFrame(dict(net=near.net, win=(near.net > 0).astype(int),
                            reclaim=((near.reason_o != 11) & (near.reason_l != 11)).astype(int)))
    for leg in ('r05', 'r0618', 'far', 'opp1618'):
        x = g[g.leg == leg].set_index('event')
        out[f'hit_{leg}'] = (x.reason_w == 4).astype(float).reindex(out.index)
    return out.reset_index()


def run():
    p = 'yoyo/evaluation/monday_limit_score_features_v2.py'
    if subprocess.run(['git','diff','--quiet','HEAD','--',p], cwd=ROOT).returncode or \
       subprocess.run(['git','ls-files','--error-unmatch',p], cwd=ROOT, capture_output=True).returncode:
        raise RuntimeError('Commit builder first')
    cfg = json.loads((EXP/'config.json').read_text())
    syms = sorted(d.name for d in ax.V2.iterdir() if (d/'done.json').exists())
    if cfg.get('symbols_limit'):
        syms = syms[:cfg['symbols_limit']]
    with ProcessPoolExecutor(cfg.get('workers', 4)) as ex:
        got = list(ex.map(per_symbol, syms, chunksize=4))
        lab = pd.concat(list(ex.map(labels, syms, chunksize=8)), ignore_index=True)
    bars = pd.concat([g for g, _ in got if len(g)], ignore_index=True)
    hourly = pd.concat([h for _, h in got], ignore_index=False)
    close = hourly.pivot_table(index=hourly.index, columns='symbol', values='close').sort_index()
    qv = hourly.pivot_table(index=hourly.index, columns='symbol', values='qv').sort_index()
    r1 = close.pct_change(1, fill_method=None); r24 = close.pct_change(24, fill_method=None)
    mkt = pd.DataFrame(dict(med1=r1.median(axis=1), med24=r24.median(axis=1), down2=(r1 < -.02).sum(axis=1)/r1.notna().sum(axis=1),
                            up2=(r1 > .02).sum(axis=1)/r1.notna().sum(axis=1), disp=r1.std(axis=1)))
    liq = qv.rolling(168, min_periods=48).sum().rank(axis=1, pct=True)
    ev = pd.read_csv(EVENTS)
    ev['event'] = ev.symbol+':'+ev.week+':'+np.where(ev.side_n == 1, '+1', '-1')
    t = bars.merge(ev[['event', 'vol_ratio', 'approach_min', 'opp_first', 'monday_close_side', 'liquidity', 'toward24', 'cluster24']], on='event', how='left')
    led = pd.concat([pd.read_csv(SRC/s/'trades.csv.gz', usecols=LCOLS+['week']) for s in syms], ignore_index=True)
    led = led[(led.population == 'signal') & (led.reclaim_minutes == 60) & (led.exit_mode == 'near_monday_edge')]
    t = t.merge(led[['trade_id', 'bucket', 'monday_high', 'monday_low', 'level']], on='trade_id', how='left')
    t['log_vol_ratio'] = np.log(t.vol_ratio.clip(lower=.05)); t['log_approach'] = np.log1p(t.approach_min.clip(lower=0))
    t['opp_first'] = (t.opp_first == '是').astype(int); t['log_liquidity'] = np.log10(t.liquidity.clip(lower=1e4))
    t['range_pct'] = 100*(t.monday_high-t.monday_low)/t.level
    hour = t.touch.dt.floor('1h')
    m = mkt.reindex(hour).to_numpy()
    t['mkt_toward_1h'] = -t.side*m[:, 0]; t['mkt_toward_24h'] = -t.side*m[:, 1]
    t['mkt_breadth_toward'] = np.where(t.side == 1, m[:, 2], m[:, 3]); t['mkt_disp_1h'] = m[:, 4]
    t['liq_rank'] = [liq.at[hh, s] if hh in liq.index and s in liq.columns else np.nan for hh, s in zip(hour, t.symbol)]
    t = t.sort_values('touch').reset_index(drop=True)
    tt = t.touch.astype('int64').to_numpy(); sd = t.side.to_numpy()
    t['cluster1h'] = 0
    for s_ in (1, -1):
        idx = np.flatnonzero(sd == s_); x = tt[idx]
        t.loc[idx, 'cluster1h'] = np.searchsorted(x, x, side='left')-np.searchsorted(x, x-3_600_000_000_000, side='left')
    t = t.merge(lab, on='event', how='left')
    prior = []
    for s, g in t.groupby('symbol'):
        g = g.sort_values('touch')
        ex = g.exit_time.astype('int64').to_numpy(); to = g.touch.astype('int64').to_numpy()
        rc, wn = g.reclaim.to_numpy(float), g.win.to_numpy(float)
        for i in range(len(g)):
            done = ex < to[i]
            done[i:] = False
            n = int(done.sum())
            prior.append((g.index[i], n, rc[done].mean() if n else np.nan, wn[done].mean() if n else np.nan))
    pr = pd.DataFrame(prior, columns=['ix', 'prior_fills', 'prior_reclaim_rate', 'prior_win_rate']).set_index('ix')
    t = t.join(pr)
    groups = dict(A=['bucket', 'log_vol_ratio', 'log_approach', 'opp_first', 'monday_close_side', 'log_liquidity', 'toward24', 'cluster24', 'side', 'range_pct'],
                  B=['mkt_toward_1h', 'mkt_toward_24h', 'mkt_breadth_toward', 'mkt_disp_1h', 'liq_rank', 'cluster1h'],
                  C=['atr_exp_1d', 'atr_exp_7d', 'rng12_vs_288'],
                  D=[c for c in t.columns if c.startswith('l2_')],
                  E=['prior_fills', 'prior_reclaim_rate', 'prior_win_rate', 'coin_age_days'],
                  F=['range_rel', 'hours_since_monday', 'dow', 'dist7d_R'])
    keep = ['event', 'trade_id', 'symbol', 'side', 'touch', 'exit_time']+[c for g in groups.values() for c in g if c != 'side']+\
           ['net', 'win', 'reclaim', 'hit_r05', 'hit_r0618', 'hit_far', 'hit_opp1618']
    t = t[keep]
    EXP.mkdir(exist_ok=True)
    t.to_csv(EXP/'features_v2.csv.gz', index=False)
    cover = {g: {c: float(t[c].notna().mean()) for c in cols} for g, cols in groups.items()}
    dump(EXP/'summary.json', dict(config=cfg, rows=len(t), symbols=len(syms), groups=groups, coverage=cover,
                                  time_range=[str(t.touch.min()), str(t.touch.max())], label_rates=dict(win=float(t.win.mean()), reclaim=float(t.reclaim.mean())),
                                  source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(), training_eligible=False, production_eligible=False))
    print(len(t), t.touch.min(), t.touch.max(), json.dumps({g: round(float(np.mean(list(v.values()))), 3) for g, v in cover.items()}))


if __name__ == '__main__':
    run()
