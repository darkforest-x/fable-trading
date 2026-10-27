"""Layered live entries for the Monday1618 v3 rule: rest on the previous-close score, confirm at the touch close, enter late at that close (owner 2026-10-27).

Owner: the spike paper score is only known when the 5m touch bar closes, too late to rest a live limit. Proposed
layers: rest on a pre-score, confirm with the real score at the touch close and keep or exit; for a level touched
without a resting order, fall back to a market entry at that close with the stop unchanged; combine them and find
the cases that keep return and cut drawdown. Scores (frozen model) from
exp-monday-limit-score-leak-audit-20261027-v1/scores_causal.csv.gz:
  pre   `causal`, features through the bar before the touch: known while the order rests;
  full  `stored`, the same model reading the touch bar: known at the touch bar's close.
v3 exits throughout: range 0.618 target below 0.6 and opposite 1618 from 0.6, -30 percent backstop from the level,
60m reclaim with breakeven at the level. Variants (independent trades; one limit run and one market run per symbol
and intrabar path):
  rest      resting limit for pre >= 0.5, target by pre, held as v3 (layer A);
  rest_chk  the same order for events with full < 0.5, closed at market at its fill bar's close (reason 14) unless
            that bar already took the target or the backstop (layer B; for full >= 0.5 it equals rest);
  late      pre < 0.5 and full >= 0.5: no resting order; market entry at the touch bar's close when that close lies
            between the backstop and the target; backstop, target and reclaim test at the resting plan's level-based
            prices, target by full (layer C).
The owner's 1m spike entry needs 1m bars through each trade and is not run here. `summarize` is read-only
arithmetic on these fills: worse path per trade, 10bp slippage on non-target exits and on late market entries,
the two bad source bars dropped, parity of rest with the earlier v3_be0 ledger, and frac N20 3x replays.
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
from yoyo.evaluation.monday_limit_be_scalein import v3_mode

KEEP = ['trade_id', 'side', 'entry', 'stop', 'target_price', 'exit_price', 'reason', 'entry_time', 'exit_time', 'entry_i',
        'exit_i', 'reclaim_status', 'net_notional_bp']
A = ROOT/'experiments/active'


def process(symbol, intrabar, exp, cfg):
    from yoyo.evaluation.monday_limit_reclaim import prepare
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native
    ccfg = json.loads((CODEX/'config.json').read_text())
    job = json.loads((CODEX/'symbols'/symbol/'identity.json').read_text())['job']
    if job['group'] != 'crypto_5m':
        return dict(symbol=symbol, skipped=job['group'])
    t0 = time.perf_counter()
    cut = cfg['cut']
    f, quality = load_job(job)
    p, e, _, _, tick = prepare(f, job, ccfg, quality)
    e = e[(e.population == 'signal') & (e.reclaim_minutes == 60) & (e.week >= cfg['since_week'])].copy()
    sc = pd.read_csv(ROOT/cfg['scores']).set_index('event')
    e['event'] = e.trade_id.str.split(':').str[:3].str.join(':')
    e = e[e.event.isin(sc.index)].copy()
    e['pre'] = e.event.map(sc.causal); e['full'] = e.event.map(sc.stored)
    e['stop'] = stops(e, 'P', 30, tick)
    e['be_offset_bp'] = 0.
    rest = e[(e.pre >= cut) & (e.exit_mode == v3_mode(e.pre))].copy()
    rest['fill_close_exit'] = False
    rest['trade_id'] = rest.trade_id+':v_rest'
    chk = e[(e.pre >= cut) & (e.full < cut) & (e.exit_mode == v3_mode(e.pre))].copy()
    chk['fill_close_exit'] = True
    chk['trade_id'] = chk.trade_id+':v_rest_chk'
    late = e[(e.pre < cut) & (e.full >= cut) & (e.exit_mode == v3_mode(e.full))].copy()
    close = p.close.to_numpy()
    late['reclaim_ref'] = late.entry
    late['entry_i'] = late.touch_i_hint.astype(int)
    late['entry'] = close[late.entry_i.to_numpy()]
    late['risk'] = late.entry
    late['trade_id'] = late.trade_id+':v_late'
    tp, s, c = late.target_price.to_numpy(float), late.side.to_numpy(float), late.entry.to_numpy(float)
    past_target = np.isfinite(tp) & (s*(tp-c) < tick*(1-1e-7))
    past_stop = s*(c-late.stop.to_numpy(float)) <= 0
    short = late.entry_i.to_numpy() >= late.end_i.to_numpy()
    skips = dict(candidates=int(len(late)), past_target=int(past_target.sum()), past_backstop=int((past_stop & ~past_target).sum()),
                 no_room=int((short & ~past_target & ~past_stop).sum()))
    late = late[~(past_target | past_stop | short)]
    out = Path(exp)/intrabar.lower()/'symbols'/symbol
    out.mkdir(parents=True, exist_ok=True)
    frame = p.copy(); frame.index = frame.index+pd.Timedelta(minutes=job['execution_minutes'])
    policy = ExitPolicy(trail_arm_r=1e12, opposite_hour_exit=False, round_trip_cost=0, target_from_entry=True,
                        intrabar_path=intrabar, reclaim_next_bar=True, breakeven_includes_cost=False,
                        fill_maker_fee=ccfg['maker_fee'], fill_taker_fee=ccfg['taker_fee'], monotone_segments=True,
                        reclaim_backstop=True)
    parts, done = [], dict(symbol=symbol, intrabar=intrabar, late=skips)
    meta = ['event', 'pre', 'full', 'population', 'exit_mode']
    for mode, x in (('limit', pd.concat([rest, chk], ignore_index=True)), ('market', late.drop(columns=['fill_close_exit'], errors='ignore'))):
        if x.empty:
            done[mode] = dict(orders=0, positions=0)
            continue
        rows, _, audit = run_native(frame, np.full(len(frame), np.nan), np.zeros(len(frame)), x.drop(columns=meta), policy,
                                    tick=tick, symbol=symbol.removesuffix('USDT'), execution_minutes=job['execution_minutes'],
                                    entry_order_mode=mode, batch_bars=ccfg['streaming_batch_bars'])
        parts.append(rows[[k for k in KEEP if k in rows]].merge(x[['trade_id', 'pre', 'full']], on='trade_id'))
        done[mode] = dict(orders=len(x), positions=audit['positions'], fills=audit['fills'],
                          engine=audit.get('engine'), version=audit.get('version'))
    t = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=KEEP+['pre', 'full'])
    t['variant'] = t.trade_id.str.rsplit(':v_', n=1).str[-1]
    t.to_csv(out/'trades.csv.gz', index=False)
    done['seconds'] = time.perf_counter()-t0
    dump(out/'done.json', done)
    return done


def committed(*paths):
    for path in paths:
        if subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--', path], cwd=ROOT).returncode or \
           subprocess.run(['git', 'ls-files', '--error-unmatch', path], cwd=ROOT, capture_output=True).returncode:
            raise RuntimeError('Commit builder and config first')


def symbols(cfg):
    sc = pd.read_csv(ROOT/cfg['scores'])
    sc = sc[(sc.stored >= cfg['cut']) | (sc.causal >= cfg['cut'])]
    return sorted({e.split(':')[0] for e in sc.event if e.split(':')[1] >= cfg['since_week']})


def run(config, workers, intrabar):
    exp = config.resolve().parent
    cfg = json.loads(config.read_text())
    committed('yoyo/evaluation/monday_limit_layered.py', 'yoyo/layers/l3_backtest/nautilus_independent.py',
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
    """Worse path per trade with slippage, MAE from the entry's first exposed bar, and parity against v3_be0."""
    exp, symbol, ref_run, slip_exit, slip_late, parity_run = (tuple(args)+(None,))[:6]
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    from yoyo.evaluation.monday_limit_reclaim_patterns import SRC
    paths, issues, parity, compared = {}, [], 0, 0
    for p in ('ohlc', 'olhc'):
        d = Path(exp)/p/'symbols'/symbol
        if (d/'error.json').exists() or not (d/'done.json').exists():
            issues.append(f'{p}: missing or failed'); continue
        done = json.loads((d/'done.json').read_text())
        t = pd.read_csv(d/'trades.csv.gz')
        if sum(done.get(m, {}).get('positions', 0) for m in ('limit', 'market')) != len(t):
            issues.append(f'{p}: rows {len(t)} != positions')
        net = t.net_notional_bp-np.where(t.reason == 4, 0., slip_exit)-np.where(t.variant == 'late', slip_late, 0.)
        paths[p] = t.assign(net=net).set_index('trade_id')
        if parity_run:
            # Same orders under another score: rows sharing a trade id (event, target mode, variant) must match.
            r = A/parity_run/p/'symbols'/symbol/'trades.csv.gz'
            if r.exists():
                ref = pd.read_csv(r, usecols=['trade_id', 'net_notional_bp']).set_index('trade_id').net_notional_bp
                both = t.set_index('trade_id').net_notional_bp.reindex(ref.index).dropna()
                compared += len(both)
                parity += int(((both-ref.reindex(both.index)).abs() > 1e-6).sum())
            continue
        r = A/ref_run/p/'symbols'/symbol/'trades.csv.gz'
        rest = t[(t.variant == 'rest') & (t.full >= .5) & (v3_mode(t.pre) == v3_mode(t.full))]
        if len(rest) and not r.exists():
            parity += len(rest)
        elif len(rest):
            ref = pd.read_csv(r, usecols=['trade_id', 'variant', 'net_notional_bp'])
            ref = ref[ref.variant == 'v3_be0'].assign(base=lambda x: x.trade_id.str.rsplit(':v_', n=1).str[0]).set_index('base')
            got = rest.assign(base=rest.trade_id.str.rsplit(':v_', n=1).str[0]).set_index('base').net_notional_bp
            parity += int(((got-ref.net_notional_bp.reindex(got.index)).abs() > 1e-6).sum()+ref.net_notional_bp.reindex(got.index).isna().sum())
    if len(paths) < 2 or paths['ohlc'].empty:
        return symbol, pd.DataFrame(), issues, parity, compared
    o = paths['ohlc']; l = paths['olhc'].reindex(o.index)
    w = (l.net < o.net).to_numpy()
    pick = lambda c: np.where(w, l[c], o[c])
    d = pd.DataFrame(dict(variant=o.variant, side=o.side, entry=o.entry, stop=o.stop, pre=o.pre, full=o.full, net=pick('net'),
                          reason=pick('reason'), entry_i=o.entry_i, exit_i=pick('exit_i'),
                          exit_t=pd.to_datetime(pd.Series(pick('exit_time'), index=o.index), utc=True)), index=o.index)
    job = json.loads((SRC/symbol/'identity.json').read_text())['job']
    f, _ = load_job(job)
    # Market-mode ledgers carry no entry_time: the fill is the entry bar's close (bar open + execution minutes).
    close_t = pd.Series(f.index[d.entry_i.astype(int).to_numpy()]+pd.Timedelta(minutes=job['execution_minutes']), index=d.index)
    known = pd.to_datetime(o.entry_time, utc=True) if 'entry_time' in o else pd.Series(pd.NaT, index=d.index, dtype='datetime64[ns, UTC]')
    if (known.notna() & (known != close_t)).any():
        issues.append('limit entry_time differs from the fill bar close')
    d['entry_t'] = known.where(known.notna(), close_t).dt.as_unit('ns')
    d['exit_t'] = d.exit_t.dt.as_unit('ns')
    h, lo = f.high.to_numpy(), f.low.to_numpy()
    mae = []
    for a, b, s, e, v in zip(d.entry_i.astype(int), d.exit_i.astype(int), d.side, d.entry, d.variant):
        a += v == 'late'  # a close-time market entry is exposed from the next bar
        seg = slice(a, max(a+1, b))
        worst = lo[seg].min() if s == 1 else h[seg].max()
        mae.append(max(0., -s*(worst-e)/e*100))
    d['mae_pct'] = mae
    d['event'] = [':'.join(i.split(':')[:3]) for i in d.index]
    return symbol, d.reset_index(drop=True), issues, parity, compared


