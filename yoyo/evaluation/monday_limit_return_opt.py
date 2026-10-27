"""Return-first version of the Monday1618 limit-reclaim rule: sizing, leverage and slots on existing fills (owner 2026-10-27).

Owner: "胜率最高的一版做完了 ... 我们现在继续做收益最高的一版本，继续之前没弄完的"; earlier limit: account drawdown <= 40
percent, single liquidated positions acceptable. Read-only on NautilusTrader fills of exp-monday-limit-be-scalein-20261026-v1:
  v3        v3_be17 (score >= 0.5, range 0.618 / opposite 1618 from 0.6, -30 percent backstop, breakeven +0.17 percent);
  v3add     v3_be0 plus the -5 percent add legs (v3_add5) of score >= 0.6 events (the v3add paper account);
  *_tilt    margin doubled when the 15m EMA order score is > 0 (l2_order_score, feature table v2).
Worse intrabar path per row, 10bp slippage on non-target exits, the two bad source bars dropped; MAE from Codex 5m
bars as in monday_limit_spike_opt.legs. Sizing families on realized equity:
  linear    margin = min(0.05 * (score - cut) / mean_excess, 0.5) * equity (x2 with tilt, cap 0.5), total margin <= equity,
            notional = margin x leverage, a position whose MAE reaches 100/leverage - 1 percent loses its margin;
  slots     monday_limit_spike_opt.simulate (N slots, leverage L, profits above 2x a slot to the reserve, resize on
            doubling), with tilt ignored;
  frac      (v2, owner 2026-10-27) margin = realized equity / N per fill at leverage L, at most N open and margin in
            use <= equity, liquidation as in linear: every fill compounds, no idle reserve.
v1 ran before the slot simulator's starting-capital fix of 2026-10-27 (it started at 1000 * (2N-1)/N), so its slot
multiples are about 2x too high; v2 reruns everything on the fixed simulator and adds frac. Periods in
report_periods are reported but take no part in the selection.
Selection: best 2020-2024 multiple with drawdown <= 40 percent there, then 2025 / 2026 / last five months as the check;
also every setting whose drawdown stays <= 40 percent in all four periods, ranked by the geometric mean multiple
(in-sample). Medians over TIES random orders of same-time fills.
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
from yoyo.evaluation.monday_limit_be_scalein_compare import BAD_EVENTS, B, RUN, clean
from yoyo.evaluation import monday_limit_spike_opt as so

EXP = ROOT/'experiments/active/exp-monday-limit-return-opt-20261027-v1'
A = ROOT/'experiments/active'


def linear(t, k, lev, seed, trace=None):
    """Isolated margin per trade, total margin <= equity; liquidation at MAE >= 100/lev - 1 percent."""
    rng = np.random.default_rng(seed)
    o = np.lexsort((rng.random(len(t)), t.entry_ns.to_numpy()))
    t = t.iloc[o].reset_index(drop=True); k = k[o]
    ev = np.concatenate([np.c_[t.exit_ns, np.where(t.exit_ns == t.entry_ns, 2, 0), np.arange(len(t))],
                         np.c_[t.entry_ns, np.ones(len(t)), np.arange(len(t))]])
    ev = ev[np.lexsort((ev[:, 1], ev[:, 0]))]
    liq = 100./lev-1. if lev > 1 else 1e9
    eq, peak, dd, used, nliq, wins, taken = 1000., 1000., 0., 0., 0, 0, 0
    margin = {}
    net, mae = t.net.to_numpy()/1e4, t.mae_pct.to_numpy()
    for ts, kind, j in ev:
        j = int(j)
        if kind == 1:
            m = max(0., min(k[j]*eq, eq-used))
            if m > 0:
                margin[j] = m; used += m; taken += 1
        elif j in margin:
            m = margin.pop(j); used -= m
            pnl = -m if mae[j] >= liq else max(-m, m*lev*net[j])
            nliq += mae[j] >= liq; wins += pnl > 0
            eq += pnl; peak = max(peak, eq); dd = max(dd, 1-eq/peak)
            if trace is not None:
                trace(t.iloc[j], int(ts), eq, pnl, m*lev)
            if eq <= 1e-6:
                return dict(final_multiple=0., max_drawdown_pct=100., liquidations=nliq, taken=taken, win_pct=100*wins/max(taken, 1))
    return dict(final_multiple=eq/1000, max_drawdown_pct=100*dd, liquidations=int(nliq), taken=taken, win_pct=100*wins/max(taken, 1))


def frac(t, n, lev, seed, trace=None):
    """Margin = realized equity / n per fill, at most n open, margin in use <= equity; liquidation as in linear."""
    rng = np.random.default_rng(seed)
    t = t.iloc[np.lexsort((rng.random(len(t)), t.entry_ns.to_numpy()))].reset_index(drop=True)
    ev = np.concatenate([np.c_[t.exit_ns, np.where(t.exit_ns == t.entry_ns, 2, 0), np.arange(len(t))],
                         np.c_[t.entry_ns, np.ones(len(t)), np.arange(len(t))]])
    ev = ev[np.lexsort((ev[:, 1], ev[:, 0]))]
    liq = 100./lev-1. if lev > 1 else 1e9
    eq, peak, dd, used, nliq, wins, taken, skipped = 1000., 1000., 0., 0., 0, 0, 0, 0
    margin = {}
    net, mae = t.net.to_numpy()/1e4, t.mae_pct.to_numpy()
    for ts, kind, j in ev:
        j = int(j)
        if kind == 1:
            m = eq/n
            if len(margin) >= n or m <= 0 or eq-used < m-1e-9:
                skipped += 1; continue
            margin[j] = m; used += m; taken += 1
        elif j in margin:
            m = margin.pop(j); used -= m
            pnl = -m if mae[j] >= liq else max(-m, m*lev*net[j])
            nliq += mae[j] >= liq; wins += pnl > 0
            eq += pnl; peak = max(peak, eq); dd = max(dd, 1-eq/peak)
            if trace is not None:
                trace(t.iloc[j], int(ts), eq, pnl, m*lev)
            if eq <= 1e-6:
                return dict(final_multiple=0., max_drawdown_pct=100., liquidations=nliq, skipped=skipped, taken=taken, win_pct=100*wins/max(taken, 1))
    return dict(final_multiple=eq/1000, max_drawdown_pct=100*dd, liquidations=int(nliq), skipped=skipped, taken=taken,
                win_pct=100*wins/max(taken, 1))


def run(config):
    exp = config.resolve().parent
    for p in ('yoyo/evaluation/monday_limit_return_opt.py', str(config.resolve().relative_to(ROOT))):
        if subprocess.run(['git','diff','--quiet','HEAD','--',p], cwd=ROOT).returncode or \
           subprocess.run(['git','ls-files','--error-unmatch',p], cwd=ROOT, capture_output=True).returncode:
            raise RuntimeError('Commit builder and config first')
    cfg = json.loads(config.read_text())
    tn = pd.read_csv(ROOT/cfg['trades'], usecols=['event', 'variant'])
    events = set(tn[tn.variant == 'v3_be17'].event)-BAD_EVENTS
    syms = sorted({e.split(':')[0] for e in events})
    src = {RUN.name: ['v3_be17', 'v3_be0', 'v3_add5']}
    with ProcessPoolExecutor(4) as ex:
        t = pd.concat([d for d in ex.map(so.legs, [(s, src, events) for s in syms], chunksize=4) if len(d)], ignore_index=True)
    feat = pd.read_csv(ROOT/cfg['features'], usecols=['event', 'l2_order_score']).rename(columns={'l2_order_score': 'order'})
    t = t.merge(feat, on='event', how='left')
    t['entry_ns'] = t.entry_t.astype('int64'); t['exit_ns'] = t.exit_t.astype('int64'); t['tid'] = t.event+':'+t.variant
    cut = json.loads((RUN/'scores.json').read_text())['threshold']
    mexcess = json.loads((A/'exp-monday-limit-v3-compare-20261025-v1/summary.json').read_text())['results']['linear_mean_excess']
    window = pd.Timestamp(json.loads((A/'exp-monday-limit-reclaim-recent-20261025-v1/summary.json').read_text())['results']['window'][0])
    pers = {'y2020_2024': lambda x: x.entry_t < B[0], 'y2025': lambda x: (x.entry_t >= B[0]) & (x.entry_t < B[1]),
            'y2026': lambda x: x.entry_t >= B[1], 'last5m': lambda x: x.entry_t >= window}
    extra = {k: (lambda x, s=pd.Timestamp(v, tz='UTC'): x.entry_t >= s) for k, v in cfg.get('report_periods', {}).items()}
    plans = {'v3': t[t.variant == 'v3_be17'], 'v3add': t[t.variant.isin(['v3_be0', 'v3_add5'])]}
    res = dict(rows={k: int(len(v)) for k, v in plans.items()}, mae_q={}, settings={})
    for name, x in plans.items():
        res['mae_q'][name] = {f'q{int(100*q)}': float(x.mae_pct.quantile(q)) for q in (.5, .9, .95, .99)}
    grid = []
    for name in plans:
        for tilt in (False, True):
            for L in cfg['linear_leverage']:
                grid.append((name, tilt, 'linear', None, L))
        for n in cfg['slots']:
            for L in cfg['slot_leverage']:
                grid.append((name, False, 'slots', n, L))
        for n in cfg.get('frac_slots', []):
            for L in cfg.get('frac_leverage', []):
                grid.append((name, False, 'frac', n, L))
    for name, tilt, fam, n, L in grid:
        key = f"{name}{'_tilt' if tilt else ''}|{fam}|{'' if n is None else f'N{n}|'}L{L}"
        res['settings'][key] = {}
        for per, f in {**pers, **extra}.items():
            x = plans[name][f(plans[name])].reset_index(drop=True)
            k = np.minimum(cfg['fraction']*(x.score.to_numpy()-cut)/mexcess, cfg['cap'])
            if tilt:
                k = np.where((x.order > 0).to_numpy(), np.minimum(2*k, cfg['cap']), k)
            reps = [linear(x, k, L, cfg['seed']+r) if fam == 'linear' else frac(x, n, L, cfg['seed']+r) if fam == 'frac'
                    else so.simulate(x, L, n, True, True, seed=cfg['seed']+r) for r in range(cfg['ties'])]
            res['settings'][key][per] = {kk: float(np.median([r[kk] for r in reps])) for kk in reps[0]}
    S = res['settings']
    ok = [k for k in S if S[k]['y2020_2024']['max_drawdown_pct'] <= cfg['max_dd']]
    res['selected_on_2020_2024'] = [dict(setting=k, **S[k]) for k in sorted(ok, key=lambda k: -S[k]['y2020_2024']['final_multiple'])[:cfg['top']]]
    allok = []
    for k, v in S.items():
        if all(v[p]['max_drawdown_pct'] <= cfg['max_dd'] for p in pers):
            g = float(np.prod([max(v[p]['final_multiple'], 1e-9) for p in pers])**(1/len(pers)))
            allok.append((g, k))
    res['dd_ok_all_periods'] = [dict(setting=k, gmean=g, **S[k]) for g, k in sorted(allok, reverse=True)[:cfg['top']]]
    res = clean(res)
    dump(exp/'summary.json', dict(config=cfg, results=res, source_commit=subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip(),
                                 training_eligible=False, production_eligible=False))
    print(json.dumps(dict(rows=res['rows'], mae_q=res['mae_q'])))
    fmt = lambda v: ' | '.join(f"x{v[p]['final_multiple']:.2f} dd{v[p]['max_drawdown_pct']:.0f} liq{v[p]['liquidations']:.0f} win{v[p]['win_pct']:.0f}" for p in pers)
    print('== baseline'); print('v3|linear|L1', fmt(S['v3|linear|L1'])); print('v3add|linear|L1', fmt(S['v3add|linear|L1']))
    print('== selected on 2020-24')
    for r in res['selected_on_2020_2024']:
        print(f"{r['setting']:28s}", fmt(r))
    print('== dd <= 40 in all periods (in-sample)')
    for r in res['dd_ok_all_periods']:
        print(f"{r['setting']:28s} g{r['gmean']:.2f}", fmt(r))
    if extra:
        print('== owner comparison, plus ' + ', '.join(extra))
        for k in cfg.get('compare', []):
            if k in S:
                print(f"{k:28s}", fmt(S[k]), '||', ' | '.join(f"{p} x{S[k][p]['final_multiple']:.2f} dd{S[k][p]['max_drawdown_pct']:.0f}"
                                                              + (f" skip{S[k][p]['skipped']:.0f}" if 'skipped' in S[k][p] else '') for p in extra))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, default=EXP/'config.json')
    run(ap.parse_args().config)
