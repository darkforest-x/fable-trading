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


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, required=True)
    a = ap.parse_args()
    run(a.config)
