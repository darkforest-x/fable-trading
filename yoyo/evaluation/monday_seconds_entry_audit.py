"""Describe first opposite 1s candles in saved 60m limit-reclaim tail losses.

Owner 2026-10-28 asks whether waiting for the first red second can improve a
short entry. This is a price-path audit, not a new execution/P&L backtest.
Select the three worst causal-score shorts since 2026-07-28 before reading
microstructure. Reuse native saved outcomes. Reconstruct traded seconds from
same-exchange Binance UM daily aggTrades, and verify the touch 5m OHLCV against
the original source. A red candle means close < open, known at its end; use
the first subsequent recorded trade only as a price proxy, never a fill.
All chart context is retrospective. No feature/model or live rule changes.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import zipfile

import numpy as np
import pandas as pd
import requests

from yoyo.evaluation.monday_limit_be_scalein_compare import BAD_EVENTS
from yoyo.evaluation.monday_limit_reclaim_inputs import load_job

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT/'experiments/active/exp-monday-seconds-entry-audit-20261028-v1'
BASE = ROOT/'experiments/active/exp-monday-limit-paper-accounts-20261028-v1'
CODEX = ROOT/'experiments/active/exp-monday-limit-reclaim-20261024-v1'
CACHE = ROOT/'data/crypto/market/binance_um_aggtrades_second_entry_20261028'


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def baseline():
    cfg = json.loads((BASE/'config.json').read_text())
    scores = pd.read_csv(ROOT/cfg['scores']).set_index('event').causal
    paths = {}
    for path in ['ohlc', 'olhc']:
        rows = []
        for f in (BASE/path/'symbols').glob('*/trades.csv.gz'):
            d = pd.read_csv(f)
            d = d[(d.variant == 'rest_v2') & d.trade_id.str.contains(':m60:')].copy()
            if d.empty:
                continue
            d['event'] = d.trade_id.str.split(':').str[:3].str.join(':')
            d['score'] = d.event.map(scores)
            d = d[(d.score >= cfg['v2_threshold']) & ~d.event.isin(BAD_EVENTS)]
            d['net'] = d.net_notional_bp-np.where(d.reason == 4, 0, cfg['slippage_bp']['non_target_exit'])
            d['symbol'] = f.parent.name
            rows.append(d)
        paths[path] = pd.concat(rows).set_index('trade_id')
    a, b = paths['ohlc'], paths['olhc'].reindex(paths['ohlc'].index)
    worse = b.net < a.net
    a.loc[worse, b.columns] = b.loc[worse]
    a['net_pct'] = a.net/100
    a['touch_bar_start'] = pd.to_datetime(a.entry_time, utc=True)-pd.Timedelta(minutes=5)
    expected = json.loads((BASE/'summary.json').read_text())['results']['plans']['v2_60|A_rest_causal']['all']
    assert len(a) == expected['n'] and np.isclose(a.net.mean(), expected['mean_bp'])
    return a.reset_index()


def archive(symbol, day):
    url = f'https://data.binance.vision/data/futures/um/daily/aggTrades/{symbol}/{symbol}-aggTrades-{day}.zip'
    path = CACHE/symbol/f'{day}.zip'
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with requests.get(url, stream=True, timeout=(20, 60)) as response:
            response.raise_for_status()
            temporary = path.with_suffix('.part')
            with temporary.open('wb') as stream:
                for chunk in response.iter_content(1024*1024):
                    stream.write(chunk)
            expected = response.headers.get('Content-Length')
            assert expected is None or temporary.stat().st_size == int(expected)
            temporary.replace(path)
    return path, url


def inspect(row):
    symbol = row['symbol']
    start = pd.Timestamp(row['touch_bar_start'])
    day = start.strftime('%Y-%m-%d')
    path, url = archive(symbol, day)
    pieces = []
    lower, upper = int((start-pd.Timedelta(minutes=1)).timestamp()*1000), int((start+pd.Timedelta(minutes=6)).timestamp()*1000)
    with zipfile.ZipFile(path) as zf:
        names = [n for n in zf.namelist() if n.endswith('.csv')]
        assert len(names) == 1
        with zf.open(names[0]) as stream:
            for chunk in pd.read_csv(stream, chunksize=250000):
                ts = chunk.transact_time
                assert ts.is_monotonic_increasing
                keep = (ts >= lower) & (ts < upper)
                if keep.any():
                    pieces.append(chunk.loc[keep])
                if ts.iloc[-1] >= upper:
                    break
    ticks = pd.concat(pieces, ignore_index=True)
    assert ticks.aggregate_trade_id.is_unique
    assert (ticks.aggregate_trade_id.diff().dropna() == 1).all(), 'Missing aggregate trade IDs'
    ticks.index = pd.to_datetime(ticks.transact_time, unit='ms', utc=True)
    price = ticks.price.astype(float)
    sec = price.resample('1s').ohlc()
    sec['volume'] = ticks.quantity.resample('1s').sum()
    sec = sec.dropna(subset=['open'])  # Empty seconds cannot create reversal signals.
    touch_ticks = ticks[(ticks.index >= start) & (ticks.price >= row['entry'])]
    assert len(touch_ticks), 'No tick touches the level in this window'
    touch = touch_ticks.index[0]
    assert touch < start+pd.Timedelta(minutes=5)
    reds = sec[(sec.index >= touch.floor('s')) & (sec.close < sec.open)]
    assert len(reds), 'No first red found within the bounded window'
    red_time, red = reds.index[0], reds.iloc[0]
    known = red_time+pd.Timedelta(seconds=1)
    subsequent = ticks[ticks.index >= known]
    assert len(subsequent)
    proxy_time, proxy = subsequent.index[0], float(subsequent.price.iloc[0])
    job = json.loads((CODEX/'symbols'/symbol/'identity.json').read_text())['job']
    frame, _ = load_job(job)
    original = frame.loc[start]
    matching = ticks[(ticks.index >= start) & (ticks.index < start+pd.Timedelta(minutes=5))]
    observed = np.array([matching.price.iloc[0], matching.price.max(), matching.price.min(), matching.price.iloc[-1], matching.quantity.sum()], float)
    expected = original[['open', 'high', 'low', 'close', 'volume']].to_numpy(float)
    assert np.allclose(observed, expected, rtol=1e-7, atol=1e-10), (symbol, observed, expected)
    directory = EXP/'cases'/symbol
    directory.mkdir(parents=True, exist_ok=True)
    sec.to_csv(directory/'seconds.csv', index_label='utc')
    context = frame.loc[start-pd.Timedelta(minutes=10):pd.Timestamp(row['exit_time'])+pd.Timedelta(minutes=5)]
    context.to_csv(directory/'original-5m-context.csv', index_label='utc')
    result = dict(symbol=symbol, trade_id=row['trade_id'], original_net_pct=row['net_pct'],
                  original_entry=row['entry'], original_exit=row['exit_price'], original_exit_time=row['exit_time'],
                  touch_time=str(touch.tz_convert('Asia/Shanghai')),
                  red_start=str(red_time.tz_convert('Asia/Shanghai')), red_known=str(known.tz_convert('Asia/Shanghai')),
                  red_open=float(red.open), red_close=float(red.close),
                  subsequent_trade_time=str(proxy_time.tz_convert('Asia/Shanghai')), subsequent_trade_price=proxy,
                  wait_seconds=(proxy_time-touch).total_seconds(), entry_price_improvement_pct=(proxy/row['entry']-1)*100,
                  later_window_high=float(ticks.loc[ticks.index >= proxy_time, 'price'].max()),
                  source_url=url, source_path=str(path.relative_to(ROOT)), source_size=path.stat().st_size,
                  ticks_read=len(ticks), touch_5m_ohlcv_parity=True, missing_seconds=int((sec.index[-1]-sec.index[0]).total_seconds()+1-len(sec)),
                  semantics='Subsequent trade is a price proxy; not an executable fill or new strategy P&L.')
    save(directory/'evidence.json', result)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result


def main():
    EXP.mkdir(parents=True, exist_ok=True)
    rows = baseline()
    rows.to_csv(EXP/'baseline-causal-60m.csv.gz', index=False)
    selected = rows[(rows.entry_time >= '2026-07-28') & (rows.side == -1)].nsmallest(3, 'net')
    selected.to_csv(EXP/'selected-cases.csv', index=False)
    with ThreadPoolExecutor(max_workers=3) as executor:
        cases = list(executor.map(inspect, selected.to_dict('records')))
    wen = pd.read_csv(ROOT/'output/seconds-kline-preview-20261028/candles-1s.csv')
    level = 6.564536
    touch = wen.index[wen.high >= level][0]
    red = wen.loc[touch:][wen.loc[touch:]['close'] < wen.loc[touch:]['open']].iloc[0]
    next_row = wen.loc[red.name+1]
    summary = dict(baseline=str(BASE.relative_to(ROOT)), n=len(rows), losses_over_10_pct=int((rows.net_pct <= -10).sum()),
                   short_losses_over_10_pct=int(((rows.net_pct <= -10) & (rows.side == -1)).sum()),
                   loss_definition='Original per-trade net return on entry notional, not account equity.',
                   selection='Three worst causal-score short trades since 2026-07-28; selected on outcome, not a representative test.',
                   cases=cases, wen=dict(level=level, touch_second=wen.loc[touch, 'utc'], red_start=red.utc,
                       red_close=red.close, next_open=next_row.open, next_time=next_row.utc,
                       entry_price_improvement_pct=(next_row.open/level-1)*100, later_high=wen.loc[red.name+1:, 'high'].max()),
                   source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
                   new_backtest=False, training_eligible=False, production_eligible=False)
    save(EXP/'summary.json', summary)


if __name__ == '__main__':
    main()
