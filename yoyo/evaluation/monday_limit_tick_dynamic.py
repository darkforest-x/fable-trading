"""Follow the score trade by trade through the touch bar; enter at market when it first clears the threshold (owner 2026-10-31).

Owner: "测一下" the untested seconds-level variant. exp-monday-limit-tick-entry-20261029-v1 scored once, at the first
trade through the level (t_star), where toward24 is at its maximum and the rebound has not started. Here the frozen
score is updated after every trade of the touch bar from t_star to the bar close, with the same partial-bar
definitions as that experiment: base volume up to the trade times its price for vol_ratio and liquidity, its price for
toward24, the other seven features unchanged. The score at the last trade equals the stored (leaky) touch-bar score,
which is the parity check. For each account threshold (v2 top-40 cut, 0.5 for the v3 family, 0.6 for spike) the
decision is the first trade whose score clears it; the market fill is the first taker trade in the order's direction at
or after decision + L, for L = 0 (the crossing trade's own price, an ideal) / 100 / 300 / 1000 ms. A touch whose
target (its mode) or its 18 / 30 percent backstop traded between t_star and the fill is no trade. v3 takes its target
band and linear size from the score at its decision. Add legs exist only when that score is already >= 0.6; they
keep their framework limit fills.

Single variable against the instant experiment: when inside the touch bar the decision is taken. The aggTrades cache,
events, exits and account sizing are reused. Step 1 (`run`) writes dyn_events.csv.gz and the scores for the framework
run: causal = score at the 0.5 decision (else at the v2 decision), stored = score at the 0.6 decision. Step 2 is
monday_limit_paper_accounts on that config (resting family; native NautilusTrader, both intrabar paths). Step 3
(`--summary`) replaces each selected framework fill's entry with its decision's market price on the framework's own exit
price and reason, as in the instant experiment. Breakeven and reclaim stay referenced to the level, so a chased entry can
exit below its own price. It compares that against resting on the pre-touch score and the leaky reference, last five
months. Read-only arithmetic on framework fills, no second engine.
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
from yoyo.evaluation.monday_limit_reclaim_patterns import SRC
from yoyo.evaluation.monday_limit_be_scalein_compare import BAD_EVENTS, clean
from yoyo.evaluation.monday_limit_tick_entry import candidates, committed, load_paths

LAT = (0, 100, 300, 1000)
MODES = ('range_0.618', 'opposite_1618', 'near_monday_edge')
BACKSTOPS = {'v2': None, '05': 30., '06': 18.}  # v3 family -30 percent, spike -18 percent; v2 has no near stop


def contrib(spec, vr, liq, tw):
    """The three bar features' share of the logit (frozen standardization and coefficients)."""
    f = spec['features']
    part = lambda name, v: spec['coef'][f.index(name)]*(v-spec['mean'][f.index(name)])/spec['scale'][f.index(name)]
    return part('log_vol_ratio', np.log(np.clip(vr, .05, None)))+part('log_liquidity', np.log10(np.clip(liq, 1e4, None)))+\
        part('toward24', tw)


