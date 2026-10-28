"""The eight Monday1618 paper accounts with their full rules, resting vs close-time entries (owner 2026-10-28).

Owner: "收盘进场测试跑完后，按模拟盘 8 个账户各自的完整规则（保本 +0.17%、v3lock +0.5%、加仓单、各自仓位）在框架里逐个
重跑，预挂单和收盘进场两种入场都测". Rules from yoyo/monitor/monday_reclaim_paper.py (frozen there):
  v2_60 / v2_5  score >= top-40 threshold 0.2262, near Monday edge target, no backstop (far stop at level / 50 or x 50),
                60m / 5m confirmation, breakeven +0.17 percent, notional 10 percent of equity, gross <= 1x;
  v3            score >= 0.5, range 0.618 target below 0.6 and opposite 1.618 from 0.6, -30 percent backstop, 60m,
                breakeven +0.17 percent, notional min(0.05 (score - 0.2262) / 0.10882, 0.5) x equity, gross <= 1x;
  v3add         v3 plus, from 0.6, an equal-notional add resting 5 percent beyond the level that shares the parent's
                exits and is cancelled once the parent closes or reaches its decision close; v3 sizing;
  v3add20       v3add with margin = realized equity / 20 per leg at 3x, at most 20 legs (return_opt.frac);
  v3lock        v3 with breakeven +0.5 percent;
  spike         score >= 0.6 and 15m EMA order score >= 3, near edge, -18 percent backstop, breakeven +0.17 percent,
                10 slots at 5x (spike_hw.slot_replay);
  allin3        score >= 0.6, opposite 1.618, -30 percent, breakeven +0.17 percent, all-in 3x, one position at a time,
                blow-up below 100 USDT (langlang.path_run 'allin', monthly starts 2023-01 to 2026-08).
Entries (one native NautilusTrader run per symbol and intrabar path, per-row order modes):
  rest   resting limit at the level from the bar before the touch (Codex orders); selected in the summary by the
         frozen causal score (what the paper engine computes, plan A) or by the touch-bar score (the leaky reference);
  ci     market at the touch bar's close when it closed back inside the level and short of the target, selected by the
         touch-bar score (known at that close);
  cb     market at that close when it closed at or beyond the level but short of the stop (same score).
The add legs of ci / cb rest at the level-based add price from the touch close; a close already past it takes no leg.
Breakeven and reclaim are tested against the level. Summary: worse path per row, 10bp slippage on market entries and
non-target exits, the two bad source bars dropped, MAE from the entry bar (limit) or the next bar (market).
"""
from __future__ import annotations
import argparse
import json
import subprocess
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation.monday_limit_backstop import CODEX, stops

KEEP = ['trade_id', 'side', 'entry', 'stop', 'target_price', 'exit_price', 'reason', 'entry_time', 'exit_time', 'entry_i',
        'exit_i', 'reclaim_status', 'net_notional_bp']
A = ROOT/'experiments/active'
V3MODE = lambda score, hi: np.where(score >= hi, 'opposite_1618', 'range_0.618')


def scores(cfg):
    s = pd.read_csv(ROOT/cfg['scores']).set_index('event')
    return s.causal.rename('pre'), s.stored.rename('full')


