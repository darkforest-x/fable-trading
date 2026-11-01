"""October 2026 backtest of the live-wick accounts on newly fetched Binance data (owner 2026-11-01).

Owner: "目前我们的几个live策略 都跑一下上个月的回测 10月份的" / "补跑10月". The frozen Codex ledger and its score files end in
late September 2026 (alt 5m series end 2026-09-23). This extends each crypto_5m symbol with the checksum-verified daily
Binance archives (yoyo.data.binance_um_daily, bars after the existing series only) and rebuilds the chain for the new
weeks with the unchanged code:
  events   monday_limit_reclaim.prepare on the extended series (first weekly touch per side, Monday levels, targets);
  score    the frozen model on the backtest definitions: vol_ratio, liquidity and toward24 at the touch bar (the stored
           score; monday_limit_score_leak_audit), approach, Monday close side and range (monday_limit_reclaim_patterns3),
           bucket from the ledger, opp_first and cluster24 across all symbols (monday_limit_reclaim_patterns{,3}); the
           same recomputation on events that already have a stored score is the parity check;
  fills    the live-wick rule in NautilusTrader as monday_limit_close_stop_grid (E20: market at the touch close, stop 20
           percent from the entry, near Monday edge target, 60m reclaim against the level, taker + 10bp entry, maker
           target, taker + 10bp other exits, skip idle bars, both intrabar paths, the worse one per trade);
  accounts 1000U on 2026-10-01 for live-wick6x3 / 3x3 / 6x3hi / 3x3far with their paper rules (frac; 1.5 x margin from
           score 0.76; close >= 1.5 percent past the level).
Entries are touch closes from 2026-10-01 to the data end; exits may run to the data end (open trades are reported).
Universe: the 536-symbol snapshot of the backtest (crypto_5m part); the 95 later listings are left out, as in the
backtest, and BNB / DOGE / SOL (1m jobs, not extended) are missing from the October cluster24 counts.
Validation before October is read: the recomputed stored / causal scores against the saved score file from
parity_from, and the same E20 fills for the earlier survivors against the saved stop grid (fill_parity).
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

EDGE = 0.6395668107499273


def job_for(symbol, cfg, exp):
    """Codex job plus the new bars after its last existing bar (written once next to the config)."""
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    job = json.loads((CODEX/'symbols'/symbol/'identity.json').read_text())['job']
    path = exp/'symbols'/symbol/'new_bars.csv.gz'
    if not path.exists():
        src = ROOT/cfg['new_data']/'series'/f'{symbol}.csv.gz'
        if not src.exists():
            return None
        f, _ = load_job(job)
        new = pd.read_csv(src)
        new = new[new.ts > f.index.max().value//1_000_000]
        if new.empty:
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        new.to_csv(path, index=False)
    return dict(job, paths=list(job['paths'])+[str(path)])


def events(args):
    """Signal events of one symbol from since_week, with the score inputs and touch-bar facts."""
    symbol, cfg, exp = args
    from yoyo.evaluation.monday_limit_reclaim import prepare
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    job = job_for(symbol, cfg, Path(exp))
    if job is None:
        return pd.DataFrame(), dict(symbol=symbol, status='no_new_data')
    ccfg = json.loads((CODEX/'config.json').read_text())
    f, q = load_job(job)
    _, e, _, _, tick = prepare(f, job, ccfg, q)
    s = e[(e.population == 'signal') & (e.reclaim_minutes == 60) & (e.exit_mode == 'near_monday_edge') & (e.week >= cfg['since_week'])]
    if s.empty:
        return pd.DataFrame(), dict(symbol=symbol, status='no_events', last=str(f.index.max()))
    o, h, l, c, v = (f[k].to_numpy() for k in ('open', 'high', 'low', 'close', 'volume'))
    qv, idx = v*c, f.index
    hour = pd.Series(qv).rolling(12, min_periods=12).sum().to_numpy()
    base = pd.Series(hour).rolling(2016, min_periods=1000).median().shift(12).to_numpy()
    day = pd.Series(qv).rolling(2016, min_periods=1000).sum().to_numpy()/7
    rows = []
    for r in s.itertuples():
        k = int(r.touch_i_hint)
        if not (288 < k < len(f)) or not ((l[k] <= r.level) if r.side == 1 else (h[k] >= r.level)):
            continue
        mon_end = (pd.Timestamp(r.week).tz_localize('America/New_York')+pd.Timedelta(days=1)).tz_convert('UTC')
        k0 = idx.searchsorted(mon_end)
        seg = (l[k0:k+1] <= r.monday_low) if r.side == 1 else (h[k0:k+1] >= r.monday_high)
        cross = k0+int(np.argmax(seg)) if seg.any() else k
        pos = (c[k0-1]-r.monday_low)/(r.monday_high-r.monday_low)
        ext = l[k] if r.side == 1 else h[k]
        past = r.side*(r.level-ext)
        row = dict(event=f'{symbol}:{r.week}:{int(r.side):+d}', symbol=symbol, week=r.week, side=int(r.side), level=float(r.level),
                   t=idx[k], touch_i=k, end_i=int(r.end_i), target=float(r.target_price), close=float(c[k]),
                   recovery=float(r.side*(c[k]-ext)/past) if past > 0 else np.nan, beyond_pct=float(100*r.side*(r.level-c[k])/r.level),
                   bucket=float(r.bucket), range_pct=float(100*(r.monday_high-r.monday_low)/r.level),
                   log_approach=float(np.log1p(max(0., (idx[k]-idx[cross]).total_seconds()/60))),
                   monday_close_side=float((1-pos) if r.side == 1 else pos), tick=float(tick))
        for tag, i in (('stored', k), ('causal', k-1)):
            row[f'log_vol_ratio_{tag}'] = float(np.log(max(hour[i]/base[i], .05))) if base[i] > 0 else np.nan
            row[f'log_liquidity_{tag}'] = float(np.log10(max(day[i], 1e4)))
            row[f'toward24_{tag}'] = float(-r.side*100*(c[i]/c[i-288]-1))
        rows.append(row)
    return pd.DataFrame(rows), dict(symbol=symbol, status='ok', events=len(rows), last=str(f.index.max()), job=job)


def cluster24(s):
    """Other symbols with a same-side first weekly touch in the prior 24 hours (monday_limit_reclaim_patterns3).
    Epochs in nanoseconds whatever the parsed resolution (pandas 3 in the framework env parses microseconds)."""
    out = pd.Series(0, index=s.index)
    for _, g in s.groupby('side'):
        ts = g.t.dt.as_unit('ns').astype('int64').to_numpy(); order = np.argsort(ts); tsorted = ts[order]; syms = g.symbol.to_numpy()[order]
        lo = np.searchsorted(tsorted, tsorted-86_400_000_000_000, 'left'); hi = np.searchsorted(tsorted, tsorted, 'left')
        out.loc[g.index[order]] = [len(set(syms[a:b])-{syms[i]}) for i, (a, b) in enumerate(zip(lo, hi))]
    return out


def score(s, spec):
    other = s[['symbol', 'week', 'side', 't']].assign(side=-s.side).rename(columns={'t': 'opp_t'})
    s = s.merge(other, on=['symbol', 'week', 'side'], how='left')
    s['opp_first'] = (s.opp_t < s.t).astype(int)
    s['cluster24'] = cluster24(s).astype(float)
    s['side_f'] = s.side.astype(float)
    for tag in ('stored', 'causal'):
        g = s.rename(columns={f'log_vol_ratio_{tag}': 'log_vol_ratio', f'log_liquidity_{tag}': 'log_liquidity',
                              f'toward24_{tag}': 'toward24'}).drop(columns=['side']).rename(columns={'side_f': 'side'})
        X = g[spec['features']].to_numpy(float)
        z = ((X-np.array(spec['mean']))/np.array(spec['scale'])*np.array(spec['coef'])).sum(1)+spec['intercept']
        s[f'score_{tag}'] = 1/(1+np.exp(-z))
    return s


def fills(args):
    """live-wick E20 rows of one symbol's October survivors in NautilusTrader, as monday_limit_close_stop_grid.process."""
    symbol, job, events_, intrabar, exp = args
    from yoyo.evaluation.monday_limit_reclaim import prepare
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native
    out = Path(exp)/intrabar.lower()/'symbols'/symbol
    if (out/'done.json').exists():
        done = json.loads((out/'done.json').read_text())
        if done.get('events') == sorted(events_):
            return done
    t0 = time.perf_counter()
    ccfg = json.loads((CODEX/'config.json').read_text())
    f, q = load_job(job)
    p, e, _, _, tick = prepare(f, job, ccfg, q)
    e = e[(e.reclaim_minutes == 60) & (e.exit_mode == 'near_monday_edge') & (e.population == 'signal')].copy()
    e['event'] = e.trade_id.str.split(':').str[:3].str.join(':')
    x = e[e.event.isin(events_)].copy()
    close = p.close.to_numpy()
    k = x.touch_i_hint.astype(int).to_numpy()
    s_, L, T, c = x.side.to_numpy(float), x.entry.to_numpy(float), x.target_price.to_numpy(float), close[k]
    ok = (s_*(c-L) < tick*(1-1e-7)) & (~np.isfinite(T) | (s_*(T-c) >= tick*(1-1e-7))) & (k < x.end_i.to_numpy())
    x = x[ok].copy()
    stats = dict(symbol=symbol, intrabar=intrabar, listed=len(events_), events=sorted(events_), signal_rows=int(len(x)))
    out.mkdir(parents=True, exist_ok=True)
    if x.empty:
        pd.DataFrame(columns=cr.KEEP+['variant']).to_csv(out/'trades.csv.gz', index=False)
        dump(out/'done.json', dict(stats, orders=0, positions=0)); return dict(stats, orders=0)
    x['reclaim_ref'] = x.entry
    x['entry_i'] = x.touch_i_hint.astype(int)
    x['entry'] = close[x.entry_i.to_numpy()]
    x['stop'] = stops(x, 'P', 20, tick)
    x['reclaim_minutes'] = 60
    x['trade_id'] = x.trade_id+':v_E20'
    x = x[x.side*(x.entry-x.stop) > 0].copy()
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
    rows['variant'] = 'E20'
    rows.to_csv(out/'trades.csv.gz', index=False)
    done = dict(stats, orders=len(x), positions=audit['positions'], fills=audit['fills'], engine=audit.get('engine'),
                version=audit.get('version'), seconds=time.perf_counter()-t0)
    dump(out/'done.json', done)
    return done


