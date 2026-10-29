"""Describe pre-crash information availability; do not fit an alarm or simulate fills.

Source: the existing 2026-08-22 Binance daily 1m touch-day cache (92 symbols),
plus the six pre-existing major-coin 1m histories. The 92-coin subset was selected
by historical touch-day research and is not an ex-ante market universe. Inputs
are OHLCV through each completed minute only. Availability is minute-open +1m,
before unmeasured feed latency. Later crash outcomes are not feature inputs.
This is one observed market event, not 92 independent validation events.
"""
from pathlib import Path
import json

import pandas as pd

from yoyo.evaluation.fhe_flash_event import ROOT, read_day

OUT = ROOT / 'experiments/active/exp-flash-precursor-20261029-v1'


def annotate(frame, symbol):
    """Minute OHLCV return uses open/close only; time_known is next minute."""
    col = 'open_time' if 'open_time' in frame else 'ts'
    frame = frame.copy()
    frame['minute_open'] = pd.to_datetime(pd.to_numeric(frame[col]), unit='ms', utc=True)
    frame['time_known'] = frame.minute_open + pd.Timedelta(minutes=1)
    frame['symbol'] = symbol
    frame['return_pct'] = 100 * (frame.close / frame.open - 1)
    return frame


def main():
    paths = sorted((ROOT / 'data/crypto/market/kline_binance_um1m_touchdays_20261027').glob('*/2026-08-22.csv*'))
    rows = [annotate(pd.read_csv(p), p.parent.name) for p in paths]
    cached = pd.concat(rows, ignore_index=True)
    cached = cached[cached.volume > 0]
    selected = cached[cached.minute_open.between(pd.Timestamp('2026-08-22 05:00Z'), pd.Timestamp('2026-08-22 05:14Z'))]
    breadth = selected.groupby('time_known').agg(
        symbols=('symbol', 'nunique'), negative=('return_pct', lambda x: int((x < 0).sum())),
        below_half_percent=('return_pct', lambda x: int((x < -.5).sum())),
        below_one_percent=('return_pct', lambda x: int((x < -1).sum())),
        median_return_pct=('return_pct', 'median'))
    breadth.index = breadth.index.tz_convert('Asia/Shanghai')
    cfg = json.loads((ROOT / 'experiments/active/exp-monday-1618-oos-1m-20261023-v1/config.json').read_text())
    majors = pd.concat([annotate(read_day(ROOT / path), symbol + 'USDT')
                        for symbol, path in cfg['series'].items()], ignore_index=True)
    all_rows = pd.concat([selected, majors], ignore_index=True)
    window = all_rows[all_rows.minute_open.between(pd.Timestamp('2026-08-22 05:04Z'), pd.Timestamp('2026-08-22 05:10Z'))]
    panel = window.pivot(index='time_known', columns='symbol', values='return_pct')
    panel.index = panel.index.tz_convert('Asia/Shanghai')
    OUT.mkdir(exist_ok=True)
    breadth.to_csv(OUT / 'cached_subset_breadth.csv')
    panel.to_csv(OUT / 'minute_returns.csv')
    selected[['symbol', 'minute_open', 'time_known', 'open', 'high', 'low', 'close', 'volume', 'return_pct']].to_csv(OUT / 'cached_subset_minutes.csv.gz', index=False)
    summary = dict(
        event_date='2026-08-22', timezone='Asia/Shanghai', cached_symbols=len(paths),
        benchmark_symbols=list(cfg['series']),
        available_1308=breadth.loc['2026-08-22 13:08:00+08:00'].to_dict(),
        available_1309=breadth.loc['2026-08-22 13:09:00+08:00'].to_dict(),
        fhe_first_touch='2026-08-22T13:11:33.608+08:00',
        nominal_seconds_from_1308_to_touch=213.608,
        nominal_seconds_from_1309_to_touch=153.608,
        independent_crash_events=1,
        caution='Selected touch-day cache; not an ex-ante universe. Evidence of time separation, not alarm accuracy, stable lead-lag, executable fills, or profit. Feed/network delays are not measured. No trigger threshold fitted.',
        prior_failures=['exp-monday-1618-book-20261023-v1', 'exp-monday-1618-flow-20261023-v1', 'exp-monday-1618-breadth-gbm-20261023-v1'],
        training_eligible=False, production_eligible=False)
    (OUT / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(breadth.to_string())
    print(panel[[s+'USDT' for s in cfg['series']]].round(3).to_string())


if __name__ == '__main__':
    main()