def orders(e, close, tick, cfg, family):
    """Order rows of one entry family for every account type; scores decide only which events get rows."""
    th, cut, hi, add = cfg['v2_threshold'], cfg['cut'], cfg['hi'], cfg['add_pct']
    th_pre = cfg.get('v2_threshold_pre', th)  # a refitted pre-touch score has its own top-40 cut
    out = []
    if family == 'rest':
        x = e.copy()
        x['order_mode'] = 'limit'
        best = np.maximum(x.pre.fillna(-1), x.full.fillna(-1))
        v2ok = (x.pre >= th_pre) | (x.full >= th)
        v3ok = ((x.pre >= cut) & (x.exit_mode == V3MODE(x.pre, hi))) | ((x.full >= cut) & (x.exit_mode == V3MODE(x.full, hi)))
    else:
        k = e.touch_i_hint.astype(int).to_numpy()
        s, L, T = e.side.to_numpy(float), e.entry.to_numpy(float), e.target_price.to_numpy(float)
        c = close[k]
        inside = s*(c-L) >= tick*(1-1e-7)
        room = (~np.isfinite(T) | (s*(T-c) >= tick*(1-1e-7))) & (k < e.end_i.to_numpy())
        x = e[room & (inside if family == 'ci' else ~inside)].copy()
        x['order_mode'] = 'market'
        best = x.full.fillna(-1)
        v2ok = x.full >= th
        v3ok = (x.full >= cut) & (x.exit_mode == V3MODE(x.full, hi))
    x['reclaim_ref'] = x.entry  # the level
    types = [('v2', v2ok & (x.exit_mode == 'near_monday_edge'), 'far', 0, 17.),
             ('v3', v3ok, 'P', 30, 17.), ('v3lock', v3ok, 'P', 30, 50.),
             ('spike', (best >= hi) & (x.exit_mode == 'near_monday_edge') & (x.reclaim_minutes == 60), 'P', 18, 17.)]
    for name, keep, kind, value, be in types:
        y = x[keep & ((x.reclaim_minutes == 60) | (name == 'v2'))].copy()
        y['stop'] = stops(y, kind, value, tick)  # from the level
        y['be_offset_bp'] = be
        if family != 'rest':
            y['entry_i'] = y.touch_i_hint.astype(int)
            y['entry'] = close[y.entry_i.to_numpy()]
        y['trade_id'] = y.trade_id+f':v_{family}_{name}'
        y = y[y.side*(y.entry-y.stop) > 0]
        out.append(y)
        if name == 'v3':
            par = y[y.exit_mode == 'opposite_1618']
            leg = par.copy()
            raw = leg.reclaim_ref*(1-leg.side*add/100)
            leg['entry'] = np.where(leg.side > 0, np.floor(raw/tick+1e-7)*tick, np.ceil(raw/tick-1e-7)*tick)
            leg['order_mode'] = 'limit'
            leg['parent'] = par.trade_id
            leg['trade_id'] = par.trade_id+'_add'
            px = close[leg.entry_i.astype(int).to_numpy()]
            leg = leg[(leg.side*(leg.entry-leg.stop) > 0) & (leg.side*(px-leg.entry) >= tick*(1-1e-7))]
            out.append(leg)
    return pd.concat(out, ignore_index=True)


