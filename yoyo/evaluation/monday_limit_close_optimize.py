"""Walk-forward search for the most profitable touch-close strategy with drawdown under 35 percent (owner 2026-11-01).

Owner: "为什么笔数这么少？收益不是很高！针对这套逻辑做策略优化？以最大利润为目标，重构出新的策略出来，回撤控制小于35%就好了".
An explicit multi-variable request, recorded in PROJECT_PLAN. The logic stays the touch-close entry of
exp-monday-limit-close-reclaim-20261028-v1: first weekly touch of a Monday 1.618, market entry at the completed touch
5m close, 60m reclaim then stop to the level. Every candidate is a set of rows of that run's saved NautilusTrader fills
(no new fills): worse of the two intrabar paths, taker entry + 10bp entry slippage, 10bp on non-target exits.
Searched together, all known at the touch close or fixed in advance:
  score_min     frozen touch-bar score (`stored` of the leak audit) >= this (0 = no filter);
  recovery_max  share of the excursion past the level recovered by the close <= this (1.0 = closed at / beyond the
                level, inf = also closed back inside);
  target        one of the five saved target modes;
  stop          -30 percent from the level (P30 / B30) or -18 percent (P18 / B18, saved for the near edge only);
  sizing        monday_limit_return_opt.frac: margin = realized equity / n per fill, at most n open, leverage lev,
                a fill whose MAE reaches the leverage's liquidation line loses its margin.
Objective, fixed before any result: the largest final equity from 1000U over the training window with realized max
drawdown < cap and at least min_trades trades; one tie seed. Walk-forward with an expanding window: choose on
2020-01-01 .. Y-01-01 and apply the choice unchanged to year Y, for Y in test_years; the yearly accounts are chained
(entries assigned by their entry time) into one out-of-sample path from 1000U. Also reported: the in-sample optimum
over the full history (an upper bound, not an estimate), the current survivor rule in the same windows, the top
training candidates of each window, a 10-seed check of every choice, and the funnel of events per year.
"""
from __future__ import annotations
import argparse
import itertools
import json
import subprocess
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT
from yoyo.evaluation import monday_limit_close_reclaim as cr
from yoyo.evaluation.monday_limit_reclaim_patterns import SRC
from yoyo.evaluation.strategy_booklet_paths import Trace, dated
from yoyo.evaluation.strategy_booklet_metrics import project

A = ROOT/'experiments/active'
STOP = {30: ('P30', 'B30'), 18: ('P18', 'B18')}


def symbol_rows(args):
    """Close-entry rows of one symbol (P30 / B30 / P18 / B18) with the touch-bar recovery of each event."""
    exp, symbol, slip_entry, slip_exit = args
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    _, d, issues = cr.load_symbol((exp, symbol, slip_entry, slip_exit))
    if d.empty:
        return d, issues
    d = d[d.variant.isin(['P30', 'B30', 'P18', 'B18'])].copy()
    lv = pd.read_csv(SRC/symbol/'trades.csv.gz', usecols=['population', 'reclaim_minutes', 'exit_mode', 'week', 'side', 'level', 'touch_i_hint'])
    lv = lv[(lv.population == 'signal') & (lv.reclaim_minutes == 60) & (lv.exit_mode == 'near_monday_edge')].copy()
    lv['event'] = symbol+':'+lv.week+':'+np.where(lv.side == 1, '+1', '-1')
    lv = lv.drop_duplicates('event')
    f, _ = load_job(json.loads((SRC/symbol/'identity.json').read_text())['job'])
    k = lv.touch_i_hint.astype(int).to_numpy()
    s, L = lv.side.to_numpy(float), lv.level.to_numpy(float)
    ext = np.where(s > 0, f.low.to_numpy()[k], f.high.to_numpy()[k])
    past = s*(L-ext)
    lv['recovery'] = np.where(past > 0, s*(f.close.to_numpy()[k]-ext)/np.where(past > 0, past, 1), np.nan)
    d['recovery'] = d.event.map(lv.set_index('event').recovery)
    d['stop'] = np.where(d.variant.str.endswith('18'), 18, 30)
    return d, issues


def load(cfg, exp, workers):
    """All rows from 2020 with score and recovery; cached next to the config (not committed)."""
    cache = exp/'rows.parquet'
    if cache.exists():
        return pd.read_parquet(cache), []
    from yoyo.evaluation.monday_limit_be_scalein_compare import BAD_EVENTS
    src = ROOT/cfg['source']
    scfg = json.loads((src/'config.json').read_text())
    slip = scfg['slippage_bp']
    with ProcessPoolExecutor(workers) as ex:
        got = list(ex.map(symbol_rows, [(str(src), s, slip['market_entry'], slip['non_target_exit']) for s in cr.symbols(scfg)], chunksize=4))
    t = pd.concat([g[0] for g in got if len(g[0])], ignore_index=True)
    t = t[~t.event.isin(BAD_EVENTS) & (t.entry_t >= pd.Timestamp('2020-01-01', tz='UTC'))].copy()
    t['score'] = t.event.map(pd.read_csv(ROOT/scfg['scores']).set_index('event').stored)
    t = dated(t[['event', 'mode', 'variant', 'stop', 'side', 'net', 'reason', 'mae_pct', 'entry_t', 'exit_t', 'score', 'recovery']])
    t.to_parquet(cache)
    return t, [i for g in got for i in g[1]]