def dynamic(args):
    """Score path through the touch bar, first crossing of each cut and its market fills, for one symbol."""
    symbol, events, cache, w0, cuts, spec = args
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    job = json.loads((SRC/symbol/'identity.json').read_text())['job']
    t = pd.read_csv(SRC/symbol/'trades.csv.gz', usecols=['population', 'reclaim_minutes', 'exit_mode', 'week', 'side', 'entry',
                                                          'touch_i_hint', 'touch_interval_open', 'target_price'])
    t = t[(t.population == 'signal') & (t.reclaim_minutes == 60)]
    t['event'] = symbol+':'+t.week+':'+np.where(t.side == 1, '+1', '-1')
    t = t[t.event.isin(list(events))]
    if t.empty:
        return pd.DataFrame()
    targets = t.pivot_table(index='event', columns='exit_mode', values='target_price', aggfunc='first')
    t = t[t.exit_mode == 'near_monday_edge'].copy()
    t['open'] = pd.to_datetime(t.touch_interval_open, utc=True)
    t = t[t.open+pd.Timedelta(minutes=5) >= w0]
    f, _ = load_job(job)
    v, c = f.volume.to_numpy(), f.close.to_numpy()
    qv = v*c
    hour = pd.Series(qv).rolling(12, min_periods=12).sum().to_numpy()
    base = pd.Series(hour).rolling(2016, min_periods=1000).median().shift(12).to_numpy()
    day = pd.Series(qv).rolling(2016, min_periods=1000).sum().to_numpy()/7
    rows = []
    for r in t.itertuples():
        k, s, L = int(r.touch_i_hint), int(r.side), float(r.entry)
        o = int(r.open.value//1_000_000)
        row = dict(event=r.event)
        p = Path(cache)/symbol/f'{r.open.date()}.csv.gz'
        if not p.exists() or k < 288 or not base[k] > 0:
            rows.append(dict(row, status='no_data')); continue
        tr = pd.read_csv(p).sort_values('ts', kind='mergesort').reset_index(drop=True)
        inbar = ((tr.ts >= o) & (tr.ts < o+300_000)).to_numpy()
        hit = np.flatnonzero(inbar & ((tr.price <= L) if s > 0 else (tr.price >= L)).to_numpy())
        if not len(hit):
            rows.append(dict(row, status='no_touch_in_trades')); continue
        i0, last = int(hit[0]), int(np.flatnonzero(inbar)[-1])
        px = tr.price.to_numpy(); ts = tr.ts.to_numpy()
        cq = np.cumsum(np.where(inbar, tr.qty.to_numpy(), 0.))
        seg = np.arange(i0, last+1)
        qd = cq[seg]*px[seg]
        z = events[r.event]+contrib(spec, (hour[k]-qv[k]+qd)/base[k], day[k]-(qv[k]-qd)/7, -s*100*(px[seg]/c[k-288]-1))
        score = 1/(1+np.exp(-z))
        row.update(status='ok', t_star_ms=int(ts[i0]), first_score=float(score[0]), final_score=float(score[-1]),
                   max_score=float(score.max()), n_trades=int(len(seg)))
        taker = (tr.buyer_maker.astype(str).str.lower().isin(['false', '0']) if s > 0 else
                 tr.buyer_maker.astype(str).str.lower().isin(['true', '1'])).to_numpy()
        for name, cut in cuts.items():
            above = np.flatnonzero(score >= cut)
            if not len(above):
                continue
            j = int(seg[above[0]])
            row.update({f's_{name}': float(score[above[0]]), f't_{name}_ms': int(ts[j]), f'off_{name}_ms': int(ts[j]-ts[i0]),
                        f'px_{name}': float(px[j])})
            for lat in LAT:
                if lat == 0:
                    fi = j
                else:
                    cand = np.flatnonzero(taker & (ts >= ts[j]+lat) & (ts < o+310_000) & (np.arange(len(tr)) > j))
                    if not len(cand):
                        continue
                    fi = int(cand[0])
                row[f'p_{name}_{lat}'] = float(px[fi])
                row[f'shift_{name}_{lat}'] = float(s*(px[fi]/L-1)*1e4)
                seen = px[i0+1:fi+1]
                for mode in MODES:
                    tp = targets.at[r.event, mode] if mode in targets.columns else np.nan
                    row[f'tp_{mode}_{name}_{lat}'] = bool(np.isfinite(tp) and len(seen) and
                                                         ((seen >= tp).any() if s > 0 else (seen <= tp).any()))
                bs = BACKSTOPS[name]
                row[f'stop_{name}_{lat}'] = bool(bs is not None and len(seen) and
                                                ((seen <= L*(1-bs/100)).any() if s > 0 else (seen >= L*(1+bs/100)).any()))
        rows.append(row)
    return pd.DataFrame(rows)


def run(config):
    exp = config.resolve().parent
    cfg = json.loads(config.read_text())
    committed('yoyo/evaluation/monday_limit_tick_dynamic.py', str(config.resolve().relative_to(ROOT)))
    tick = json.loads((ROOT/cfg['tick_config']).read_text())
    spec = json.loads((ROOT/tick['score_model']).read_text())
    ticks = pd.read_csv(ROOT/cfg['tick_events']).set_index('event')
    ok = ticks[ticks.status == 'ok']
    _, w0 = candidates(tick)
    # Logit share of the seven fixed features: the instant logit minus the three bar features at t_star, recomputed below
    # from the same trades. Per symbol: event -> fixed part.
    fixed = fixed_parts(ok, spec, tick, w0)
    cuts = {'v2': cfg['v2_threshold'], '05': cfg['cut'], '06': cfg['hi']}
    syms = sorted({e.split(':')[0] for e in fixed})
    jobs = [(s, {e: z for e, z in fixed.items() if e.startswith(s+':')}, str(ROOT/tick['cache']), w0, cuts, spec) for s in syms]
    with ProcessPoolExecutor(cfg['workers']) as ex:
        d = pd.concat([x for x in ex.map(dynamic, jobs, chunksize=2) if len(x)], ignore_index=True).set_index('event')
    d = d.join(ticks[['instant', 'stored', 'causal']])
    d.reset_index().to_csv(exp/'dyn_events.csv.gz', index=False)
    pre = d.s_05.where(d.s_05.notna(), d.s_v2)
    pd.DataFrame(dict(event=d.index, causal=pre, stored=d.s_06)).to_csv(exp/'scores_dynamic.csv.gz', index=False)
    g = d[d.status == 'ok']
    diff = (g.final_score-g.stored).abs()
    res = clean(dict(events=int(len(d)), status={str(k): int(v) for k, v in d.status.value_counts().items()},
                     parity_final_vs_stored=dict(q50=float(diff.quantile(.5)), q99=float(diff.quantile(.99)), max=float(diff.max())),
                     crossed={n: int(g[f's_{n}'].notna().sum()) for n in cuts},
                     instant_ge={n: int((g.instant >= c).sum()) for n, c in cuts.items()},
                     stored_ge={n: int((g.stored >= c).sum()) for n, c in cuts.items()},
                     causal_ge={n: int((g.causal >= c).sum()) for n, c in cuts.items()},
                     parity_first_vs_instant_max=float((g.first_score-g.instant).abs().max()),
                     decision_offset_s_q={n: {f'q{int(100*q)}': float(g[f'off_{n}_ms'].quantile(q)/1000) for q in (.1, .5, .9)}
                                          for n in cuts},
                     entry_vs_level_bp_q={f'{n}_{lat}': {f'q{int(100*q)}': float(g[f'shift_{n}_{lat}'].quantile(q)) for q in (.1, .5, .9)}
                                          for n in cuts for lat in LAT}))
    dump(exp/'summary_step1.json', dict(config=cfg, results=res, source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()))
    print(json.dumps(res, ensure_ascii=False), flush=True)


def fixed_parts(ok, spec, tick, w0):
    """event -> logit of the seven touch-bar-free features: logit(instant) minus the bar features at t_star."""
    with ProcessPoolExecutor(6) as ex:
        parts = [p for p in ex.map(_fixed_symbol, [(s, ok[ok.index.str.startswith(s+':')], spec, str(ROOT/tick['cache']))
                                                    for s in sorted({e.split(':')[0] for e in ok.index})]) if p]
    return {e: z for p in parts for e, z in p.items()}


def _fixed_symbol(args):
    symbol, ok, spec, cache = args
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    job = json.loads((SRC/symbol/'identity.json').read_text())['job']
    t = pd.read_csv(SRC/symbol/'trades.csv.gz', usecols=['population', 'reclaim_minutes', 'exit_mode', 'week', 'side', 'entry',
                                                          'touch_i_hint', 'touch_interval_open'])
    t = t[(t.population == 'signal') & (t.reclaim_minutes == 60) & (t.exit_mode == 'near_monday_edge')]
    t['event'] = symbol+':'+t.week+':'+np.where(t.side == 1, '+1', '-1')
    t = t[t.event.isin(ok.index)]
    if t.empty:
        return {}
    f, _ = load_job(job)
    v, c = f.volume.to_numpy(), f.close.to_numpy()
    qv = v*c
    hour = pd.Series(qv).rolling(12, min_periods=12).sum().to_numpy()
    base = pd.Series(hour).rolling(2016, min_periods=1000).median().shift(12).to_numpy()
    day = pd.Series(qv).rolling(2016, min_periods=1000).sum().to_numpy()/7
    out = {}
    for r in t.itertuples():
        k, s = int(r.touch_i_hint), int(r.side)
        o = int(pd.Timestamp(r.touch_interval_open).value//1_000_000)
        p = Path(cache)/symbol/f'{pd.Timestamp(r.touch_interval_open).date()}.csv.gz'
        if not p.exists() or k < 288 or not base[k] > 0:
            continue
        tr = pd.read_csv(p).sort_values('ts', kind='mergesort').reset_index(drop=True)
        bar = tr[(tr.ts >= o) & (tr.ts < o+300_000)]
        x = ok.loc[r.event]
        upto = bar[bar.ts <= x.t_star_ms]
        if upto.empty or not np.isclose(upto.price.iat[-1], x.t_star_price, rtol=1e-9):
            continue
        # The instant experiment counts quantity through the first touching trade; ties at t_star end at that trade.
        i0 = bar.index[(bar.ts == x.t_star_ms) & np.isclose(bar.price, x.t_star_price, rtol=1e-9)][0]
        qd = float(bar.loc[bar.index <= i0].qty.sum())*x.t_star_price
        zi = np.log(x.instant/(1-x.instant))
        out[r.event] = float(zi-contrib(spec, (hour[k]-qv[k]+qd)/base[k], day[k]-(qv[k]-qd)/7, -s*100*(x.t_star_price/c[k-288]-1)))
    return out


def plans_dyn(g, cfg):
    """Account selections with each account's own decision: v2 top-40 cut, v3 family 0.5, spike 0.6."""
    from yoyo.evaluation.monday_limit_paper_accounts import V3MODE
    sz, hi = cfg['v3_sizing'], cfg['hi']
    lin = lambda y: np.minimum(sz['fraction']*(y.s_05-sz['cut'])/sz['mean_excess'], sz['cap'])
    v3sel = lambda kind: g[(g.kind == kind) & g.s_05.notna() & (g['mode'] == V3MODE(g.s_05, hi))]
    v2 = g[(g.kind == 'v2') & g.s_v2.notna() & (g.minutes == 'm60')].assign(k=cfg['v2_fraction'])
    v3 = v3sel('v3').assign(k=lambda y: lin(y))
    legs = g[(g.kind == 'v3_add') & g.base.isin(v3.trade_id) & (g.s_05 >= hi)]
    legs = legs.assign(k=legs.base.map(v3.set_index('trade_id').k))
    return {'v2_60': ('fixed', v2), 'v3': ('linear', v3), 'v3add': ('linear', pd.concat([v3, legs])),
            'v3add20': ('frac', pd.concat([v3, legs])), 'v3lock': ('linear', v3sel('v3lock').assign(k=lambda y: lin(y))),
            'spike': ('slots', g[(g.kind == 'spike') & g.s_06.notna() & (g.order >= cfg['min_order'])])}


def summarize(config, workers):
    from yoyo.evaluation.monday_limit_paper_accounts import load_symbol, replay, scores as acct_scores
    from yoyo.evaluation.monday_limit_tick_entry import plans_for
    exp = config.resolve().parent
    cfg = json.loads(config.read_text())
    committed('yoyo/evaluation/monday_limit_tick_dynamic.py')
    tick = json.loads((ROOT/cfg['tick_config']).read_text())
    acfg = json.loads((ROOT/tick['accounts_config']).read_text())
    dcfg = json.loads((ROOT/cfg['accounts_config']).read_text())
    ro = json.loads((ROOT/'experiments/active/exp-monday-limit-return-opt-20261027-v2/config.json').read_text())
    sh = json.loads((ROOT/'experiments/active/exp-monday-limit-spike-hw-20261026-v1/config.json').read_text())
    py = json.loads((ROOT/'experiments/active/exp-monday-limit-pyramid-20261027-v1/compare.json').read_text())
    d = pd.read_csv(exp/'dyn_events.csv.gz').set_index('event')
    _, w0 = candidates(tick)
    order = pd.read_csv(ROOT/acfg['features'], usecols=['event', 'l2_order_score']).set_index('event').l2_order_score
    drun, arun = ROOT/cfg['accounts_run'], ROOT/tick['accounts_run']
    syms = sorted({e.split(':')[0] for e in d.index})
    with ProcessPoolExecutor(workers) as ex:
        dk = list(ex.map(load_symbol, [(str(drun), s, 10., 10.) for s in syms], chunksize=4))
        ac = list(ex.map(load_symbol, [(str(arun), s, 10., 10.) for s in syms], chunksize=4))
        raw = pd.concat([x for x in ex.map(load_paths, [(str(drun), s) for s in syms], chunksize=4) if len(x)], ignore_index=True)
    issues = [f'{g[0]}: {i}' for g in dk+ac for i in g[2]]
    dk = pd.concat([g[1] for g in dk if len(g[1])], ignore_index=True)
    ac = pd.concat([g[1] for g in ac if len(g[1])], ignore_index=True)
    for x in (dk, ac):
        x['family'] = x.variant.str.split('_').str[0]; x['kind'] = x.variant.str.split('_', n=1).str[1]
        x['order'] = x.event.map(order)
    dk = dk[~dk.event.isin(BAD_EVENTS) & (dk.entry_t >= w0)].copy()
    ac = ac[~ac.event.isin(BAD_EVENTS) & (ac.entry_t >= w0)].copy()
    for n in ('v2', '05', '06'):
        dk[f's_{n}'] = dk.event.map(d[f's_{n}'])
    pre, full = acct_scores(acfg)
    ac['pre'] = ac.event.map(pre); ac['full'] = ac.event.map(full)
    raw['event'] = raw.trade_id.str.split(':').str[:3].str.join(':')
    raw['mode'] = raw.trade_id.str.split(':').str[4]
    raw['kind'] = raw.variant.str.split('_', n=1).str[1]
    raw['add_leg'] = raw.variant.str.endswith('_add')
    fee_exit = np.where(raw.reason == 4, tick['maker_fee_bp'], tick['taker_fee_bp'])*raw.exit_price/raw.entry
    rep0 = raw.side*(raw.exit_price/raw.entry-1)*1e4-tick['maker_fee_bp']-fee_exit
    parity = dict(max_abs_bp=float((rep0-raw.net_notional_bp).abs().max()))
    which = {'v2': 'v2', 'v3': '05', 'v3lock': '05', 'spike': '06'}
    res = dict(checks=dict(issues=issues[:20], rows=int(len(dk)), account_rows=int(len(ac)), fee_formula_parity=parity),
               latency={}, plans={})
    plans = {}
    for lat in LAT:
        r = raw[~raw.add_leg & raw.kind.isin(which)].copy()
        cut = r.kind.map(which)
        get = lambda col: pd.Series([d.at[e, col.format(c)] if e in d.index and col.format(c) in d else np.nan
                                     for e, c in zip(r.event, cut)], index=r.index)
        p = get('p_{}_'+str(lat))
        flag = lambda e, col: bool(d.at[e, col]) if e in d.index and col in d and pd.notna(d.at[e, col]) else False
        tp = pd.Series([flag(e, f'tp_{m}_{c}_{lat}') for e, m, c in zip(r.event, r['mode'], cut)], index=r.index)
        stop = pd.Series([flag(e, f'stop_{c}_{lat}') for e, c in zip(r.event, cut)], index=r.index)
        fee_exit = np.where(r.reason == 4, tick['maker_fee_bp'], tick['taker_fee_bp'])*r.exit_price/p
        r['net_l'] = r.side*(r.exit_price/p-1)*1e4-tick['taker_fee_bp']-fee_exit-np.where(r.reason == 4, 0., tick['slippage_bp'])
        r['shift_bp'] = r.side*(p/r.entry-1)*1e4
        r['valid'] = p.notna() & ~tp & ~stop
        worse = r[r.valid].sort_values('net_l').drop_duplicates('trade_id')[['trade_id', 'net_l', 'shift_bp']]
        res['latency'][str(lat)] = dict(entries=int(r.valid.sum()//2), invalid=int((~r.valid).sum()//2),
                                        shift_bp_q={k: {f'q{int(100*q)}': float(v.shift_bp.quantile(q)) for q in (.1, .5, .9)}
                                                    for k, v in r[r.valid].groupby('kind')})
        tl = dk.merge(worse, on='trade_id', how='left')
        tl = tl[tl.variant.str.endswith('_add') | tl.net_l.notna()].copy()
        legs = tl.variant.str.endswith('_add')
        tl.loc[~legs, 'mae_pct'] = np.maximum(0., tl.loc[~legs, 'mae_pct']+tl.loc[~legs, 'shift_bp']/100)
        tl.loc[~legs, 'net'] = tl.loc[~legs, 'net_l']
        tl = tl[~legs | tl.base.isin(tl.trade_id)]
        for acct, (sizing, x) in plans_dyn(tl, dcfg).items():
            plans.setdefault(acct, {})[f'dynamic_market_{lat}ms'] = (sizing, x)
    for acct, (sizing, x) in plans_for(ac[ac.family == 'rest'], 'pre', acfg).items():
        plans[acct]['resting_causal'] = (sizing, x)
    for acct, (sizing, x) in plans_for(ac[ac.family == 'rest'], 'full', acfg).items():
        plans[acct]['leaky_reference'] = (sizing, x)
    jobs = [(f'{acct}|{k}', s, x.reset_index(drop=True), w0, acfg, ro, sh, py) for acct, v in plans.items() for k, (s, x) in v.items()]
    with ProcessPoolExecutor(workers) as ex:
        for name, out in ex.map(replay, jobs):
            acct, k = name.split('|')
            res['plans'].setdefault(acct, {})[k] = out['last5m']
    prior = json.loads((ROOT/cfg['tick_summary']).read_text())['results']['plans']
    for acct in res['plans']:
        res['plans'][acct]['instant_market_100ms_prior'] = prior.get(acct, {}).get('instant_market_100ms')
    res = clean(res)
    dump(exp/'summary.json', dict(config=cfg, results=res, source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                                 training_eligible=False, production_eligible=False))
    print(json.dumps(res['checks'], ensure_ascii=False))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, required=True); ap.add_argument('--workers', type=int, default=6)
    ap.add_argument('--summary', action='store_true')
    a = ap.parse_args()
    summarize(a.config, a.workers) if a.summary else run(a.config)