def process(symbol, intrabar, exp, cfg):
    from yoyo.evaluation.monday_limit_reclaim import prepare
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native
    ccfg = json.loads((CODEX/'config.json').read_text())
    job = json.loads((CODEX/'symbols'/symbol/'identity.json').read_text())['job']
    if job['group'] != 'crypto_5m':
        return dict(symbol=symbol, skipped=job['group'])
    t0 = time.perf_counter()
    f, quality = load_job(job)
    p, e, _, _, tick = prepare(f, job, ccfg, quality)
    e = e[(e.population == 'signal') & e.reclaim_minutes.isin([5, 60]) & (e.week >= cfg['since_week'])].copy()
    e['event'] = e.trade_id.str.split(':').str[:3].str.join(':')
    pre, full = scores(cfg)
    e['pre'] = e.event.map(pre); e['full'] = e.event.map(full)
    close = p.close.to_numpy()
    x = pd.concat([orders(e, close, tick, cfg, fam) for fam in cfg.get('families', ['rest', 'ci', 'cb'])], ignore_index=True)
    out = Path(exp)/intrabar.lower()/'symbols'/symbol
    out.mkdir(parents=True, exist_ok=True)
    if x.empty:
        pd.DataFrame(columns=KEEP+['variant']).to_csv(out/'trades.csv.gz', index=False)
        dump(out/'done.json', dict(symbol=symbol, intrabar=intrabar, orders=0, positions=0))
        return dict(symbol=symbol, orders=0)
    x['parent'] = x.parent.where(x.parent.notna(), None) if 'parent' in x else None
    x['risk'] = x.entry
    frame = p.copy(); frame.index = frame.index+pd.Timedelta(minutes=job['execution_minutes'])
    policy = ExitPolicy(trail_arm_r=1e12, opposite_hour_exit=False, round_trip_cost=0, target_from_entry=True,
                        intrabar_path=intrabar, reclaim_next_bar=True, breakeven_includes_cost=False,
                        fill_maker_fee=ccfg['maker_fee'], fill_taker_fee=ccfg['taker_fee'], monotone_segments=True,
                        reclaim_backstop=True)
    rows, _, audit = run_native(frame, np.full(len(frame), np.nan), np.zeros(len(frame)),
                                x.drop(columns=['event', 'pre', 'full', 'population', 'exit_mode']), policy, tick=tick,
                                symbol=symbol.removesuffix('USDT'), execution_minutes=job['execution_minutes'],
                                entry_order_mode='limit', batch_bars=ccfg['streaming_batch_bars'])
    rows = rows[[k for k in KEEP if k in rows]]
    rows['variant'] = rows.trade_id.str.rsplit(':v_', n=1).str[-1]
    rows.to_csv(out/'trades.csv.gz', index=False)
    done = dict(symbol=symbol, intrabar=intrabar, orders=len(x), positions=audit['positions'], fills=audit['fills'],
                by_variant={str(k): int(v) for k, v in x.trade_id.str.rsplit(':v_', n=1).str[-1].value_counts().items()},
                cancelled=sum(o['status'] == 'parent_closed' for o in audit.get('opportunities', [])),
                engine=audit.get('engine'), version=audit.get('version'), seconds=time.perf_counter()-t0)
    dump(out/'done.json', done)
    return done


def committed(*paths):
    for path in paths:
        if subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--', path], cwd=ROOT).returncode or \
           subprocess.run(['git', 'ls-files', '--error-unmatch', path], cwd=ROOT, capture_output=True).returncode:
            raise RuntimeError('Commit builder and config first')


def symbols(cfg):
    pre, full = scores(cfg)
    pre = pre.reindex(full.index)
    ev = full.index[(pre >= cfg.get('v2_threshold_pre', cfg['v2_threshold'])) | (full >= cfg['v2_threshold'])]
    return sorted({e.split(':')[0] for e in ev if e.split(':')[1] >= cfg['since_week']})


def run(config, workers, intrabar):
    exp = config.resolve().parent
    cfg = json.loads(config.read_text())
    committed('yoyo/evaluation/monday_limit_paper_accounts.py', 'yoyo/layers/l3_backtest/nautilus_independent.py',
              str(config.resolve().relative_to(ROOT)))
    root = exp/intrabar.lower()
    todo = [s for s in symbols(cfg) if not (root/'symbols'/s/'done.json').exists()]
    with ProcessPoolExecutor(workers) as ex:
        futs = {ex.submit(process, s, intrabar, str(exp), cfg): s for s in todo}
        for k, fu in enumerate(as_completed(futs), 1):
            try:
                r = fu.result()
            except Exception as err:  # keep going; record the failure per symbol
                r = dict(symbol=futs[fu], error=repr(err))
                (root/'symbols'/futs[fu]).mkdir(parents=True, exist_ok=True); dump(root/'symbols'/futs[fu]/'error.json', r)
            print(json.dumps(dict(progress=k, total=len(todo), **r)), flush=True)


