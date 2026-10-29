"""Score at the instant of the touch and enter at market after 100 / 300 / 1000 ms, last five months (owner 2026-10-29).

Owner: "做秒级成交测试" (Binance trade data, score at the touch instant, market fill after 100 / 300 / 1000 ms, in the
framework). Step 1 (this file's `run`): for touches in the last-five-months window whose resting or touch-bar score
is >= the candidate floor, download the touch day's Binance UM aggTrades (data.binance.vision, public), keep only the
touch bar's trades (plus a short tail) in a cache, and compute:
  t_star   time of the first trade at or through the level inside the touch bar (Codex touch_interval_open);
  instant  the frozen paper-engine score with vol_ratio / liquidity / toward24 from data up to t_star: the touch
           bar's base volume up to and including t_star times the t_star price (the 5m convention qv = volume x
           close), price at t_star for toward24; the other seven features as the frozen score;
  p_L      for L in 100 / 300 / 1000 ms, the first taker trade in the order's direction (buyer-taker for a long)
           at or after t_star + L: the market fill price; flags if the target or the backstop traded in
           (t_star, t_star + L], or no such trade inside the cached window.
Parity: the touch bar's aggTrades quantity must equal the Codex 5m volume, and its high / low the 5m bar's.
Step 2 is a NautilusTrader run of the paper accounts (monday_limit_paper_accounts, resting family) for events whose
instant score clears an account threshold; `summarize` replaces each such fill's entry with p_L (taker fee instead
of maker) on the framework's own exit price, and compares resting on the causal score (paper engine), instant market
entries, both together, and the leaky reference. Read-only arithmetic on framework fills.
"""
from __future__ import annotations
import argparse
import io
import json
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation.monday_limit_reclaim_patterns import SRC
from yoyo.evaluation.monday_limit_be_scalein_compare import BAD_EVENTS, clean
from yoyo.evaluation import monday_limit_reclaim_allexits as ax
from yoyo.evaluation import monday_limit_reclaim_optimize as opt

URL = 'https://data.binance.vision/data/futures/um/daily/aggTrades/{s}/{s}-aggTrades-{d}.zip'
COLS = ['agg_id', 'price', 'qty', 'first_id', 'last_id', 'ts', 'buyer_maker']
LAT = (100, 300, 1000)


def fetch_window(args):
    """Touch-bar trades of one symbol-day for its touch bars, cached as csv.gz; status string."""
    symbol, day, opens, cache = args
    out = Path(cache)/symbol/f'{day}.csv.gz'
    if out.exists():
        return 'cached'
    url = URL.format(s=urllib.parse.quote(symbol), d=day)
    for attempt in range(4):
        try:
            raw = urllib.request.urlopen(url, timeout=120).read()
            break
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return 'http_404'
            err = f'http_{e.code}'
        except Exception as e:
            err = f'error:{type(e).__name__}'
        time.sleep(2*(attempt+1)**2)
    else:
        return err
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        t = pd.read_csv(z.open(z.namelist()[0]), header=None, low_memory=False)
    if not str(t.iat[0, 0]).isdigit():
        t = t.iloc[1:]
    t = t.iloc[:, :7]
    t.columns = COLS
    t['ts'] = t.ts.astype('int64')
    t['price'] = t.price.astype(float); t['qty'] = t.qty.astype(float)
    if t.ts.iat[0] > 1e14:  # microsecond stamps
        t['ts'] = t.ts//1000
    keep = np.zeros(len(t), bool)
    ts = t.ts.to_numpy()
    for o in opens:  # touch bar plus a 10 s tail for fills after the bar
        keep |= (ts >= o) & (ts < o+300_000+10_000)
    t = t[keep][['price', 'qty', 'ts', 'buyer_maker']]
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix('.tmp')
    t.to_csv(tmp, index=False, compression='gzip')
    tmp.rename(out)
    return 'downloaded'


