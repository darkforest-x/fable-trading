"""Decide inside the touch bar: score at minute 3 / 4 of the 5m bar, then rest the limit (owner 2026-10-27).

Owner: "最后一根5mink线 在第3 4分钟的时候判断呗  是我们要的就还是挂单  测试一下？有多少能继续挂到的？有多少失去了的？".
The stored entry score reads the full touch bar (exp-monday-limit-score-leak-audit-20261027-v1). Live, a program can
recompute vol_ratio, liquidity and toward24 after D minutes of the current 5m bar from Binance UM 1m bars and only
then rest the limit. A fill survives if price reaches the level at or after minute D (or the order already rested
because the score at the previous 5m close cleared the threshold); a touch before minute D is lost.

Events: the v3 parents (stored score >= 0.5, v3_be0 fills of exp-monday-limit-be-scalein, worse path, 10bp slippage,
BAD events dropped, as monday_limit_return_opt). For each, Binance data.binance.vision daily 1m klines of the touch
day (cached under data/, not in git) give the touch minute (first 1m bar of the touch 5m bar reaching the limit) and
partial-bar features at D = 1..4: quote volume = summed 1m base volume x close at minute D (the 5m definition volume x
close), close at minute D; all other features as stored. D = 5 rebuilds the full bar from 1m and must reproduce the
stored score (parity); 1m high/low must match the 5m bar. Outcome = the existing v3 fill (same limit, same price), so
no new trading engine. Not measured: orders placed at minute D on levels that are never touched in that bar, and
events outside the v3 set whose partial score clears the threshold (they have no v3 fills).
"""
from __future__ import annotations
import argparse
import io
import json
import subprocess
import urllib.request
import zipfile
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation.monday_limit_reclaim_patterns import SRC
from yoyo.evaluation.monday_limit_be_scalein_compare import BAD_EVENTS, RUN, clean
from yoyo.evaluation import monday_limit_reclaim_allexits as ax
from yoyo.evaluation import monday_limit_reclaim_optimize as opt
from yoyo.evaluation import monday_limit_spike_opt as so

EXP = ROOT/'experiments/active/exp-monday-limit-partial-bar-score-20261027-v1'
URL = 'https://data.binance.vision/data/futures/um/daily/klines/{s}/1m/{s}-1m-{d}.zip'
LCOLS = ['population', 'reclaim_minutes', 'exit_mode', 'week', 'side', 'touch_i_hint', 'entry']


def fetch(args):
    """Cached daily 1m klines of one symbol-day; None when the archive has no file."""
    symbol, day, cache = args
    out = Path(cache)/symbol/f'{day}.csv'
    if out.exists():
        return str(out)
    try:
        raw = urllib.request.urlopen(URL.format(s=symbol, d=day), timeout=60).read()
    except Exception as e:
        return f'error:{type(e).__name__}:{e}'
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        body = z.read(z.namelist()[0]).decode()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body)
    return str(out)