def specs(cfg):
    out = []
    for s, r, m, st in itertools.product(cfg['score_min'], cfg['recovery_max'], cfg['targets'], cfg['stops']):
        if st == 18 and m != 'near_monday_edge':
            continue
        out.append(dict(score_min=s, recovery_max=r, target=m, stop=st))
    return out


def select(t, sp):
    keep = (t['mode'] == sp['target']) & t.variant.isin(STOP[sp['stop']]) & (t.score.fillna(-1) >= sp['score_min'])
    if sp['recovery_max'] != 'inf':
        keep &= t.variant.str.startswith('B') & (t.recovery <= sp['recovery_max'])
    return t[keep]


def window(x, a, b):
    return x[(x.entry_t >= pd.Timestamp(a, tz='UTC')) & (x.entry_t < pd.Timestamp(b, tz='UTC'))].reset_index(drop=True)


def evaluate(args):
    """Every sizing of one selection inside one window."""
    sp, x, sizings, seed = args
    from yoyo.evaluation.monday_limit_return_opt import frac
    out = []
    for n, lev in sizings:
        r = frac(x, n, lev, seed)
        out.append(dict(**sp, n=n, lev=lev, trades=int(len(x)), mean_bp=float(x.net.mean()) if len(x) else None,
                        multiple=float(r['final_multiple']), dd=float(r['max_drawdown_pct']), liquidations=int(r['liquidations']),
                        taken=int(r['taken'])))
    return out


def search(t, cfg, a, b, workers):
    jobs = []
    for sp in specs(cfg):
        x = window(select(t, sp), a, b)
        if len(x) >= cfg['min_trades']:
            jobs.append((sp, x, cfg['sizings'], cfg['seed']))
    rows = []
    with ProcessPoolExecutor(workers) as ex:
        for out in ex.map(evaluate, jobs, chunksize=1):
            rows += out
    g = pd.DataFrame(rows)
    ok = g[(g.dd < cfg['dd_cap']) & (g.multiple > 0)].sort_values(['multiple', 'dd'], ascending=[False, True])
    return g, ok


def path(t, sp, n, lev, a, b, seed):
    """One account from 1000U on [a, b) with its trace."""
    from yoyo.evaluation.monday_limit_return_opt import frac
    x = window(select(t, sp), a, b)
    trace = Trace(pd.Timestamp(a, tz='UTC').value)
    r = frac(x, n, lev, seed, trace=trace)
    return pd.DataFrame(trace.rows), r, x


def seeds(t, sp, n, lev, a, b, cfg):
    from yoyo.evaluation.monday_limit_return_opt import frac
    x = window(select(t, sp), a, b)
    reps = [frac(x, n, lev, cfg['seed']+k) for k in range(cfg['ties'])]
    return dict(final_median=float(np.median([r['final_multiple'] for r in reps])), dd_median=float(np.median([r['max_drawdown_pct'] for r in reps])),
                dd_max=float(np.max([r['max_drawdown_pct'] for r in reps])))


def key(r):
    return {k: (r[k].item() if hasattr(r[k], 'item') else r[k]) for k in ('score_min', 'recovery_max', 'target', 'stop')}