def candidates(cfg):
    sc = pd.read_csv(ROOT/cfg['scores']).set_index('event')
    w0 = pd.Timestamp(json.loads((ROOT/cfg['window_from']).read_text())['results']['window'][0])
    best = sc[['stored', 'causal']].max(axis=1)
    ev = best.index[best >= cfg['candidate_floor']]
    return {e for e in ev if pd.Timestamp(e.split(':')[1], tz='UTC') >= w0-pd.Timedelta(days=7)}, w0


def instant(args):
    """t_star, partial-bar features at t_star and market fill prices after each latency, for one symbol."""
    symbol, events, cache, w0 = args
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    job = json.loads((SRC/symbol/'identity.json').read_text())['job']
    t = pd.read_csv(SRC/symbol/'trades.csv.gz', usecols=['population', 'reclaim_minutes', 'exit_mode', 'week', 'side', 'entry',
                                                          'touch_i_hint', 'touch_interval_open', 'target_price'])
    t = t[(t.population == 'signal') & (t.reclaim_minutes == 60)]
    t['event'] = symbol+':'+t.week+':'+np.where(t.side == 1, '+1', '-1')
    t = t[t.event.isin(events)]
    if t.empty:
        return pd.DataFrame()
    targets = t.pivot_table(index='event', columns='exit_mode', values='target_price', aggfunc='first')
    t = t[t.exit_mode == 'near_monday_edge'].copy()
    t['open'] = pd.to_datetime(t.touch_interval_open, utc=True)
    t = t[t.open+pd.Timedelta(minutes=5) >= w0]
    f, _ = load_job(job)
    v, c = f.volume.to_numpy(), f.close.to_numpy()
    h, lo = f.high.to_numpy(), f.low.to_numpy()
    qv = v*c
    hour = pd.Series(qv).rolling(12, min_periods=12).sum().to_numpy()
    base = pd.Series(hour).rolling(2016, min_periods=1000).median().shift(12).to_numpy()
    day = pd.Series(qv).rolling(2016, min_periods=1000).sum().to_numpy()/7
    rows = []
    for r in t.itertuples():
        k, s, L = int(r.touch_i_hint), int(r.side), float(r.entry)
        o = int(r.open.value//1_000_000)
        p = Path(cache)/symbol/f'{r.open.date()}.csv.gz'
        row = dict(event=r.event, touch_open_ms=o)
        if not p.exists():
            rows.append(dict(row, status='no_trades_file')); continue
        tr = pd.read_csv(p)
        tr = tr.sort_values('ts', kind='mergesort').reset_index(drop=True)
        bar = tr[(tr.ts >= o) & (tr.ts < o+300_000)]
        if bar.empty:
            rows.append(dict(row, status='no_trades_in_bar')); continue
        row.update(qty_vs_5m=float(bar.qty.sum()/v[k]) if v[k] > 0 else np.nan,
                   high_match=bool(np.isclose(bar.price.max(), h[k], rtol=1e-7)), low_match=bool(np.isclose(bar.price.min(), lo[k], rtol=1e-7)))
        hit = bar[(bar.price <= L) if s > 0 else (bar.price >= L)]
        if hit.empty:
            rows.append(dict(row, status='no_touch_in_trades')); continue
        i0 = hit.index[0]  # positional in tr (reset above), inside bar
        ts0, px0 = int(tr.ts[i0]), float(tr.price[i0])
        upto = bar.loc[bar.index <= i0]
        qd = float(upto.qty.sum())*px0
        row.update(status='ok', t_star_ms=ts0, t_star_offset_ms=ts0-o, t_star_price=px0,
                   vol_ratio_i=(hour[k]-qv[k]+qd)/base[k] if base[k] > 0 else np.nan, liquidity_i=day[k]-(qv[k]-qd)/7,
                   toward24_i=-s*100*(px0/c[k-288]-1) if k >= 288 else np.nan)
        after = tr.loc[tr.index > i0]
        taker = after[after.buyer_maker.astype(str).str.lower().isin(['false', '0'])] if s > 0 else \
            after[after.buyer_maker.astype(str).str.lower().isin(['true', '1'])]
        for lat in LAT:
            due = taker[taker.ts >= ts0+lat]
            row[f'p_{lat}'] = float(due.price.iat[0]) if len(due) else np.nan
            row[f'delay_{lat}'] = int(due.ts.iat[0]-ts0) if len(due) else -1
            seen = after[after.ts <= ts0+lat]
            for mode in ('range_0.618', 'opposite_1618', 'near_monday_edge'):
                tp = targets.at[r.event, mode] if mode in targets.columns else np.nan
                row[f'tp_{mode}_before_{lat}'] = bool(np.isfinite(tp) and len(seen) and
                                                      ((seen.price >= tp).any() if s > 0 else (seen.price <= tp).any()))
        rows.append(row)
    return pd.DataFrame(rows)


def committed(*paths):
    for path in paths:
        if subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--', path], cwd=ROOT).returncode or \
           subprocess.run(['git', 'ls-files', '--error-unmatch', path], cwd=ROOT, capture_output=True).returncode:
            raise RuntimeError('Commit builder and config first')


def run(config):
    exp = config.resolve().parent
    cfg = json.loads(config.read_text())
    committed('yoyo/evaluation/monday_limit_tick_entry.py', str(config.resolve().relative_to(ROOT)))
    cache = str(ROOT/cfg['cache'])
    events, w0 = candidates(cfg)
    syms = sorted({e.split(':')[0] for e in events})
    jobs = {}
    for sym in syms:
        t = pd.read_csv(SRC/sym/'trades.csv.gz', usecols=['population', 'reclaim_minutes', 'exit_mode', 'week', 'side', 'touch_interval_open'])
        t = t[(t.population == 'signal') & (t.reclaim_minutes == 60) & (t.exit_mode == 'near_monday_edge')]
        t = t[(sym+':'+t.week+':'+np.where(t.side == 1, '+1', '-1')).isin(events)]
        for o in pd.to_datetime(t.touch_interval_open, utc=True):
            if o+pd.Timedelta(minutes=5) >= w0:
                jobs.setdefault((sym, str(o.date())), []).append(int(o.value//1_000_000))
    print(json.dumps(dict(candidates=len(events), symbols=len(syms), symbol_days=len(jobs))), flush=True)
    status = {}
    with ThreadPoolExecutor(cfg['download_threads']) as ex:
        for k, st in enumerate(ex.map(fetch_window, [(s, d, o, cache) for (s, d), o in sorted(jobs.items())]), 1):
            status[st] = status.get(st, 0)+1
            if k % 200 == 0:
                print(json.dumps(dict(done=k, total=len(jobs), status=status)), flush=True)
    failed = sum(v for k, v in status.items() if k not in ('cached', 'downloaded', 'http_404'))
    if failed > cfg['max_failed_share']*len(jobs):
        raise RuntimeError(f'Download failures {failed} of {len(jobs)}: {status}; rerun to retry')
    with ProcessPoolExecutor(cfg['workers']) as ex:
        x = pd.concat([d for d in ex.map(instant, [(s, {e for e in events if e.startswith(s+':')}, cache, w0) for s in syms]) if len(d)],
                      ignore_index=True).set_index('event')
    # Instant score: the frozen spec with the three bar features at t_star.
    spec = json.loads((ROOT/cfg['score_model']).read_text())
    ev = pd.read_csv(ROOT/cfg['events'])
    ev['event'] = ev.symbol+':'+ev.week+':'+np.where(ev.side_n == 1, '+1', '-1')
    v2 = sorted(d.name for d in ax.V2.iterdir() if (d/'done.json').exists())
    with ProcessPoolExecutor(cfg['workers']) as ex:
        extra = pd.concat(list(ex.map(opt.ledger_extra, v2, chunksize=8)), ignore_index=True)
    extra['event'] = extra.trade_id.str.split(':').str[:3].str.join(':')
    s = ev.set_index('event').join(extra.set_index('event')[['bucket', 'range_pct']], how='inner').join(x, how='inner')
    s['side'] = s.side_n.astype(float); s['log_approach'] = np.log1p(s.approach_min.clip(lower=0))
    s['opp_first'] = (s.opp_first == '是').astype(int)
    s['log_vol_ratio'] = np.log(s.vol_ratio_i.clip(lower=.05)); s['log_liquidity'] = np.log10(s.liquidity_i.clip(lower=1e4))
    s['toward24'] = s.toward24_i
    X = s[spec['features']].to_numpy(float)
    z = ((X-np.array(spec['mean']))/np.array(spec['scale'])*np.array(spec['coef'])).sum(1)+spec['intercept']
    s['instant'] = 1/(1+np.exp(-z))
    prev = pd.read_csv(ROOT/cfg['scores']).set_index('event')
    s = s.join(prev[['stored', 'causal']])
    cols = ['status', 'touch_open_ms', 't_star_ms', 't_star_offset_ms', 't_star_price', 'qty_vs_5m', 'high_match', 'low_match',
            'instant', 'stored', 'causal']+[c for c in x.columns if c.startswith(('p_', 'delay_', 'tp_'))]
    out = s[[c for c in cols if c in s]]
    out.reset_index().to_csv(exp/'tick_events.csv.gz', index=False)
    # Scores for the framework run: resting rows only where the instant score clears an account threshold.
    pd.DataFrame(dict(event=out.index, causal=out.instant, stored=np.nan)).to_csv(exp/'scores_instant.csv.gz', index=False)
    ok = out[out.status == 'ok']
    res = clean(dict(candidates=len(events), symbol_days=len(jobs), download=status,
                     status={str(k): int(v) for k, v in out.status.value_counts().items()},
                     parity=dict(qty_vs_5m_q={f'q{int(100*q)}': float(ok.qty_vs_5m.quantile(q)) for q in (.01, .5, .99)},
                                 high_match=float(ok.high_match.mean()), low_match=float(ok.low_match.mean())),
                     t_star_offset_s_q={f'q{int(100*q)}': float(ok.t_star_offset_ms.quantile(q)/1000) for q in (.1, .5, .9)},
                     instant_ge={str(c): int((ok.instant >= c).sum()) for c in (.2262, .5, .6)},
                     stored_ge={str(c): int((ok.stored >= c).sum()) for c in (.2262, .5, .6)},
                     causal_ge={str(c): int((ok.causal >= c).sum()) for c in (.2262, .5, .6)}))
    dump(exp/'summary_step1.json', dict(config=cfg, results=res, source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()))
    print(json.dumps(res, ensure_ascii=False), flush=True)


def load_paths(args):
    """Both intrabar paths of one symbol's framework rows with exit prices (for the entry replacement)."""
    run_dir, symbol = args
    out = []
    for p in ('ohlc', 'olhc'):
        f = Path(run_dir)/p/'symbols'/symbol/'trades.csv.gz'
        if f.exists():
            t = pd.read_csv(f, usecols=['trade_id', 'variant', 'side', 'entry', 'exit_price', 'reason', 'net_notional_bp'])
            out.append(t.assign(path=p))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def plans_for(g, col, cfg):
    """The paper accounts' selections (as monday_limit_paper_accounts.summarize) on rows scored by `col`."""
    from yoyo.evaluation.monday_limit_paper_accounts import V3MODE
    sz, cut, hi = cfg['v3_sizing'], cfg['cut'], cfg['hi']
    sc = g[col]
    lin = lambda y: np.minimum(sz['fraction']*(y[col]-sz['cut'])/sz['mean_excess'], sz['cap'])
    v3sel = lambda kind: g[(g.kind == kind) & (sc >= cut) & (g['mode'] == V3MODE(sc, hi))]
    v2 = g[(g.kind == 'v2') & (sc >= cfg['v2_threshold']) & (g.minutes == 'm60')].assign(k=cfg['v2_fraction'])
    v3 = v3sel('v3').assign(k=lambda y: lin(y))
    legs = g[(g.kind == 'v3_add') & g.base.isin(v3.trade_id) & (sc >= hi)]
    legs = legs.assign(k=legs.base.map(v3.set_index('trade_id').k))
    return {'v2_60': ('fixed', v2), 'v3': ('linear', v3), 'v3add': ('linear', pd.concat([v3, legs])),
            'v3add20': ('frac', pd.concat([v3, legs])), 'v3lock': ('linear', v3sel('v3lock').assign(k=lambda y: lin(y))),
            'spike': ('slots', g[(g.kind == 'spike') & (sc >= hi) & (g.order >= cfg['min_order'])])}


def summarize(config, workers):
    """Step 3: resting (causal score) vs instant market entries after each latency vs both vs the leaky reference."""
    from yoyo.evaluation.monday_limit_paper_accounts import load_symbol, replay, scores as acct_scores
    exp = config.resolve().parent
    cfg = json.loads(config.read_text())
    committed('yoyo/evaluation/monday_limit_tick_entry.py')
    acfg = json.loads((ROOT/cfg['accounts_config']).read_text())
    tcfg = json.loads((ROOT/cfg['tick_accounts_config']).read_text())
    ro = json.loads((ROOT/'experiments/active/exp-monday-limit-return-opt-20261027-v2/config.json').read_text())
    sh = json.loads((ROOT/'experiments/active/exp-monday-limit-spike-hw-20261026-v1/config.json').read_text())
    py = json.loads((ROOT/'experiments/active/exp-monday-limit-pyramid-20261027-v1/compare.json').read_text())
    ticks = pd.read_csv(exp/'tick_events.csv.gz').set_index('event')
    _, w0 = candidates(cfg)
    order = pd.read_csv(ROOT/acfg['features'], usecols=['event', 'l2_order_score']).set_index('event').l2_order_score
    trun, arun = ROOT/cfg['tick_accounts_run'], ROOT/cfg['accounts_run']
    syms = sorted({e.split(':')[0] for e in ticks.index})
    with ProcessPoolExecutor(workers) as ex:
        tk = [g for g in ex.map(load_symbol, [(str(trun), s, 10., 10.) for s in syms], chunksize=4)]
        ac = [g for g in ex.map(load_symbol, [(str(arun), s, 10., 10.) for s in syms], chunksize=4)]
        raw = pd.concat([d for d in ex.map(load_paths, [(str(trun), s) for s in syms], chunksize=4) if len(d)], ignore_index=True)
    issues = [f'{g[0]}: {i}' for g in tk+ac for i in g[2]]
    tk = pd.concat([g[1] for g in tk if len(g[1])], ignore_index=True)
    ac = pd.concat([g[1] for g in ac if len(g[1])], ignore_index=True)
    for d in (tk, ac):
        d['family'] = d.variant.str.split('_').str[0]; d['kind'] = d.variant.str.split('_', n=1).str[1]
        d['order'] = d.event.map(order)
    tk = tk[~tk.event.isin(BAD_EVENTS) & (tk.entry_t >= w0)].copy()
    ac = ac[~ac.event.isin(BAD_EVENTS) & (ac.entry_t >= w0)].copy()
    pre, full = acct_scores(acfg)
    ac['pre'] = ac.event.map(pre); ac['full'] = ac.event.map(full)
    tk['instant'] = tk.event.map(ticks.instant)
    # Fee parity of the replacement formula on the framework's own limit fills.
    raw['event'] = raw.trade_id.str.split(':').str[:3].str.join(':')
    raw['mode'] = raw.trade_id.str.split(':').str[4]
    raw['add_leg'] = raw.variant.str.endswith('_add')
    # Commission per fill on its own notional, expressed per entry notional (the framework's normalization).
    fee_exit = np.where(raw.reason == 4, cfg['maker_fee_bp'], cfg['taker_fee_bp'])*raw.exit_price/raw.entry
    rep0 = raw.side*(raw.exit_price/raw.entry-1)*1e4-cfg['maker_fee_bp']-fee_exit
    parity = dict(max_abs_bp=float((rep0-raw.net_notional_bp).abs().max()), p99_abs_bp=float((rep0-raw.net_notional_bp).abs().quantile(.99)))
    res = dict(checks=dict(issues=issues[:20], tick_rows=int(len(tk)), account_rows=int(len(ac)), fee_formula_parity=parity,
                           tick_status={str(k): int(v) for k, v in ticks.status.value_counts().items()}), latency={}, plans={})
    for lat in cfg['latencies_ms']:
        r = raw[~raw.add_leg].copy()
        p = r.event.map(ticks[f'p_{lat}'])
        tp_before = np.zeros(len(r), bool)
        for mode in ('range_0.618', 'opposite_1618', 'near_monday_edge'):
            col = f'tp_{mode}_before_{lat}'
            if col in ticks:
                tp_before |= (r['mode'] == mode).to_numpy() & r.event.map(ticks[col]).fillna(False).astype(bool).to_numpy()
        fee_exit = np.where(r.reason == 4, cfg['maker_fee_bp'], cfg['taker_fee_bp'])*r.exit_price/p
        r['net_l'] = r.side*(r.exit_price/p-1)*1e4-cfg['taker_fee_bp']-fee_exit-np.where(r.reason == 4, 0., cfg['slippage_bp'])
        r['shift_bp'] = r.side*(p/r.entry-1)*1e4
        r['valid'] = p.notna() & ~tp_before
        worse = r[r.valid].sort_values('net_l').drop_duplicates('trade_id')[['trade_id', 'net_l', 'shift_bp']]
        res['latency'][str(lat)] = dict(entries=int(r.valid.sum()//2), no_fill_or_target_first=int((~r.valid).sum()//2),
                                        entry_shift_bp_q={f'q{int(100*q)}': float(worse.shift_bp.quantile(q)) for q in (.1, .25, .5, .75, .9)})
        tl = tk.merge(worse, on='trade_id', how='left')
        tl = tl[tl.variant.str.endswith('_add') | tl.net_l.notna()].copy()
        legs = tl.variant.str.endswith('_add')
        tl.loc[~legs, 'mae_pct'] = np.maximum(0., tl.loc[~legs, 'mae_pct']+tl.loc[~legs, 'shift_bp']/100)
        tl.loc[~legs, 'net'] = tl.loc[~legs, 'net_l']
        tl = tl[~legs | tl.base.isin(tl.trade_id)]  # an add leg needs its parent's market entry
        for acct, (sizing, x) in plans_for(tl, 'instant', tcfg).items():
            res['plans'].setdefault(acct, {})[f'instant_market_{lat}ms'] = (sizing, x)
            a = plans_for(ac[ac.family == 'rest'], 'pre', acfg)[acct][1]
            both = pd.concat([a, x[~x.event.isin(a.event)]])
            res['plans'][acct][f'resting_plus_instant_{lat}ms'] = (sizing, both)
    for acct, (sizing, x) in plans_for(tk, 'instant', tcfg).items():
        res['plans'][acct]['instant_at_level_ideal'] = (sizing, x)
    for acct, (sizing, x) in plans_for(ac[ac.family == 'rest'], 'pre', acfg).items():
        res['plans'][acct]['resting_causal'] = (sizing, x)
    for acct, (sizing, x) in plans_for(ac[ac.family == 'rest'], 'full', acfg).items():
        res['plans'][acct]['leaky_reference'] = (sizing, x)
    jobs = [(f'{acct}|{k}', s, x.reset_index(drop=True), w0, acfg, ro, sh, py) for acct, d in res['plans'].items() for k, (s, x) in d.items()]
    res['plans'] = {}
    with ProcessPoolExecutor(workers) as ex:
        for name, out in ex.map(replay, jobs):
            acct, k = name.split('|')
            res['plans'].setdefault(acct, {})[k] = out['last5m']
    res = clean(res)
    dump(exp/'summary.json', dict(config=cfg, results=res, source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                                 training_eligible=False, production_eligible=False))
    print(json.dumps(res['checks'], ensure_ascii=False))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, required=True); ap.add_argument('--workers', type=int, default=6)
    ap.add_argument('--summary', action='store_true')
    a = ap.parse_args()
    summarize(a.config, a.workers) if a.summary else run(a.config)