def load_fills(args):
    """Worse path per trade with 10bp entry and non-target slippage, as monday_limit_close_reclaim.load_symbol but on the
    extended job (its bar indexes run past the frozen series)."""
    exp, symbol, job = args
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    paths = {}
    for p in ('ohlc', 'olhc'):
        d = Path(exp)/p/'symbols'/symbol
        if not (d/'done.json').exists():
            return pd.DataFrame()
        t = pd.read_csv(d/'trades.csv.gz')
        t['net'] = t.net_notional_bp-10-np.where(t.reason == 4, 0., 10)
        paths[p] = t.set_index('trade_id')
    o = paths['ohlc']
    if o.empty:
        return pd.DataFrame()
    l = paths['olhc'].reindex(o.index)
    w = (l.net < o.net).to_numpy()
    pick = lambda c: np.where(w, l[c], o[c])
    d = pd.DataFrame(dict(side=o.side, entry=o.entry, net=pick('net'), reason=pick('reason'), entry_i=o.entry_i,
                          exit_i=pick('exit_i'), exit_price=pick('exit_price'),
                          exit_t=pd.to_datetime(pd.Series(pick('exit_time'), index=o.index), utc=True)), index=o.index)
    f, _ = load_job(job)
    d['entry_t'] = pd.Series(f.index[d.entry_i.astype(int).to_numpy()]+pd.Timedelta(minutes=job['execution_minutes']), index=d.index)
    h, lo = f.high.to_numpy(), f.low.to_numpy()
    d['mae_pct'] = [max(0., -s*((lo[a:max(a+1, b)].min() if s == 1 else h[a:max(a+1, b)].max())-e)/e*100)
                    for a, b, s, e in zip(d.entry_i.astype(int)+1, d.exit_i.astype(int), d.side, d.entry)]
    d['event'] = d.index.str.split(':').str[:3].str.join(':')
    d['symbol'] = symbol
    d['data_end'] = f.index.max()
    return d.reset_index()


