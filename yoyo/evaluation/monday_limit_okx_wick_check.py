"""OKX cross-check of 2026 same-bar flash-wick trades in the score >= 0.6 + EMA order > 0 subset (owner 2026-10-26).

The backtest bars are Binance USDT-M 5m (Codex inputs); trading would be on OKX. For every 2026 trade of the subset
whose worse-path fill and target exit fall in the same 5m bar (near target and opposite 1618 target, -30 percent
backstop; exp-monday-limit-v3-grid / -levels fills), the OKX USDT swap 5m candle of the fill bar is fetched from the
public endpoint GET /api/v5/market/history-candles (read-only market data, no key), saved under inputs/, and
compared with the Binance-derived prices: did the OKX wick reach the entry (low <= entry for longs, high >= entry for
shorts), and did the OKX bar close beyond the target (the same-bar target rule). Levels are not recomputed from OKX
Monday ranges, so this checks the wick depth only. Symbols with a 1000x Binance prefix or no OKX swap are reported
as unmapped. Read-only.
"""
from __future__ import annotations
import argparse
import json
import subprocess
import time
import urllib.request
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation.monday_limit_be_scalein_compare import BAD_EVENTS, B, RUN
from yoyo.evaluation.monday_limit_order_targets import load

EXP = ROOT/'experiments/active/exp-monday-limit-okx-wick-check-20261026-v1'


def okx_bar(inst, open_ms):
    url = f'https://www.okx.com/api/v5/market/history-candles?instId={inst}&bar=5m&after={open_ms+1}&limit=1'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    for attempt in range(3):
        try:
            d = json.load(urllib.request.urlopen(req, timeout=15))
            if d.get('code') == '0':
                return d['data'][0] if d['data'] and int(d['data'][0][0]) == open_ms else None
        except Exception:
            time.sleep(1+attempt)
    return 'error'


def run(config):
    exp = config.resolve().parent
    for p in ('yoyo/evaluation/monday_limit_okx_wick_check.py', str(config.resolve().relative_to(ROOT))):
        if subprocess.run(['git','diff','--quiet','HEAD','--',p], cwd=ROOT).returncode or \
           subprocess.run(['git','ls-files','--error-unmatch',p], cwd=ROOT, capture_output=True).returncode:
            raise RuntimeError('Commit builder and config first')
    cfg = json.loads(config.read_text())
    syms = json.loads((RUN/'config.json').read_text())['symbols']
    with ProcessPoolExecutor(4) as ex:
        m = pd.concat(ex.map(load, [(s, cfg['variants']) for s in syms], chunksize=8), ignore_index=True)
    m['event'] = m.trade_id.str.split(':').str[:3].str.join(':')
    m = m[~m.event.isin(BAD_EVENTS)].copy()
    w = (m.net_l < m.net_o).to_numpy()
    m['net'] = np.where(w, m.net_l, m.net_o); m['reason'] = np.where(w, m.reason_l, m.reason_o)
    m['t'] = pd.to_datetime(m.entry_time_o.fillna(m.entry_time_l), utc=True)
    m['x'] = pd.to_datetime(pd.Series(np.where(w, m.exit_time_l, m.exit_time_o)), utc=True).to_numpy()
    f = pd.read_csv(ROOT/cfg['features'], usecols=['event', 'l2_order_score']).rename(columns={'l2_order_score': 'order'})
    m = m.merge(f, on='event', how='left')
    m = m[(m.order > 0) & (m.t >= B[1]) & (m.x == m.t) & (m.reason == 4)].copy()
    m['symbol'] = m.event.str.split(':').str[0]
    m['bar_open'] = m.t-pd.Timedelta(minutes=5)  # ledger times are bar closes
    (exp/'inputs').mkdir(exist_ok=True)
    cache_path = exp/'inputs'/'okx_5m_fill_bars.json'
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    rows = []
    for r in m.itertuples():
        base = r.symbol.removesuffix('USDT')
        key = f'{base}|{int(r.bar_open.timestamp()*1000)}'
        if base.startswith('1000'):
            rows.append(dict(trade_id=r.trade_id, status='unmapped_1000x')); continue
        if key not in cache:
            cache[key] = okx_bar(f'{base}-USDT-SWAP', int(r.bar_open.timestamp()*1000)); time.sleep(cfg['sleep_s'])
        bar = cache[key]
        if bar in (None, 'error'):
            rows.append(dict(trade_id=r.trade_id, status='no_okx_bar' if bar is None else 'error')); continue
        o, h, l, c = map(float, bar[1:5])
        s = int(r.side)
        reached = (l <= r.entry) if s == 1 else (h >= r.entry)
        closed_beyond = (c >= r.target_price) if s == 1 else (c <= r.target_price)
        depth = (r.entry-l)/r.entry if s == 1 else (h-r.entry)/r.entry
        rows.append(dict(trade_id=r.trade_id, variant=r.variant, side=s, entry=float(r.entry), target=float(r.target_price),
                         okx_open=o, okx_high=h, okx_low=l, okx_close=c, okx_reached=bool(reached), okx_closed_beyond=bool(closed_beyond),
                         okx_beyond_entry_pct=float(100*depth), net_bp=float(r.net), status='checked', bar_open=str(r.bar_open)))
    cache_path.write_text(json.dumps(cache))
    d = pd.DataFrame(rows)
    d.to_csv(exp/'checks.csv', index=False)
    res = {}
    for v in cfg['variants']:
        x = d[d.trade_id.str.endswith(v)]
        ok = x[x.status == 'checked']
        res[v] = dict(trades=int(len(x)), status=x.status.value_counts().to_dict(), checked=int(len(ok)),
                      okx_reached=int(ok.okx_reached.sum()), okx_reached_and_closed_beyond=int((ok.okx_reached & ok.okx_closed_beyond).sum()),
                      net_bp_checked=float(ok.net_bp.sum()), net_bp_okx_both=float(ok.net_bp[ok.okx_reached & ok.okx_closed_beyond].sum()),
                      bar_opens=x.bar_open.value_counts().head(5).to_dict() if 'bar_open' in x else {})
    dump(exp/'summary.json', dict(config=cfg, results=res, source_commit=subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip(),
                                 training_eligible=False, production_eligible=False))
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, default=EXP/'config.json')
    run(ap.parse_args().config)
