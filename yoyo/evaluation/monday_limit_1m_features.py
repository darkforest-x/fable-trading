"""Pre-touch 1m features for every Monday1618 signal event from Binance UM 1m klines (owner 2026-10-28).

Owner: "做 1 分钟模型：下载所有触及前后的币安 1 分钟 K 线，用触及前一分钟为止的特征（含最近几分钟的放量和速度）训练，
2026 年前训练、2026 年至今测试，再在框架里跑策略". This step downloads the touch day and the day before for each event
(data.binance.vision daily 1m archives, cached under the partial-bar study's cache directory; new files gzip-compressed)
and computes features at the close of the last 1m bar before the touch minute (the first 1m bar of the touch 5m bar
that reaches the level). Every input is a completed 1m bar before the touch minute; the touch minute only locates d.
With d that minute and base the median 1m quote volume of the 1440 minutes through d (>= 1000 present):
  lv1 / lv3 / lv5 / lv15 / lv60  log(mean quote volume of the last k minutes / base), ratio clipped at 0.05;
  ln5    log(trades in the last 5 minutes / (5 x median trades per minute over the same 1440));
  imb5   side-aligned taker imbalance of the last 5 minutes: side x (sell - buy) / quote volume (positive = aggressive
         flow toward the level);
  spd1 / spd5 / spd15 / spd60  percent move toward the level over the last k minutes, -side x (c_d / c_(d-k) - 1);
  dist   percent distance of c_d from the level on the trade's side, side x (c_d / level - 1);
  rng5   high-low range of the last 5 minutes, percent of c_d.
Status per event: ok, no_1m_file, short_history or touch_not_in_1m (the 1m bars do not reach the level in the 5m bar).
"""
from __future__ import annotations
import argparse
import gzip
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

