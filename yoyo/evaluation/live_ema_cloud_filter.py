"""EMA cloud direction filter on the live- paper-account rules (owner 2026-11-03).

Owner: "我们的那几个live策略，测试一下，使用ema cloud这个指标 默认参数 20 50 50 … 基于12h级别，只做一边，比如红色云带只做空，
绿色云只做多，12h。4h。1h的都试试，live原有的所有规则都不变". The owner's Pine "EMA Trend" fills the band between EMA(fast) and
EMA(slow) green when fast > slow and red when fast < slow; with 20 / 50 / 50 the colour is EMA20 against EMA50 of close.
Rule tested: take a live- candidate only when its side matches the colour of the last bar closed at the decision (12h
primary; 4h, 1h), longs on green, shorts on red; no colour (EMA50 undefined or equal) takes neither side.
Candidates, fills, sizing and seeds are the saved live- accounts', no new fills:
  live-wick6x3 / 3x3 / 6x3hi / 3x3far  the E20 survivor rows of monday_limit_close_survivor_paths (configs v10-v13, with
                                       the October extension appended), decision at the touch 5m close (entry_t);
  live-allin3                          the allin3 A_rest_causal plan of pretouch_booklet_paths from 2023, decision at the
                                       open of the 5m fill bar (the limit rests before the touch; entry_t is its close).
The unfiltered arm must reproduce each saved account's end; 'against' (opposite colour) is reported for reference, and
each with-cloud account is compared with random same-size subsets of its own candidates under the same allocator.
Bars: the execution series of each symbol (Codex job, plus the October new bars when present) resampled to 1h / 4h / 12h
on UTC epoch alignment; EMA as Pine (alpha 2/(n+1), SMA seed). Columns used: close only, bars closed by the decision.
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
from yoyo.evaluation import monday_limit_close_reclaim as cr
from yoyo.evaluation.monday_limit_backstop import CODEX
from yoyo.evaluation.strategy_booklet_paths import Trace, dated

TF = {'12h': 12*3600, '4h': 4*3600, '1h': 3600}
WICK_SHARED = ('source', 'fills', 'extension', 'variant', 'max_beyond_pct', 'slippage_bp', 'tie_seed_from')


def pine_ema(close, n):
    """Pine ema: undefined before n bars, SMA of the first n closes as the seed, then alpha 2/(n+1)."""
    v = close.to_numpy(float)
    out = np.full(len(v), np.nan)
    if len(v) >= n:
        out[n-1:] = pd.Series(np.r_[v[:n].mean(), v[n:]]).ewm(alpha=2/(n+1), adjust=False).mean().to_numpy()
    return out


def clouds(args):
    """Cloud colour (+1 green, -1 red, 0 none) of the last bar closed at or before each decision, per timeframe."""
    symbol, decisions, fast, slow, october = args
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    job = json.loads((CODEX/'symbols'/symbol/'identity.json').read_text())['job']
    extra = Path(october)/symbol/'new_bars.csv.gz'
    if extra.exists():
        job = dict(job, paths=list(job['paths'])+[str(extra)])
    f, _ = load_job(job)
    out = dict(symbol=symbol, decision_ns=decisions)
    for tf, sec in TF.items():
        c = f.close.resample(f'{sec}s', label='left', closed='left', origin='epoch').last().dropna()
        close_ns = c.index.as_unit('ns').asi8+sec*10**9
        a, b = pine_ema(c, fast), pine_ema(c, slow)
        k = np.searchsorted(close_ns, decisions, side='right')-1
        kk = np.maximum(k, 0)
        fa, sl = np.where(k >= 0, a[kk], np.nan), np.where(k >= 0, b[kk], np.nan)
        out[f'cloud_{tf}'] = np.where(np.isnan(fa) | np.isnan(sl), 0, np.sign(fa-sl)).astype(int)
        out[f'bar_close_{tf}_ns'] = np.where(k >= 0, close_ns[kk], -1)
        out[f'ema_fast_{tf}'] = fa; out[f'ema_slow_{tf}'] = sl
    return pd.DataFrame(out)


def wick_rows(pcfg, workers):
    """Candidate rows of monday_limit_close_survivor_paths.run, same order, before the per-account filters."""
    from yoyo.evaluation.monday_limit_be_scalein_compare import BAD_EVENTS
    from yoyo.evaluation.monday_limit_close_survivor_paths import symbol_rows, extension_rows
    src = ROOT/pcfg['source']
    surv = pd.read_csv(src/'survivors.csv.gz').set_index('event')
    slip = pcfg['slippage_bp']
    with ProcessPoolExecutor(workers) as ex:
        got = list(ex.map(symbol_rows, [(str(ROOT/pcfg['fills']), s, slip['market_entry'], slip['non_target_exit'], pcfg['variant'])
                                        for s in sorted(set(surv.symbol))], chunksize=4))
    t = pd.concat([g[0] for g in got if len(g[0])], ignore_index=True)
    t = t[~t.event.isin(BAD_EVENTS) & (t.entry_t >= pd.Timestamp('2020-01-01', tz='UTC')) & t.event.isin(surv.index)].copy()
    t['score'] = t.event.map(surv.score); t['recovery'] = t.event.map(surv.recovery)
    t['symbol'] = t.event.str.split(':').str[0]
    t = pd.concat([t, extension_rows(ROOT/pcfg['extension'], set(t.event), workers)], ignore_index=True)
    t['beyond_pct'] = 100*t.side*(t.level-t.entry)/t.level
    return t[(t.beyond_pct < pcfg['max_beyond_pct']) & (t['mode'] == 'near_monday_edge')].copy()


def candidates(cfg, workers):
    """Per account and start: (allocator spec, candidate frame in the saved run's row order, saved end)."""
    from yoyo.evaluation.pretouch_booklet_paths import rows as booklet_rows
    out, wick = {}, None
    for name, a in cfg['accounts'].items():
        if 'paths' in a:
            pcfg = json.loads((ROOT/a['paths']/'config.json').read_text())
            saved = json.loads((ROOT/a['paths']/'manifest.json').read_text())['accounts']
            if wick is None:
                shared, wick = {k: pcfg.get(k) for k in WICK_SHARED}, wick_rows(pcfg, workers)
            if {k: pcfg.get(k) for k in WICK_SHARED} != shared:
                raise ValueError(f'{name}: live-wick paths differ in shared inputs')
            t = wick if pcfg.get('min_beyond_pct') is None else wick[wick.beyond_pct >= pcfg['min_beyond_pct']]
            seed = json.loads((ROOT/pcfg['tie_seed_from']).read_text())['seed']
            spec = dict(kind='frac', slots=pcfg['slots'], leverage=pcfg['leverage'], seed=seed, boost=pcfg.get('boost'))
            for start in a['starts']:
                x = dated(t[t.entry_t >= pd.Timestamp(start, tz='UTC')]).reset_index(drop=True)
                out[(name, start)] = (spec, x.assign(decision_ns=x.entry_ns), saved[f'near_monday_edge-{start[:4]}']['summary'])
        else:
            bcfg = json.loads((ROOT/a['booklet']/'config.json').read_text())
            plans, _ = booklet_rows(bcfg, workers)
            p = bcfg['paths'][a['track']]
            x = plans[a['plan']][1]
            x = dated(x.assign(symbol=x.event.str.split(':').str[0], tid=x.trade_id))
            x = x[x.entry_t >= pd.Timestamp(p['start'], tz='UTC')].reset_index(drop=True)
            arm = a['plan'].split('|')[1]
            saved = json.loads((ROOT/a['booklet']/'manifest.json').read_text())['accounts'][f"{a['track']}-{arm}"]['summary']
            spec = dict(kind='allin', leverage=p['leverage'], seed=p['seed'], start=p['start'])
            out[(name, p['start'])] = (spec, x.assign(decision_ns=x.entry_ns-5*60*10**9), saved)
    return out


def replay(x, spec, start):
    """The account's own allocator on rows x: end, trading P&L after cash added, drawdown, taken, win rate."""
    trace = Trace(pd.Timestamp(start, tz='UTC').value)
    if spec['kind'] == 'allin':
        from yoyo.evaluation.monday_limit_langlang import path_run
        path_run(x, 'allin', spec['leverage'], spec['seed'], trace=trace)
    elif spec.get('boost'):
        from yoyo.evaluation.monday_limit_close_order_boost import weighted_frac
        b = spec['boost']
        weighted_frac(x, spec['slots'], spec['leverage'], spec['seed'], np.where(x.score >= b['from_score'], b['x'], 1.), trace=trace)
    else:
        from yoyo.evaluation.monday_limit_return_opt import frac
        frac(x, spec['slots'], spec['leverage'], spec['seed'], trace=trace)
    f = pd.DataFrame(trace.rows)
    funded = f[f.notional > 0]
    eq = f.equity.to_numpy()
    # Drawdown on realized equity within each stake (a refill restarts the peak, as the allin3 booklets show blow-ups).
    stake = np.cumsum(f.cashflow.to_numpy() > 0)
    peak = pd.Series(eq).groupby(stake).cummax().to_numpy()
    cash = float(f.cashflow.sum())
    return dict(end=float(eq[-1]), cash_added=cash, trading_pnl=float(eq[-1]-1000-cash), multiple=float(eq[-1]/1000),
                max_drawdown_pct=float(100*np.max(1-eq/peak)), taken=int(len(funded)), refills=int((f.cashflow > 0).sum()),
                win_pct=float(100*(funded.pnl > 0).mean()) if len(funded) else None)


def per_trade(x, periods):
    """Unsized stats of the candidate rows (net bp per trade after costs) by period."""
    yr = x.entry_t.dt.year
    sel = {'y2020_2024': yr <= 2024, 'y2023': yr == 2023, 'y2024': yr == 2024, 'y2025': yr == 2025, 'y2026': yr == 2026,
           'all': yr > 0}
    out = {}
    for p in list(periods)+['all']:
        y = x[sel[p]]
        out[p] = dict(n=int(len(y)), win_pct=float(100*(y.net > 0).mean()) if len(y) else None,
                      mean_bp=float(y.net.mean()) if len(y) else None, median_bp=float(y.net.median()) if len(y) else None)
    return out


def control(args):
    """Random subsets of the account's candidates with the filtered arm's size, same allocator and seed."""
    key, tf, x, spec, start, k, draws, seed, mine = args
    rng = np.random.default_rng(seed)
    metric = 'trading_pnl' if spec['kind'] == 'allin' else 'multiple'
    vals = np.array([replay(x.iloc[np.sort(rng.choice(len(x), k, replace=False))].reset_index(drop=True), spec, start)[metric]
                     for _ in range(draws)])
    return key, tf, dict(metric=metric, filtered=mine, random_median=float(np.median(vals)), random_p5=float(np.percentile(vals, 5)),
                         random_p95=float(np.percentile(vals, 95)), share_below=float(100*(vals < mine).mean()), draws=draws)


def run(config, workers):
    from yoyo.evaluation.monday_limit_be_scalein_compare import clean
    exp = config.resolve().parent
    cfg = json.loads(config.read_text())
    cr.committed('yoyo/evaluation/live_ema_cloud_filter.py', str(config.resolve().relative_to(ROOT)))
    fast, slow = (int(cfg['indicator']['lengths'][k]) for k in ('fast', 'slow'))
    cands = candidates(cfg, workers)
    # One bar pass per symbol over every decision time.
    dec = pd.concat([x[['symbol', 'decision_ns']] for _, x, _ in cands.values()]).drop_duplicates()
    with ProcessPoolExecutor(workers) as ex:
        got = list(ex.map(clouds, [(s, np.sort(g.decision_ns.to_numpy()), fast, slow, str(ROOT/cfg['october_bars']))
                                   for s, g in dec.groupby('symbol')], chunksize=2))
    cl = pd.concat(got, ignore_index=True).drop_duplicates(['symbol', 'decision_ns']).set_index(['symbol', 'decision_ns'])
    res = dict(checks=dict(parity={}, no_colour={}), per_trade={}, accounts={}, control={})
    rows, jobs = [], []
    for (name, start), (spec, x, saved) in cands.items():
        key = f'{name}|{start}'
        x = x.join(cl, on=['symbol', 'decision_ns'])
        if x[[f'cloud_{tf}' for tf in TF]].isna().any().any():
            raise ValueError(f'{key}: cloud missing')
        base = replay(x, spec, start)
        err = abs(base['end']-saved['end'])
        res['checks']['parity'][key] = dict(replay_end=base['end'], saved_end=saved['end'], abs_error=err)
        if err > 1e-6*max(1., saved['end']):
            raise ValueError(f'{key}: unfiltered replay {base["end"]} != saved {saved["end"]}')
        periods = cfg['periods']['live-allin3' if spec['kind'] == 'allin' else 'live-wick']
        acc = dict(base=base)
        pt = dict(base=per_trade(x, periods))
        res['checks']['no_colour'][key] = {tf: int((x[f'cloud_{tf}'] == 0).sum()) for tf in TF}
        res['checks']['no_colour'][key]['candidates'] = int(len(x))
        # A cloud bar older than two bars of its timeframe means missing data before the decision.
        res['checks'].setdefault('stale_bar', {})[key] = {
            tf: int((x.decision_ns-x[f'bar_close_{tf}_ns'] > 2*sec*10**9).sum()) for tf, sec in TF.items()}
        for tf in TF:
            for arm, keep in (('with', x.side == x[f'cloud_{tf}']), ('against', x.side == -x[f'cloud_{tf}'])):
                y = x[keep].reset_index(drop=True)
                acc[f'{arm}_{tf}'] = dict(candidates=int(len(y)), **replay(y, spec, start))
                pt[f'{arm}_{tf}'] = per_trade(y, periods)
            mine = acc[f'with_{tf}']['trading_pnl' if spec['kind'] == 'allin' else 'multiple']
            jobs.append((key, tf, x, spec, start, acc[f'with_{tf}']['candidates'], cfg['control']['draws'],
                         cfg['control']['seed']+len(jobs), mine))
        res['accounts'][key], res['per_trade'][key] = acc, pt
        rows.append(x.assign(account=name, start=start)[['account', 'start', 'event', 'symbol', 'side', 'entry_t', 'net']
                                                        + [c for c in cl.columns]])
        print(f"{key}: parity {err:.2e}; " + '; '.join(f"{tf} with {acc[f'with_{tf}']['candidates']} end {acc[f'with_{tf}']['end']:.0f}"
                                                     for tf in TF), flush=True)
    with ProcessPoolExecutor(workers) as ex:
        for key, tf, out in ex.map(control, jobs):
            res['control'].setdefault(key, {})[tf] = out
    pd.concat(rows, ignore_index=True).to_csv(exp/'candidates.csv.gz', index=False, compression='gzip')
    dump(exp/'summary.json', dict(config=cfg, results=clean(res), source_commit=subprocess.check_output(
        ['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(), training_eligible=False, production_eligible=False))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, required=True); ap.add_argument('--workers', type=int, default=6)
    a = ap.parse_args()
    run(a.config, a.workers)
