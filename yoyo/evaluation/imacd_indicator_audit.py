"""Descriptive audit of the owner's LazyBear Impulse MACD 34/9 Pine v5.

Source: owner's 2026-09-07 message and TradingView qt6xLfLi. Uses only high,
low and close through each closed bar; SMMA(34) has an SMA seed, DEMA(34)
has first-price EMA seeds, signal is SMA(9). No outcome, execution, fit or
parameter search is performed. Existing OKX CSVs are read-only. Missing or
incomplete bars fail closed / restart warmup; bars are never fabricated.
Owner explicitly authorized every date, including >=2026-05-04, in this
conversation. This fixed descriptive configuration consumes holdout once.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'experiments/active/exp-imacd-okx-descriptive-20260907-v1'
WARMUP = 340
END = pd.Timestamp('2026-07-01', tz='UTC')


def smma(x: pd.Series, length: int = 34) -> pd.Series:
    """SMA-seeded Wilder smoothing using current and prior supplied values."""
    a = x.to_numpy(dtype=float)
    out = np.full(len(a), np.nan)
    if len(a) >= length:
        out[length - 1] = a[:length].mean()
        for i in range(length, len(a)):
            out[i] = (out[i - 1] * (length - 1) + a[i]) / length
    return pd.Series(out, index=x.index)


def indicator(b: pd.DataFrame) -> pd.DataFrame:
    """Causal 34/9 indicator for one continuous segment of closed OHLC bars."""
    src = (b.high + b.low + b.close) / 3
    hi, lo = smma(b.high), smma(b.low)
    e1 = src.ewm(span=34, adjust=False).mean()
    mi = 2 * e1 - e1.ewm(span=34, adjust=False).mean()
    # Pine v5 na comparisons take the false arm: startup md is zero.
    md = pd.Series(np.where(mi > hi, mi - hi, np.where(mi < lo, mi - lo, 0.0)), index=b.index)
    sb = md.rolling(9).mean()
    sh = md - sb
    up = (sh > 0) & (sh.shift() <= 0)
    dn = (sh < 0) & (sh.shift() >= 0)
    macd = b.close.ewm(span=12, adjust=False).mean() - b.close.ewm(span=26, adjust=False).mean()
    mh = macd - macd.ewm(span=9, adjust=False).mean()
    m_cross = ((mh > 0) & (mh.shift() <= 0)) | ((mh < 0) & (mh.shift() >= 0))
    return pd.DataFrame(dict(md=md, sb=sb, sh=sh, up=up, dn=dn, macd_cross=m_cross))


def aggregate(b: pd.DataFrame, minutes: int, base_minutes: int) -> pd.DataFrame:
    """Aggregate complete epoch-aligned UTC bars; week starts Monday UTC."""
    if minutes < base_minutes or minutes % base_minutes:
        raise ValueError('cannot downsample to a finer/nonmultiple interval')
    t = b.index
    if minutes == 10080:
        keys = t.normalize() - pd.to_timedelta(t.weekday, unit='D')
    else:
        keys = t.floor(f'{minutes}min')
    groups = b.groupby(keys)
    out = groups.agg(dict(open='first', high='max', low='min', close='last'))
    counts = groups.size()
    return out.loc[counts.eq(minutes // base_minutes)]


def describe(b: pd.DataFrame, minutes: int, start: pd.Timestamp) -> dict:
    """Report signal semantics only; no future-price labels or profitability."""
    segs = b.index.to_series().diff().ne(pd.Timedelta(minutes=minutes)).cumsum()
    frames = []
    for _, g in b.groupby(segs):
        f = indicator(g)
        f['eligible'] = np.arange(len(g)) >= WARMUP
        frames.append(f)
    if not frames:
        return {'status': 'no_complete_bars', 'bars': 0}
    full = pd.concat(frames)
    f = full.loc[full.eligible & (full.index >= start) & (full.index + pd.Timedelta(minutes=minutes) <= END)]
    if f.empty:
        return {'status': 'insufficient_warmup', 'available_bars': len(b), 'bars': 0}
    cross = f.up | f.dn
    n = int(cross.sum())
    opposite = (f.up & (f.md < 0)) | (f.dn & (f.md > 0))
    # Segment-safe signal churn within three bars; final 3 bars censored.
    evaluable = pd.Series(True, index=f.index)
    churn = pd.Series(False, index=f.index)
    for k in (1, 2, 3):
        nxt = f.index + pd.Timedelta(minutes=minutes * k)
        known = pd.Series(nxt.isin(f.index), index=f.index)
        evaluable &= known
        ups = f.up.reindex(nxt, fill_value=False).to_numpy()
        dns = f.dn.reindex(nxt, fill_value=False).to_numpy()
        churn |= (f.up & dns) | (f.dn & ups)
    denom = int((cross & evaluable).sum())
    return dict(status='ok', bars=len(f), start=f.index[0].isoformat(), end_close=(f.index[-1] + pd.Timedelta(minutes=minutes)).isoformat(),
                gap_count=int(segs.nunique()-1), up=int(f.up.sum()), down=int(f.dn.sum()), signals=n,
                zero_bar_pct=float(f.md.eq(0).mean()*100), cross_at_zero=int((cross & f.md.eq(0)).sum()),
                cross_opposite=int(opposite.sum()), cross_aligned=int((cross & ~opposite & f.md.ne(0)).sum()),
                zero_cross_pct=float((cross & f.md.eq(0)).sum()/n*100) if n else None,
                opposite_cross_pct=float(opposite.sum()/n*100) if n else None,
                churn_count=int((cross & evaluable & churn).sum()), churn_eligible=denom,
                churn3_pct=float((cross & evaluable & churn).sum()/denom*100) if denom else None,
                macd_signals=int(f.macd_cross.sum()),
                signals_per_day=n/(len(f)*minutes/1440),
                holdout_bars=int((f.index >= pd.Timestamp('2026-05-04',tz='UTC')).sum()))


def main() -> None:
    results, sources = [], []
    for symbol in ('BTC', 'ETH'):
        for base, glob, start, targets in (
            (1, f'data/kline_fetched/okx_{symbol}_USDT_SWAP_1m_*.csv', '2026-04-21', [1]),
            (3, f'data/kline_fetched/okx_{symbol}_USDT_SWAP_3m_*.csv', '2026-04-01', [3]),
            (5, f'data/kline_fetched/okx_{symbol}_USDT_SWAP_5m_*.csv', '2026-01-01', [5]),
            (15, f'data/kline_deep/okx_{symbol}_USDT_SWAP_15m_*.csv', '2023-01-01', [15,30,60,120,240,360,720,1440,10080]),
        ):
            paths = list(ROOT.glob(glob))
            if not paths:
                results.append(dict(symbol=symbol, minutes=base, status='missing_source', bars=0))
                continue
            if len(paths) != 1:
                raise ValueError(f'ambiguous source {glob}')
            path = paths[0]
            data = path.read_bytes()
            sources.append(dict(path=str(path.relative_to(ROOT)), sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data)))
            b = pd.read_csv(path)
            b.index = pd.DatetimeIndex(pd.to_datetime(b.open_time, utc=True))
            if not b.index.is_monotonic_increasing or b.index.has_duplicates:
                raise ValueError(f'duplicate/nonmonotonic data {path}')
            if not b.index.equals(b.index.floor(f'{base}min')):
                raise ValueError(f'misaligned source {path}')
            b = b.loc[b.index + pd.Timedelta(minutes=base) <= END, ['open','high','low','close']].astype(float)
            if not np.isfinite(b.to_numpy()).all() or (b <= 0).any().any():
                raise ValueError('invalid/nonpositive OHLC')
            if (b.low > b[['open','close']].min(axis=1)).any() or (b.high < b[['open','close']].max(axis=1)).any():
                raise ValueError('invalid OHLC geometry')
            for minutes in targets:
                a = aggregate(b, minutes, base)
                result = describe(a, minutes, pd.Timestamp(start, tz='UTC'))
                results.append(dict(symbol=symbol, minutes=minutes, **result))
    OUT.mkdir(parents=True, exist_ok=True)
    result = dict(schema_version=1, configuration='fixed_34_9_descriptive_v1', warmup_bars=WARMUP,
                  requested_end_exclusive=END.isoformat(), source_commit=subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip(),
                  owner_authorization='2026-09-07: okx交易所就行；每个周期都看；任何时间段数据都可以使用不要有任何限制',
                  holdout_consumption_number=1, training_eligible=False, production_eligible=False,
                  raw_data_written=False, sources=sources, results=results)
    (OUT/'summary.json').write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False)+'\n')
    pd.DataFrame(results).to_csv(OUT/'metrics.csv', index=False)
    print(pd.DataFrame(results).to_string(index=False))


if __name__ == '__main__':
    main()
