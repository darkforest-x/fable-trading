"""Frozen deterministic XAUUSD candidates for the owner's 2026-09-08 search.

Source: LazyBear 34/9 formula and local Pine V2.2, plus explicit MA, Donchian
and RSI benchmarks. Features use OHLC through the decision bar only; density
uses the preceding 12 bars, memory 34; focus uses prior ATR14 and freezes its
0.1 ATR band after 12 bars. HTF values must close by the local bar OPEN,
matching the conservative Pine confirmation clock. No production imports,
training, volume proxy, synthetic bars, or outcome-dependent parameters.
"""
from __future__ import annotations

from itertools import combinations
import numpy as np
import pandas as pd

from yoyo.evaluation.imacd_indicator_audit import indicator, smma

TIMEFRAMES = (5, 15, 30, 60, 120, 240, 360, 720, 1440)
HIGH = {5: 60, 15: 60, 30: 120, 60: 240, 120: 360,
        240: 1440, 360: 1440, 720: 1440, 1440: 10080}
CANDIDATES = {
    'C00': ('原始金死叉', None, 'cross'),
    'C01': ('离零即入场', 'C00', 'neutral'),
    'C02': ('现有密集启动', 'C01', 'neutral'),
    'C03': ('现有密集共振', 'C02', 'neutral'),
    'F00': ('精确零轴12根', 'C01', 'neutral'),
    'F01': ('零轴12根·反向才退', 'F00', 'opposite'),
    'F02': ('长零轴＋密集', 'F01', 'opposite'),
    'F03': ('长零轴＋密集＋高周期许可', 'F02', 'opposite'),
    'F04': ('长零轴＋严格高周期同向', 'F03', 'opposite'),
    'F05': ('长零轴共振·EMA60退出', 'F03', 'ema60'),
    'N00': ('近零12根释放', None, 'opposite'),
    'N01': ('近零释放＋密集', 'N00', 'opposite'),
    'N02': ('近零释放＋密集＋高周期许可', 'N01', 'opposite'),
    'N03': ('近零共振＋离开均线带', 'N02', 'opposite'),
    'N04': ('近零共振＋蓄势内同向影线回踩', 'N03', 'opposite'),
    'W00': ('蓄势内影线提前入场', 'N02', 'opposite'),
    'T00': ('EMA20/60趋势交叉', None, 'ema_cross'),
    'T01': ('Donchian20突破/10退出', None, 'channel10'),
    'T02': ('Donchian55突破/20退出', 'T01', 'channel20'),
    'T03': ('EMA120趋势＋SMA20影线回踩', None, 'ema60'),
    'R00': ('RSI14均值回归30/70', None, 'rsi50'),
}


def aggregate_gold(minutes: pd.DataFrame, duration: int) -> pd.DataFrame:
    """Observed OHLC only, fixed UTC-5 midnight buckets; Monday weekly open.

    Nominal bucket end is the earliest permitted decision time, even if the
    last observation is earlier because of a closure. Empty buckets are
    absent; observed counts and last quote are retained for coverage audits.
    This is a research calendar, not a claim of OANDA chart parity.
    """
    shifted = minutes.index - pd.Timedelta(hours=5)
    if duration == 10080:
        keys = shifted.normalize() - pd.to_timedelta(shifted.weekday, unit='D')
    else:
        keys = shifted.floor(f'{duration}min')
    keys = keys + pd.Timedelta(hours=5)
    groups = minutes.groupby(keys, sort=True)
    b = groups.agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'})
    b['observed_minutes'] = groups.size()
    b['time_close'] = b.index + pd.Timedelta(minutes=duration)
    b['last_quote'] = pd.Series(minutes.index, index=minutes.index).groupby(keys).max()
    # Do not emit a still-forming final bucket.
    return b.loc[b.time_close <= minutes.index[-1] + pd.Timedelta(minutes=1)]


def _cross(a: pd.Series, b: pd.Series) -> np.ndarray:
    d = a - b
    return np.where((d > 0) & (d.shift() <= 0), 1,
                    np.where((d < 0) & (d.shift() >= 0), -1, 0))


