"""Where inside an alarm episode does the 25-coin alarm-entry edge sit, and did the growing universe move it? (2026-11-08)

Owner 2026-11-08: "你能基于这些研究 再找找还有没有更好的方案吗 做成策略". The breadth BTC/ALT rebound family already
failed matched controls after 6.1M specifications (exp-monday-breadth-optimization-20261108-v1); the alarm-entry limit
rule (exp-monday-limit-alarm-entry-20261029-v1) is the one alarm rule that beat random-time entries in every period
but earned only +3bp per trade in 2025. This read-only diagnostic asks two pre-stated questions of the saved fills:
1. Universe growth: the fixed 25-coin threshold is a shrinking share of the active universe (distinct symbols with any
   first weekly touch in the 8 weeks before, causal). If share explained the 2025 weakness, 2025 and 2026 (similar
   universes) should behave alike and higher counts should repair 2025 in the saved threshold study.
2. Episode position: per-trade net by follower rank, minutes after the alarm bar and final episode size (size is known
   only after the 30-minute window, so it is a diagnostic, not a rule). A causal cap is only worth a rule if its sign is
   the same in 2020-24, 2025 and 2026.
No new fills: the NautilusTrader fills of the alarm, combo and threshold studies are reused exactly as the threshold
summary loads them (hold plans, worse path, 10bp slippage on non-target exits, BAD_EVENTS removed). Episodes are the
independent unit for the bootstrap intervals.
"""
from __future__ import annotations
import argparse
import json
import subprocess
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation import monday_limit_alarm_entry as ae
from yoyo.evaluation.monday_limit_alarm_combo import load, source_events

A = ROOT/'experiments/active'


def fills(cfg, workers):
    from yoyo.evaluation.monday_limit_be_scalein_compare import BAD_EVENTS
    src, combo, thr = ROOT/cfg['source'], ROOT/cfg['combo'], ROOT/cfg['threshold_study']
    win = pd.read_csv(thr/'alarm_windows.csv.gz')
    old = source_events(src)
    rest = pd.read_csv(combo/'rest_events.csv.gz'); rest = set(rest.event[~rest.filled_by_source])
    new = set(win.event)-old-rest
    parts = []
    for e, keys in ((src, old), (combo, rest), (thr, new)):
        x, _ = load(e, sorted({k.split(':')[0] for k in keys}), cfg['slippage_bp'], workers)
        parts.append(x[x.event.isin(keys)])
    t = pd.concat(parts, ignore_index=True)
    return t[(t.act == 'hold') & ~t.event.isin(BAD_EVENTS)], win


def universe(t, weeks_back):
    """Median per calendar year of distinct symbols with any first weekly touch in the prior `weeks_back` weeks."""
    wk = t.groupby('week').symbol.apply(set)
    weeks = sorted(wk.index)
    act = {}
    for i, w in enumerate(weeks):
        s = set()
        for ww in weeks[max(0, i-weeks_back):i]:
            s |= wk[ww]
        act[w] = len(s)
    act = pd.Series(act); act.index = pd.to_datetime(act.index)
    act = act[act.index >= '2020-01-01']
    return {str(y): float(v) for y, v in act.groupby(act.index.year).median().items()}


def ci(net, episode, seed, draws=2000):
    per = net.groupby(episode).mean().to_numpy()
    rng = np.random.default_rng(seed)
    boot = per[rng.integers(0, len(per), (draws, len(per)))].mean(1)
    return dict(trades=int(len(net)), episodes=int(len(per)), trade_mean_bp=float(net.mean()), episode_mean_bp=float(per.mean()),
                ci95_bp=[float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))])


