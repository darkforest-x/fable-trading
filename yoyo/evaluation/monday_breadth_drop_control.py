"""Breadth signal vs similar same-coin drops without a breadth crash (2026-11-08).

Owner 2026-11-08 approved a stricter control for the 13 native 5m cases where 50-99 coins first touched the weekly
lower 1.618 in one bar and no new coin touched it for 30 minutes (exp-monday-breadth-six-day-excursions-20261108-v1).
The question is whether those six-day returns come from the breadth signal or from the coin's own sharp drop.

Treatment: the frozen event decisions from the six-day study. Controls: for each event and coin, the 10 nearest 5m
entry opens of the same coin within +-365 days whose own drop profile is similar but which had no >=25-coin lower
bar in the 24 hours before entry. All matching features use only closes before the entry open (no look-ahead):
  drop24   min(12 closes before T) / max(288 closes before T) - 1
  bounce1h open(T) / min(12 closes before T) - 1
  trend30  close before T / close 8640 bars earlier - 1
  vol7     std of 5m log close changes over the 2016 bars before T
Both arms use the same price series and the same VectorBT six-day fixed exit (monday_breadth_reversal_window
.trade_returns, taker 0.05% on both fills), run once. The event returns must equal the six-day study's net_pct.
"""
from __future__ import annotations
import argparse
import json
import subprocess
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation.monday_limit_alarm_entry import committed

CODE = 'yoyo/evaluation/monday_breadth_drop_control.py'
HOLD = 1728


def features(frame):
    prev = frame.close.shift(1)
    low1h = prev.rolling(12).min()
    return pd.DataFrame(dict(drop24=low1h/prev.rolling(288).max()-1, bounce1h=frame.open/low1h-1,
                             trend30=prev/prev.shift(8640)-1, vol7=np.log(prev).diff().rolling(2016).std()))


def breadth_hits(ev, index, min_coins):
    """Per entry open T: count of >=min_coins lower bars whose close falls in (T-24h, T]."""
    bars = ev[(ev.minutes == 5)].drop_duplicates('known').set_index('known').initial
    hit = (bars >= min_coins).astype(float).reindex(index, fill_value=0.)
    return hit


def population(ev, spec):
    x = ev[(ev.minutes == spec['minutes']) & ev.initial.between(spec['initial_min'], spec['initial_max'])
           & (ev.new_coins == spec['new_coins'])]
    return pd.DatetimeIndex(sorted(x.decision.unique()))


def match(feat, ok, t, k, caliper, spacing, trend_caliper=None):
    """Greedy nearest controls for one event entry t from candidate mask ok.

    trend_caliper (v2, added after the v1 balance check showed events in stronger 30-day trends): absolute
    limit on |trend30 control - trend30 event|. None keeps the v1 behaviour.
    """
    f = feat[ok]
    e = feat.loc[t]
    near = (f.drop24-e.drop24).abs() <= caliper*abs(e.drop24)
    if trend_caliper is not None:
        near &= (f.trend30-e.trend30).abs() <= trend_caliper
    f = f[near]
    if f.empty:
        return []
    sd = f.std().replace(0, np.nan)
    dist = (((f-e)/sd)**2).sum(axis=1).pow(.5).sort_values(kind='mergesort')
    picked = []
    for c in dist.index:
        if all(abs((c-p).total_seconds()) >= spacing*3600 for p in picked):
            picked.append(c)
            if len(picked) == k:
                break
    return picked


