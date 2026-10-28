"""Enter at market when the Monday1618 touch bar closes back inside the level, and which target suits it (owner 2026-10-28).

Owner: "收盘已收回点位内才市价进 那止盈是不是需要调整呢？带着这个问题去回测看看吧". The frozen score read at the
touch bar's close is causal for an entry at that close: the bar has closed. Native NautilusTrader run, market entries
at the completed touch-bar close (taker), every signal event of Codex's limit-reclaim inputs (alts, 60m confirmation):
  entry    touch bar closed back inside the level (side x (close - level) >= one tick) and short of the target
           (variants P30 / P18); the owner asked why exclude the rest, so touches that closed at or beyond the level
           (but short of the stop) also enter at that close as separate variants (B30 / B18);
  targets  all five Codex target modes (near Monday edge, range 0.5, range 0.618, opposite Monday edge, opposite
           1618), prices unchanged;
  stop     -30 percent from the level (P30) for every mode, plus -18 percent (P18) for the near edge (spike);
  reclaim  60m confirmation tested against the level, then the stop moves to the level (layer C of
           monday_limit_layered);
  control  for events whose stored score is >= 0.5, Codex's three matched random submissions of the same symbol, week,
           side and volatility bucket enter at market at their own close with the same percentage geometry (target,
           stop and reclaim level scaled by close / touch close), v3 target mode, P30.
Rows for every event are run once; scores (exp-monday-limit-score-leak-audit-20261027-v1 `stored`) and the 15m EMA
order score are applied in `summarize`, read-only on fills: worse path per row, 10bp slippage on the market entry and
on non-target exits, the two bad source bars dropped, MAE from the bar after the entry.
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

KEEP = ['trade_id', 'side', 'entry', 'stop', 'target_price', 'exit_price', 'reason', 'exit_time', 'entry_i', 'exit_i',
        'reclaim_status', 'net_notional_bp']
A = ROOT/'experiments/active'


def rnd(x, tick, side, toward):
    """Round a price onto the tick grid; toward=+1 rounds in the trade's favour, -1 against."""
    up = (side > 0) == (toward > 0)
    return np.where(up, np.ceil(x/tick-1e-7)*tick, np.floor(x/tick+1e-7)*tick)


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
    e = e[(e.reclaim_minutes == 60) & (e.week >= cfg['since_week'])].copy()
    e['event'] = e.trade_id.str.split(':').str[:3].str.join(':')
    sc = pd.read_csv(ROOT/cfg['scores']).set_index('event').stored
    close = p.close.to_numpy()
    sig = e[e.population == 'signal'].copy()
    k = sig.touch_i_hint.astype(int).to_numpy()
    s, L, T = sig.side.to_numpy(float), sig.entry.to_numpy(float), sig.target_price.to_numpy(float)
    c = close[k]
    inside = s*(c-L) >= tick*(1-1e-7)
    room = ~np.isfinite(T) | (s*(T-c) >= tick*(1-1e-7))
    sig['inside'] = inside
    sig = sig[room & (k < sig.end_i.to_numpy())].copy()
    stats = dict(signal_rows=int(len(e[e.population == 'signal'])), inside_rows=int(inside.sum()), entered_rows=int(len(sig)))
    sig['reclaim_ref'] = sig.entry
    parts = []
    for name, pct, modes, where in (('P30', 30, None, True), ('P18', 18, ['near_monday_edge'], True),
                                    ('B30', 30, None, False), ('B18', 18, ['near_monday_edge'], False)):
        x = sig[sig.inside == where] if modes is None else sig[(sig.inside == where) & sig.exit_mode.isin(modes)]
        x = x.copy()
        x['stop'] = stops(x, 'P', pct, tick)  # from the level, as the resting plan
        x['entry_i'] = x.touch_i_hint.astype(int)
        x['entry'] = close[x.entry_i.to_numpy()]
        x['trade_id'] = x.trade_id+f':v_{name}'
        parts.append(x[x.side*(x.entry-x.stop) > 0])
    # Matched random control: Codex's random submissions of events whose touch-close score clears the cut.
    hi = sig[sig.inside & (sig.event.map(sc) >= cfg['cut']) & (sig.exit_mode == np.where(sig.event.map(sc) >= cfg['hi'], 'opposite_1618', 'range_0.618'))]
    ref = hi.set_index('pair_id')
    r = e[(e.population == 'random') & e.pair_id.isin(ref.index)].copy()
    if len(r):
        g = ref.loc[r.pair_id]
        j = r.entry_i.astype(int).to_numpy()
        ratio = close[j]/close[g.touch_i_hint.astype(int).to_numpy()]
        sd = r.side.to_numpy(float)
        r['entry'] = close[j]
        r['target_price'] = np.where(np.isfinite(g.target_price.to_numpy(float)), rnd(g.target_price.to_numpy(float)*ratio, tick, sd, -1), np.nan)
        r['reclaim_ref'] = rnd(g.entry.to_numpy(float)*ratio, tick, sd, -1)
        r['stop'] = np.maximum(rnd(stops(g, 'P', 30, tick)*ratio, tick, sd, -1), tick)
        r['trade_id'] = r.trade_id+':v_rnd'
        ok = (sd*(r.entry-r.stop) > 0) & (sd*(r.entry-r.reclaim_ref) >= tick*(1-1e-7)) & (j < r.end_i.to_numpy()) & \
             (~np.isfinite(r.target_price) | (sd*(r.target_price-r.entry) >= tick*(1-1e-7)))
        parts.append(r[ok])
        stats['random_rows'] = int(ok.sum())
    x = pd.concat(parts, ignore_index=True)
    out = Path(exp)/intrabar.lower()/'symbols'/symbol
    out.mkdir(parents=True, exist_ok=True)
    if x.empty:
        pd.DataFrame(columns=KEEP+['variant']).to_csv(out/'trades.csv.gz', index=False)
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
                                x.drop(columns=['event', 'population', 'exit_mode', 'inside'], errors='ignore'), policy, tick=tick,
                                symbol=symbol.removesuffix('USDT'), execution_minutes=job['execution_minutes'],
                                entry_order_mode='market', batch_bars=ccfg['streaming_batch_bars'])
    rows = rows[[k_ for k_ in KEEP if k_ in rows]]
    rows['variant'] = rows.trade_id.str.rsplit(':v_', n=1).str[-1]
    rows.to_csv(out/'trades.csv.gz', index=False)
    done = dict(symbol=symbol, intrabar=intrabar, orders=len(x), positions=audit['positions'], fills=audit['fills'],
                engine=audit.get('engine'), version=audit.get('version'), seconds=time.perf_counter()-t0, **stats)
    dump(out/'done.json', done)
    return done