def accounts(config, workers):
    """1000U on the window start for each live-wick account; trades in entry order with their saved facts."""
    from yoyo.evaluation.monday_limit_be_scalein_compare import clean
    from yoyo.evaluation.monday_limit_return_opt import frac
    from yoyo.evaluation.monday_limit_close_order_boost import weighted_frac
    from yoyo.evaluation.strategy_booklet_paths import Trace
    exp = config.resolve().parent
    cfg = json.loads(config.read_text())
    cr.committed('yoyo/evaluation/monday_limit_live_october.py')
    surv = pd.read_csv(exp/'survivors.csv.gz')
    jobs = json.loads((exp/'jobs.json').read_text())
    with ProcessPoolExecutor(workers) as ex:
        got = list(ex.map(load_fills, [(str(exp), s, jobs[s]) for s in sorted(set(surv.symbol))]))
    t = pd.concat([g for g in got if len(g)], ignore_index=True)
    facts = surv.set_index('event')
    t = t[t.event.isin(facts.index)].copy()
    for c in ('score_stored', 'recovery', 'beyond_pct', 'level'):
        t[c] = t.event.map(facts[c])
    t = t.assign(entry_ns=t.entry_t.dt.as_unit('ns').astype('int64'), exit_ns=t.exit_t.dt.as_unit('ns').astype('int64')).sort_values('entry_t').reset_index(drop=True)
    start = pd.Timestamp(cfg['window'][0], tz='UTC').value
    names = {4: '止盈', 11: '60分钟未收回', 5: '保本止损', 1: '−20%止损', 14: '收盘平仓', 0: '数据断点平仓'}
    res = {}
    for name, x, n, w in (('live-wick6x3', t, 6, None), ('live-wick3x3', t, 3, None),
                          ('live-wick6x3hi', t, 6, np.where(t.score_stored >= .76, 1.5, 1.)),
                          ('live-wick3x3far', t[t.beyond_pct >= 1.5].reset_index(drop=True), 3, None)):
        tr = Trace(start)
        r = weighted_frac(x, n, 3, cfg['seed'], w, trace=tr) if w is not None else frac(x, n, 3, cfg['seed'], trace=tr)
        led = pd.DataFrame(tr.rows[1:])
        trades = []
        for q in led.itertuples():
            row = x[x.event == q.trade_id].iloc[0]
            trades.append(dict(symbol=row.symbol, side='多' if row.side > 0 else '空', entry=str(row.entry_t)[:16],
                               exit=str(row.exit_t)[:16], score=round(float(row.score_stored), 4), beyond_pct=round(float(row.beyond_pct), 2),
                               net_pct=round(float(row.net)/100, 2), exit_reason=names.get(int(row.reason), str(int(row.reason))),
                               notional=round(q.notional, 2), pnl=round(q.pnl, 2), equity=round(q.equity, 2)))
        res[name] = dict(candidates=int(len(x)), funded=len(trades), skipped=int(r['skipped']) if 'skipped' in r else None,
                         final=float(r['final_multiple']*1000), max_dd_pct=float(r['max_drawdown_pct']),
                         wins=int(sum(tt['pnl'] > 0 for tt in trades)), trades=trades)
    res['data_end'] = str(t.data_end.max()) if len(t) else None
    res['closed_at_data_break'] = sorted(t.event[t.reason == 0])
    dump(exp/'summary.json', dict(config=cfg, results=clean(res), source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                                 training_eligible=False, production_eligible=False))
    print(json.dumps({k: (v if not isinstance(v, dict) else {kk: vv for kk, vv in v.items() if kk != 'trades'}) for k, v in res.items()}, default=str))


