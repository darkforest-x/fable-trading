"""Audit of the live- paper strategies: where the backtest money comes from and what may not survive live trading.

Owner 2026-11-01: "目前我们的live策略是否有过拟合的可能。或者有没有什么bug?实盘真的有这个效果？我看live-all3 从1000u打到
42wu 其实也就是最近几个月的事情？能不能详细拆解一下live-all3". Read-only on saved results; no fills, scores or
parameters change.
  live-allin3  the booklet ledger of exp-pretouch-booklet-20261031-v1 (allin3-A_rest_causal: frozen pre-touch score
               >= 0.6, limit resting at the 1.618, opposite 1.618 target, whole account at 3x, refilled to 1000U below
               100U). Its candidate rows (pretouch_booklet_paths.rows, the same admission) are replayed with
               monday_limit_langlang.path_run from every month start and several tie seeds to show path dependence.
  live-wick    the 6x3 ledger of exp-monday-limit-close-survivor-paths-20261101-v10 (to 2026-10-27).
Per funded trade:
  breadth      other symbols whose first weekly near-edge touch on the same side falls in the same 5m bar
               (monday_limit_reclaim_patterns3 events, to 2026-09; the alarm study's line is 25);
  volume       the touch 5m bar's quote volume (close x base volume) and the median 5m quote volume of the prior day,
               against the trade's notional;
  depth        how far the touch bar went past the 1.618 (a resting limit at the level needs the price to trade
               through it; a bar that only reached the level may not fill);
  okx          whether OKX lists a USDT swap for the coin today (okx_swaps.json, saved with its fetch time; the 1000 /
               1000000 prefixes of Binance contracts are stripped, an approximation).
"""
from __future__ import annotations
import argparse
import json
import re
import subprocess
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation import monday_limit_close_reclaim as cr
from yoyo.evaluation.monday_limit_backstop import CODEX

DAY_MS = 86_400_000


def base(symbol):
    return re.sub(r'^(1000000|100000|10000|1000|1M)(?=[A-Z])', '', symbol.removesuffix('USDT'))


def bar_facts(args):
    """Touch-bar quote volume, prior-day median 5m quote volume and depth past the level, per event of one symbol."""
    symbol, rows, job = args
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    f, _ = load_job(job)
    h, l, c, v = (f[k].to_numpy() for k in ('high', 'low', 'close', 'volume'))
    qv = c*v
    out = []
    for event, k, level, side in rows:
        k = int(k)
        ext = l[k] if side == 1 else h[k]
        out.append(dict(event=event, bar_quote=float(qv[k]), day_median_quote=float(np.median(qv[max(0, k-288):k])) if k else np.nan,
                        depth_pct=float(100*side*(level-ext)/level)))
    return out


def touch_rows(events, october):
    """(symbol, event, touch bar index, level, side) from the frozen ledger, else the October extension events."""
    from yoyo.evaluation.monday_limit_reclaim_patterns import SRC
    want = pd.Series(sorted(events))
    rows = []
    for s, g in want.groupby(want.str.split(':').str[0]):
        path = SRC/s/'trades.csv.gz'
        if not path.exists():
            continue
        t = pd.read_csv(path, usecols=['population', 'reclaim_minutes', 'exit_mode', 'week', 'side', 'touch_i_hint', 'level'])
        t = t[(t.population == 'signal') & (t.reclaim_minutes == 60) & (t.exit_mode == 'near_monday_edge')]
        t = t.assign(event=s+':'+t.week+':'+np.where(t.side == 1, '+1', '-1')).drop_duplicates('event')
        t = t[t.event.isin(set(g))]
        rows += [(s, e, k, lv, sd) for e, k, lv, sd in zip(t.event, t.touch_i_hint, t.level, t.side)]
    found = {r[1] for r in rows}
    ext = pd.read_csv(october/'events.csv.gz', usecols=['event', 'symbol', 'touch_i', 'level', 'side'])
    ext = ext[ext.event.isin(set(events)-found)]
    rows += [(s, e, k, lv, sd) for s, e, k, lv, sd in zip(ext.symbol, ext.event, ext.touch_i, ext.level, ext.side)]
    return rows


def facts(events, october, workers):
    jobs = json.loads((october/'jobs.json').read_text())
    by = {}
    for s, e, k, lv, sd in touch_rows(events, october):
        by.setdefault(s, []).append((e, k, lv, sd))
    args = [(s, r, jobs.get(s) or json.loads((CODEX/'symbols'/s/'identity.json').read_text())['job']) for s, r in sorted(by.items())]
    with ProcessPoolExecutor(workers) as ex:
        got = [x for part in ex.map(bar_facts, args) for x in part]
    return pd.DataFrame(got).set_index('event')