def run(config):
    from yoyo.evaluation.monday_breadth_excursion_prices import prices
    from yoyo.evaluation.monday_breadth_reversal_window import trade_returns
    exp = config.resolve().parent
    cfg = json.loads(config.read_text())
    committed(CODE, str(config.resolve().relative_to(ROOT)))
    ev = pd.read_csv(ROOT/cfg['source_events'], usecols=['known', 'decision', 'initial', 'new_coins', 'minutes', 'asset', 'net_pct'],
                     parse_dates=['known', 'decision'])
    pool_cfg = cfg['control_pool']
    rows, checks = [], {}
    for name in ('primary', 'secondary'):
        decisions = population(ev, cfg[f'{name}_population'])
        for asset in cfg['assets']:
            frame = prices(asset)
            feat = features(frame)
            idx = frame.index
            hit = breadth_hits(ev, idx, pool_cfg['no_breadth_bar_min_coins']).to_numpy()
            lb = pool_cfg['no_breadth_lookback_hours']*12
            cs = np.r_[0., np.cumsum(hit)]
            pos = np.arange(len(idx))
            # hit[i] marks a >=min_coins lower bar whose close equals open time idx[i].
            past = pd.Series(cs[pos+1]-cs[np.maximum(0, pos+1-lb)] > 0, index=idx)                 # closes in (T-24h, T]
            future = pd.Series(cs[np.minimum(len(idx), pos+1+HOLD)]-cs[pos+1] > 0, index=idx)      # closes in (T, T+6d]
            ahead = pd.Series(pos+HOLD < len(idx), index=idx) & frame.open.shift(-HOLD).notna()
            base_ok = feat.notna().all(axis=1) & ahead & ~past
            near_event = pd.Series(False, index=idx)
            for d in decisions:
                near_event |= (idx > d-pd.Timedelta(days=pool_cfg['exclude_days_around_population_events'])) & \
                              (idx < d+pd.Timedelta(days=pool_cfg['exclude_days_around_population_events']))
            base_ok &= ~near_event
            for arm, ok_all in (('main', base_ok), ('conservative', base_ok & ~future)):
                for d in decisions:
                    if d not in feat.index or feat.loc[d].isna().any():
                        continue
                    w = (idx >= d-pd.Timedelta(days=pool_cfg['window_days'])) & (idx <= d+pd.Timedelta(days=pool_cfg['window_days']))
                    picked = match(feat, ok_all & w, d, pool_cfg['k'], pool_cfg['drop24_caliper_relative'], pool_cfg['spacing_hours'],
                                   pool_cfg.get('trend30_caliper_abs'))
                    rows.append(dict(population=name, arm=arm, asset=asset, event=d, role='event', t=d))
                    rows += [dict(population=name, arm=arm, asset=asset, event=d, role='control', t=c) for c in picked]
            m = pd.DataFrame([r for r in rows if r['asset'] == asset and r['population'] == name])
            starts = idx.get_indexer(pd.DatetimeIndex(m.t.unique()))
            ret = trade_returns(frame.open.to_numpy(), starts, hold_bars=HOLD)
            ret.index = idx[ret.index]
            for r in rows:
                if r['asset'] == asset and r['population'] == name:
                    r['net_pct'] = float(ret[r['t']])
                    r.update({k: float(v) for k, v in feat.loc[r['t']].items()})
            if name == 'primary':
                src = ev[(ev.asset == asset)].drop_duplicates('decision').set_index('decision').net_pct.reindex(decisions)
                got = ret.reindex(decisions)
                checks[asset] = dict(events=int(len(decisions)), max_abs_diff_pct=float((got-src).abs().max()))
    out = pd.DataFrame(rows)
    out.to_csv(exp/'matches.csv.gz', index=False)
    summarize(exp, cfg, out, checks)


def summarize(exp, cfg, out, checks):
    rng_seed, draws = cfg['bootstrap_seed'], cfg['bootstrap_draws']
    res = {}
    for (pop, arm, asset), g in out.groupby(['population', 'arm', 'asset']):
        e = g[g.role == 'event'].set_index('event')
        c = g[g.role == 'control'].groupby('event')
        cm = c.net_pct.mean()
        diff = (e.net_pct-cm.reindex(e.index)).dropna()
        rng = np.random.default_rng(rng_seed)
        v = diff.to_numpy()
        boot = v[rng.integers(0, len(v), (draws, len(v)))].mean(1) if len(v) else np.array([np.nan])
        feats = ['drop24', 'bounce1h', 'trend30', 'vol7']
        res.setdefault(pop, {}).setdefault(arm, {})[asset] = dict(
            events=int(len(e)), matched_events=int(len(diff)), controls_per_event=float(c.size().mean()),
            event_mean_pct=float(e.net_pct.mean()), control_mean_pct=float(cm.mean()),
            control_positive_share=float((g[g.role == 'control'].net_pct > 0).mean()),
            excess_mean_pp=float(diff.mean()), excess_median_pp=float(diff.median()), excess_positive=int((diff > 0).sum()),
            excess_ci95_pp=[float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))],
            balance_event={f: float(e[f].mean()) for f in feats},
            balance_control={f: float(g[g.role == 'control'][f].mean()) for f in feats},
            per_event=[dict(event=str(t), event_net=float(e.net_pct[t]), control_mean=float(cm.get(t, np.nan)),
                            controls=int(c.size().get(t, 0))) for t in e.index])
    p = res['primary']['main']
    decision = {a: bool(p[a]['excess_ci95_pp'][0] > 0 and p[a]['excess_mean_pp'] > 0) for a in p}
    dump(exp/'summary.json', dict(config=cfg, checks=checks, results=res, decision=decision,
                                 source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                                 training_eligible=False, production_eligible=False))
    print(json.dumps(checks))
    for pop in res:
        for arm in res[pop]:
            for a, s in res[pop][arm].items():
                print(pop, arm, a, s['events'], round(s['controls_per_event'], 1), 'event', round(s['event_mean_pct'], 2), 'ctrl',
                      round(s['control_mean_pct'], 2), 'excess', round(s['excess_mean_pp'], 2), [round(x, 2) for x in s['excess_ci95_pp']],
                      f"{s['excess_positive']}/{s['matched_events']}")
    print('decision', decision)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, required=True)
    run(ap.parse_args().config)