def run(config, workers):
    from yoyo.evaluation.monday_limit_be_scalein_compare import clean
    exp = config.resolve().parent
    cfg = json.loads(config.read_text())
    cr.committed('yoyo/evaluation/monday_limit_live_october.py', 'yoyo/layers/l3_backtest/nautilus_independent.py',
                 str(config.resolve().relative_to(ROOT)))
    syms = json.loads((exp/'symbols.json').read_text())
    path = exp/'events.csv.gz'
    if not path.exists():
        rows, notes = [], []
        with ProcessPoolExecutor(workers) as ex:
            futs = [ex.submit(events, (s, cfg, str(exp))) for s in syms]
            for fu in as_completed(futs):
                try:
                    d, note = fu.result()
                except Exception as err:  # keep going; record the failure per symbol
                    d, note = pd.DataFrame(), dict(status='error', error=repr(err)[:300])
                rows.append(d); notes.append(note)
        pd.concat([d for d in rows if len(d)], ignore_index=True).to_csv(path, index=False)
        dump(exp/'jobs.json', {n['symbol']: n['job'] for n in notes if n.get('job')})
        dump(exp/'events_notes.json', [{k: v for k, v in n.items() if k != 'job'} for n in notes])
    s = pd.read_csv(path, parse_dates=['t'])
    # The backtest universe (monday_limit_reclaim_patterns3 SNAP): later listings are neither traded nor counted in cluster24.
    snap = set(json.loads((ROOT/cfg['universe']).read_text()))
    later = s[~s.symbol.isin(snap)]
    s = s[s.symbol.isin(snap)].reset_index(drop=True)
    spec = json.loads((ROOT/cfg['score_model']).read_text())
    s = score(s, spec)
    # Parity: events that already have a stored / causal score.
    old = pd.read_csv(ROOT/cfg['scores']).set_index('event')
    m = s.set_index('event').join(old[['stored', 'causal']], how='inner')
    m = m[m.t >= pd.Timestamp(cfg['parity_from'], tz='UTC')]
    parity = {}
    for tag, col in (('stored', 'stored'), ('causal', 'causal')):
        d = (m[f'score_{tag}']-m[col]).abs().dropna()
        parity[tag] = dict(n=int(len(d)), median_abs=float(d.median()), p95_abs=float(d.quantile(.95)), max_abs=float(d.max()),
                           agree_05=float(((m[f'score_{tag}'] >= .5) == (m[col] >= .5))[d.index].mean()),
                           agree_06=float(((m[f'score_{tag}'] >= .6) == (m[col] >= .6))[d.index].mean()))
    s.to_csv(exp/'scored.csv.gz', index=False)
    a, b = pd.Timestamp(cfg['window'][0], tz='UTC'), pd.Timestamp(cfg['window'][1], tz='UTC')
    s['entry_t'] = s.t+pd.Timedelta(minutes=5)
    win = s[(s.entry_t >= a) & (s.entry_t < b)].copy()
    rule = lambda y: y[(y.score_stored >= .5) & (y.recovery < EDGE) & (y.beyond_pct > 0) & (y.beyond_pct < 30)]
    surv = rule(win)
    surv.to_csv(exp/'survivors.csv.gz', index=False)
    # Fill parity: earlier survivors that the saved stop grid already filled on the frozen series.
    pre = rule(s[(s.t >= pd.Timestamp(cfg['parity_from'], tz='UTC')) & (s.entry_t < a)])
    pre.to_csv(exp/'parity_survivors.csv.gz', index=False)
    jobs = json.loads((exp/'jobs.json').read_text())
    both = pd.concat([surv, pre])
    for intrabar in ('OHLC', 'OLHC'):
        todo = [(sym, jobs[sym], set(g.event), intrabar, str(exp)) for sym, g in both.groupby('symbol')]
        with ProcessPoolExecutor(workers) as ex:
            for fu in as_completed([ex.submit(fills, j) for j in todo]):
                try:
                    fu.result()
                except Exception as err:
                    print('fill error', repr(err)[:300], flush=True)
    fill_parity = {}
    for p in ('ohlc', 'olhc'):
        new_, ref = [], []
        for sym in sorted(set(pre.symbol)):
            n_ = exp/p/'symbols'/sym/'trades.csv.gz'; r_ = ROOT/cfg['fill_parity']/p/'symbols'/sym/'trades.csv.gz'
            if n_.exists():
                new_.append(pd.read_csv(n_))
            if r_.exists():
                r = pd.read_csv(r_); ref.append(r[(r.variant == 'E20') & r.trade_id.str.contains(':near_monday_edge:')])
        n_, r_ = pd.concat(new_), pd.concat(ref)
        n_ = n_[n_.trade_id.str.split(':').str[:3].str.join(':').isin(set(pre.event))].set_index('trade_id')
        r_ = r_[r_.trade_id.str.split(':').str[:3].str.join(':').isin(set(pre.event))].set_index('trade_id')
        j = n_[['net_notional_bp', 'reason']].join(r_[['net_notional_bp', 'reason']], rsuffix='_ref', how='outer')
        both_ = j.dropna()
        fill_parity[p] = dict(new=int(len(n_)), saved=int(len(r_)), matched=int(len(both_)),
                              same_reason=int((both_.reason == both_.reason_ref).sum()),
                              max_abs_bp=float((both_.net_notional_bp-both_.net_notional_bp_ref).abs().max()) if len(both_) else None,
                              only_new=sorted(j.index[j.net_notional_bp_ref.isna()])[:20],
                              only_saved=sorted(j.index[j.net_notional_bp.isna()])[:20])
    res = dict(parity=parity, fill_parity=fill_parity, later_listings=dict(symbols=int(later.symbol.nunique()), events=int(len(later))), parity_survivors=int(len(pre)), window_events=int(len(win)), survivors=int(len(surv)),
               new_data_last=str(s.t.max()), events_total=int(len(s)))
    dump(exp/'summary_events.json', clean(res))
    print(json.dumps(res, default=str))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, required=True); ap.add_argument('--workers', type=int, default=6)
    ap.add_argument('--accounts', action='store_true')
    a = ap.parse_args()
    accounts(a.config, a.workers) if a.accounts else run(a.config, a.workers)
