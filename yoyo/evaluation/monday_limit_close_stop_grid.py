"""Tighter stops for the touch-close survivor slice (owner 2026-11-01).

Owner: "−30%兜底止损 是不是有点太多了？" then "测更紧止损". The survivor slice (frozen touch-bar score >= 0.5, touch
5m bar closed still beyond the Monday 1.618 having recovered < 0.64 of the excursion; market entry at that close; 60m
reclaim then stop to the level) used a backstop 30 percent beyond the level. The saved -18 percent (B18) was worse.
This runs the same signal rows in NautilusTrader with only the stop changed:
  L30, L18  stop 30 / 18 percent beyond the level, as B30 / B18 (parity: must equal the saved fills);
  E5..E30   stop that percent from the entry price (the close), so no event drops out: a level-based stop tighter than
            the entry's own distance past the level would sit on the wrong side of the entry.
Everything else is monday_limit_close_slice_control.process for the signal: same events (survivors.csv.gz of that run),
B30 entry conditions, targets near edge and range 0.5, reclaim at the next 60m close then stop to the level, market
entry, maker target, taker stop / reclaim exits (OKX Lv1, read 2026-10-23), 10bp slippage on the market entry and on
non-target exits, both intrabar paths and the worse one per row. Only signal rows, no random controls.
Summary: per stop, target and period the per-trade mean, win rate and exit mix; frac account replays at fixed sizings
(2020-2024 one seed; 2025 onward 10 tie seeds, median final and worst drawdown); parity of L30 and L18.
NautilusTrader rather than VectorBT: the reclaim, breakeven and target semantics live in the Nautilus adapter that
produced every fill being compared, so the stop is the only change.
Follow-up, owner "60分钟未收回·市价平 为什么加这个逻辑 还有更好的逻辑吗": a variant may also set `reclaim_minutes`
(default 60, the saved rule). The adapter decides at the close of the candle after the one holding the touch on that
clock (5m .. 1440m): closed back inside the level -> stop to the level, else exit at that close. `parity` lists which
variants must equal which saved fills.
The engine streams day chunks and skips idle stretches (run_native skip_idle_bars, 288-bar batches): fills are
identical to streaming the whole history (checked on 1000CATUSDT and DEGOUSDT, engine 20 s -> 0.1 s per symbol);
symbols finished before this switch were streamed in full.
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
from yoyo.evaluation import monday_limit_close_reclaim as cr
from yoyo.evaluation.monday_limit_backstop import CODEX, stops

A = ROOT/'experiments/active'
PER = {'y2020_2024': ('2020-01-01', '2025-01-01'), 'y2025': ('2025-01-01', '2026-01-01'), 'y2026': ('2026-01-01', '2100-01-01'),
       'from2025': ('2025-01-01', '2100-01-01'), 'all': ('2020-01-01', '2100-01-01')}


def process(symbol, intrabar, exp, cfg, events):
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
    e = e[(e.reclaim_minutes == 60) & (e.week >= cfg['since_week']) & e.exit_mode.isin(cfg['targets'])].copy()
    e['event'] = e.trade_id.str.split(':').str[:3].str.join(':')
    close = p.close.to_numpy()
    sig = e[(e.population == 'signal') & e.event.isin(events)].copy()
    k = sig.touch_i_hint.astype(int).to_numpy()
    s, L, T = sig.side.to_numpy(float), sig.entry.to_numpy(float), sig.target_price.to_numpy(float)
    c = close[k]
    ok = (s*(c-L) < tick*(1-1e-7)) & (~np.isfinite(T) | (s*(T-c) >= tick*(1-1e-7))) & (k < sig.end_i.to_numpy())
    dropped = sorted(set(sig.event[~ok]))
    sig = sig[ok].copy()
    stats = dict(signal_rows=int(len(sig)), events=int(sig.event.nunique()), listed=len(events), dropped=dropped, wrong_side={})
    parts = []
    for st in cfg['stops']:
        if not len(sig):
            break
        x = sig.copy()
        x['reclaim_ref'] = x.entry
        if st['base'] == 'level':
            x['stop'] = stops(x, 'P', st['pct'], tick)  # computed while entry is still the level, as B30 / B18
        x['entry_i'] = x.touch_i_hint.astype(int)
        x['entry'] = close[x.entry_i.to_numpy()]
        if st['base'] == 'entry':
            x['stop'] = stops(x, 'P', st['pct'], tick)
        x['reclaim_minutes'] = int(st.get('reclaim_minutes', 60))
        x['trade_id'] = x.trade_id+':v_'+st['name']
        good = x.side*(x.entry-x.stop) > 0
        stats['wrong_side'][st['name']] = sorted(set(x.event[~good]))
        parts.append(x[good])
    out = Path(exp)/intrabar.lower()/'symbols'/symbol
    out.mkdir(parents=True, exist_ok=True)
    x = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    if x.empty:
        pd.DataFrame(columns=cr.KEEP+['variant']).to_csv(out/'trades.csv.gz', index=False)
        dump(out/'done.json', dict(symbol=symbol, intrabar=intrabar, orders=0, positions=0, **stats))
        return dict(symbol=symbol, orders=0)
    x['risk'] = x.entry
    x['be_offset_bp'] = 0.
    frame = p.copy(); frame.index = frame.index+pd.Timedelta(minutes=job['execution_minutes'])
    policy = ExitPolicy(trail_arm_r=1e12, opposite_hour_exit=False, round_trip_cost=0, target_from_entry=True,
                        intrabar_path=intrabar, reclaim_next_bar=True, breakeven_includes_cost=False,
                        fill_maker_fee=ccfg['maker_fee'], fill_taker_fee=ccfg['taker_fee'], monotone_segments=True,
                        reclaim_backstop=True)
    rows, _, audit = run_native(frame, np.full(len(frame), np.nan), np.zeros(len(frame)),
                                x.drop(columns=['event', 'population', 'exit_mode'], errors='ignore'), policy, tick=tick,
                                symbol=symbol.removesuffix('USDT'), execution_minutes=job['execution_minutes'],
                                entry_order_mode='market', batch_bars=288, skip_idle_bars=True)
    rows = rows[[k_ for k_ in cr.KEEP if k_ in rows]]
    rows['variant'] = rows.trade_id.str.rsplit(':v_', n=1).str[-1]
    rows.to_csv(out/'trades.csv.gz', index=False)
    done = dict(symbol=symbol, intrabar=intrabar, orders=len(x), positions=audit['positions'], fills=audit['fills'],
                engine=audit.get('engine'), version=audit.get('version'), seconds=time.perf_counter()-t0, **stats)
    dump(out/'done.json', done)
    return done


def run(config, workers, intrabar):
    exp = config.resolve().parent
    cfg = json.loads(config.read_text())
    cr.committed('yoyo/evaluation/monday_limit_close_stop_grid.py', 'yoyo/layers/l3_backtest/nautilus_independent.py',
                 str(config.resolve().relative_to(ROOT)))
    sv = pd.read_csv(ROOT/cfg['survivors'])
    root = exp/intrabar.lower()
    todo = [s for s in sorted(set(sv.symbol)) if not (root/'symbols'/s/'done.json').exists()]
    with ProcessPoolExecutor(workers) as ex:
        futs = {ex.submit(process, s, intrabar, str(exp), cfg, set(sv.event[sv.symbol == s])): s for s in todo}
        for n, fu in enumerate(as_completed(futs), 1):
            try:
                r = fu.result()
            except Exception as err:  # keep going; record the failure per symbol
                r = dict(symbol=futs[fu], error=repr(err))
                (root/'symbols'/futs[fu]).mkdir(parents=True, exist_ok=True); dump(root/'symbols'/futs[fu]/'error.json', r)
            print(json.dumps(dict(progress=n, total=len(todo), **{k: v for k, v in r.items() if k != 'wrong_side'})), flush=True)


def window(x, p):
    a, b = PER[p]
    return x[(x.entry_t >= pd.Timestamp(a, tz='UTC')) & (x.entry_t < pd.Timestamp(b, tz='UTC'))].reset_index(drop=True)


def replays(args):
    name, x, cfg = args
    from yoyo.evaluation.monday_limit_return_opt import frac
    x = x.assign(entry_ns=x.entry_t.astype('int64'), exit_ns=x.exit_t.astype('int64'))
    out = {}
    for n, lev in cfg['sizings']:
        a = frac(window(x, 'y2020_2024'), n, lev, cfg['seed'])
        res = {}
        for p in ('from2025', 'all'):
            reps = [frac(window(x, p), n, lev, cfg['seed']+k) for k in range(cfg['ties'])]
            res[p] = dict(final_median=float(np.median([r['final_multiple'] for r in reps])), dd_max=float(np.max([r['max_drawdown_pct'] for r in reps])),
                          liquidations_median=float(np.median([r['liquidations'] for r in reps])))
        out[f'{n}x{lev}'] = dict(y2020_2024=dict(final=a['final_multiple'], dd=a['max_drawdown_pct'], liquidations=a['liquidations']), **res)
    return name, out


def summarize(config, workers):
    from yoyo.evaluation.monday_limit_be_scalein_compare import BAD_EVENTS, clean
    exp = config.resolve().parent
    cfg = json.loads(config.read_text())
    cr.committed('yoyo/evaluation/monday_limit_close_stop_grid.py')
    syms = sorted(set(pd.read_csv(ROOT/cfg['survivors']).symbol))
    missing = [(p, s) for p in ('ohlc', 'olhc') for s in syms if not (exp/p/'symbols'/s/'done.json').exists()]
    if missing:
        raise RuntimeError(f'Incomplete run: {len(missing)}')
    slip = cfg['slippage_bp']
    with ProcessPoolExecutor(workers) as ex:
        got = list(ex.map(cr.load_symbol, [(str(exp), s, slip['market_entry'], slip['non_target_exit']) for s in syms], chunksize=4))
    t = pd.concat([g[1] for g in got if len(g[1])], ignore_index=True)
    t = t[~t.event.isin(BAD_EVENTS) & (t.entry_t >= pd.Timestamp('2020-01-01', tz='UTC'))].copy()
    wrong = {}
    for p in ('ohlc', 'olhc'):
        for s in syms:
            for k, v in json.loads((exp/p/'symbols'/s/'done.json').read_text()).get('wrong_side', {}).items():
                wrong.setdefault(k, set()).update(v)
    # Parity: L30 against the control run's B30 (both targets), L18 against the close-reclaim B18 (near edge only).
    par = {}
    checks = cfg.get('parity') or [dict(name='L30', source=cfg['parity_b30'], variant='B30', modes=cfg['targets']),
                                   dict(name='L18', source=cfg['parity_b18'], variant='B18', modes=['near_monday_edge'])]
    for name, src, variant, modes in ((c['name'], c['source'], c['variant'], c['modes']) for c in checks):
        with ProcessPoolExecutor(workers) as ex:
            old = list(ex.map(cr.load_symbol, [(str(ROOT/src), s, slip['market_entry'], slip['non_target_exit']) for s in syms], chunksize=4))
        o = pd.concat([g[1] for g in old if len(g[1])], ignore_index=True)
        o = o[(o.variant == variant) & o['mode'].isin(modes)].assign(key=lambda y: y.event+':'+y['mode']).set_index('key')
        mine = t[(t.variant == name) & t['mode'].isin(modes)].assign(key=lambda y: y.event+':'+y['mode']).set_index('key')
        d = (mine.net-o.net.reindex(mine.index)).abs()
        par[name] = dict(rows=int(len(mine)), missing=int(d.isna().sum()), max_abs_bp=float(d.max()))
    stats, jobs = {}, []
    for (mode, v), x in t.groupby(['mode', 'variant']):
        for p in PER:
            y = window(x, p)
            stats[f'{mode}|{v}|{p}'] = dict(n=int(len(y)), mean_bp=float(y.net.mean()) if len(y) else None,
                                            win_pct=float(100*(y.net > 1).mean()) if len(y) else None,
                                            stops=int((y.reason == 1).sum()), stop_mean_bp=float(y.net[y.reason == 1].mean()) if (y.reason == 1).any() else None,
                                            reasons={str(k): int(c) for k, c in y.reason.value_counts().items()})
        jobs.append((f'{mode}|{v}', x.reset_index(drop=True), cfg))
    plans = {}
    with ProcessPoolExecutor(workers) as ex:
        for name, out in ex.map(replays, jobs):
            plans[name] = out
    res = dict(checks=dict(rows=int(len(t)), events=int(t.event.nunique()), issues=[f'{g[0]}: {i}' for g in got for i in g[2]][:20],
                           wrong_side={k: len(v) for k, v in wrong.items()}, parity=par), stats=stats, plans=plans)
    dump(exp/'summary.json', dict(config=cfg, results=clean(res), source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                                 training_eligible=False, production_eligible=False))
    print(json.dumps(res['checks'], default=str))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, required=True); ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--intrabar', choices=['OHLC', 'OLHC']); ap.add_argument('--summary', action='store_true')
    a = ap.parse_args()
    summarize(a.config, a.workers) if a.summary else run(a.config, a.workers, a.intrabar)
