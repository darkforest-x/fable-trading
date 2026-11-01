"""Losing streaks of the live- strategies and the earlier loss-doubling sizing on them (owner 2026-11-01).

Owner: "目前我们这几个策略最长的连续止损是多少次？不考虑同时持仓的情况下，假如再补充上我们之前的倍投策略，最后的收益会
如何呢". Trade streams (saved fills, no replay):
  live-wick      the E20 near-edge survivors (live-wick6x3, 3x3 and 6x3hi trade the same events; only sizing differs);
  live-wick-far  the same with the close >= 1.5 percent past the level (live-wick3x3far);
  live-allin3    the funded settlements of exp-pretouch-booklet-20261031-v1 allin3-A_rest_causal (one at a time).
Streaks in entry order, ignoring overlaps as asked: consecutive losing trades (net < 0) and consecutive -20 / -30 percent
backstops. Loss-doubling is spike_martingale_account.simulate unchanged (the earlier ETH study's rule: risk =
base x 2^level, notional = risk / stop distance, level +1 after a losing stop, reset after a win, at most max_level
doublings, fixed base, admission needs margin at the leverage cap plus a 20bp fee reserve). Its stream must not overlap,
so the trades keep their entry order on a synthetic one-hour clock: concurrency is ignored, as the owner asked, which
overstates what one account could hold. Trigger variants: every loss counts as a stop (classic doubling after any
loss), or only backstop / breakeven stops count (the earlier study's literal trigger).
"""
from __future__ import annotations
import argparse
import json
import subprocess
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation import monday_limit_close_reclaim as cr
from yoyo.evaluation.monday_limit_close_survivor_paths import symbol_rows
from yoyo.evaluation.spike_martingale_account import simulate

STOPS = {1: 'backstop', 5: 'breakeven_stop', 2: 'stop', 8: 'stop', 9: 'stop', 13: 'stop'}


def streaks(x):
    """Longest runs of losing trades and of backstops, in entry order."""
    out = {}
    for name, flag in (('losses', x.net.to_numpy() < 0), ('backstops', (x.reason == 1).to_numpy())):
        best = cur = 0
        for f in flag:
            cur = cur+1 if f else 0
            best = max(best, cur)
        out[name] = int(best)
    return out


def stream(x, trigger):
    """Non-overlapping synthetic clock in entry order; exit_reason decides which losses double the stake."""
    start = datetime(2020, 1, 1, tzinfo=timezone.utc)
    rows = []
    for k, r in enumerate(x.itertuples()):
        reason = 'stop' if trigger == 'any_loss' else STOPS.get(int(r.reason), 'exit')
        rows.append(dict(trade_id=str(r.event), entry_time=start+timedelta(hours=k), exit_time=start+timedelta(hours=k, minutes=30),
                         net_return=float(r.net)/1e4, initial_risk_frac=float(r.risk_frac), exit_reason=reason))
    return rows


def replay(args):
    name, x, cfg = args
    out = {}
    for p, start in cfg['periods'].items():
        y = x[x.entry_t >= pd.Timestamp(start, tz='UTC')].sort_values(['entry_t', 'event']).reset_index(drop=True)
        res = dict(trades=int(len(y)), win_pct=float(100*(y.net > 0).mean()), streaks=streaks(y), runs={})
        for trig in cfg['triggers']:
            trades = stream(y, trig)
            for base in cfg['base_risk_usdt']:
                for mult, level in [(1, 0)]+[(cfg['multiplier'], m) for m in cfg['max_levels']]:
                    s = simulate(trades, initial_balance=1000, base_risk_usdt=base, multiplier=mult, max_level=level,
                                 reset_mode='win', leverage_cap=cfg['leverage_cap'], record_ledger=False)['summary']
                    res['runs'][f'{trig}|risk{base}|x{mult}|L{level}'] = {k: s[k] for k in (
                        'final_balance', 'min_balance', 'max_realized_drawdown', 'n_accepted', 'n_rejected', 'ruined',
                        'max_executed_level', 'max_executed_notional', 'max_effective_leverage', 'capped_cycle_resets')}
        out[p] = res
    return name, out


def run(config, workers):
    from yoyo.evaluation.monday_limit_be_scalein_compare import BAD_EVENTS, clean
    exp = config.resolve().parent
    cfg = json.loads(config.read_text())
    cr.committed('yoyo/evaluation/monday_limit_live_streaks.py', str(config.resolve().relative_to(ROOT)))
    src = ROOT/cfg['fills']
    surv = pd.read_csv(ROOT/cfg['survivors'])
    with ProcessPoolExecutor(workers) as ex:
        got = list(ex.map(symbol_rows, [(str(src), s, 10, 10, cfg['variant']) for s in sorted(set(surv.symbol))], chunksize=4))
    t = pd.concat([g[0] for g in got if len(g[0])], ignore_index=True)
    t = t[(t['mode'] == cfg['target']) & t.event.isin(set(surv.event)) & ~t.event.isin(BAD_EVENTS)
          & (t.entry_t >= pd.Timestamp('2020-01-01', tz='UTC'))].copy()
    t['beyond_pct'] = 100*t.side*(t.level-t.entry)/t.level
    t = t[t.beyond_pct < 30].copy()
    t['risk_frac'] = cfg['wick_stop_frac']
    a = pd.read_csv(ROOT/cfg['allin3_ledger'])
    a = a[a.notional > 0].copy()
    a = pd.DataFrame(dict(event=a.trade_id, entry_t=pd.to_datetime(a.entry_ms, unit='ms', utc=True), net=a.net_bp,
                          reason=a.reason.astype(int), risk_frac=cfg['allin3_stop_frac']))
    streams = {'live-wick': t, 'live-wick-far': t[t.beyond_pct >= 1.5], 'live-allin3': a}
    res = dict(checks={k: int(len(v)) for k, v in streams.items()}, streams={})
    with ProcessPoolExecutor(workers) as ex:
        for name, out in ex.map(replay, [(k, v[['event', 'entry_t', 'net', 'reason', 'risk_frac']], cfg) for k, v in streams.items()]):
            res['streams'][name] = out
    dump(exp/'summary.json', dict(config=cfg, results=clean(res), source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                                 training_eligible=False, production_eligible=False))
    print(json.dumps(res['checks']))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, required=True); ap.add_argument('--workers', type=int, default=6)
    a = ap.parse_args()
    run(a.config, a.workers)
