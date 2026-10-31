"""Matched random-time control for the touch-close survivor slice (owner 2026-11-01).

Owner: "补跑随机对照". exp-monday-limit-close-slices-20261101-v1 left one slice that holds in 2025 and 2026: events whose
frozen touch-bar score is >= 0.5 and whose touch 5m bar closed still beyond the level, having recovered less than the
2020-2024 lower tercile (0.64) of the excursion past the 1.618; market entry at that close, near Monday edge or range
0.5 target. The question is whether the moment matters: Codex's three matched random submissions of each event (same
symbol, week, side and volatility bucket) enter at market at their own 5m close with the event's geometry scaled by
close / touch close (target, the -30 percent stop and the reclaim level), so the control also enters the same
percentage beyond a virtual level and needs the same 60m reclaim. The survivor's signal rows are run again in the same
NautilusTrader run (the adapter changed after the 10-28 run) and must equal the saved close-reclaim fills; policy, fees
and slippage are those of monday_limit_close_reclaim.process. The event list is frozen in survivors.csv.gz before any
fill, from the touch bar and earlier bars only, as the slice study: score (`stored`, known at the touch close) and
recovery = side x (close - extreme) / side x (level - extreme) of the touch bar; process then applies the B30 entry
conditions (closed at or beyond the level, short of the target) and records any listed event they drop.
Summary: worse path, 10bp slippage on the market entry and on non-target exits; per period the signal, the controls and
the paired per-event difference (signal minus the mean of its controls, cluster bootstrap by side x touch bar), plus
frac N20 3x replays of the signal and of each control index alone.
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
from yoyo.evaluation.monday_limit_reclaim_patterns import SRC

A = ROOT/'experiments/active'
PER = {'y2020_2024': ('2020-01-01', '2025-01-01'), 'y2025': ('2025-01-01', '2026-01-01'), 'y2026': ('2026-01-01', '2100-01-01'),
       'test': ('2025-01-01', '2100-01-01'), 'all': ('2020-01-01', '2100-01-01')}


def edge(cfg):
    return json.loads((ROOT/cfg['slices']/'summary.json').read_text())['results']['edges']['recovery'][0]


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
    # B30 conditions of the close-reclaim run: closed at or beyond the level, short of the target, before the segment end.
    ok = (s*(c-L) < tick*(1-1e-7)) & (~np.isfinite(T) | (s*(T-c) >= tick*(1-1e-7))) & (k < sig.end_i.to_numpy())
    dropped = sorted(set(sig.event[~ok]))
    sig = sig[ok].copy()
    stats = dict(signal_rows=int(len(sig)), events=int(sig.event.nunique()), listed=len(events), dropped=dropped)
    parts = []
    if len(sig):
        x = sig.copy()
        x['reclaim_ref'] = x.entry
        x['stop'] = stops(x, 'P', 30, tick)  # from the level, as B30 of the close-reclaim run
        x['entry_i'] = x.touch_i_hint.astype(int)
        x['entry'] = close[x.entry_i.to_numpy()]
        x['trade_id'] = x.trade_id+':v_B30'
        parts.append(x[x.side*(x.entry-x.stop) > 0])
        ref = sig.set_index('pair_id')
        r = e[(e.population == 'random') & e.pair_id.isin(ref.index)].copy()
        if len(r):
            g = ref.loc[r.pair_id]
            j = r.entry_i.astype(int).to_numpy()
            ratio = close[j]/close[g.touch_i_hint.astype(int).to_numpy()]
            sd = r.side.to_numpy(float)
            r['entry'] = close[j]
            r['target_price'] = np.where(np.isfinite(g.target_price.to_numpy(float)), cr.rnd(g.target_price.to_numpy(float)*ratio, tick, sd, -1), np.nan)
            r['reclaim_ref'] = cr.rnd(g.entry.to_numpy(float)*ratio, tick, sd, -1)
            r['stop'] = np.maximum(cr.rnd(stops(g, 'P', 30, tick)*ratio, tick, sd, -1), tick)
            r['trade_id'] = r.trade_id+':v_rnd'
            # The control enters beyond its virtual level, as the signal did.
            ok = (sd*(r.entry-r.stop) > 0) & (sd*(r.reclaim_ref-r.entry) >= tick*(1-1e-7)) & (j < r.end_i.to_numpy()) & \
                 (~np.isfinite(r.target_price) | (sd*(r.target_price-r.entry) >= tick*(1-1e-7)))
            parts.append(r[ok])
            stats.update(random_rows=int(len(r)), random_ok=int(ok.sum()))
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
                                entry_order_mode='market', batch_bars=ccfg['streaming_batch_bars'])
    rows = rows[[k_ for k_ in cr.KEEP if k_ in rows]]
    rows['variant'] = rows.trade_id.str.rsplit(':v_', n=1).str[-1]
    rows.to_csv(out/'trades.csv.gz', index=False)
    done = dict(symbol=symbol, intrabar=intrabar, orders=len(x), positions=audit['positions'], fills=audit['fills'],
                engine=audit.get('engine'), version=audit.get('version'), seconds=time.perf_counter()-t0, **stats)
    dump(out/'done.json', done)
    return done


def touch_facts(args):
    """Survivor events of one symbol: stored score >= cut and recovery < the slice edge, as monday_limit_close_slices."""
    symbol, cfg, cut_edge = args
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    from yoyo.evaluation.monday_limit_close_slices import LCOLS
    lv = pd.read_csv(SRC/symbol/'trades.csv.gz', usecols=LCOLS)
    lv = lv[(lv.population == 'signal') & (lv.reclaim_minutes == 60) & (lv.exit_mode == 'near_monday_edge') & (lv.week >= cfg['since_week'])].copy()
    lv['event'] = symbol+':'+lv.week+':'+np.where(lv.side == 1, '+1', '-1')
    sc = pd.read_csv(ROOT/cfg['scores']).set_index('event').stored
    lv = lv[lv.event.map(sc) >= cfg['cut']]
    if lv.empty:
        return pd.DataFrame()
    f, _ = load_job(json.loads((SRC/symbol/'identity.json').read_text())['job'])
    k = lv.touch_i_hint.astype(int).to_numpy()
    s, L = lv.side.to_numpy(float), lv.level.to_numpy(float)
    ext = np.where(s > 0, f.low.to_numpy()[k], f.high.to_numpy()[k])
    past = s*(L-ext)
    lv['recovery'] = np.where(past > 0, s*(f.close.to_numpy()[k]-ext)/np.where(past > 0, past, 1), np.nan)
    lv['score'] = lv.event.map(sc)
    return lv.loc[lv.recovery < cut_edge, ['event', 'score', 'recovery']].assign(symbol=symbol)


def survivors(cfg, workers):
    sc = pd.read_csv(ROOT/cfg['scores'])
    syms = sorted({e.split(':')[0] for e, v in zip(sc.event, sc.stored) if v >= cfg['cut'] and e.split(':')[1] >= cfg['since_week']})
    with ProcessPoolExecutor(workers) as ex:
        got = list(ex.map(touch_facts, [(s, cfg, edge(cfg)) for s in syms], chunksize=4))
    return pd.concat([g for g in got if len(g)], ignore_index=True)


def run(config, workers, intrabar):
    exp = config.resolve().parent
    cfg = json.loads(config.read_text())
    cr.committed('yoyo/evaluation/monday_limit_close_slice_control.py', 'yoyo/layers/l3_backtest/nautilus_independent.py',
                 str(config.resolve().relative_to(ROOT)))
    path = exp/'survivors.csv.gz'
    if not path.exists():
        survivors(cfg, workers).to_csv(path, index=False)
    sv = pd.read_csv(path)
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
            print(json.dumps(dict(progress=n, total=len(todo), **r)), flush=True)


def period(x, p):
    a, b = PER[p]
    return x[(x.sig_t >= pd.Timestamp(a, tz='UTC')) & (x.sig_t < pd.Timestamp(b, tz='UTC'))]


def summarize(config, workers):
    from yoyo.evaluation.monday_limit_be_scalein_compare import BAD_EVENTS, clean
    from yoyo.evaluation.monday_limit_close_slices import boot
    exp = config.resolve().parent
    cfg = json.loads(config.read_text())
    cr.committed('yoyo/evaluation/monday_limit_close_slice_control.py')
    ro = json.loads((A/'exp-monday-limit-return-opt-20261027-v2/config.json').read_text())
    sh = json.loads((A/'exp-monday-limit-spike-hw-20261026-v1/config.json').read_text())
    py = json.loads((A/'exp-monday-limit-pyramid-20261027-v1/compare.json').read_text())
    window = pd.Timestamp(json.loads((A/'exp-monday-limit-reclaim-recent-20261025-v1/summary.json').read_text())['results']['window'][0])
    syms = sorted(set(pd.read_csv(exp/'survivors.csv.gz').symbol))
    missing = [(p, s) for p in ('ohlc', 'olhc') for s in syms if not (exp/p/'symbols'/s/'done.json').exists()]
    if missing:
        raise RuntimeError(f'Incomplete run: {len(missing)}')
    slip = cfg['slippage_bp']
    with ProcessPoolExecutor(workers) as ex:
        got = list(ex.map(cr.load_symbol, [(str(exp), s, slip['market_entry'], slip['non_target_exit']) for s in syms], chunksize=4))
    t = pd.concat([g[1] for g in got if len(g[1])], ignore_index=True)
    t = t[~t.event.isin(BAD_EVENTS)].copy()
    t['key'] = t.event+':'+t['mode']
    sig = t[t.variant == 'B30'].copy()
    sig = sig[sig.entry_t >= pd.Timestamp('2020-01-01', tz='UTC')]
    t = t[t.key.isin(sig.key)].copy()
    t['sig_t'] = t.key.map(sig.set_index('key').entry_t)
    t['shock'] = t.side.astype(int).astype(str)+':'+t.sig_t.astype('int64').astype(str)
    # Parity: the re-run signal rows against the saved close-reclaim B30 fills.
    src = ROOT/cfg['source']
    with ProcessPoolExecutor(workers) as ex:
        old = list(ex.map(cr.load_symbol, [(str(src), s, slip['market_entry'], slip['non_target_exit']) for s in sorted(set(sig.event.str.split(':').str[0]))], chunksize=4))
    o = pd.concat([g[1] for g in old if len(g[1])], ignore_index=True)
    o = o[(o.variant == 'B30')].assign(key=lambda y: y.event+':'+y['mode']).set_index('key')
    diff = (sig.set_index('key').net-o.net.reindex(sig.key)).abs()
    res = dict(checks=dict(signal_rows=int(len(sig)), events=int(sig.event.nunique()), random_rows=int((t.variant == 'rnd').sum()),
                           parity_missing=int(diff.isna().sum()), parity_max_abs_bp=float(diff.max()),
                           issues=[f'{g[0]}: {i}' for g in got for i in g[2]][:20]), periods={}, plans={})
    rng = np.random.default_rng(cfg['bootstrap_seed'])
    jobs = []
    for mode in cfg['targets']:
        m = t[t['mode'] == mode]
        s_, r_ = m[m.variant == 'B30'], m[m.variant == 'rnd'].copy()
        r_['cid'] = r_.groupby('key').cumcount()
        cm = r_.groupby('key').net.mean()
        paired = s_.set_index('key').net.sub(cm).dropna()
        ps = s_.set_index('key').shock
        for p in PER:
            sp, rp = period(s_, p), period(r_, p)
            pp = paired[paired.index.isin(sp.key)]
            res['periods'][f'{mode}|{p}'] = dict(signal=boot(sp.net, sp.shock, rng, cfg['draws']), random=boot(rp.net, rp.shock, rng, cfg['draws']),
                                                 events_without_random=int((~sp.key.isin(cm.index)).sum()),
                                                 paired=boot(pp, ps.reindex(pp.index), rng, cfg['draws']))
        jobs.append((f'{mode}|signal', 'frac', s_.reset_index(drop=True), window, ro, sh, py))
        for k in range(3):
            jobs.append((f'{mode}|random_c{k}', 'frac', r_[r_.cid == k].reset_index(drop=True), window, ro, sh, py))
    with ProcessPoolExecutor(workers) as ex:
        for name, out in ex.map(cr.replay, jobs):
            res['plans'][name] = out
    dump(exp/'summary.json', dict(config=cfg, results=clean(res), source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                                 training_eligible=False, production_eligible=False))
    print(json.dumps(res['checks']))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, required=True); ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--intrabar', choices=['OHLC', 'OLHC']); ap.add_argument('--summary', action='store_true')
    a = ap.parse_args()
    summarize(a.config, a.workers) if a.summary else run(a.config, a.workers, a.intrabar)
