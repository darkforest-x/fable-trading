"""Chart projections of observed capital ledgers, without any trade simulation.

Only ledger timestamps, equity, P&L, cash additions, side, notional and original
entry timestamps are used. Returns/drawdowns cover the full observed path;
monthly returns use calendar month-end equity and never interpolate endpoints.
Refilled accounts show monetary trading P&L instead of cash-flow-biased returns.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'experiments/active/exp-strategy-booklet-paths-20261027-v1'
DEST = ROOT / 'yoyo/monitor/static/strategy-booklet'


def histogram(values, edges, labels):
    counts, _ = np.histogram(values, bins=edges)
    return dict(labels=labels, values=counts.astype(int).tolist(), unit='笔')


def project(frame, meta):
    required = ['time_ms', 'equity', 'pnl', 'cashflow', 'stake_budget', 'notional']
    if frame.empty or not np.isfinite(frame[required].to_numpy(float)).all() or not frame.time_ms.is_monotonic_increasing:
        raise ValueError('Incomplete or unordered capital ledger')
    if frame.equity.iloc[0] != 1000 or (frame.equity < -1e-8).any():
        raise ValueError('Invalid initial/ending capital')
    if not np.allclose(frame.equity, 1000 + frame.pnl.cumsum() + frame.cashflow.cumsum(), rtol=1e-10, atol=1e-5):
        raise ValueError('Ledger does not reconcile')
    trades = frame[frame.notional > 0].copy()
    if trades.empty:
        raise ValueError('No funded trades')
    refilled = bool((frame.cashflow != 0).any())
    pnl = frame.pnl.cumsum()
    cash = 1000 + frame.cashflow.cumsum()
    peak = 1000.
    dd = []
    for row in frame.itertuples():
        if row.cashflow:
            peak = row.equity
        peak = max(peak, row.equity)
        dd.append(100*(row.equity/peak-1) if peak else 0.)
    points = [[int(row.time_ms), float(row.equity), float(pnl.iloc[i]) if refilled else float(100*(row.equity/1000-1)),
               float(dd[i]), float(cash.iloc[i])] for i, row in enumerate(frame.itertuples())]
    dates = pd.to_datetime(frame.time_ms, unit='ms', utc=True)
    monthly = []
    previous = 1000.
    for month in pd.period_range(dates.iloc[0].strftime('%Y-%m'), dates.iloc[-1].strftime('%Y-%m'), freq='M'):
        mask = dates.dt.strftime('%Y-%m') == str(month)
        end = float(frame.loc[mask, 'equity'].iloc[-1]) if mask.any() else previous
        value = float(frame.loc[mask, 'pnl'].sum()) if refilled else (100*(end/previous-1) if previous else None)
        monthly.append([str(month), value])
        previous = end
    yearly = []
    for year in sorted({m[:4] for m, _ in monthly}):
        values = [v for m, v in monthly if m.startswith(year) and v is not None]
        yearly.append([year, float(sum(values) if refilled else (np.prod([1+v/100 for v in values])-1)*100)])
    gains = float(trades.loc[trades.pnl > 0, 'pnl'].sum())
    losses = float(-trades.loc[trades.pnl < 0, 'pnl'].sum())
    returns = 100*trades.pnl/trades.notional
    duration = (trades.time_ms-trades.entry_ms)/3_600_000
    if (duration < 0).any():
        raise ValueError('Trade exits before its entry')
    distribution = histogram(returns, [-np.inf,-30,-10,-3,-1,0,1,3,10,30,np.inf], ['≤−30','−30~−10','−10~−3','−3~−1','−1~0','0~1','1~3','3~10','10~30','≥30'])
    holding = histogram(duration, [-np.inf,1,4,12,24,72,np.inf], ['<1h','1–4h','4–12h','12–24h','1–3天','≥3天'])
    sides = []
    for side, title in [(1,'做多'),(-1,'做空')]:
        x = trades[trades.side == side]
        sides.append([title,len(x),float(100*(x.pnl>0).mean()) if len(x) else None,float(x.pnl.sum())])
    recent = [[str(r.symbol), int(r.entry_ms), int(r.time_ms), '多' if r.side == 1 else '空',
               float(r.pnl),float(100*r.pnl/r.notional)] for r in trades.tail(50).iloc[::-1].itertuples()]
    return dict(schema=1,meta=meta,refilled=refilled,points=points,monthly=monthly,yearly=yearly,
                summary=dict(start=1000.,end=float(frame.equity.iloc[-1]),pnl=float(pnl.iloc[-1]),
                             return_pct=None if refilled else float(100*(frame.equity.iloc[-1]/1000-1)),
                             max_drawdown_pct=float(-min(dd)),win_pct=float(100*(trades.pnl>0).mean()),
                             trades=len(trades),profit_factor=gains/losses if losses else None,
                             profit_factor_state='finite' if losses else 'no_losses',cash_added=float(frame.cashflow.sum()),
                             stake_budget=float(frame.stake_budget.sum()),avg_win=gains/max(1,int((trades.pnl>0).sum())),
                             avg_loss=-losses/max(1,int((trades.pnl<0).sum()))),
                distribution=distribution,holding=holding,sides=sides,recent=recent)


def run():
    for path in ['yoyo/evaluation/strategy_booklet_metrics.py']:
        if subprocess.run(['git','diff','--quiet','HEAD','--',path],cwd=ROOT).returncode or subprocess.run(['git','ls-files','--error-unmatch',path],cwd=ROOT,capture_output=True).returncode:
            raise RuntimeError('Commit metrics builder first')
    manifest=json.loads((SOURCE/'manifest.json').read_text())
    index={}
    for track, meta in manifest['accounts'].items():
        data=project(pd.read_csv(SOURCE/f'{track}-ledger.csv.gz'),meta)
        data['source_commit']=manifest['source_commit']
        file=f'{track}-history.json'
        (DEST/file).write_text(json.dumps(data,ensure_ascii=False,separators=(',',':'),allow_nan=False))
        index[track]=dict(file=file,summary=data['summary'])
    (SOURCE/'chart-index.json').write_text(json.dumps(index,ensure_ascii=False,indent=2))
    print(json.dumps(index,ensure_ascii=False,indent=2))


if __name__=='__main__':
    run()