def summarize(config, workers):
    exp = config.resolve().parent
    cfg = json.loads(config.read_text())
    ae.committed('yoyo/evaluation/monday_limit_alarm_episode_diag.py', str(config.resolve().relative_to(ROOT)))
    t, win = fills(cfg, workers)
    touch = ae.touches()
    uni = universe(touch, cfg['universe_weeks'])
    tch = touch.drop_duplicates('event').set_index('event').ms
    al = pd.read_csv(ROOT/cfg['threshold_study']/'alarms.csv.gz')
    al['year'] = pd.to_datetime(al.ms, unit='ms', utc=True).dt.year
    alarms_per_year = {str(k): v for k, v in al.pivot_table(index='year', columns='threshold', values='ms', aggfunc='count')
                       .fillna(0).astype(int).to_dict('index').items()}
    w = win[win.threshold == cfg['threshold']].drop_duplicates('event').set_index('event')
    rows, buckets = [], {}
    for v in cfg['variants']:
        x = t[(t.kind == v) & t.event.isin(w.index)].copy()
        x['alarm_ms'] = x.event.map(w.alarm_ms); x['touch_ms'] = x.event.map(tch)
        if x.touch_ms.isna().any():
            raise RuntimeError('touch time missing')
        x['minutes'] = (x.touch_ms-x.alarm_ms)/60_000
        x = x.sort_values(['alarm_ms', 'touch_ms', 'event'])
        x['rank'] = x.groupby('alarm_ms').cumcount()+1
        x['episode_size'] = x.groupby('alarm_ms').event.transform('count')
        yr = pd.to_datetime(x.alarm_ms, unit='ms', utc=True).dt.year
        x['period'] = np.where(yr <= 2024, '2020-24', yr.astype(str))
        x['rank_b'] = pd.cut(x['rank'], cfg['rank_bins'], labels=cfg['rank_labels']).astype(str)
        x['minutes_b'] = pd.cut(x.minutes, cfg['minute_bins'], labels=cfg['minute_labels']).astype(str)
        x['size_b'] = pd.cut(x.episode_size, cfg['size_bins'], labels=cfg['size_labels']).astype(str)
        for col in ('rank_b', 'minutes_b', 'size_b'):
            for (b, p), g in x.groupby([col, 'period']):
                rows.append(dict(variant=v, split=col, bucket=b, period=p, **ci(g.net, g.alarm_ms, cfg['bootstrap_seed'])))
        for k in cfg['rank_caps']:
            for p, g in x.groupby('period'):
                kept, cut = g[g['rank'] <= k], g[g['rank'] > k]
                buckets.setdefault(v, {}).setdefault(f'cap{k}', {})[p] = dict(
                    kept=ci(kept.net, kept.alarm_ms, cfg['bootstrap_seed']) if len(kept) else None,
                    dropped=ci(cut.net, cut.alarm_ms, cfg['bootstrap_seed']) if len(cut) else None,
                    dropped_sum_pct=float(cut.net.sum()/100))
    tab = pd.DataFrame(rows)
    tab.to_csv(exp/'buckets.csv', index=False)
    sign = (tab.assign(pos=tab.trade_mean_bp > 0).groupby(['variant', 'split', 'bucket']).pos.agg(['sum', 'count']))
    consistent = sign[(sign['count'] == 3) & sign['sum'].isin([0, 3])].reset_index()
    res = dict(universe_median_by_year=uni, alarms_per_year_by_threshold=alarms_per_year, rank_caps=buckets,
               same_sign_all_periods=consistent.to_dict('records'))
    dump(exp/'summary.json', dict(config=cfg, results=res, source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                                 training_eligible=False, production_eligible=False))
    print(json.dumps(dict(universe=uni), ensure_ascii=False))
    print(tab[tab.variant == 'near30'].pivot_table(index=['split', 'bucket'], columns='period', values=['trades', 'trade_mean_bp']).round(1).to_string())
    print(json.dumps(res['rank_caps']['near30'], ensure_ascii=False)[:3000])


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, required=True); ap.add_argument('--workers', type=int, default=6)
    a = ap.parse_args()
    summarize(a.config, a.workers)