def breadth(cfg):
    """Same-side first touches of other symbols in the same 5m bar, per event (events to 2026-09)."""
    e = pd.read_csv(ROOT/cfg['events'], usecols=['symbol', 'week', 'side_n', 't'])
    e['event'] = e.symbol+':'+e.week+':'+np.where(e.side_n == 1, '+1', '-1')
    e['bar'] = pd.to_datetime(e.t, utc=True).dt.floor('5min')
    n = e.groupby(['bar', 'side_n']).symbol.transform('nunique')-1
    return pd.Series(n.to_numpy(), index=e.event)


def trade_stats(x):
    if not len(x):
        return dict(n=0)
    w, l = x.net_bp[x.net_bp > 0], x.net_bp[x.net_bp <= 0]
    return dict(n=int(len(x)), win_pct=float(100*(x.net_bp > 0).mean()), mean_bp=float(x.net_bp.mean()),
                median_bp=float(x.net_bp.median()), sum_bp=float(x.net_bp.sum()),
                profit_factor=float(w.sum()/-l.sum()) if len(l) and l.sum() < 0 else None,
                pnl=float(x.pnl.sum()) if 'pnl' in x else None)


def realism(f, okx, cfg):
    """Breadth, volume, depth and OKX listing summaries for one ledger's funded trades (with columns already joined)."""
    f = f.assign(listed=f.symbol.map(base).isin(okx), ratio_bar=f.notional/f.bar_quote, ratio_day=f.notional/f.day_median_quote)
    crash = f.breadth >= cfg['alarm_breadth']
    q = lambda s: {f'q{int(100*p)}': float(s.quantile(p)) for p in (.5, .9, 1.)} if s.notna().any() else None
    return dict(
        breadth=dict(known=int(f.breadth.notna().sum()), crash_bar=trade_stats(f[crash]), other=trade_stats(f[~crash & f.breadth.notna()]),
                     unknown=trade_stats(f[f.breadth.isna()])),
        okx=dict(listed=trade_stats(f[f.listed]), not_listed=trade_stats(f[~f.listed])),
        notional_over_touch_bar_quote=q(f.ratio_bar), notional_over_day_median_5m_quote=q(f.ratio_day),
        depth_past_level_pct=q(f.depth_pct), shallow_fills_lt_0_1pct=trade_stats(f[f.depth_pct < .1]),
        top=f.sort_values('pnl', ascending=False).head(10)[['trade_id', 'entry', 'net_bp', 'pnl', 'notional', 'reason', 'breadth',
                                                             'bar_quote', 'depth_pct', 'listed']].to_dict('records'))


def month_starts(cfg, workers):
    """Replay the live-allin3 candidates from every month start with several tie seeds (same allocator)."""
    from yoyo.evaluation.pretouch_booklet_paths import rows
    from yoyo.evaluation.strategy_booklet_paths import Trace, dated
    from yoyo.evaluation.monday_limit_langlang import path_run
    bcfg = json.loads((ROOT/cfg['allin3_booklet']/'config.json').read_text())
    plans, _ = rows(bcfg, workers)
    x = plans[cfg['allin3_key']][1]
    x = dated(x.assign(symbol=x.event.str.split(':').str[0], tid=x.trade_id))
    p = bcfg['paths']['allin3']
    out = []
    for start in pd.date_range(*cfg['month_starts'], freq='MS', tz='UTC'):
        y = x[x.entry_t >= start].reset_index(drop=True)
        for k in range(cfg['start_ties']):
            tr = Trace(start.value)
            path_run(y, 'allin', p['leverage'], p['seed']+k, trace=tr)
            fr = pd.DataFrame(tr.rows)
            out.append(dict(start=str(start.date()), tie=k, end=float(fr.equity.iloc[-1]), cash_added=float(fr.cashflow.sum()),
                            trading_pnl=float(fr.pnl.sum()), refills=int((fr.cashflow > 0).sum()), trades=int((fr.notional > 0).sum())))
    return pd.DataFrame(out)


