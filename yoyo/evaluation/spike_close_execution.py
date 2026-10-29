"""Frozen spike execution after a completed touch 5m candle (owner 2026-10-29).

Reuse BE17 native close fills. Run only new retest limits, one-bar-delayed market
entries and conditional matched controls through NautilusTrader. No model fit,
parameter search or handwritten fill engine. All signal inputs are frozen at
touch close; delayed admission may additionally read the next completed bar.
Controls share symbol, NY week, side and completed 14-bar relative-TR bucket;
the signal's target/level ratios are scaled to their own decision close. They
test timing conditional on geometry, not an independently scored strategy.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
from pathlib import Path
import subprocess
import time
import zlib

import numpy as np
import pandas as pd

from yoyo.evaluation.range_framework import ROOT, dump, file_stamp
from yoyo.evaluation.monday_limit_backstop import CODEX
from yoyo.evaluation.allin3_close_hybrid import CACHED, KEEP, configure_order
from yoyo.evaluation.monday_limit_be_scalein_compare import BAD_EVENTS, clean

EXP = ROOT/'experiments/active/exp-spike-close-execution-20261029-v1'
VARIANTS = ('close_now', 'hybrid_retest', 'delay_5m')


def eligible(cfg):
    scores = pd.read_csv(ROOT/cfg['scores']).set_index('event')
    order = pd.read_csv(ROOT/cfg['features'], usecols=['event', 'l2_order_score']).set_index('event')
    scores = scores.join(order)
    return scores[(scores.stored >= cfg['score_cut']) & (scores.l2_order_score >= cfg['min_order'])
                  & ~scores.index.isin(BAD_EVENTS)]


def decision(source, p, tick, cfg, variant):
    """Admit using OHLC only through decision_i (+ one closed bar for delay_5m)."""
    k = int(source['decision_i'])
    base = dict(source, entry_i=k)
    row, status = configure_order(base, float(p.close.iloc[k]), tick, cfg)
    if row is None:
        return None, status
    if variant == 'hybrid_retest':
        return row, status
    if variant == 'delay_5m':
        j = k+1
        if j >= source['end_i'] or j >= source['expire_i']:
            return None, 'delay_no_remaining_interval'
        if p.index[j]-p.index[k] != pd.Timedelta(minutes=5):
            return None, 'delay_source_gap'
        bar = p.iloc[j]
        favorable = bar.high if source['side'] == 1 else bar.low
        adverse = bar.low if source['side'] == 1 else bar.high
        if source['side']*(favorable-source['target_price']) >= 0:
            return None, 'target_reached_while_waiting'
        if source['side']*(adverse-row['stop']) <= 0:
            return None, 'backstop_reached_while_waiting'
        row, status = configure_order(dict(base, entry_i=j), float(bar.close), tick, cfg)
        if row is None:
            return None, 'delay_'+status
    row.update(order_mode='market', entry=float(p.close.iloc[int(row['entry_i'])]))
    row['risk'] = row['entry']
    return row, status


def make_orders(p, original, contexts, tick, selected, cfg):
    """Fixed three execution rules, plus three seeded controls per eligible close."""
    e = original[(original.population == 'signal') & (original.reclaim_minutes == 60)
                 & (original.exit_mode == 'near_monday_edge') & original.event_id.isin(selected.index)].copy()
    c = p.close.to_numpy()
    tr = pd.concat([p.high-p.low, (p.high-p.close.shift()).abs(),
                    (p.low-p.close.shift()).abs()], axis=1).max(axis=1)
    vol = tr.rolling(14, min_periods=14).mean()/p.close
    buckets = np.floor(np.log2(vol.where(vol > 0))*4).fillna(-999).to_numpy(int)
    from yoyo.evaluation.monday_limit_reclaim import segment_ends
    ends = segment_ends(p, dict(execution_minutes=5, symbol=str(original.symbol.iloc[0])))
    weeks = {w['week']: w for w in contexts}
    orders, decisions = [], []
    for source in e.to_dict('records'):
        event, k = source['event_id'], int(source['touch_i_hint'])
        source.update(event=event, decision_i=k, entry_i=k, control_id=0,
                      score=float(selected.loc[event, 'stored']),
                      order_score=float(selected.loc[event, 'l2_order_score']), bucket=int(buckets[k]))
        sources = [source]
        initial, _ = decision(source, p, tick, cfg, 'close_now')
        if initial is not None:
            w = weeks[source['week']]
            a, b = int(w['monitor_start_i']), int(w['monitor_end_i_exclusive'])
            pool = np.arange(a, max(a, b-1))
            pool = pool[(buckets[pool] == buckets[k]) & (buckets[pool] != -999)
                        & (ends[pool] > pool) & (pool != k)]
            rng = np.random.default_rng(cfg['control_seed']+zlib.crc32(event.encode()))
            for control, j in enumerate(rng.choice(pool, min(cfg['controls'], len(pool)), replace=False), 1):
                ratio = c[j]/c[k]
                raw = source['entry']*ratio
                level = (np.floor(raw/tick+1e-7) if source['side'] == 1 else np.ceil(raw/tick-1e-7))*tick
                sources.append(dict(source, decision_i=int(j), entry_i=int(j), entry=level,
                    target_price=float(np.round(source['target_price']*ratio/tick)*tick),
                    end_i=int(ends[j]), expire_i=min(b-1, int(ends[j])),
                    population='random', control_id=control, bucket=int(buckets[j])))
        for src in sources:
            for variant in VARIANTS:
                tid = event+f':{variant}:c{src["control_id"]}'
                row, status = decision(src, p, tick, cfg, variant)
                k0 = int(src['decision_i'])
                metadata = dict(trade_id=tid, event=event, symbol=source['symbol'], week=source['week'],
                    variant=variant, control_id=src['control_id'], side=src['side'], score=src['score'],
                    order_score=src['order_score'], decision_i=k0, decision_time=str(p.index[k0]+pd.Timedelta(minutes=5)),
                    close=float(c[k0]), level=src['entry'], target=src['target_price'], status=status,
                    close_position='rebounded' if src['side']*(c[k0]-src['entry']) >= tick*(1-1e-7) else 'favorable',
                    chase_fraction=float((c[k0]-src['entry'])/(src['target_price']-src['entry'])),
                    remaining_target_bp=float(src['side']*(src['target_price']/c[k0]-1)*1e4))
                decisions.append(metadata)
                if row is not None:
                    row.update(trade_id=tid, variant=variant, decision_time=metadata['decision_time'])
                    orders.append(row)
    return pd.DataFrame(orders), pd.DataFrame(decisions)


def process(symbol, config):
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native
    cfg = json.loads(Path(config).read_text()); out = Path(config).parent/'symbols'/symbol
    out.mkdir(parents=True, exist_ok=True)
    if (out/'done.json').exists():
        return json.loads((out/'done.json').read_text())
    started = time.perf_counter()
    origin = CODEX/'symbols'/symbol
    job = json.loads((origin/'identity.json').read_text())['job']
    if job['group'] != 'crypto_5m':
        raise ValueError('Only original Binance crypto_5m sources are eligible')
    f, _ = load_job(job)
    prior = json.loads((origin/'done.json').read_text())
    if (len(f), str(f.index[0]), str(f.index[-1])) != (prior['rows'], prior['first'], prior['last']):
        raise ValueError('Source index changed since original native orders')
    decimals = max(2, min(9, int(np.ceil(-np.log10(f[['open','high','low','close']].to_numpy().min())))+7))
    tick = 10.**(-decimals); p = f[['open','high','low','close']].round(decimals)
    p.index = p.index.as_unit('ns')
    original = pd.read_csv(origin/'orders.csv.gz')
    contexts = json.loads((origin/'weeks.json').read_text())
    x, decisions = make_orders(p, original, contexts, tick, eligible(cfg), cfg)
    decisions.to_csv(out/'decisions.csv.gz', index=False)
    x.to_csv(out/'orders.csv.gz', index=False)
    counts = {}
    if len(x):
        frame = p.copy(); frame.index += pd.Timedelta(minutes=5)
        reuse = (x.control_id == 0) & (x.order_mode == 'market') & (x.variant != 'delay_5m')
        runtime = x[~reuse].copy()
        for path in ('OHLC', 'OLHC'):
            dest = out/path.lower(); dest.mkdir(exist_ok=True)
            if (dest/'audit.json').exists():
                counts[path] = json.loads((dest/'audit.json').read_text()); continue
            cached = pd.read_csv(CACHED/path.lower()/'symbols'/symbol/'trades.csv.gz').set_index('trade_id')
            reused = []
            for r in x[reuse].to_dict('records'):
                fam = 'ci' if r['side']*(r['entry']-r['reclaim_ref']) >= tick*(1-1e-7) else 'cb'
                old = cached.loc[r['event']+f':m60:near_monday_edge:c0:v_{fam}_spike'].to_dict()
                for field in ('entry','entry_i','stop','target_price'):
                    np.testing.assert_allclose(old[field], r[field], atol=tick*1e-6, rtol=1e-12)
                old['trade_id'] = r['trade_id']; reused.append(old)
            rows, fills, audit = pd.DataFrame(columns=KEEP), pd.DataFrame(), dict(positions=0, fills=0, opportunities=[])
            if len(runtime):
                policy = ExitPolicy(trail_arm_r=1e12, opposite_hour_exit=False, round_trip_cost=0,
                    target_from_entry=True, intrabar_path=path, reclaim_next_bar=True,
                    breakeven_includes_cost=False, fill_maker_fee=cfg['maker_fee'],
                    fill_taker_fee=cfg['taker_fee'], monotone_segments=True, reclaim_backstop=True)
                rows, fills, audit = run_native(frame, np.full(len(frame), np.nan), np.zeros(len(frame)),
                    runtime, policy, tick=tick, symbol=symbol.removesuffix('USDT'),
                    entry_order_mode='limit', batch_bars=288, skip_idle_bars=True)
            combined = pd.concat([rows.reindex(columns=KEEP), pd.DataFrame(reused).reindex(columns=KEEP)], ignore_index=True)
            combined = combined.merge(x[['trade_id','event','control_id','variant','order_mode','decision_time']],
                                      on='trade_id', validate='one_to_one')
            combined['entry_time'] = [str(frame.index[int(i)]) for i in combined.entry_i]
            h, lo = p.high.to_numpy(), p.low.to_numpy()
            mae = []
            for r in combined.itertuples():
                a = int(r.entry_i)+(r.order_mode == 'market'); b = max(a+1, int(r.exit_i))
                worst = lo[a:b].min() if r.side == 1 else h[a:b].max()
                mae.append(max(0., -r.side*(worst-r.entry)/r.entry*100))
            combined['mae_pct'] = mae
            combined['net'] = (combined.net_notional_bp
                - np.where(combined.order_mode == 'market', cfg['slippage_bp']['market_entry'], 0.)
                - np.where(combined.reason == 4, 0., cfg['slippage_bp']['non_target_exit']))
            combined.to_csv(dest/'trades.csv.gz', index=False)
            fills.to_csv(dest/'new_native_fills.csv.gz', index=False)
            pd.DataFrame(audit.get('opportunities', [])).to_csv(dest/'opportunities.csv.gz', index=False)
            receipt = dict(engine='nautilus_trader', version='1.231.0', new_orders=len(runtime),
                new_positions=audit['positions'], new_fills=audit['fills'], reused_positions=len(reused),
                cancelled=audit.get('cancelled', 0), expired=audit.get('expired', 0), positions=len(combined))
            dump(dest/'audit.json', receipt); counts[path] = receipt
    done = dict(symbol=symbol, events=int((decisions.control_id.eq(0)&decisions.variant.eq('close_now')).sum()),
        orders=len(x), paths=counts, sources=[file_stamp(ROOT/Path(v)) for v in job['paths']],
        seconds=time.perf_counter()-started)
    dump(out/'done.json', done)
    return done


def run(config, workers):
    from yoyo.evaluation.monday_limit_paper_accounts import committed
    committed('yoyo/evaluation/spike_close_execution.py', 'yoyo/evaluation/allin3_close_hybrid.py',
              'yoyo/layers/l3_backtest/nautilus_independent.py', str(config.relative_to(ROOT)))
    cfg = json.loads(config.read_text())
    symbols = sorted(set(eligible(cfg).index.str.split(':').str[0]))
    with ProcessPoolExecutor(workers) as pool:
        tasks = {pool.submit(process, symbol, str(config)): symbol for symbol in symbols}
        for k, task in enumerate(as_completed(tasks), 1):
            try:
                result = task.result()
            except Exception as error:
                result = dict(symbol=tasks[task], error=repr(error))
                dest = config.parent/'symbols'/tasks[task]; dest.mkdir(parents=True, exist_ok=True)
                dump(dest/'error.json', result)
            print(json.dumps(dict(progress=k, total=len(symbols), **result)), flush=True)


def stats(g):
    f = g[g.filled]
    result = dict(opportunities=len(g), fills=len(f), mean_opportunity_bp=float(g.net.mean()),
                  mean_fill_bp=float(f.net.mean()) if len(f) else None,
                  win_pct=float(100*(f.net > 1).mean()) if len(f) else None,
                  sum_independent_pct=float(g.net.sum()/100), censored_fills=int((f.reason == 0).sum()))
    if len(f):
        result.update(median_fill_bp=float(f.net.median()), mean_winner_bp=float(f.loc[f.net > 1,'net'].mean()),
                      mean_loser_bp=float(f.loc[f.net < -1,'net'].mean()), worst_bp=float(f.net.min()),
                      best_bp=float(f.net.max()), without_best_mean_bp=float(f.net.nsmallest(len(f)-1).mean()) if len(f)>1 else None)
    return clean(result)


def week_sign_flip(week, count, rng):
    """Exact small-cluster null; include equality despite floating-point summation."""
    observed = float(week.sum()/count)
    if len(week) <= 16:
        bits = (np.arange(2**len(week))[:,None] >> np.arange(len(week))) & 1
        null = ((2*bits-1)@week)/count
        correction = 0
    else:
        null = (rng.choice([-1.,1.],size=(20000,len(week)))@week)/count
        correction = 1
    tolerance = 1e-12*max(1., abs(observed), float(np.abs(week).sum()/count))
    return float((correction+(null >= observed-tolerance).sum())/(len(null)+correction))


def paired(g, rng):
    sig = g[g.control_id == 0].set_index('event').net
    control = g[g.control_id > 0].groupby('event').net.mean()
    joint = pd.concat([sig.rename('signal'), control.rename('random')], axis=1).dropna()
    d = joint.signal-joint.random
    if not len(d):
        return dict(pairs=0)
    # One sign per NY week, preserving same-week cross-coin correlation.
    clustered = d.groupby(d.index.str.split(':').str[1]).agg(['sum','size'])
    week = clustered['sum'].to_numpy()
    p_value = week_sign_flip(week, len(d), rng)
    sampled = rng.integers(0, len(week), size=(3000,len(week)))
    boot = week[sampled].sum(axis=1)/clustered['size'].to_numpy()[sampled].sum(axis=1)
    return dict(pairs=len(d), weeks=len(week), signal_bp=float(joint.signal.mean()), random_bp=float(joint.random.mean()),
        excess_bp=float(d.mean()), week_sign_flip_p=p_value,
        week_bootstrap_excess_ci95_bp=[float(x) for x in np.quantile(boot,[.025,.975])])


def summarize(config):
    cfg = json.loads(config.read_text()); root = config.parent
    selected = eligible(cfg); syms = sorted(set(selected.index.str.split(':').str[0]))
    all_decisions, all_paths, receipts = [], {'ohlc': [], 'olhc': []}, []
    for symbol in syms:
        d = root/'symbols'/symbol
        if not (d/'done.json').exists() or (d/'error.json').exists():
            raise RuntimeError(f'Incomplete native output for {symbol}')
        receipts.append(json.loads((d/'done.json').read_text()))
        decisions = pd.read_csv(d/'decisions.csv.gz')
        all_decisions.append(decisions)
        for path in all_paths:
            p = d/path/'trades.csv.gz'
            t = pd.read_csv(p) if p.exists() else pd.DataFrame(columns=['trade_id','net'])
            cols = ['trade_id','net','net_notional_bp','entry','entry_time','exit_time','entry_i','exit_i',
                    'reason','mae_pct','order_mode','stop','target_price','exit_price']
            joined = decisions.merge(t.reindex(columns=cols), on='trade_id', how='left', validate='one_to_one')
            joined['filled'] = joined.net.notna()
            joined['net'] = joined.net.fillna(0.)
            all_paths[path].append(joined)
    decisions = pd.concat(all_decisions, ignore_index=True)
    paths = {k: pd.concat(v, ignore_index=True).set_index('trade_id') for k,v in all_paths.items()}
    cohort_time = decisions[(decisions.control_id == 0)&(decisions.variant == 'close_now')].set_index('event').decision_time
    # A symbol with no admitted orders contributes empty object-typed columns.
    for t in paths.values():
        t['cohort_time'] = t.event.map(cohort_time)
        for col in ('net','net_notional_bp','entry','entry_i','exit_i','reason','mae_pct',
                    'stop','target_price','exit_price'):
            t[col] = pd.to_numeric(t[col], errors='raise')
    a, b = paths['ohlc'], paths['olhc']
    b = b.reindex(a.index)
    if b.variant.isna().any():
        raise RuntimeError('Intrabar opportunity sets differ')
    pick = (b.net < a.net) | ((b.net == a.net)&b.filled&~a.filled)
    worst = a.copy(); worst.loc[pick] = b.loc[pick]
    worst['chosen_path'] = np.where(pick, 'olhc', 'ohlc')
    for key,t in [*paths.items(), ('worse_opportunity', worst)]:
        t.to_csv(root/f'{key}.csv.gz', index=True)
    decisions.to_csv(root/'decisions.csv.gz', index=False)
    signal = worst[worst.control_id == 0]
    groups = {'all': lambda x: np.ones(len(x), bool),
              '2020_2024': lambda x: x.cohort_time < '2025-01-01',
              '2025': lambda x: (x.cohort_time >= '2025-01-01') & (x.cohort_time < '2026-01-01'),
              '2026': lambda x: x.cohort_time >= '2026-01-01'}
    result = dict(symbols=len(syms), eligible_signals=len(selected), variants={}, decomposition={}, controls={}, branch_controls={},
                  fixed_buckets={}, concentration={}, cases={}, checks={})
    rng = np.random.default_rng(cfg['control_seed'])
    for variant in VARIANTS:
        s = signal[signal.variant == variant]
        result['variants'][variant] = {per: stats(s[mask(s)]) for per,mask in groups.items()}
        result['controls'][variant] = {per: paired(worst[(worst.variant == variant)&mask(worst)], rng)
                                       for per,mask in groups.items()}
        result['decomposition'][variant] = {key: {per:stats(g[mask(g)]) for per,mask in groups.items()}
                                            for key,g in s.groupby('close_position')}
        result['branch_controls'][variant] = {}
        for key,g in s.groupby('close_position'):
            matched = worst[(worst.variant == variant)&worst.event.isin(g.event)]
            result['branch_controls'][variant][key] = {per:paired(matched[mask(matched)],rng)
                                                      for per,mask in groups.items()}
        f = s[s.filled].copy()
        f['room_bucket'] = pd.cut(f.remaining_target_bp, [0,50,100,200,500,np.inf], right=False).astype(str)
        f['chase_bucket'] = pd.cut(f.chase_fraction, [-np.inf,0,.25,.5,.75,1], right=False).astype(str)
        result['fixed_buckets'][variant] = {col: {str(k):stats(g) for k,g in f.groupby(col)}
                                            for col in ('room_bucket','chase_bucket')}
        result['concentration'][variant] = {}
        for per,mask in groups.items():
            z = f[mask(f)]
            top_week = z.groupby('week').net.sum().sort_values(ascending=False)
            top_coin = z.groupby('symbol').net.sum().sort_values(ascending=False)
            result['concentration'][variant][per] = dict(
                best_week=str(top_week.index[0]) if len(top_week) else None,
                mean_without_best_week_bp=float(z.loc[z.week != top_week.index[0],'net'].mean()) if len(top_week)>1 else None,
                best_coin=str(top_coin.index[0]) if len(top_coin) else None,
                mean_without_best_coin_bp=float(z.loc[z.symbol != top_coin.index[0],'net'].mean()) if len(top_coin)>1 else None,
                mean_without_top3_trades_bp=float(z.net.nsmallest(max(0,len(z)-3)).mean()))
    # Preserve the old account projection as a labelled reference, not a live forecast.
    prior = json.loads((CACHED/'summary.json').read_text())['results']['plans']
    result['legacy_account_reference'] = {k:v for k,v in prior.items() if k.startswith('spike|')}
    baseline = signal[(signal.variant == 'close_now')&signal.filled]
    ref = prior['spike|close_all']['all']
    np.testing.assert_allclose([len(baseline), baseline.net.mean()], [ref['n'], ref['mean_bp']], rtol=1e-10)
    result['checks'] = dict(baseline_exact_saved_summary=True,
        adverse_path_opportunities=int(pick.sum()), native_receipts=len(receipts),
        status={v:{str(k):int(n) for k,n in signal[signal.variant == v].status.value_counts().items()} for v in VARIANTS},
        path_sensitivity={v:{p:stats(t[(t.variant == v)&(t.control_id == 0)]) for p,t in paths.items()} for v in VARIANTS})
    for event in ['HEMIUSDT:2026-08-31:+1']:
        result['cases'][event] = signal[signal.event == event].reset_index().to_dict('records')
    result['new_orders_per_path'] = sum(r['paths'].get('OHLC',{}).get('new_orders',0) for r in receipts)
    result['cached_rows_per_path'] = sum(r['paths'].get('OHLC',{}).get('reused_positions',0) for r in receipts)
    dump(root/'summary.json', clean(dict(config=cfg, results=result,
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        training_eligible=False, production_eligible=False)))
    print(json.dumps(clean({k:result[k] for k in ('eligible_signals','variants','controls','new_orders_per_path','cached_rows_per_path')})), flush=True)


def validate(config):
    """Reconcile recorded native fills and all opportunities; no execution replay."""
    cfg = json.loads(config.read_text()); root = config.parent
    counts = dict(symbols=0, native_positions=0, native_fills=0, reused_rows=0,
                  future_limit_fills=0, scored_market_fills=0, fee_checks=0)
    max_net_error = 0.
    for symbol in sorted(set(eligible(cfg).index.str.split(':').str[0])):
        d = root/'symbols'/symbol
        done = json.loads((d/'done.json').read_text()); counts['symbols'] += 1
        if not done['orders']:
            continue
        orders = pd.read_csv(d/'orders.csv.gz').set_index('trade_id')
        assert orders.index.is_unique
        assert (orders.score >= cfg['score_cut']).all() and (orders.order_score >= cfg['min_order']).all()
        for path in ('ohlc','olhc'):
            receipt = json.loads((d/path/'audit.json').read_text())
            ledger = pd.read_csv(d/path/'trades.csv.gz').set_index('trade_id')
            assert len(ledger) == receipt['positions']
            counts['reused_rows'] += receipt['reused_positions']
            if not receipt['new_fills']:
                continue
            fills = pd.read_csv(d/path/'new_native_fills.csv.gz')
            assert len(fills) == receipt['new_fills']
            counts['native_fills'] += len(fills)
            for tid, g in fills.groupby('trade_id'):
                r = ledger.loc[tid]; order = orders.loc[tid]
                entry = g[g.phase == 'entry']; exit_ = g[g.phase != 'entry']
                assert len(entry) == 1 and len(exit_) == 1
                en, ex = entry.iloc[0], exit_.iloc[0]
                assert en.quantity == ex.quantity
                assert en.bar_i == r.entry_i and ex.bar_i == r.exit_i
                assert r.entry_i >= order.decision_i
                if order.order_mode == 'limit':
                    assert order.entry_i < en.bar_i <= order.expire_i
                    counts['future_limit_fills'] += 1
                else:
                    assert order.entry_i == en.bar_i
                    counts['scored_market_fills'] += 1
                for fill in (en,ex):
                    fee = cfg['maker_fee'] if fill.phase == 'target' or (fill.phase == 'entry' and order.order_mode == 'limit') else cfg['taker_fee']
                    np.testing.assert_allclose(fill.commission, fill.price*fill.quantity*fee, atol=1e-8, rtol=1e-7)
                    counts['fee_checks'] += 1
                gross = r.side*(ex.price/en.price-1)*1e4
                expected = gross-g.commission.sum()/(en.price*en.quantity)*1e4
                err = abs(expected-r.net_notional_bp); max_net_error = max(max_net_error,err)
                np.testing.assert_allclose(expected,r.net_notional_bp,atol=1e-6,rtol=1e-10)
                counts['native_positions'] += 1
    a,b,w = [pd.read_csv(root/f'{name}.csv.gz').set_index('trade_id') for name in ('ohlc','olhc','worse_opportunity')]
    np.testing.assert_allclose(w.net,np.minimum(a.net,b.net),atol=1e-10,rtol=1e-10)
    assert (w.loc[~w.filled,'net'] == 0).all()
    assert (w.loc[w.status != 'admitted','filled'] == False).all()
    result = dict(checks=counts, max_native_net_reconciliation_error_bp=max_net_error,
        opportunity_rows=len(w), original_signals=int(((w.control_id == 0)&(w.variant == 'close_now')).sum()),
        tests='11 passed: admission/future mutation/mirrored bounds, native cancellation/path/idle parity, small-cluster exact null',
        native_builder_commit='efadc6a7d6', summary_source_commit=subprocess.check_output(
            ['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        no_new_native_replay=True, no_source_download=True, content_hashing=False)
    dump(root/'validation.json',result); print(json.dumps(result),flush=True)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('action', choices=['run','summary','validate'])
    ap.add_argument('--config', type=Path, default=EXP/'config.json'); ap.add_argument('--workers', type=int, default=4)
    args = ap.parse_args(); config = args.config.resolve()
    if args.action == 'run':
        run(config,args.workers)
    elif args.action == 'summary':
        summarize(config)
    else:
        validate(config)