def committed(*paths):
    for path in paths:
        if subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--', path], cwd=ROOT).returncode or \
           subprocess.run(['git', 'ls-files', '--error-unmatch', path], cwd=ROOT, capture_output=True).returncode:
            raise RuntimeError('Commit builder and config first')


def symbols(cfg):
    sc = pd.read_csv(ROOT/cfg['scores'])
    return sorted({e.split(':')[0] for e in sc.event if e.split(':')[1] >= cfg['since_week']})


def run(config, workers, intrabar):
    exp = config.resolve().parent
    cfg = json.loads(config.read_text())
    committed('yoyo/evaluation/monday_limit_close_reclaim.py', 'yoyo/layers/l3_backtest/nautilus_independent.py',
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
    """Worse path per row with slippage, entry time from the entry bar's close, MAE from the next bar."""
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
        t['net'] = t.net_notional_bp-slip_entry-np.where(t.reason == 4, 0., slip_exit)
        paths[p] = t.set_index('trade_id')
    if len(paths) < 2 or paths['ohlc'].empty:
        return symbol, pd.DataFrame(), issues
    o = paths['ohlc']; l = paths['olhc'].reindex(o.index)
    if l.net.isna().any():
        issues.append('rows missing on one path')
    w = (l.net < o.net).to_numpy()
    pick = lambda c: np.where(w, l[c], o[c])
    d = pd.DataFrame(dict(variant=o.variant, side=o.side, entry=o.entry, net=pick('net'), reason=pick('reason'),
                          entry_i=o.entry_i, exit_i=pick('exit_i'),
                          exit_t=pd.to_datetime(pd.Series(pick('exit_time'), index=o.index), utc=True)), index=o.index)
    job = json.loads((CODEX/'symbols'/symbol/'identity.json').read_text())['job']
    f, _ = load_job(job)
    d['entry_t'] = pd.Series(f.index[d.entry_i.astype(int).to_numpy()]+pd.Timedelta(minutes=job['execution_minutes']), index=d.index)
    d['entry_t'] = d.entry_t.dt.as_unit('ns'); d['exit_t'] = d.exit_t.dt.as_unit('ns')
    h, lo = f.high.to_numpy(), f.low.to_numpy()
    mae = []
    for a, b, s, e in zip(d.entry_i.astype(int)+1, d.exit_i.astype(int), d.side, d.entry):
        seg = slice(a, max(a+1, b))
        worst = lo[seg].min() if s == 1 else h[seg].max()
        mae.append(max(0., -s*(worst-e)/e*100))
    d['mae_pct'] = mae
    parts = d.index.str.split(':')
    d['event'] = parts.str[:3].str.join(':'); d['mode'] = parts.str[4]
    return symbol, d.reset_index(drop=True), issues


PERIODS = ('all', 'y2020_2024', 'y2025', 'y2026', 'last5m')


def in_period(x, per, window):
    b25, b26 = pd.Timestamp('2025-01-01', tz='UTC'), pd.Timestamp('2026-01-01', tz='UTC')
    return {'all': x.entry_t >= pd.Timestamp('2020-01-01', tz='UTC'), 'y2020_2024': x.entry_t < b25,
            'y2025': (x.entry_t >= b25) & (x.entry_t < b26), 'y2026': x.entry_t >= b26, 'last5m': x.entry_t >= window}[per]


def stat(y):
    if not len(y):
        return dict(n=0)
    return dict(n=int(len(y)), mean_bp=float(y.net.mean()), win_pct=float(100*(y.net > 1).mean()), sum_pct=float(y.net.sum()/100),
                target_pct=float(100*(y.reason == 4).mean()))


def replay(args):
    """One plan: per-period stats and its sizing replay (frac / slots / all-in from monthly starts)."""
    name, kind, x, window, ro, sh, py = args
    from yoyo.evaluation.monday_limit_return_opt import frac
    from yoyo.evaluation.monday_limit_spike_hw import slot_replay
    from yoyo.evaluation.monday_limit_langlang import path_run
    x = x.assign(entry_ns=x.entry_t.astype('int64'), exit_ns=x.exit_t.astype('int64'))
    if (x.exit_ns < x.entry_ns).any():
        raise RuntimeError(f'{name}: exit before entry')
    out = {}
    for per in PERIODS:
        y = x[in_period(x, per, window)].reset_index(drop=True)
        st = stat(y)
        if len(y) and kind in ('frac', 'slots'):
            reps = ([frac(y, 20, 3, ro['seed']+r) for r in range(ro['ties'])] if kind == 'frac' else
                    [slot_replay(y, sh['sizing']['slots'], sh['sizing']['leverage'], sh['sizing']['liq_pct'], sh['seed']+r)[0] for r in range(sh['ties'])])
            st['replay'] = dict(final_median=float(np.median([r['final_multiple'] for r in reps])),
                                dd_median=float(np.median([r['max_drawdown_pct'] for r in reps])),
                                liquidations_median=float(np.median([r['liquidations'] for r in reps])))
        out[per] = st
    if kind == 'allin':
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
    committed('yoyo/evaluation/monday_limit_close_reclaim.py')
    ro = json.loads((A/'exp-monday-limit-return-opt-20261027-v2/config.json').read_text())
    sh = json.loads((A/'exp-monday-limit-spike-hw-20261026-v1/config.json').read_text())
    py = json.loads((A/'exp-monday-limit-pyramid-20261027-v1/compare.json').read_text())
    syms = symbols(cfg)
    slip = cfg['slippage_bp']
    with ProcessPoolExecutor(workers) as ex:
        got = list(ex.map(load_symbol, [(str(exp), s, slip['market_entry'], slip['non_target_exit']) for s in syms], chunksize=4))
    t = pd.concat([g[1] for g in got if len(g[1])], ignore_index=True)
    t = t[~t.event.isin(BAD_EVENTS)].copy()
    sc = pd.read_csv(ROOT/cfg['scores']).set_index('event').stored
    order = pd.read_csv(ROOT/cfg['features'], usecols=['event', 'l2_order_score']).set_index('event').l2_order_score
    t['score'] = t.event.map(sc); t['order'] = t.event.map(order)
    v3mode = np.where(t.score >= cfg['hi'], 'opposite_1618', 'range_0.618')
    window = pd.Timestamp(json.loads((A/'exp-monday-limit-reclaim-recent-20261025-v1/summary.json').read_text())['results']['window'][0])
    res = dict(checks=dict(symbols=len(syms), issues=[f'{g[0]}: {i}' for g in got for i in g[2]], rows={str(k): int(v) for k, v in t.variant.value_counts().items()}))
    # Target table: closed inside (P) vs at/beyond the level (B), by score band, every target mode, -30 percent stop.
    bands = {'all': t.score.notna(), 'ge_0.5': t.score >= cfg['cut'], 'ge_0.6': t.score >= cfg['hi'],
             'spike_filter': (t.score >= cfg['hi']) & (t.order >= cfg['min_order'])}
    res['targets'] = {}
    for close, var in (('inside', 'P30'), ('beyond', 'B30')):
        for b, m in bands.items():
            for mode in cfg['targets']:
                y = t[m & (t.variant == var) & (t['mode'] == mode)]
                res['targets'][f'{close}|{b}|{mode}'] = {per: stat(y[in_period(y, per, window)]) for per in PERIODS}
    for close, var in (('inside', 'P18'), ('beyond', 'B18')):
        for b in ('ge_0.6', 'spike_filter'):
            y = t[bands[b] & (t.variant == var)]
            res['targets'][f'{close}|{b}|near_monday_edge_P18'] = {per: stat(y[in_period(y, per, window)]) for per in PERIODS}
    # Matched random control on the events of the v3-like plan.
    sig = t[(t.variant == 'P30') & (t.score >= cfg['cut']) & (t['mode'] == v3mode)]
    rnd = t[t.variant == 'rnd']
    res['random_control'] = dict(signal=stat(sig), random=stat(rnd),
                                 signal_events_with_random=stat(sig[sig.event.isin(rnd.event)]),
                                 by_period={per: dict(signal=stat(sig[in_period(sig, per, window)]), random=stat(rnd[in_period(rnd, per, window)]))
                                            for per in PERIODS})
    # Strategy plans and sizing replays.
    ins, bey = t.variant == 'P30', t.variant == 'B30'
    plans = {'v3|inside': ('frac', t[ins & (t.score >= cfg['cut']) & (t['mode'] == v3mode)]),
             'v3|inside+beyond': ('frac', t[(ins | bey) & (t.score >= cfg['cut']) & (t['mode'] == v3mode)]),
             'spike|inside': ('slots', t[(t.variant == 'P18') & bands['spike_filter']]),
             'spike|inside+beyond': ('slots', t[t.variant.isin(['P18', 'B18']) & bands['spike_filter']])}
    for mode in cfg['targets']:
        plans[f'v3_target_{mode}|inside'] = ('frac', t[ins & (t.score >= cfg['cut']) & (t['mode'] == mode)])
        plans[f'allin3_target_{mode}|inside'] = ('allin', t[ins & (t.score >= cfg['hi']) & (t['mode'] == mode)])
    plans['allin3|inside+beyond'] = ('allin', t[(ins | bey) & (t.score >= cfg['hi']) & (t['mode'] == 'opposite_1618')])
    res['plans'] = {}
    with ProcessPoolExecutor(workers) as ex:
        for name, out in ex.map(replay, [(k, kind, x.reset_index(drop=True), window, ro, sh, py) for k, (kind, x) in plans.items()]):
            res['plans'][name] = out
    res = clean(res)
    dump(exp/'summary.json', dict(config=cfg, results=res, source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                                 training_eligible=False, production_eligible=False))
    print(json.dumps(res['checks']))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, required=True); ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--intrabar', choices=['OHLC', 'OLHC']); ap.add_argument('--summary', action='store_true')
    a = ap.parse_args()
    summarize(a.config, a.workers) if a.summary else run(a.config, a.workers, a.intrabar)
