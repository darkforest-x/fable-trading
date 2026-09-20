"""Verify frozen source samples against native OKX bars and render the study.

No new signals, cost assumptions, or parameter searches. Six preselected UTC
timestamps per asset include both DST transition dates and source seam dates.
All responses are preserved. Plot reads the immutable run_v1 ledgers only.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

import numpy as np
import pandas as pd
import requests

from yoyo.evaluation.four_hour_range_study import ROOT, EXP, sha, dump, committed_identity, read_source

STAMPS = ['2023-09-20T08:00Z', '2023-12-31T16:00Z', '2024-03-10T07:00Z',
          '2025-11-02T06:00Z', '2026-07-08T18:15Z', '2026-09-18T23:55Z']
URL = 'https://www.okx.com/api/v5/market/history-candles'


def run(output):
    identity = committed_identity([Path(__file__), EXP/'config.json'])
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    cfg = json.loads((EXP/'config.json').read_text())
    run_dir = EXP/'run_v1'
    ledger_identity = {}
    source_checks, diagnostics, frames = [], {}, {}
    session = requests.Session()
    for symbol, spec in cfg['sources'].items():
        frame = read_source(spec)
        for stamp in STAMPS:
            timestamp = pd.Timestamp(stamp)
            params = dict(instId=symbol, bar='5m', after=int(timestamp.timestamp()*1000)+1, limit=1)
            response = session.get(URL, params=params, timeout=30)
            response.raise_for_status()
            payload = response.json()
            raw = output/f'{symbol}_{timestamp.strftime("%Y%m%dT%H%M")}.json'
            dump(raw, payload)
            if payload.get('code') != '0':
                raise ValueError(payload)
            rows = [r for r in payload['data'] if int(r[0]) == int(timestamp.timestamp()*1000)]
            if len(rows) != 1 or rows[0][8] != '1':
                raise ValueError('missing/unconfirmed native check bar')
            prices = np.asarray(rows[0][1:5], dtype=float)
            local = frame.loc[timestamp, ['open','high','low','close']].to_numpy(float)
            if not np.array_equal(local, prices):
                raise ValueError(f'native OHLC mismatch: {symbol} {timestamp}')
            source_checks.append(dict(symbol=symbol, timestamp=timestamp, exact_ohlc=True,
                                      raw_sha256=sha(raw), params=params))
            time.sleep(.15)
        trades_path, curve_path = run_dir/symbol/'trades.csv', run_dir/symbol/'marked_curve.csv.gz'
        ledger_identity[str(trades_path.relative_to(ROOT))] = sha(trades_path)
        ledger_identity[str(curve_path.relative_to(ROOT))] = sha(curve_path)
        tr = pd.read_csv(trades_path, parse_dates=['entry_time','exit_time'])
        curve = pd.read_csv(curve_path, index_col=0, parse_dates=True)
        monthly = tr.assign(month=tr.entry_time.dt.tz_convert('America/New_York').dt.strftime('%Y-%m')).groupby('month').net_r.sum()
        diagnostics[symbol] = dict(tight_risk_below_10bp=int((tr.risk_pct < .001).sum()),
            full_target_but_net_loss=int(((tr.gross_r >= 1.999999) & (tr.net_r <= 0)).sum()),
            positive_months=int((monthly > 0).sum()), months=len(monthly),
            target_inside_fraction=float(tr.target_inside_range.mean()),
            entry_outside_range=int((~tr.entry_inside_range).sum()),
            worst_net_r_trade=tr.loc[tr.net_r.idxmin()].to_dict())
        frames[symbol] = (tr, curve, monthly)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    fig, axes = plt.subplots(2, 2, figsize=(13, 8), constrained_layout=True)
    for col, (symbol, (tr, curve, monthly)) in enumerate(frames.items()):
        ax = axes[0,col]
        ax.plot(tr.exit_time, tr.gross_r.cumsum(), label='Before cost', color='#4477aa')
        ax.plot(curve.index, curve.marked_net_r, label='After 20bp cost', color='#cc6677')
        ax.axhline(0, color='gray', linewidth=.7)
        ax.set(title=symbol, ylabel='Cumulative R (not account return)')
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=6))
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
        ax.tick_params(axis='x', labelsize=9)
        ax.legend()
        ax = axes[1,col]
        ax.bar(np.arange(len(monthly)), monthly, color=np.where(monthly >= 0, '#268c79', '#cc6677'))
        ticks = np.arange(0, len(monthly), 3)
        ax.set_xticks(ticks)
        ax.set_xticklabels(monthly.index[ticks], rotation=45, ha='right', fontsize=8)
        ax.set(title='Monthly results after cost', ylabel='Net R')
        ax.axhline(0, color='gray', linewidth=.7)
    fig.suptitle('NY 00-04 range | 5m return | next open | extreme SL / 2R TP / day-end exit')
    fig.savefig(output/'overview.png', dpi=150)
    plt.close(fig)
    dump(output/'verification.json', dict(builders=identity, ledgers=ledger_identity,
        native_source_checks=source_checks, diagnostics=diagnostics,
        generated_at=pd.Timestamp.now(tz='UTC')))
    print(json.dumps(dict(native_ohlc_exact_matches=len(source_checks), diagnostics=diagnostics), default=str), flush=True)


def finalize(output):
    """Hash the existing delivery without rescoring or rewriting any run."""
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    identity = committed_identity([Path(__file__), EXP/'config.json'])
    paths = list(EXP.rglob('*')) + [ROOT/'analysis/p1_four_hour_range_btc_eth_20260920.md',
        ROOT/'yoyo/evaluation/four_hour_range.py', ROOT/'yoyo/evaluation/four_hour_range_study.py',
        ROOT/'yoyo/data/four_hour_range_source.py', ROOT/'yoyo/evaluation/pine/four_hour_range_v1.pine',
        ROOT/'yoyo/evaluation/pine/four_hour_range_v1_README.md']
    files = {str(p.relative_to(ROOT)): dict(sha256=sha(p), size_bytes=p.stat().st_size)
             for p in sorted(paths) if p.is_file()}
    dump(output, dict(experiment_id=EXP.name, generated_at=pd.Timestamp.now(tz='UTC'),
        builders=identity, files=files, training_eligible=False, production_eligible=False,
        economic_run='run_v1', native_parity=False,
        notion_url='https://app.notion.com/p/3e18856479af8131a2b9d56908ea9397'))
    print(f'Final delivery manifest: {len(files)} files; {sha(output)}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--finalize', action='store_true')
    args = parser.parse_args()
    finalize(args.output) if args.finalize else run(args.output)