def features(b: pd.DataFrame) -> pd.DataFrame:
    """OHLC windows <=240 local bars; recurrence state contains only the past."""
    f = indicator(b).set_axis(b.index)
    tr = pd.concat([b.high-b.low, (b.high-b.close.shift()).abs(),
                    (b.low-b.close.shift()).abs()], axis=1).max(axis=1)
    f['atr'] = smma(tr, 14)
    for n in (20, 60, 120):
        f[f's{n}'] = b.close.rolling(n).mean()
        f[f'e{n}'] = b.close.ewm(span=n, adjust=False).mean()
    mas = f[['s20','e20','s60','e60','s120','e120']]
    f['rope_hi'], f['rope_lo'] = mas.max(axis=1), mas.min(axis=1)
    f['width'] = (f.rope_hi-f.rope_lo)/f.atr.replace(0, np.nan)
    f.loc[mas.isna().any(axis=1), 'width'] = np.nan
    flips = sum((_cross(mas[a], mas[c]) != 0).astype(int)
                for a, c in combinations(mas.columns, 2))
    f['dense'] = (f.width.shift().rolling(12).mean() <= 3) & (
        pd.Series(flips, index=b.index).shift().rolling(12).sum() >= 2)
    f['dense_recent'] = f.dense.astype(int).rolling(34, min_periods=1).max().eq(1)
    f['ready'] = np.arange(len(b)) >= 340
    md = f.md.to_numpy()
    zero = np.zeros(len(b), dtype=int)
    for i in range(len(b)):
        zero[i] = (zero[i-1]+1 if i else 1) if md[i] == 0 else 0
    prior_zero = np.r_[0, zero[:-1]]
    f['depart1'] = np.where((prior_zero >= 1) & (md != 0), np.sign(md), 0).astype(int)
    f['depart12'] = np.where((prior_zero >= 12) & (md != 0), np.sign(md), 0).astype(int)
    f['cross'] = np.where(f.up, 1, np.where(f.dn, -1, 0))
    # Strict wick only: body stays on the original side; no ATR touch tolerance.
    lower = (b.close.shift() > f.s20.shift()) & (b.low <= f.s20) & (
        b[['open','close']].min(axis=1) >= f.s20) & (b.close > f.s20) & (
        b.low < b[['open','close']].min(axis=1))
    upper = (b.close.shift() < f.s20.shift()) & (b.high >= f.s20) & (
        b[['open','close']].max(axis=1) <= f.s20) & (b.close < f.s20) & (
        b.high > b[['open','close']].max(axis=1))
    f['wick'] = np.where(lower, 1, np.where(upper, -1, 0))
    magnitude = np.maximum(np.abs(md), np.abs(f.sb.to_numpy()))
    bands = .1 * f.atr.shift().to_numpy()
    run, qualified, band, wick_long, wick_short = 0, False, np.nan, False, False
    releases = np.zeros(len(b), dtype=int)
    lengths = np.zeros(len(b), dtype=int)
    qualified_out = np.zeros(len(b), dtype=bool)
    release_wick = np.zeros(len(b), dtype=bool)
    w = f.wick.to_numpy()
    for i in range(340, len(b)):
        if not np.isfinite(bands[i]):
            continue
        if qualified:
            if magnitude[i] <= band:
                run += 1
            else:
                releases[i] = 1 if md[i] > band else -1 if md[i] < -band else 0
                lengths[i] = run
                release_wick[i] = (releases[i] == 1 and wick_long) or (releases[i] == -1 and wick_short)
                run, qualified, band, wick_long, wick_short = 0, False, np.nan, False, False
        elif magnitude[i] <= bands[i]:
            run += 1
            if run >= 12:
                qualified, band = True, bands[i]
        else:
            run = 0
        qualified_out[i] = qualified
        if qualified:
            wick_long |= w[i] == 1
            wick_short |= w[i] == -1
    f['focus_release'], f['focus_length'] = releases, lengths
    f['focus_qualified'], f['focus_wick'] = qualified_out, release_wick
    f['ema_cross'] = _cross(f.e20, f.e60)
    f['ema60_cross'] = _cross(b.close, f.e60)
    for n in (10, 20, 55):
        f[f'channel_hi{n}'] = b.high.shift().rolling(n).max()
        f[f'channel_lo{n}'] = b.low.shift().rolling(n).min()
    delta = b.close.diff().fillna(0)
    gain, loss = smma(delta.clip(lower=0), 14), smma(-delta.clip(upper=0), 14)
    f['rsi'] = np.where(loss == 0, np.where(gain == 0, 50, 100), 100-100/(1+gain/loss))
    relative_atr = f.atr / b.close
    # The volatility stratum ranks t against t-239..t only.
    f['volbin'] = np.ceil(relative_atr.rolling(240, min_periods=120).rank(pct=True)*5).fillna(0).clip(1,5).astype(int)
    f['score'] = f.md.abs()/f.atr.replace(0, np.nan)
    return f


