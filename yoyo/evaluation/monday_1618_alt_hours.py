"""Out-of-sample test of the Beijing04-08 touch effect on Binance alts (method14).

Owner2026-10-23 chose this after exp-monday-1618-timing: on OKX alts
(2025-06..2026-08) first weekly1618 touches whose bar opened04:00-08:00
Beijing held47.5percent. Hypothesis frozen in config before any Binance alt
outcome is read: on Binance USD-M USDT perps other than BTC/ETH, stablecoins
and index products, 2020-01-01..2025-05-31 (entirely before the discovery
window, delisted symbols included from the S3 index):
  1. 04-08 Beijing validity >38.2 with week-cluster bootstrap95 lower >38.2
  2. 04-08 validity above all other touches, within-week permutation p<0.01
  3. 04-08 mean R of the1618 limit trade >0 (SL2.0, TP Monday edge, fee0)
Touches and labels: monday_1618_validity.touches on vision15m klines
(conservative15m ordering). The resulting ledger is also the crypto
primary dataset for the macro-release study (method15).
"""
from __future__ import annotations
import argparse
import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation import monday_1618_validity as v
from yoyo.evaluation.monday_1618_flow import klines
from yoyo.data import binance_um_vision_listing as listing
from yoyo.data.binance_um_vision_fetch import fetch

EXP = ROOT/'experiments/active/exp-monday-1618-alt-hours-20261023-v1'
VISION = 'https://data.binance.vision/'


def universe(cfg):
    keep = []
    for s in listing.symbols():
        if not re.fullmatch(r'[A-Z0-9]+USDT', s):
            continue
        base = s[:-4]
        if s in cfg['exclude_symbols'] or base in cfg['exclude_bases']:
            continue
        keep.append(s)
    return keep


def download(cfg, out):
    syms = universe(cfg)
    with ThreadPoolExecutor(cfg['workers']) as pool:
        keys = dict(zip(syms, pool.map(listing.files, syms)))
    lo, hi = cfg['fetch_start_month'], cfg['fetch_end_month']
    jobs = [('klines15m', s, VISION+k) for s, ks in keys.items() for k in ks
            if lo <= k.rsplit('-15m-', 1)[1][:7] <= hi]
    with ThreadPoolExecutor(cfg['workers']) as pool:
        status = list(pool.map(lambda j: fetch(out, *j), jobs))
    counts = pd.Series([x[2] for x in status]).value_counts().to_dict()
    with_files = sorted({j[1] for j in jobs})
    receipt = dict(listed_symbols=len(syms), symbols_with_window_files=len(with_files), jobs=len(jobs),
                   counts={k: int(c) for k, c in counts.items()}, symbols=with_files)
    dump(out/'fetch_receipt.json', receipt)
    return with_files


def ledger(cfg, symbols, out):
    rows = []
    start, end = pd.Timestamp(cfg['start']), pd.Timestamp(cfg['end'])
    for s in symbols:
        f = klines(out, s)
        if len(f) < 2000:
            continue
        for r in v.touches(f, s[:-4], cfg):
            t = pd.Timestamp(r['touch_time'])
            if start <= t < end:
                rows.append(r)
    data = pd.DataFrame(rows)
    t = pd.DatetimeIndex(pd.to_datetime(data.touch_time, utc=True)).tz_convert('Asia/Shanghai')
    data['hour_bj'] = t.hour
    data['block_bj'] = [f'{4*(h//4):02d}-{4*(h//4)+4:02d}' for h in t.hour]
    return data


def boot_ci(d, mask, rng, draws=2000):
    g = {w: x for w, x in d.assign(m=mask).groupby('week')}
    weeks = list(g)
    vals = []
    for _ in range(draws):
        s = pd.concat([g[w] for w in rng.choice(weeks, len(weeks))])
        x = s[s.m]
        if len(x):
            vals.append(100*x.valid.mean())
    return [float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))]


def within_week_p(d, mask, rng, draws=5000):
    """One-sided: validity gap (in-block minus rest) beats week-preserving shuffles."""
    v_ = d.valid.to_numpy(float); m = np.asarray(mask)
    def gap(mm):
        return v_[mm].mean()-v_[~mm].mean()
    obs = gap(m)
    groups = [np.flatnonzero(d.week.to_numpy() == w) for w in d.week.unique()]
    count = 0
    for _ in range(draws):
        s = m.copy()
        for g in groups:
            s[g] = rng.permutation(s[g])
        count += gap(s) >= obs
    return float((count+1)/(draws+1)), float(100*obs)


def rate(x):
    return dict(n=len(x), valid_pct=float(100*x.valid.mean()) if len(x) else None,
                mean_r=float(x.r.mean()) if len(x) else None)


def run():
    cfg = json.loads((EXP/'config.json').read_text())
    if subprocess.run(['git','diff','--quiet','HEAD','--','yoyo/evaluation/monday_1618_alt_hours.py',
                       'yoyo/data/binance_um_vision_listing.py'], cwd=ROOT).returncode:
        raise RuntimeError('Commit builder first')
    out = EXP/'inputs'
    symbols = download(cfg, out)
    data = ledger(cfg, symbols, out)
    data.to_csv(EXP/'touches_alt.csv.gz', index=False)
    rng = np.random.default_rng(cfg['seed'])
    m = (data.block_bj == '04-08').to_numpy()
    ci = boot_ci(data, m, rng)
    p, gap = within_week_p(data.reset_index(drop=True), m, rng)
    block = rate(data[m])
    passed = bool(block['valid_pct'] > 100/2.618 and ci[0] > 100/2.618 and p < .01 and block['mean_r'] > 0)
    years = {str(y): dict(block_04_08=rate(g[g.block_bj == '04-08']), other=rate(g[g.block_bj != '04-08']))
             for y, g in data.groupby(pd.to_datetime(data.touch_time, utc=True).dt.year)}
    summary = dict(config=cfg, symbols=len(symbols), touches=len(data), base=rate(data),
        primary=dict(block='04-08', **block, week_boot95=ci, gap_vs_other_pp=gap, within_week_p=p),
        blocks={b: rate(g) for b, g in data.groupby('block_bj')}, hours={int(h): rate(g) for h, g in data.groupby('hour_bj')},
        years=years, passed=passed, source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
        training_eligible=False, production_eligible=False)
    dump(EXP/'summary.json', summary)
    print(json.dumps({k: summary[k] for k in ('symbols','touches','base','primary','blocks','passed')}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--config', type=Path, default=EXP/'config.json')
    args = parser.parse_args(); EXP = args.config.resolve().parent; run()
