"""Owner-fixed allin3: score at the touch close, favorable market or resting limit.

Only the completed touch-bar score/close, original Monday geometry and source
time boundaries admit orders. Long close <= lower 1.618 buys at that close;
shorts mirror. Otherwise rest at 1.618 until opposite target or original weekly
expiry. Native Nautilus owns fills, cancellations, backstop and H1 confirmation.
The existing BE17 close-beyond native fills are reused exactly, never replayed.
Matched random decision closes share symbol, NY week, side and completed-bar
14-bar relative-TR bucket; original price ratios scale to each random close.
Random controls are conditional geometry controls, not a second score model.
No training, live orders, dependency changes or outcome-selected parameters.
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
from yoyo.evaluation.monday_limit_backstop import CODEX, stops
from yoyo.evaluation.monday_limit_be_scalein_compare import BAD_EVENTS

EXP = ROOT / 'experiments/active/exp-allin3-close-hybrid-20261029-v1'
CACHED = ROOT / 'experiments/active/exp-monday-limit-paper-accounts-20261028-v1'
KEEP = ['trade_id', 'side', 'entry', 'stop', 'target_price', 'exit_price', 'reason',
        'entry_time', 'exit_time', 'entry_i', 'exit_i', 'reclaim_status', 'net_notional_bp']


def configure_order(row, close, tick, cfg):
    """Use only a decision close and frozen geometry; return a rejection explicitly."""
    r = dict(row)
    side, level, target = r['side'], r['entry'], r['target_price']
    k = int(r['entry_i'])
    if k >= r['end_i'] or k >= r['expire_i']:
        return None, 'no_remaining_interval'
    if np.isfinite(target) and side * (target-close) < tick * (1-1e-7):
        return None, 'target_already_reached_at_decision_close'
    stop = float(stops(pd.DataFrame([r]), 'P', cfg['backstop_pct'], tick)[0])
    if side * (close-stop) <= 0:
        return None, 'close_beyond_backstop'
    r.update(reclaim_ref=level, stop=stop, be_offset_bp=cfg['be_offset_bp'],
             order_mode='limit' if side*(close-level) >= tick*(1-1e-7) else 'market')
    if r['order_mode'] == 'market':
        r['entry'] = close
    r['cancel_boundary'] = target  # pending only; native intrabar cancellation
    r['risk'] = r['entry']
    return r, 'admitted'


def make_orders(p, original, contexts, tick, score, cfg):
    """Generate signal/control decisions without reading a post-decision price.

    Control matching uses high/low/previous close TR rolling 14, divided by the
    current close, bucket=floor(4*log2(vol)). This is known at each decision.
    The control clock and source continuity end remain its own, never inherited
    from the signal's exit. Unfilled controls are retained, never resampled.
    """
    e = original[(original.population == 'signal') & (original.reclaim_minutes == 60)
                 & (original.exit_mode == 'opposite_1618')].copy()
    e['event'] = e.event_id
    e['score'] = e.event.map(score)
    e = e[(e.score >= cfg['score_cut']) & ~e.event.isin(BAD_EVENTS)]
    e = e[p.index[e.touch_i_hint.astype(int)].asi8 >= pd.Timestamp(cfg['start']).value]
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
        event = source['event']; k = int(source['touch_i_hint'])
        source.update(entry_i=k, decision_i=k, control_id=0, trade_id=event+':hybrid:c0',
                      bucket=int(buckets[k]))
        row, status = configure_order(source, c[k], tick, cfg)
        decisions.append(dict(event=event, trade_id=source['trade_id'], control_id=0,
                              decision_i=k, status=status, score=source['score']))
        if row is None:
            continue
        orders.append(row)
        w = weeks[source['week']]
        a, b = int(w['monitor_start_i']), int(w['monitor_end_i_exclusive'])
        pool = np.arange(a, max(a, b-1))
        pool = pool[(buckets[pool] == buckets[k]) & (buckets[pool] != -999)
                    & (ends[pool] > pool) & (pool != k)
                    & (p.index[pool].asi8 >= pd.Timestamp(cfg['start']).value)]
        rng = np.random.default_rng(cfg['control_seed'] + zlib.crc32(event.encode()))
        chosen = rng.choice(pool, min(cfg['controls'], len(pool)), replace=False)
        for control, j in enumerate(chosen, 1):
            r = dict(source)
            ratio = c[j]/c[k]
            # Same rounding conventions as original signal orders.
            raw = source['entry']*ratio
            level = (np.floor(raw/tick+1e-7) if source['side'] == 1
                     else np.ceil(raw/tick-1e-7))*tick
            r.update(entry_i=int(j), decision_i=int(j), entry=level,
                     target_price=float(np.round(source['target_price']*ratio/tick)*tick),
                     end_i=int(ends[j]), expire_i=min(b-1, int(ends[j])),
                     population='random', control_id=control, bucket=int(buckets[j]),
                     trade_id=event+f':hybrid:c{control}')
            r, status = configure_order(r, c[j], tick, cfg)
            decisions.append(dict(event=event, trade_id=event+f':hybrid:c{control}',
                                  control_id=control, decision_i=int(j), status=status,
                                  score=source['score']))
            if r is not None:
                orders.append(r)
    return pd.DataFrame(orders), pd.DataFrame(decisions)


def process(symbol, config):
    """One load, both declared intrabar paths, only new native orders replayed."""
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native
    cfg = json.loads(Path(config).read_text()); out = Path(config).parent/'symbols'/symbol
    out.mkdir(parents=True, exist_ok=True)
    if (out/'done.json').exists():
        return json.loads((out/'done.json').read_text())
    started = time.perf_counter()
    origin = CODEX/'symbols'/symbol
    identity = json.loads((origin/'identity.json').read_text()); job = identity['job']
    if job['group'] != 'crypto_5m':
        raise ValueError('Frozen allin3 universe must use original crypto_5m jobs')
    f, _ = load_job(job)
    original_done = json.loads((origin/'done.json').read_text())
    if (len(f) != original_done['rows'] or str(f.index[0]) != original_done['first']
            or str(f.index[-1]) != original_done['last']):
        raise ValueError('Source indices differ from the saved native orders')
    decimals = max(2, min(9, int(np.ceil(-np.log10(f[['open','high','low','close']].to_numpy().min())))+7))
    tick = 10.**(-decimals); p = f[['open','high','low','close']].round(decimals)
    p.index = p.index.as_unit('ns')
    original = pd.read_csv(origin/'orders.csv.gz')
    contexts = json.loads((origin/'weeks.json').read_text())
    score = pd.read_csv(ROOT/cfg['scores']).set_index('event').stored
    x, decisions = make_orders(p, original, contexts, tick, score, cfg)
    decisions.to_csv(out/'decisions.csv.gz', index=False)
    if x.empty:
        done = dict(symbol=symbol, orders=0, seconds=time.perf_counter()-started)
        dump(out/'done.json', done); return done
    frame = p.copy(); frame.index += pd.Timedelta(minutes=5)
    x['decision_time'] = frame.index[x.decision_i.astype(int)].astype(str).to_numpy()
    x.to_csv(out/'orders.csv.gz', index=False)
    reuse = (x.control_id == 0) & (x.order_mode == 'market')
    runtime = x[~reuse].copy()
    counts = {}
    for path in ('OHLC', 'OLHC'):
        dest = out/path.lower(); dest.mkdir(exist_ok=True)
        if (dest/'audit.json').exists():
            counts[path] = json.loads((dest/'audit.json').read_text()); continue
        cached = pd.read_csv(CACHED/path.lower()/'symbols'/symbol/'trades.csv.gz')
        ids = dict(zip(x[reuse].event+':m60:opposite_1618:c0:v_cb_v3', x[reuse].trade_id))
        old = cached[cached.trade_id.isin(ids)].copy()
        if len(old) != reuse.sum():
            raise ValueError('Missing exact cached market rows')
        old['trade_id'] = old.trade_id.map(ids).astype('str')
        merged = old.merge(x[reuse][['trade_id','entry','entry_i','stop','target_price']], on='trade_id', suffixes=('', '_intended'))
        for field in ('entry','entry_i','stop','target_price'):
            np.testing.assert_allclose(merged[field], merged[field+'_intended'], atol=tick*1e-6, rtol=1e-12)
        rows, fills, audit = pd.DataFrame(columns=KEEP), pd.DataFrame(), dict(positions=0,fills=0,opportunities=[])
        if len(runtime):
            policy = ExitPolicy(trail_arm_r=1e12, opposite_hour_exit=False, round_trip_cost=0,
                target_from_entry=True, intrabar_path=path, reclaim_next_bar=True,
                breakeven_includes_cost=False, fill_maker_fee=cfg['maker_fee'],
                fill_taker_fee=cfg['taker_fee'], monotone_segments=True, reclaim_backstop=True)
            rows, fills, audit = run_native(frame, np.full(len(frame), np.nan), np.zeros(len(frame)),
                runtime, policy, tick=tick, symbol=symbol.removesuffix('USDT'),
                entry_order_mode='limit', batch_bars=288, skip_idle_bars=True)
        combined = pd.concat([rows.reindex(columns=KEEP), old.reindex(columns=KEEP)], ignore_index=True)
        combined = combined.merge(x[['trade_id','event','control_id','order_mode','decision_time','score']],
                                  on='trade_id', validate='one_to_one')
        # Historical account's independent MAE overlay, for legacy comparability only.
        h, lo = p.high.to_numpy(), p.low.to_numpy()
        mae = []
        for r in combined.itertuples():
            a = int(r.entry_i) + (r.order_mode == 'market'); b = max(a+1, int(r.exit_i))
            worst = lo[a:b].min() if r.side == 1 else h[a:b].max()
            mae.append(max(0., -r.side*(worst-r.entry)/r.entry*100))
        combined['mae_pct'] = mae
        combined['net'] = (combined.net_notional_bp
            - np.where(combined.order_mode == 'market', cfg['slippage_bp']['market_entry'], 0.)
            - np.where(combined.reason == 4, 0., cfg['slippage_bp']['non_target_exit']))
        combined.to_csv(dest/'trades.csv.gz', index=False)
        fills.to_csv(dest/'new_native_fills.csv.gz', index=False)
        opportunities = pd.DataFrame(audit.get('opportunities', []))
        if len(opportunities):
            opportunities['release_time'] = [str(frame.index[int(r.get('cancelled_i', r.get('expire_i')))])
                if r['status'] != 'filled' else None for r in opportunities.to_dict('records')]
        opportunities.to_csv(dest/'opportunities.csv.gz', index=False)
        receipt = dict(engine='nautilus_trader', version='1.231.0', new_orders=len(runtime),
            new_positions=audit['positions'], new_fills=audit['fills'], reused_market_positions=len(old),
            cancelled=audit.get('cancelled',0), expired=audit.get('expired',0),
            positions=len(combined), skip_idle_bars=True)
        dump(dest/'audit.json', receipt); counts[path] = receipt
    done = dict(symbol=symbol, orders=len(x), signal_orders=int((x.control_id == 0).sum()),
        paths=counts, sources=[file_stamp(ROOT/Path(v)) for v in job['paths']],
        seconds=time.perf_counter()-started)
    dump(out/'done.json', done); return done


def run(config, workers):
    from yoyo.evaluation.monday_limit_paper_accounts import committed
    committed('yoyo/evaluation/allin3_close_hybrid.py',
              'yoyo/layers/l3_backtest/nautilus_independent.py', str(config.relative_to(ROOT)))
    cfg = json.loads(config.read_text())
    score = pd.read_csv(ROOT/cfg['scores'])
    score = score[(score.stored >= cfg['score_cut']) & (score.event.str.split(':').str[1] >= '2022-12-26')]
    symbols = sorted(set(score.event.str.split(':').str[0]))
    with ProcessPoolExecutor(workers) as ex:
        jobs = {ex.submit(process, s, str(config)): s for s in symbols}
        for k, task in enumerate(as_completed(jobs), 1):
            try:
                result = task.result()
            except Exception as error:
                result = dict(symbol=jobs[task], error=repr(error))
                dest = config.parent/'symbols'/jobs[task]; dest.mkdir(parents=True, exist_ok=True)
                dump(dest/'error.json', result)
            print(json.dumps(dict(progress=k, total=len(symbols), **result)), flush=True)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, default=EXP/'config.json')
    ap.add_argument('--workers', type=int, default=4)
    args = ap.parse_args(); run(args.config.resolve(), args.workers)