def allin3(cfg, okx, brd, workers):
    led = pd.read_csv(ROOT/cfg['allin3_booklet']/cfg['allin3_ledger'])
    led['time'] = pd.to_datetime(led.time_ms, unit='ms', utc=True)
    fund = led[led.notional > 0].copy()
    fund['entry'] = pd.to_datetime(fund.entry_ms, unit='ms', utc=True).astype(str).str[:16]
    refill = led[led.cashflow > 0]
    month = led.assign(m=led.time.dt.strftime('%Y-%m')).groupby('m').agg(
        trades=('notional', lambda s: int((s > 0).sum())), pnl=('pnl', 'sum'), refills=('cashflow', lambda s: int((s > 0).sum())),
        cash=('cashflow', 'sum'), end=('equity', 'last'))
    last_refill = refill.time.max()
    after = led[led.time > last_refill]
    peak = after.loc[after.equity.idxmax()]
    data_end = fund[fund.reason == 0]
    res = dict(
        summary=dict(trades=int(len(fund)), trading_pnl=float(fund.pnl.sum()), cash_added=float(led.cashflow.sum()), refills=int(len(refill)),
                     end=float(led.equity.iloc[-1]), last_refill=str(last_refill), pnl_before_last_refill=float(led[led.time <= last_refill].pnl.sum()),
                     pnl_after_last_refill=float(after.pnl.sum()), trades_after_last_refill=int((after.notional > 0).sum()),
                     peak=float(peak.equity), peak_time=str(peak.time), data_end_trades=data_end[['trade_id', 'pnl', 'net_bp']].to_dict('records'),
                     end_without_data_end=float(led.equity.iloc[-1]-data_end.pnl.sum())),
        monthly=month.reset_index().to_dict('records'),
        years={str(y): trade_stats(g) for y, g in fund.groupby(pd.to_datetime(fund.entry_ms, unit='ms', utc=True).dt.year)},
        top10_share=float(fund.nlargest(10, 'pnl').pnl.sum()/fund.pnl.sum()))
    f = fund.join(facts(set(fund.trade_id), ROOT/cfg['wick_october'], workers), on='trade_id')
    f['breadth'] = f.trade_id.map(brd)
    res['realism'] = realism(f, okx, cfg)
    late = f[f.time > last_refill]
    res['after_last_refill'] = realism(late, okx, cfg)
    ms = month_starts(cfg, workers)
    g = ms.assign(net=ms.end-1000-ms.cash_added, multiple=ms.end/(1000+ms.cash_added))
    res['month_starts'] = dict(runs=int(len(g)), net_positive_pct=float(100*(g.net > 0).mean()),
                               multiple_q={f'q{int(100*p)}': float(g.multiple.quantile(p)) for p in (.1, .25, .5, .75, .9)},
                               by_start=g.groupby('start').agg(net_median=('net', 'median'), multiple_median=('multiple', 'median'),
                                                                refills_median=('refills', 'median')).reset_index().to_dict('records'))
    return res


def wick(cfg, okx, brd, workers):
    led = pd.read_csv(ROOT/cfg['wick_ledger'])
    fund = led[led.notional > 0].copy()
    fund['time'] = pd.to_datetime(fund.time_ms, unit='ms', utc=True)
    fund['entry'] = pd.to_datetime(fund.entry_ms, unit='ms', utc=True).astype(str).str[:16]
    res = dict(years={str(y): trade_stats(g) for y, g in fund.groupby(pd.to_datetime(fund.entry_ms, unit='ms', utc=True).dt.year)},
               top10_share=float(fund.nlargest(10, 'pnl').pnl.sum()/fund.pnl.sum()))
    f = fund.join(facts(set(fund.trade_id), ROOT/cfg['wick_october'], workers), on='trade_id')
    f['breadth'] = f.trade_id.map(brd)
    res['realism'] = realism(f, okx, cfg)
    return res


def run(config, workers):
    from yoyo.evaluation.monday_limit_be_scalein_compare import clean
    exp = config.resolve().parent
    cfg = json.loads(config.read_text())
    cr.committed('yoyo/evaluation/live_strategy_audit.py', str(config.resolve().relative_to(ROOT)))
    okx = {r['instId'].split('-')[0] for r in json.loads((ROOT/cfg['okx']).read_text())['swaps'] if r.get('state') == 'live'}
    brd = breadth(cfg)
    res = dict(live_allin3=allin3(cfg, okx, brd, workers), live_wick6x3=wick(cfg, okx, brd, workers))
    dump(exp/'summary.json', dict(config=cfg, results=clean(res), source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                                 training_eligible=False, production_eligible=False))
    print(json.dumps(res['live_allin3']['summary'], default=str)[:1500])


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, required=True); ap.add_argument('--workers', type=int, default=6)
    a = ap.parse_args()
    run(a.config, a.workers)