def load_symbol(args):
    """Worse path per row with slippage; entry time and MAE by entry kind (limit fill bar / market close)."""
    exp, symbol, slip_entry, slip_exit = args
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    paths, issues = {}, []
    for p in ('ohlc', 'olhc'):
        d = Path(exp)/p/'symbols'/symbol
        if (d/'error.json').exists() or not (d/'done.json').exists():
            issues.append(f'{p}: missing or failed'); continue
        done = json.loads((d/'done.json').read_text())
        t = pd.read_csv(d/'trades.csv.gz')
        if done.get('positions', 0) != len(t):
            issues.append(f'{p}: rows {len(t)} != positions')
        market = ~t.variant.str.startswith('rest') & ~t.variant.str.endswith('_add')
        t['market'] = market
        t['net'] = t.net_notional_bp-np.where(market, slip_entry, 0.)-np.where(t.reason == 4, 0., slip_exit)
        paths[p] = t.set_index('trade_id')
    if len(paths) < 2 or paths['ohlc'].empty:
        return symbol, pd.DataFrame(), issues
    # An add leg can fill under one intrabar path only; keep the rows of the OHLC ledger as monday_limit_spike_opt.legs.
    o = paths['ohlc']; l = paths['olhc'].reindex(o.index)
    w = (l.net < o.net).fillna(False).to_numpy()
    pick = lambda c: np.where(w, l[c], o[c])
    d = pd.DataFrame(dict(variant=o.variant, market=o.market, side=o.side, entry=o.entry, net=pick('net'), reason=pick('reason'),
                          entry_i=o.entry_i, exit_i=pick('exit_i'),
                          exit_t=pd.to_datetime(pd.Series(pick('exit_time'), index=o.index), utc=True)), index=o.index)
    job = json.loads((CODEX/'symbols'/symbol/'identity.json').read_text())['job']
    f, _ = load_job(job)
    # Every fill time is its bar's close (limit fills too: entry_i is the fill bar).
    d['entry_t'] = pd.Series(f.index[d.entry_i.astype(int).to_numpy()]+pd.Timedelta(minutes=job['execution_minutes']), index=d.index)
    known = pd.to_datetime(o.entry_time, utc=True)
    if (known.notna() & (known != d.entry_t)).any():
        issues.append('limit entry_time differs from the fill bar close')
    d['entry_t'] = d.entry_t.dt.as_unit('ns'); d['exit_t'] = d.exit_t.dt.as_unit('ns')
    h, lo = f.high.to_numpy(), f.low.to_numpy()
    mae = []
    for a, b, s, e, m in zip(d.entry_i.astype(int), d.exit_i.astype(int), d.side, d.entry, d.market):
        a += bool(m)
        seg = slice(a, max(a+1, b))
        worst = lo[seg].min() if s == 1 else h[seg].max()
        mae.append(max(0., -s*(worst-e)/e*100))
    d['mae_pct'] = mae
    parts = d.index.str.split(':')
    d['event'] = parts.str[:3].str.join(':'); d['minutes'] = parts.str[3]; d['mode'] = parts.str[4]
    d['base'] = d.index.str.removesuffix('_add')
    d['trade_id'] = d.index
    return symbol, d.reset_index(drop=True), issues


PERIODS = ('all', 'y2020_2024', 'y2025', 'y2026', 'last5m')


def in_period(x, per, window):
    b25, b26 = pd.Timestamp('2025-01-01', tz='UTC'), pd.Timestamp('2026-01-01', tz='UTC')
    return {'all': x.entry_t >= pd.Timestamp('2020-01-01', tz='UTC'), 'y2020_2024': x.entry_t < b25,
            'y2025': (x.entry_t >= b25) & (x.entry_t < b26), 'y2026': x.entry_t >= b26, 'last5m': x.entry_t >= window}[per]