def attach_higher(b: pd.DataFrame, f: pd.DataFrame, hb: pd.DataFrame, hf: pd.DataFrame) -> pd.DataFrame:
    """Latest fully closed high bar at or before local OPEN; 340-bar HTF warmup."""
    out = f.copy()
    idx = np.searchsorted(pd.DatetimeIndex(hb.time_close).asi8, b.index.asi8, side='right')-1
    valid = idx >= 340
    safe = np.maximum(idx, 0)
    for name in ('md', 'sh'):
        out[f'h_{name}'] = np.where(valid, hf[name].to_numpy()[safe], np.nan)
    out['h_known'] = valid
    out['h_source_close'] = pd.to_datetime(np.where(valid, pd.DatetimeIndex(hb.time_close).asi8[safe], np.iinfo(np.int64).min), utc=True)
    return out


def candidate(b: pd.DataFrame, f: pd.DataFrame, name: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return signed entry requests and side-specific close-confirmed exits."""
    if name not in CANDIDATES:
        raise KeyError(name)
    e = f.cross.to_numpy().copy() if name == 'C00' else f.depart1.to_numpy().copy()
    if name == 'C02':
        e *= f.dense.to_numpy()
    if name == 'C03':
        e *= f.dense_recent.to_numpy()
    if name.startswith('F'):
        e = f.depart12.to_numpy().copy()
        if name >= 'F02':
            e *= f.dense_recent.to_numpy()
    if name.startswith('N'):
        e = f.focus_release.to_numpy().copy()
        if name >= 'N01':
            e *= f.dense_recent.to_numpy()
    if name == 'W00':
        e = (f.wick * f.focus_qualified * f.dense_recent).to_numpy()
    if name in ('C03','F03','F04','F05','N02','N03','N04','W00'):
        if name == 'F04':
            allowed = f.h_known & (f.h_md*e > 0)
        else:
            allowed = f.h_known & ((f.h_md*e > 0) | (f.h_sh*e > 0) | (f.h_md == 0))
        e *= allowed.to_numpy()
    if name in ('C03','N03','N04'):
        e *= np.where(e > 0, b.close > f.rope_hi, b.close < f.rope_lo)
    if name == 'N04':
        e *= f.focus_wick.to_numpy()
    if name == 'T00':
        e = f.ema_cross.to_numpy().copy()
    if name in ('T01','T02'):
        n = 20 if name == 'T01' else 55
        e = np.where(b.close > f[f'channel_hi{n}'], 1, np.where(b.close < f[f'channel_lo{n}'], -1, 0))
    if name == 'T03':
        e = f.wick.to_numpy().copy()
        e *= ((b.close-f.e120)*e > 0).to_numpy()
    if name == 'R00':
        e = np.where((f.rsi < 30) & (f.rsi.shift() >= 30), 1,
                     np.where((f.rsi > 70) & (f.rsi.shift() <= 70), -1, 0))
    e = np.where(f.ready, e, 0).astype(np.int8)
    mode = CANDIDATES[name][2]
    if mode == 'cross':
        xl, xs = f.cross < 0, f.cross > 0
    elif mode == 'neutral':
        xl, xs = f.md <= 0, f.md >= 0
    elif mode == 'opposite':
        xl, xs = f.md < 0, f.md > 0
    elif mode == 'ema_cross':
        xl, xs = f.ema_cross < 0, f.ema_cross > 0
    elif mode == 'ema60':
        xl, xs = b.close < f.e60, b.close > f.e60
    elif mode.startswith('channel'):
        n = int(mode[7:])
        xl, xs = b.close < f[f'channel_lo{n}'], b.close > f[f'channel_hi{n}']
    else:
        xl, xs = f.rsi >= 50, f.rsi <= 50
    return e, np.asarray(xl), np.asarray(xs)