def run(config, workers):
    exp = config.resolve().parent
    cfg = json.loads(config.read_text())
    cr.committed('yoyo/evaluation/monday_limit_close_optimize.py', str(config.resolve().relative_to(ROOT)))
    cfg['sizings'] = [tuple(s) for s in itertools.product(cfg['slots'], cfg['leverage'])]
    t, issues = load(cfg, exp, workers)
    end = '2100-01-01'
    # Funnel: why the survivor rule has few trades.
    ev = t[(t['mode'] == 'near_monday_edge') & t.variant.isin(STOP[30])].drop_duplicates('event').copy()
    ev['year'] = ev.entry_t.dt.year
    funnel = {}
    for y, g in ev.groupby('year'):
        s5 = g[g.score >= 0.5]
        funnel[str(y)] = dict(symbols=int(g.event.str.split(':').str[0].nunique()), touches=int(len(g)), score_ge_0_5=int(len(s5)),
                              beyond=int(s5.variant.str.startswith('B').sum()),
                              recovery_lt_0_64=int((s5.variant.str.startswith('B') & (s5.recovery < 0.64)).sum()))
    res = dict(checks=dict(rows=int(len(t)), events=int(t.event.nunique()), issues=issues[:20], specs=len(specs(cfg)),
                           sizings=len(cfg['sizings'])), funnel=funnel, windows={}, oos={})
    survivor = cfg['survivor']
    chain, carried, picks = [], 1000., []
    for y in cfg['test_years']:
        a, b = '2020-01-01', f'{y}-01-01'
        g, ok = search(t, cfg, a, b, workers)
        best = ok.iloc[0].to_dict()
        sp = key(best)
        frame, r, x = path(t, sp, int(best['n']), float(best['lev']), b, f'{y+1}-01-01', cfg['seed'])
        base = evaluate((survivor, window(select(t, survivor), b, f'{y+1}-01-01'), [tuple(cfg['survivor_sizing'])], cfg['seed']))[0]
        res['windows'][str(y)] = dict(train=[a, b], candidates=int(len(g)), feasible=int(len(ok)), choice=best,
                                      top=ok.head(cfg['top']).to_dict('records'),
                                      train_seeds=seeds(t, sp, int(best['n']), float(best['lev']), a, b, cfg),
                                      test=dict(trades=int(len(x)), mean_bp=float(x.net.mean()) if len(x) else None, multiple=float(r['final_multiple']),
                                                dd=float(r['max_drawdown_pct']), liquidations=int(r['liquidations']),
                                                seeds=seeds(t, sp, int(best['n']), float(best['lev']), b, f'{y+1}-01-01', cfg)),
                                      survivor_test=base)
        g.to_csv(exp/f'grid_train_to_{y}.csv.gz', index=False, compression='gzip')
        scale = carried/1000.
        f = frame.copy()
        for c in ('equity', 'pnl', 'notional', 'stake_budget'):
            f[c] = f[c]*scale
        chain.append(f if not chain else f.iloc[1:])
        carried = float(f.equity.iloc[-1])
        picks.append(dict(year=y, **sp, n=int(best['n']), lev=float(best['lev'])))
        print(f"{y}: choice {sp} n{int(best['n'])} lev{best['lev']} train {best['multiple']:.2f}x dd{best['dd']:.1f} | "
              f"test {r['final_multiple']:.2f}x dd{r['max_drawdown_pct']:.1f} ({len(x)} trades) | survivor {base['multiple']:.2f}x", flush=True)
    # Yearly accounts overlap at the boundaries (a December entry can exit in January): order the chain by exit
    # time and restate equity as 1000 plus the cumulative realized P&L (same final value).
    oos = pd.concat(chain, ignore_index=True)
    oos = pd.concat([oos.iloc[:1], oos.iloc[1:].sort_values('time_ms', kind='stable')], ignore_index=True)
    oos['equity'] = 1000.+oos.pnl.cumsum()
    oos['cashflow'] = 0.
    oos.loc[0, 'stake_budget'] = 1000.
    oos.to_csv(exp/'oos-ledger.csv.gz', index=False, compression='gzip')
    data = project(oos, dict(track='walk_forward', picks=picks, note=''))
    (exp/'oos-history.json').write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':'), allow_nan=False, default=lambda v: v.item()))
    res['oos'] = dict(start=f"{cfg['test_years'][0]}-01-01", picks=picks, summary=data['summary'], yearly=data['yearly'])
    # Full-history optimum (in sample, an upper bound) and the latest choice over the whole history.
    g, ok = search(t, cfg, '2020-01-01', end, workers)
    g.to_csv(exp/'grid_full.csv.gz', index=False, compression='gzip')
    full = ok.iloc[0].to_dict()
    res['full_in_sample'] = dict(choice=full, top=ok.head(cfg['top']).to_dict('records'),
                                 seeds=seeds(t, key(full), int(full['n']), float(full['lev']), '2020-01-01', end, cfg))
    last = picks[-1]
    for name, sp, n, lev in (('latest_choice', key(last), last['n'], last['lev']), ('full_in_sample', key(full), int(full['n']), float(full['lev'])),
                             ('survivor', survivor, *cfg['survivor_sizing'])):
        for a in ('2020-01-01', '2025-01-01'):
            frame, r, x = path(t, sp, int(n), float(lev), a, end, cfg['seed'])
            h = project(frame, dict(track=name, start=a, note=''))
            (exp/f'{name}-{a[:4]}-history.json').write_text(json.dumps(h, ensure_ascii=False, separators=(',', ':'), allow_nan=False, default=lambda v: v.item()))
            frame.to_csv(exp/f'{name}-{a[:4]}-ledger.csv.gz', index=False, compression='gzip')
            res.setdefault('paths', {})[f'{name}-{a[:4]}'] = dict(spec=sp, n=int(n), lev=float(lev), summary=h['summary'], yearly=h['yearly'])
    out = dict(config=cfg, results=res, source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
               training_eligible=False, production_eligible=False)
    (exp/'summary.json').write_text(json.dumps(out, ensure_ascii=False, indent=1, default=lambda v: v.item() if hasattr(v, 'item') else str(v)))
    print(json.dumps(res['oos'], ensure_ascii=False, default=str)[:600])


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, required=True); ap.add_argument('--workers', type=int, default=6)
    a = ap.parse_args()
    run(a.config, a.workers)
