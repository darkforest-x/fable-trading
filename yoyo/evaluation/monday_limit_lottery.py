"""Lottery version of the Monday1618 limit-reclaim rule: all-in leverage, blow-up and restart (owner 2026-10-27).

Owner: "我希望再做一版本盈亏比极高的策略 ... 本金很小 只有几千块 就算爆仓也没事 ... 近半年我可以爆仓5次 但是只要被我抓住
一次 我能赚大的 翻几十甚至上百上千倍". Read-only on NautilusTrader fills (worse intrabar path, 10bp slippage on non-target
exits, the two bad source bars dropped; MAE from Codex 5m bars as in monday_limit_spike_opt.legs):
  sets   A v3 score >= 0.6, opposite 1618 target, -30 percent backstop (exp-monday-limit-v3-levels-20261025-v1);
         B the same without backstop (liquidation is the stop);
         C A restricted to 15m EMA order >= 3 (spike-HW filter);
         D A restricted to score >= 0.8;
         E near Monday edge target, score >= 0.6 and order >= 3 (spike-HW trades, grid near_P18), for contrast.
  sizing one position at a time, margin = a fraction f (1.0 or 0.5) of the account at leverage L (3 / 5 / 10 / 20); a
         position whose MAE reaches 100/L - 1 percent is liquidated (loses its margin); otherwise the account changes
         by margin x L x net (never below -margin). When the account falls under 10 percent of a stake it counts as a
         blow-up and a fresh 1000 USDT stake starts. Fills that arrive while a position is open are skipped; ties in
         random order (TIES draws).
  window rolling 26-week windows starting each month from 2023-01 (the last ends 2026-09); per window: stakes used,
         blow-ups, peak account and final account in stakes, net result = final - stakes used.
Reported per set x f x L: probability that the account ever reaches 10x / 100x / 1000x one stake within the window,
mean blow-ups, median and mean net result in stakes. In-sample: filters and targets come from the same history.
Added after the first run: per-window medians for the settings listed in cfg['detail'], to show which half-years
carry the 100x / 1000x outcomes.
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
from yoyo.evaluation.monday_limit_be_scalein_compare import BAD_EVENTS, clean
from yoyo.evaluation import monday_limit_spike_opt as so

EXP = ROOT/'experiments/active/exp-monday-limit-lottery-20261027-v1'
A = ROOT/'experiments/active'


def window(t, f, lev, seed):
    """One 26-week window: one position at a time, all-in fraction f, restart after a blow-up."""
    rng = np.random.default_rng(seed)
    o = np.lexsort((rng.random(len(t)), t.entry_ns.to_numpy()))
    t = t.iloc[o].reset_index(drop=True)
    liq = 100./lev-1.
    acct, stakes, blow, peak, busy_until = 1000., 1, 0, 1000., -1
    for e, x, net, mae in zip(t.entry_ns.to_numpy(), t.exit_ns.to_numpy(), t.net.to_numpy()/1e4, t.mae_pct.to_numpy()):
        if e < busy_until:
            continue
        busy_until = x if x > e else e+1
        m = f*acct
        acct += -m if mae >= liq else max(-m, m*lev*net)
        peak = max(peak, acct)
        if acct < 100.:
            blow += 1; stakes += 1; acct = 1000.
    return dict(stakes=stakes, blowups=blow, peak=peak/1000, final=acct/1000, net=acct/1000-stakes)


def run(config):
    exp = config.resolve().parent
    for p in ('yoyo/evaluation/monday_limit_lottery.py', str(config.resolve().relative_to(ROOT))):
        if subprocess.run(['git','diff','--quiet','HEAD','--',p], cwd=ROOT).returncode or \
           subprocess.run(['git','ls-files','--error-unmatch',p], cwd=ROOT, capture_output=True).returncode:
            raise RuntimeError('Commit builder and config first')
    cfg = json.loads(config.read_text())
    tn = pd.read_csv(ROOT/cfg['trades'], usecols=['event', 'variant', 'score'])
    feat = pd.read_csv(ROOT/cfg['features'], usecols=['event', 'l2_order_score']).rename(columns={'l2_order_score': 'order'})
    ev = tn[(tn.variant == 'v3_be17') & (tn.score >= .6)].merge(feat, on='event', how='left')
    events = set(ev.event)-BAD_EVENTS; syms = sorted({e.split(':')[0] for e in events})
    src = {'exp-monday-limit-v3-levels-20261025-v1': ['opp1618_P30', 'opp1618_far'], 'exp-monday-limit-v3-grid-20261025-v1': ['near_P18']}
    with ProcessPoolExecutor(4) as ex:
        t = pd.concat([d for d in ex.map(so.legs, [(s, src, events) for s in syms], chunksize=4) if len(d)], ignore_index=True)
    t = t.merge(ev[['event', 'order']], on='event', how='left')
    t['entry_ns'] = t.entry_t.astype('int64'); t['exit_ns'] = t.exit_t.astype('int64')
    sets = {'A_v3_ge06_opp1618_P30': t[t.variant == 'opp1618_P30'],
            'B_v3_ge06_opp1618_nostop': t[t.variant == 'opp1618_far'],
            'C_order_ge3_opp1618_P30': t[(t.variant == 'opp1618_P30') & (t.order >= 3)],
            'D_score_ge08_opp1618_P30': t[(t.variant == 'opp1618_P30') & (t.score >= .8)],
            'E_spike_hw_near_P18': t[(t.variant == 'near_P18') & (t.order >= 3)]}
    starts = pd.date_range(cfg['first_window'], cfg['last_window'], freq='MS', tz='UTC')
    res = dict(sets={k: dict(n=int(len(v)), win_pct=float(100*(v.net > 1).mean()), mean_bp=float(v.net.mean()),
                             mae_q90=float(v.mae_pct.quantile(.9))) for k, v in sets.items()}, windows=len(starts), grid={})
    for name, x in sets.items():
        for f in cfg['fractions']:
            for L in cfg['leverage']:
                rows = []
                for k, s0 in enumerate(starts):
                    y = x[(x.entry_t >= s0) & (x.entry_t < s0+pd.Timedelta(weeks=26))].reset_index(drop=True)
                    for r in range(cfg['ties']):
                        rows.append(window(y, f, L, cfg['seed']+1000*k+r))
                d = pd.DataFrame(rows)
                key = f'{name}|f{f}|L{L}'
                if key in cfg.get('detail', []):
                    d['start'] = np.repeat([str(s0.date()) for s0 in starts], cfg['ties'])
                    res.setdefault('detail', {})[key] = {k: dict(peak_median=float(g.peak.median()), peak_max=float(g.peak.max()),
                                                                  final_median=float(g.final.median()), blowups_mean=float(g.blowups.mean()))
                                                         for k, g in d.groupby('start')}
                res['grid'][f'{name}|f{f}|L{L}'] = dict(
                    p_peak_10x=float(100*(d.peak >= 10).mean()), p_peak_100x=float(100*(d.peak >= 100).mean()),
                    p_peak_1000x=float(100*(d.peak >= 1000).mean()), blowups_mean=float(d.blowups.mean()), blowups_p90=float(d.blowups.quantile(.9)),
                    net_median=float(d.net.median()), net_mean=float(d.net.mean()), p_net_positive=float(100*(d.net > 0).mean()),
                    final_median=float(d.final.median()), peak_median=float(d.peak.median()))
    res = clean(res)
    dump(exp/'summary.json', dict(config=cfg, results=res, source_commit=subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip(),
                                 training_eligible=False, production_eligible=False))
    print(json.dumps(res['sets'], ensure_ascii=False), res['windows'])
    for k, v in res['grid'].items():
        print(f"{k:34s} P>=10x {v['p_peak_10x']:5.1f} P>=100x {v['p_peak_100x']:5.1f} P>=1000x {v['p_peak_1000x']:5.1f} "
              f"blow {v['blowups_mean']:.1f}/{v['blowups_p90']:.0f} net med {v['net_median']:.2f} mean {v['net_mean']:.2f} P(net>0) {v['p_net_positive']:.0f}")


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, default=EXP/'config.json')
    run(ap.parse_args().config)