def minute_features(args):
    """Touch minute and partial-bar features of one symbol's events from its 5m bars and cached 1m days."""
    symbol, events, cache = args
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    job = json.loads((SRC/symbol/'identity.json').read_text())['job']
    t = pd.read_csv(SRC/symbol/'trades.csv.gz', usecols=LCOLS)
    t = t[(t.population == 'signal') & (t.reclaim_minutes == 60) & (t.exit_mode == 'near_monday_edge')]
    t['event'] = symbol+':'+t.week+':'+np.where(t.side == 1, '+1', '-1')
    t = t[t.event.isin(events)]
    f, _ = load_job(job)
    o, h, l, c, v = (f[k].to_numpy() for k in ('open', 'high', 'low', 'close', 'volume'))
    qv = v*c; idx = f.index
    hour = pd.Series(qv).rolling(12, min_periods=12).sum().to_numpy()
    base = pd.Series(hour).rolling(2016, min_periods=1000).median().shift(12).to_numpy()
    day = pd.Series(qv).rolling(2016, min_periods=1000).sum().to_numpy()/7
    rows = []
    for r in t.itertuples():
        k = int(r.touch_i_hint); t0 = idx[k]
        p = Path(cache)/symbol/f'{t0.date()}.csv'
        row = dict(event=r.event, dist_prev=r.side*(c[k-1]-r.entry)/c[k-1]*100)
        if not p.exists():
            rows.append(dict(row, status='no_1m_file')); continue
        m = pd.read_csv(p)
        if not str(m.iloc[0, 0]).isdigit():
            m = pd.read_csv(p, header=None)
        m = m.iloc[:, :6].astype(float); m.columns = ['ts', 'open', 'high', 'low', 'close', 'volume']
        if m.ts.iat[0] > 1e14:  # microsecond stamps
            m['ts'] = m.ts//1000
        ms = int(t0.value//1_000_000)
        b = m[(m.ts >= ms) & (m.ts < ms+300_000)].sort_values('ts')
        if len(b) != 5:
            rows.append(dict(row, status=f'1m_rows_{len(b)}')); continue
        hit = (b.low.to_numpy() <= r.entry) if r.side == 1 else (b.high.to_numpy() >= r.entry)
        if not hit.any():
            rows.append(dict(row, status='no_touch_in_1m')); continue
        row.update(status='ok', touch_minute=int(np.argmax(hit)),
                   hl_match=bool(np.isclose(b.high.max(), h[k], rtol=1e-7) and np.isclose(b.low.min(), l[k], rtol=1e-7)),
                   vol_match=bool(np.isclose(b.volume.sum(), v[k], rtol=1e-6)))
        for D in range(1, 6):
            cd = b.close.iat[D-1]; qd = b.volume.iloc[:D].sum()*cd
            row[f'vol_ratio_{D}'] = (hour[k]-qv[k]+qd)/base[k] if base[k] > 0 else np.nan
            row[f'liquidity_{D}'] = day[k]-(qv[k]-qd)/7
            row[f'toward24_{D}'] = -r.side*100*(cd/c[k-288]-1) if k >= 288 else np.nan
        rows.append(row)
    return pd.DataFrame(rows)


def run(config):
    exp = config.resolve().parent
    for p in ('yoyo/evaluation/monday_limit_partial_bar_score.py', str(config.resolve().relative_to(ROOT))):
        if subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--', p], cwd=ROOT).returncode or \
           subprocess.run(['git', 'ls-files', '--error-unmatch', p], cwd=ROOT, capture_output=True).returncode:
            raise RuntimeError('Commit builder and config first')
    cfg = json.loads(config.read_text())
    spec = json.loads((ROOT/cfg['score_model']).read_text())
    cache = ROOT/cfg['cache']
    sc = pd.read_csv(ROOT/cfg['causal_scores']).set_index('event')
    tn = pd.read_csv(ROOT/cfg['trades'], usecols=['event', 'variant'])
    events = set(tn[tn.variant == 'v3_be17'].event)-BAD_EVENTS
    syms = sorted({e.split(':')[0] for e in events})
    with ProcessPoolExecutor(cfg['workers']) as ex:
        legs = pd.concat([d for d in ex.map(so.legs, [(s, {RUN.name: ['v3_be0']}, events) for s in syms], chunksize=4) if len(d)],
                         ignore_index=True).set_index('event')
    touch_day = (legs.entry_t-pd.Timedelta(minutes=5)).dt.strftime('%Y-%m-%d')
    jobs = sorted({(e.split(':')[0], d, str(cache)) for e, d in touch_day.items()})
    with ThreadPoolExecutor(cfg['download_threads']) as ex:
        got = list(ex.map(fetch, jobs))
    errors = [(j[0], j[1], g) for j, g in zip(jobs, got) if g.startswith('error:')]
    with ProcessPoolExecutor(cfg['workers']) as ex:
        mf = pd.concat([d for d in ex.map(minute_features, [(s, set(legs.index[legs.index.str.startswith(s+':')]), str(cache)) for s in syms],
                                          chunksize=4) if len(d)], ignore_index=True).set_index('event')
    ev = pd.read_csv(ROOT/cfg['events'])
    ev['event'] = ev.symbol+':'+ev.week+':'+np.where(ev.side_n == 1, '+1', '-1')
    v2 = sorted(d.name for d in ax.V2.iterdir() if (d/'done.json').exists())
    with ProcessPoolExecutor(cfg['workers']) as ex:
        extra = pd.concat(list(ex.map(opt.ledger_extra, v2, chunksize=8)), ignore_index=True)
    extra['event'] = extra.trade_id.str.split(':').str[:3].str.join(':')
    s = ev.set_index('event').join(extra.set_index('event')[['bucket', 'range_pct']], how='inner')
    s = s.loc[s.index.intersection(mf.index)].join(mf)
    s['side'] = s.side_n.astype(float); s['log_approach'] = np.log1p(s.approach_min.clip(lower=0))
    s['opp_first'] = (s.opp_first == '是').astype(int)
    for D in range(1, 6):
        g = s.copy()
        g['log_vol_ratio'] = np.log(g[f'vol_ratio_{D}'].clip(lower=.05)); g['log_liquidity'] = np.log10(g[f'liquidity_{D}'].clip(lower=1e4))
        g['toward24'] = g[f'toward24_{D}']
        X = g[spec['features']].to_numpy(float)
        z = ((X-np.array(spec['mean']))/np.array(spec['scale'])*np.array(spec['coef'])).sum(1)+spec['intercept']
        s[f'score_{D}'] = 1/(1+np.exp(-z))
    s = s.join(sc[['stored', 'causal']]).join(legs[['net', 'entry_t']])
    ok = s[s.status == 'ok']
    thr = cfg['min_score']
    window = pd.Timestamp(json.loads((ROOT/cfg['window_from']).read_text())['results']['window'][0])
    stat = lambda g: dict(n=int(len(g)), mean_bp=float(g.net.mean()) if len(g) else None, win_pct=float(100*(g.net > 1).mean()) if len(g) else None,
                          sum_bp=float(g.net.sum()))
    res = dict(v3_parents=int(len(legs)), download=dict(files=len(jobs), errors=len(errors), error_examples=errors[:5]),
               status=s.status.value_counts().astype(int).to_dict(),
               parity=dict(hl_match=float(ok.hl_match.mean()), vol_match=float(ok.vol_match.mean()),
                           full_bar_vs_stored_abs_q={f'q{int(100*q)}': float((ok.score_5-ok.stored).abs().quantile(q)) for q in (.5, .9, .99, 1.)}),
               touch_minute=ok.touch_minute.value_counts().sort_index().astype(int).to_dict(),
               touch_minute_far=ok[ok.dist_prev > 3].touch_minute.value_counts().sort_index().astype(int).to_dict(),
               touch_minute_near=ok[ok.dist_prev <= 3].touch_minute.value_counts().sort_index().astype(int).to_dict(),
               decide={}, last5m={})
    for name, x in (('all', ok), ('last5m', ok[ok.entry_t >= window])):
        out = {}
        for D in cfg['decide_minutes']:
            resting = x.causal >= thr
            in_time = x.touch_minute >= D
            passes = x[f'score_{D}'] >= thr
            keep = resting | (in_time & passes)
            out[f'minute_{D}'] = dict(already_resting=int(resting.sum()), placed_in_time_and_passes=int((~resting & in_time & passes).sum()),
                                      lost_touched_before=int((~resting & ~in_time).sum()), lost_score_below=int((~resting & in_time & ~passes).sum()),
                                      kept=stat(x[keep]), lost=stat(x[~keep]),
                                      passes_among_in_time=float(passes[in_time].mean()) if in_time.any() else None)
        out['previous_close_only'] = dict(kept=stat(x[x.causal >= thr]), lost=stat(x[x.causal < thr]))
        out['all_v3'] = stat(x)
        res['decide' if name == 'all' else 'last5m'] = out
    s.reset_index()[['event', 'status', 'touch_minute', 'dist_prev', 'stored', 'causal', *[f'score_{D}' for D in range(1, 6)], 'net']].to_csv(
        exp/'events.csv.gz', index=False)
    res = clean(res)
    dump(exp/'summary.json', dict(config=cfg, results=res, source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                                 training_eligible=False, production_eligible=False))
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, default=EXP/'config.json')
    run(ap.parse_args().config)
