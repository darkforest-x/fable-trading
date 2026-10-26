"""Pyramid (滚仓) groups against the parent alone, and how fast they compound (owner 2026-10-27).

Owner: "你知道滚仓吗，这个可不可行 ... 反正你想尽一切办法去做吧". Read-only on the NautilusTrader fills of
exp-monday-limit-pyramid-20261027-v1 (monday_limit_pyramid). Per event and variant a group is the parent plus its
filled legs, every leg run at the same notional. Leg weights are applied after the run, because a leg's size changes no
exit (the ratchet fires on its fill whatever its size):
  eq    1, 1, 1, 1, 1 (as run);
  half  1, 1/2, 1/4, 1/8, 1/16 (classic shrinking pyramid);
  trail 1, 0, 0, 0, 0: the parent with the step stop only, separating the stop from the added size.
Group net = sum of weight x leg net (bp of one leg's notional), 10bp slippage on every non-target exit, the worse of the
OHLC / OLHC paths per group; the two bad source bars dropped. Liquidation from Codex 5m bars: S = sum over open legs of
weight x side x (worst price / entry - 1), bars from each leg's fill to the bar before its exit (monday_limit_spike_opt.legs
convention) plus the exit price on a non-target exit bar; D = max over bars of (0.01 x open weight - S). The account
liquidates at leverage L when L >= 1/D, stored as mae_pct = 100 D - 1 so that the single-leg rule MAE >= 100/L - 1 applies
unchanged. L is the notional of one leg over the slot capital; the exchange leverage setting must allow the summed legs.
Sizing: monday_limit_langlang.path_run (langlang and allin) from each monthly start, days to 10x / 100x. In-sample:
levels are structural (Monday range multiples, not tuned), but no matched random control has been run for the legs.
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
from yoyo.evaluation.monday_limit_reclaim_patterns import SRC
from yoyo.evaluation.monday_limit_langlang import path_run
from yoyo.evaluation import monday_limit_reclaim_optimize as opt

EXP = ROOT/'experiments/active/exp-monday-limit-pyramid-20261027-v1'
COLS = ['trade_id', 'side', 'entry', 'exit_price', 'reason', 'entry_time', 'exit_time', 'entry_i', 'exit_i', 'net_notional_bp',
        'pyramid_step', 'score', 'event', 'variant']


def groups(args):
    """Worse-path pyramid groups of one symbol for every variant and weight scheme, with the liquidation measure."""
    symbol, weights = args
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    paths = {}
    for p in ('ohlc', 'olhc'):
        f = EXP/p/'symbols'/symbol/'trades.csv.gz'
        if not f.exists():
            return pd.DataFrame()
        t = pd.read_csv(f, usecols=COLS)
        t = t[~t.event.isin(BAD_EVENTS)].copy()
        t['net'] = t.net_notional_bp-np.where(t.reason == 4, 0., opt.SLIP)
        t['group'] = t.trade_id.str.split(':pyr').str[0]
        paths[p] = t
    if paths['ohlc'].empty:
        return pd.DataFrame()
    job = json.loads((SRC/symbol/'identity.json').read_text())['job']
    f, _ = load_job(job)
    hi, lo = f.high.to_numpy(), f.low.to_numpy()
    by = {p: dict(tuple(t.groupby('group'))) for p, t in paths.items()}
    out = []
    for g, o in by['ohlc'].items():
        base = not o.variant.iloc[0].startswith('pyr')
        for wname, w in weights.items():
            if base and wname != 'eq':
                continue
            w = np.asarray(w, float)
            nets = {}
            for p in ('ohlc', 'olhc'):  # ties keep OHLC
                legs = by[p].get(g)
                if legs is not None and (legs.pyramid_step == 0).any():
                    nets[p] = float((legs.net*w[legs.pyramid_step.astype(int)]).sum())
            p = min(nets, key=nets.get)
            legs = by[p][g]
            lg = legs[w[legs.pyramid_step.astype(int)] > 0]
            side, a0, z = int(lg.side.iloc[0]), int(lg.entry_i.min()), int(lg.exit_i.max())
            worst = (lo if side > 0 else hi)[a0:z+1]
            S, n = np.zeros(len(worst)), np.zeros(len(worst))
            for r in lg.itertuples():
                a, b, wk = int(r.entry_i)-a0, int(r.exit_i)-a0, w[int(r.pyramid_step)]
                end = max(a+1, b)
                S[a:end] += wk*side*(worst[a:end]/r.entry-1); n[a:end] += wk
                if r.reason != 4 and b > a:
                    S[b] += wk*side*(r.exit_price/r.entry-1); n[b] += wk
            parent = legs[legs.pyramid_step == 0].iloc[0]
            out.append(dict(symbol=symbol, event=parent.event, variant=parent.variant, weights=wname, path=p, net=nets[p],
                            parent_net=float(parent.net), parent_reason=int(parent.reason), legs=int((legs.pyramid_step > 0).sum()),
                            score=float(parent.score), entry_t=parent.entry_time, exit_t=str(pd.to_datetime(legs.exit_time, utc=True).max()),
                            mae_pct=float(100*(0.01*n-S).max()-1)))
    return pd.DataFrame(out)


def stats(x, lev):
    """Per-group payoff profile of one set (bp of one leg's notional)."""
    pos = x.net.clip(lower=0).sort_values(ascending=False)
    top = max(1, len(x)//100)
    yr = x.groupby(pd.to_datetime(x.entry_t, utc=True).dt.year).net
    return dict(n=int(len(x)), win_pct=float(100*(x.net > 1).mean()), mean_bp=float(x.net.mean()), median_bp=float(x.net.median()),
                sum_r=float(x.net.sum()/1e4), q90_bp=float(x.net.quantile(.9)), q99_bp=float(x.net.quantile(.99)), max_bp=float(x.net.max()),
                legs_filled_pct=float(100*(x.legs > 0).mean()), top1pct_profit_share=float(pos.iloc[:top].sum()/pos.sum()) if pos.sum() > 0 else None,
                ge_3000bp_pct=float(100*(x.net >= 3000).mean()),
                liquidated_pct={f'L{L}': float(100*(x.mae_pct >= 100./L-1).mean()) for L in lev},
                by_year={int(k): dict(n=int(len(v)), sum_r=float(v.sum()/1e4)) for k, v in yr})


def run(config):
    for p in ('yoyo/evaluation/monday_limit_pyramid_compare.py', str(config.resolve().relative_to(ROOT))):
        if subprocess.run(['git','diff','--quiet','HEAD','--',p], cwd=ROOT).returncode or \
           subprocess.run(['git','ls-files','--error-unmatch',p], cwd=ROOT, capture_output=True).returncode:
            raise RuntimeError('Commit builder and config first')
    if not (EXP/'run_done.txt').exists():
        raise RuntimeError('Pyramid framework run not finished')
    cfg = json.loads(config.read_text())
    syms = json.loads((EXP/'config.json').read_text())['symbols']
    with ProcessPoolExecutor(4) as ex:
        g = pd.concat([d for d in ex.map(groups, [(s, cfg['weights']) for s in syms], chunksize=4) if len(d)], ignore_index=True)
    g.to_csv(EXP/'groups.csv.gz', index=False)
    g['entry_ns'] = pd.to_datetime(g.entry_t, utc=True).astype('int64'); g['exit_ns'] = pd.to_datetime(g.exit_t, utc=True).astype('int64')
    g['entry_t'] = pd.to_datetime(g.entry_t, utc=True)
    sets = {(v if v.startswith('base') else f'{v}|{w}'): x for (v, w), x in g.groupby(['variant', 'weights'])}
    starts = pd.date_range(cfg['first_start'], cfg['last_start'], freq='MS', tz='UTC')
    res = dict(sets={k: stats(v, cfg['leverage']) for k, v in sets.items()}, starts=len(starts), grid={})
    for name, x in sets.items():
        for scheme in cfg['schemes']:
            for L in cfg['leverage']:
                rows = []
                for k, s0 in enumerate(starts):
                    y = x[x.entry_t >= s0].reset_index(drop=True)
                    for r in range(cfg['ties']):
                        h = path_run(y, scheme, L, cfg['seed']+1000*k+r)
                        rows.append({**{f'd{q}': (h[q]['days'] if h[q] else np.nan) for q in (10, 100)},
                                     **{f'b{q}': (h[q]['blowups'] if h[q] else np.nan) for q in (10, 100)}})
                d = pd.DataFrame(rows)
                out = {}
                for q in (10, 100):
                    reached = d[f'd{q}'].notna()
                    out[f'x{q}'] = dict(reached_pct=float(100*reached.mean()), days_median=float(d[f'd{q}'][reached].median()) if reached.any() else None,
                                        within_90d_pct=float(100*(d[f'd{q}'] <= 90).mean()), within_180d_pct=float(100*(d[f'd{q}'] <= 180).mean()),
                                        within_365d_pct=float(100*(d[f'd{q}'] <= 365).mean()),
                                        blowups_before_median=float(d[f'b{q}'][reached].median()) if reached.any() else None)
                res['grid'][f'{name}|{scheme}|L{L}'] = out
    res = clean(res)
    dump(EXP/'summary.json', dict(config=cfg, results=res, source_commit=subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip(),
                                  training_eligible=False, production_eligible=False))
    for k, v in res['sets'].items():
        print(f"{k:22s} n {v['n']:4d} win {v['win_pct']:5.1f}% mean {v['mean_bp']:6.0f} med {v['median_bp']:5.0f} sum {v['sum_r']:6.1f}R "
              f"q99 {v['q99_bp']:6.0f} legs {v['legs_filled_pct']:4.1f}% top1% {v['top1pct_profit_share'] or 0:.2f} liq {v['liquidated_pct']}")
    for k, v in res['grid'].items():
        a, c = v['x10'], v['x100']
        print(f"{k:34s} 10x: {a['reached_pct']:5.1f}% med {a['days_median'] or 0:5.0f}d <=180d {a['within_180d_pct']:5.1f} blow {a['blowups_before_median'] or 0:.0f} | "
              f"100x: {c['reached_pct']:5.1f}% med {c['days_median'] or 0:5.0f}d <=365d {c['within_365d_pct']:5.1f} blow {c['blowups_before_median'] or 0:.0f}")


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, default=EXP/'compare.json')
    run(ap.parse_args().config)