def counts(s):
    return {str(k): int(v) for k, v in s.value_counts().sort_index().items()}


def stats(x):
    n = len(x)
    if not n:
        return dict(n=0)
    q = x.net.quantile(.05)
    return dict(n=n, mean_bp=float(x.net.mean()), win_pct=float(100*(x.net > 1).mean()), sum_pct=float(x.net.sum()/100),
                worst5_mean_bp=float(x.net[x.net <= q].mean()), le_minus1000=int((x.net <= -1000).sum()))


def summarize(config, workers):
    from yoyo.evaluation import monday_limit_spike_opt as so
    from yoyo.evaluation.monday_limit_be_scalein_compare import BAD_EVENTS, B, RUN, clean
    from yoyo.evaluation.monday_limit_return_opt import frac
    exp = config.resolve().parent
    cfg = json.loads(config.read_text())
    committed('yoyo/evaluation/monday_limit_layered.py')
    ro = json.loads((A/'exp-monday-limit-return-opt-20261027-v2/config.json').read_text())
    syms = symbols(cfg)
    slip = cfg['slippage_bp']
    with ProcessPoolExecutor(workers) as ex:
        got = list(ex.map(load_symbol, [(str(exp), s, RUN.name, slip['non_target_exit'], slip['late_market_entry'], cfg.get('parity_run'))
                                        for s in syms], chunksize=4))
    t = pd.concat([g[1] for g in got if len(g[1])], ignore_index=True)
    t = t[~t.event.isin(BAD_EVENTS)].copy()
    sc = pd.read_csv(ROOT/cfg['scores']).set_index('event')
    # The reference plan is the leaky v3_be0 selection; another run can name the scores that selected it.
    rs = pd.read_csv(ROOT/cfg['reference_scores']).set_index('event') if cfg.get('reference_scores') else sc
    ref_events = set(rs.index[rs.stored >= cfg['cut']])-BAD_EVENTS
    with ProcessPoolExecutor(workers) as ex:
        leak = pd.concat([d for d in ex.map(so.legs, [(s, {RUN.name: ['v3_be0']}, ref_events) for s in syms], chunksize=4) if len(d)],
                         ignore_index=True)
    leak['pre'] = leak.event.map(sc.causal); leak['full'] = leak.event.map(sc.stored)
    res = dict(checks=dict(symbols=len(syms), issues=[f'{g[0]}: {i}' for g in got for i in g[2]],
                           parity_mismatch=int(sum(g[3] for g in got)), parity_against=cfg.get('parity_run') or f'{RUN.name} v3_be0',
                           parity_compared=int(sum(g[4] for g in got)), rows=counts(t.variant)))
    late_skip = {}
    for s in syms:
        f = exp/'ohlc'/'symbols'/s/'done.json'
        if f.exists():
            for k, v in json.loads(f.read_text()).get('late', {}).items():
                late_skip[k] = late_skip.get(k, 0)+v
    res['checks']['late_skip'] = late_skip
    rest, chk, late = (t[t.variant == v] for v in ('rest', 'rest_chk', 'late'))
    plans = {'leak_v3_be0': leak, 'A_rest': rest, 'AB_rest_chk': pd.concat([rest[rest.full >= cfg['cut']], chk]),
             'AC_rest_late': pd.concat([rest, late]), 'ABC': pd.concat([rest[rest.full >= cfg['cut']], chk, late])}
    window = pd.Timestamp(json.loads((A/'exp-monday-limit-reclaim-recent-20261025-v1/summary.json').read_text())['results']['window'][0])
    pers = {'all': lambda x: x.entry_t >= pd.Timestamp('2020-01-01', tz='UTC'), 'y2020_2024': lambda x: x.entry_t < B[0],
            'y2025': lambda x: (x.entry_t >= B[0]) & (x.entry_t < B[1]), 'y2026': lambda x: x.entry_t >= B[1],
            'last5m': lambda x: x.entry_t >= window}
    res['plans'] = {}
    for name, x in plans.items():
        # One unit for both clocks: a mixed ns/us concat orders exits before their entries.
        x = x.assign(entry_ns=x.entry_t.dt.as_unit('ns').astype('int64'), exit_ns=x.exit_t.dt.as_unit('ns').astype('int64'))
        res['plans'][name] = {}
        for per, within in pers.items():
            y = x[within(x)].reset_index(drop=True)
            reps = [frac(y, 20, 3, ro['seed']+r) for r in range(ro['ties'])] if len(y) else []
            res['plans'][name][per] = dict(stats(y), frac20_3x=dict(
                final_median=float(np.median([r['final_multiple'] for r in reps])) if reps else None,
                dd_median=float(np.median([r['max_drawdown_pct'] for r in reps])) if reps else None,
                liquidations_median=float(np.median([r['liquidations'] for r in reps])) if reps else None))
    # Cases on identical events.
    lk = leak.set_index('event')
    g1 = rest[rest.full >= cfg['cut']]
    g2 = rest[rest.full < cfg['cut']].set_index('event')
    c2 = chk.set_index('event').reindex(g2.index)
    g3 = sc[(sc.causal < cfg['cut']) & (sc.stored >= cfg['cut'])].index.difference(list(BAD_EVENTS))
    lt = late.set_index('event')
    res['cases'] = dict(
        G1_pre_and_full=dict(rest=stats(g1), leak_same_events=stats(lk.loc[lk.index.intersection(g1.event)])),
        G2_pre_only=dict(rest=stats(g2), rest_chk=stats(c2), chk_reason=counts(c2.reason),
                         chk_better_pct=float(100*(c2.net > g2.net).mean()) if len(g2) else None,
                         by_full={f'{a}-{b}': dict(rest=stats(g2[(g2.full >= a) & (g2.full < b)]), rest_chk=stats(c2[(c2.full >= a) & (c2.full < b)]))
                                  for a, b in ((0., .4), (.4, .45), (.45, .5))}),
        G3_full_only=dict(events=int(len(g3)), late=stats(lt), leak_limit_fill_same_events=stats(lk.loc[lk.index.intersection(lt.index)]),
                          leak_limit_fill_all_g3=stats(lk.loc[lk.index.intersection(g3)]),
                          late_close_vs_level_pct={f'q{int(100*q)}': float(v) for q, v in
                                                   (lt.side*(lt.entry/lk.reindex(lt.index).entry-1)*100).quantile([.1, .25, .5, .75, .9]).items()}
                          if len(lt) else None,
                          late_reason=counts(lt.reason)),
        same_bar_rest=dict(target=int(((rest.reason == 4) & (rest.exit_i == rest.entry_i)).sum()),
                           backstop=int(((rest.reason == 1) & (rest.exit_i == rest.entry_i)).sum())))
    if not cfg.get('one_minute_layer', True):
        res = clean(res)
        dump(exp/'summary.json', dict(config=cfg, results=res))
        print(json.dumps(res['checks']))
        return
    pb = pd.read_csv(A/'exp-monday-limit-partial-bar-score-20261027-v1/events.csv.gz').set_index('event')
    pb = pb.loc[pb.index.intersection(g3)]
    tm = pb.touch_minute.dropna().astype(int)
    at_close = pd.Series([pb.at[e, f'score_{m+1}'] for e, m in tm.items()], index=tm.index)
    res['cases']['G3_full_only']['one_minute_layer'] = dict(
        with_1m=int(len(tm)), touch_minute=counts(tm),
        score_ge_cut_at_touch_minute_close=int((at_close >= cfg['cut']).sum()),
        of_which_before_5m_close=int(((at_close >= cfg['cut']) & (tm < 4)).sum()))
    res = clean(res)
    dump(exp/'summary.json', dict(config=cfg, results=res))
    print(json.dumps(res['checks']))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, required=True); ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--intrabar', choices=['OHLC', 'OLHC']); ap.add_argument('--summary', action='store_true')
    a = ap.parse_args()
    summarize(a.config, a.workers) if a.summary else run(a.config, a.workers, a.intrabar)