def replay(args):
    """One account x entry: per-period stats and the account's own sizing replay."""
    name, sizing, x, window, cfg, ro, sh, py = args
    from yoyo.evaluation.monday_limit_return_opt import frac
    from yoyo.evaluation.monday_limit_spike_hw import slot_replay
    from yoyo.evaluation.monday_limit_langlang import path_run
    from yoyo.evaluation.monday_limit_reclaim_optimize import replay_w
    x = x.assign(entry_ns=x.entry_t.astype('int64'), exit_ns=x.exit_t.astype('int64'))
    x['entry'], x['exit'] = x.entry_ns, x.exit_ns  # replay_w clock columns
    if (x.exit_ns < x.entry_ns).any():
        raise RuntimeError(f'{name}: exit before entry')
    out = {}
    for per in PERIODS:
        y = x[in_period(x, per, window)].reset_index(drop=True)
        st = dict(n=int(len(y)))
        if len(y):
            st.update(mean_bp=float(y.net.mean()), win_pct=float(100*(y.net > 1).mean()), sum_pct=float(y.net.sum()/100),
                      target_pct=float(100*(y.reason == 4).mean()))
            reps = []
            if sizing in ('fixed', 'linear'):
                rng = np.random.default_rng(ro['seed'])
                for _ in range(ro['ties']):
                    z = y.iloc[np.lexsort((rng.random(len(y)), y.entry_ns.to_numpy()))].reset_index(drop=True)
                    reps.append(replay_w(z, z.k.to_numpy(), 1.)[0])
            elif sizing == 'frac':
                reps = [frac(y, 20, 3, ro['seed']+r) for r in range(ro['ties'])]
            elif sizing == 'slots':
                reps = [slot_replay(y, sh['sizing']['slots'], sh['sizing']['leverage'], sh['sizing']['liq_pct'], sh['seed']+r)[0] for r in range(sh['ties'])]
            if reps:
                st['replay'] = dict(final_median=float(np.median([r['final_multiple'] for r in reps])),
                                    dd_median=float(np.median([r['max_drawdown_pct'] for r in reps])))
        out[per] = st
    if sizing == 'allin':
        rows = []
        for k, s0 in enumerate(pd.date_range(py['first_start'], py['last_start'], freq='MS', tz='UTC')):
            y = x[x.entry_t >= s0].reset_index(drop=True)
            for r in range(py['ties']):
                h = path_run(y, 'allin', 3, py['seed']+1000*k+r)
                rows.append({**{f'd{q}': (h[q]['days'] if h[q] else np.nan) for q in (10, 100)},
                             **{f'b{q}': (h[q]['blowups'] if h[q] else np.nan) for q in (10, 100)}})
        d = pd.DataFrame(rows)
        out['allin3x_monthly_starts'] = {f'x{q}': dict(reached_pct=float(100*d[f'd{q}'].notna().mean()),
                                                       days_median=float(d[f'd{q}'].median()) if d[f'd{q}'].notna().any() else None,
                                                       blowups_before_median=float(d[f'b{q}'].median()) if d[f'b{q}'].notna().any() else None)
                                         for q in (10, 100)}
    return name, out