URL = 'https://data.binance.vision/data/futures/um/daily/klines/{s}/1m/{s}-1m-{d}.zip'
COLS = ['ts', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote', 'count', 'taker_base', 'taker_quote']
FEATS = ['lv1', 'lv3', 'lv5', 'lv15', 'lv60', 'ln5', 'imb5', 'spd1', 'spd5', 'spd15', 'spd60', 'dist', 'rng5']


def fetch(args):
    """Cached daily 1m klines of one symbol-day (an existing .csv from the earlier study or a new .csv.gz)."""
    symbol, day, cache = args
    old, new = Path(cache)/symbol/f'{day}.csv', Path(cache)/symbol/f'{day}.csv.gz'
    if old.exists() or new.exists():
        return 'cached'
    url = URL.format(s=urllib.parse.quote(symbol), d=day)  # some symbols are not ASCII
    for attempt in range(4):
        try:
            raw = urllib.request.urlopen(url, timeout=60).read()
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
        body = z.read(z.namelist()[0])
    new.parent.mkdir(parents=True, exist_ok=True)
    tmp = new.with_suffix('.tmp')
    tmp.write_bytes(gzip.compress(body))
    tmp.rename(new)
    return 'downloaded'


def read_day(cache, symbol, day):
    for p in (Path(cache)/symbol/f'{day}.csv', Path(cache)/symbol/f'{day}.csv.gz'):
        if p.exists():
            m = pd.read_csv(p, header=None)
            if not str(m.iat[0, 0]).isdigit():
                m = m.iloc[1:]
            m = m.iloc[:, :11].astype(float)
            m.columns = COLS
            if m.ts.iat[0] > 1e14:  # microsecond stamps
                m['ts'] = m.ts//1000
            return m
    return None


def events(symbol, since):
    t = pd.read_csv(SRC/symbol/'trades.csv.gz', usecols=['population', 'reclaim_minutes', 'exit_mode', 'week', 'side', 'entry',
                                                          'touch_interval_open'])
    t = t[(t.population == 'signal') & (t.reclaim_minutes == 60) & (t.exit_mode == 'near_monday_edge') & (t.week >= since)].copy()
    t['event'] = symbol+':'+t.week+':'+np.where(t.side == 1, '+1', '-1')
    t['touch'] = pd.to_datetime(t.touch_interval_open, utc=True)
    return t[['event', 'side', 'entry', 'touch']]


def features(args):
    symbol, since, cache = args
    t = events(symbol, since)
    rows = []
    for r in t.itertuples():
        days = [(r.touch-pd.Timedelta(days=1)).date(), r.touch.date()]
        parts = [read_day(cache, symbol, d) for d in days]
        if parts[1] is None:
            rows.append(dict(event=r.event, status='no_1m_file')); continue
        m = pd.concat([x for x in parts if x is not None]).drop_duplicates('ts').sort_values('ts').reset_index(drop=True)
        ms = int(r.touch.value//1_000_000)
        bar = m[(m.ts >= ms) & (m.ts < ms+300_000)]
        hit = bar[bar.low <= r.entry] if r.side == 1 else bar[bar.high >= r.entry]
        if hit.empty:
            rows.append(dict(event=r.event, status='touch_not_in_1m')); continue
        d = int(hit.index[0])-1
        if d < 1000 or int(m.ts[d]) != int(m.ts[hit.index[0]])-60_000:
            rows.append(dict(event=r.event, status='short_history')); continue
        q, n, c, h, lo = (m[k].to_numpy() for k in ('quote', 'count', 'close', 'high', 'low'))
        win = slice(max(0, d-1439), d+1)
        if d+1-max(0, d-1439) < 1000:
            rows.append(dict(event=r.event, status='short_history')); continue
        base, nb = np.median(q[win]), np.median(n[win])
        s = int(r.side)
        lv = lambda k: float(np.log(max(q[d-k+1:d+1].mean()/base, .05))) if base > 0 else np.nan
        sell = q[d-4:d+1].sum()-m.taker_quote.to_numpy()[d-4:d+1].sum()
        tot = q[d-4:d+1].sum()
        row = dict(event=r.event, status='ok', touch_minute=int((int(m.ts[hit.index[0]])-ms)//60_000),
                   lv1=lv(1), lv3=lv(3), lv5=lv(5), lv15=lv(15), lv60=lv(60),
                   ln5=float(np.log(max(n[d-4:d+1].sum()/(5*nb), .05))) if nb > 0 else np.nan,
                   imb5=float(s*(sell-(tot-sell))/tot) if tot > 0 else 0.,
                   dist=float(s*(c[d]/r.entry-1)*100),
                   rng5=float((h[d-4:d+1].max()-lo[d-4:d+1].min())/c[d]*100))
        for k in (1, 5, 15, 60):
            row[f'spd{k}'] = float(-s*(c[d]/c[d-k]-1)*100)
        rows.append(row)
    return pd.DataFrame(rows)


def run(config):
    exp = config.resolve().parent
    for p in ('yoyo/evaluation/monday_limit_1m_features.py', str(config.resolve().relative_to(ROOT))):
        if subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--', p], cwd=ROOT).returncode or \
           subprocess.run(['git', 'ls-files', '--error-unmatch', p], cwd=ROOT, capture_output=True).returncode:
            raise RuntimeError('Commit builder and config first')
    cfg = json.loads(config.read_text())
    cache = str(ROOT/cfg['cache'])
    syms = sorted(d.name for d in SRC.iterdir() if (d/'trades.csv.gz').exists() and
                  json.loads((d/'identity.json').read_text())['job']['group'] == 'crypto_5m')
    jobs = set()
    for s in syms:
        for r in events(s, cfg['since_week']).itertuples():
            for d in ((r.touch-pd.Timedelta(days=1)).date(), r.touch.date()):
                jobs.add((s, str(d), cache))
    jobs = sorted(jobs)
    print(json.dumps(dict(symbols=len(syms), symbol_days=len(jobs))), flush=True)
    status = {}
    with ThreadPoolExecutor(cfg['download_threads']) as ex:
        for k, st in enumerate(ex.map(fetch, jobs), 1):
            status[st] = status.get(st, 0)+1
            if k % 5000 == 0:
                print(json.dumps(dict(downloaded=k, total=len(jobs), status=status)), flush=True)
    failed = sum(v for k, v in status.items() if k not in ('cached', 'downloaded', 'http_404'))
    if failed > cfg['max_failed_share']*len(jobs):
        # A dropped network turns into missing days; never build features from a partial download.
        raise RuntimeError(f'Download failures {failed} of {len(jobs)}: {status}; rerun to retry')
    with ProcessPoolExecutor(cfg['workers']) as ex:
        x = pd.concat([d for d in ex.map(features, [(s, cfg['since_week'], cache) for s in syms], chunksize=4) if len(d)], ignore_index=True)
    x.to_csv(exp/'features_1m.csv.gz', index=False)
    ok = x[x.status == 'ok']
    res = dict(symbols=len(syms), symbol_days=len(jobs), download=status, events=int(len(x)),
               status={str(k): int(v) for k, v in x.status.value_counts().items()},
               touch_minute={str(k): int(v) for k, v in ok.touch_minute.value_counts().sort_index().items()},
               feature_q={f: {f'q{int(100*q)}': float(ok[f].quantile(q)) for q in (.05, .5, .95)} for f in FEATS})
    dump(exp/'summary.json', dict(config=cfg, results=res, source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                                 training_eligible=False, production_eligible=False))
    print(json.dumps({k: v for k, v in res.items() if k != 'feature_q'}), flush=True)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, required=True)
    run(ap.parse_args().config)