def summarize(config, workers):
    from yoyo.evaluation.monday_limit_be_scalein_compare import BAD_EVENTS, clean
    exp = config.resolve().parent
    cfg = json.loads(config.read_text())
    committed('yoyo/evaluation/monday_limit_paper_accounts.py')
    ro = json.loads((A/'exp-monday-limit-return-opt-20261027-v2/config.json').read_text())
    sh = json.loads((A/'exp-monday-limit-spike-hw-20261026-v1/config.json').read_text())
    py = json.loads((A/'exp-monday-limit-pyramid-20261027-v1/compare.json').read_text())
    syms = symbols(cfg)
    slip = cfg['slippage_bp']
    with ProcessPoolExecutor(workers) as ex:
        got = list(ex.map(load_symbol, [(str(exp), s, slip['market_entry'], slip['non_target_exit']) for s in syms], chunksize=4))
    t = pd.concat([g[1] for g in got if len(g[1])], ignore_index=True)
    t = t[~t.event.isin(BAD_EVENTS)].copy()
    pre, full = scores(cfg)
    order = pd.read_csv(ROOT/cfg['features'], usecols=['event', 'l2_order_score']).set_index('event').l2_order_score
    t['pre'] = t.event.map(pre); t['full'] = t.event.map(full); t['order'] = t.event.map(order)
    t['family'] = t.variant.str.split('_').str[0]
    t['kind'] = t.variant.str.split('_', n=1).str[1]
    sz = cfg['v3_sizing']
    th, cut, hi = cfg['v2_threshold'], cfg['cut'], cfg['hi']
    window = pd.Timestamp(json.loads((A/'exp-monday-limit-reclaim-recent-20261025-v1/summary.json').read_text())['results']['window'][0])
    entries = {'A_rest_causal': (['rest'], 'pre'), 'leaky_rest_touchbar': (['rest'], 'full'),
               'close_inside': (['ci'], 'full'), 'close_all': (['ci', 'cb'], 'full')}
    entries = {k: v for k, v in entries.items() if set(v[0]) <= set(cfg.get('families', ['rest', 'ci', 'cb']))}
    plans = {}
    for ename, (fams, col) in entries.items():
        g = t[t.family.isin(fams)]
        sc = g[col]
        th = cfg.get('v2_threshold_pre', cfg['v2_threshold']) if col == 'pre' else cfg['v2_threshold']
        v3sel = lambda kind: g[(g.kind == kind) & (sc >= cut) & (g['mode'] == V3MODE(sc, hi))]
        lin = lambda y: np.minimum(sz['fraction']*(y[col]-sz['cut'])/sz['mean_excess'], sz['cap'])
        v2 = g[(g.kind == 'v2') & (sc >= th)]
        v3 = v3sel('v3').assign(k=lambda y: lin(y))
        legs = g[(g.kind == 'v3_add') & g.base.isin(v3.trade_id) & (sc >= hi)]
        legs = legs.assign(k=legs.base.map(v3.set_index('trade_id').k))
        plans[f'v2_60|{ename}'] = ('fixed', v2[v2.minutes == 'm60'].assign(k=cfg['v2_fraction']))
        plans[f'v2_5|{ename}'] = ('fixed', v2[v2.minutes == 'm5'].assign(k=cfg['v2_fraction']))
        plans[f'v3|{ename}'] = ('linear', v3)
        plans[f'v3add|{ename}'] = ('linear', pd.concat([v3, legs]))
        plans[f'v3add20|{ename}'] = ('frac', pd.concat([v3, legs]))
        plans[f'v3lock|{ename}'] = ('linear', v3sel('v3lock').assign(k=lambda y: lin(y)))
        plans[f'spike|{ename}'] = ('slots', g[(g.kind == 'spike') & (sc >= hi) & (g.order >= cfg['min_order'])])
        plans[f'allin3|{ename}'] = ('allin', g[(g.kind == 'v3') & (sc >= hi) & (g['mode'] == 'opposite_1618')])
    res = dict(checks=dict(symbols=len(syms), issues=[f'{g[0]}: {i}' for g in got for i in g[2]],
                           rows={str(k): int(v) for k, v in t.variant.value_counts().items()},
                           plan_rows={k: int(len(v)) for k, (_, v) in plans.items()}), plans={})
    with ProcessPoolExecutor(workers) as ex:
        for name, out in ex.map(replay, [(k, s, v.reset_index(drop=True), window, cfg, ro, sh, py) for k, (s, v) in plans.items()]):
            res['plans'][name] = out
    res = clean(res)
    dump(exp/'summary.json', dict(config=cfg, results=res, source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                                 training_eligible=False, production_eligible=False))
    print(json.dumps(res['checks']['issues'][:20]))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, required=True); ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--intrabar', choices=['OHLC', 'OLHC']); ap.add_argument('--summary', action='store_true')
    a = ap.parse_args()
    summarize(a.config, a.workers) if a.summary else run(a.config, a.workers, a.intrabar)
