"""Causal alpha factors on a single OHLCV frame. Every function returns a
Series aligned to the frame index; all windows look backward only.

Column contract: open, high, low, close, volume (+ atr_pct, ema* if present).
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def _safe(s: pd.Series) -> pd.Series:
    return s.replace([np.inf, -np.inf], np.nan)


def _ts_rank(s: pd.Series, n: int) -> pd.Series:
    # rank of the last value within its own trailing window (0..1), causal
    return s.rolling(n).apply(lambda x: (x.argsort().argsort()[-1] + 1) / len(x), raw=False)


def alpha_reversal_5(df):
    """Alpha101#1-ish short-term reversal: -1 * (close/close[5] - 1)."""
    return _safe(-(df["close"] / df["close"].shift(5) - 1))


def alpha_mom_20(df):
    """20-bar momentum (close ROC). Cols: close[t-20..t]."""
    return _safe(df["close"] / df["close"].shift(20) - 1)


def alpha_hl_range_pos(df):
    """Where close sits in the trailing 20-bar high-low range (Stoch %K)."""
    lo = df["low"].rolling(20).min()
    hi = df["high"].rolling(20).max()
    return _safe((df["close"] - lo) / (hi - lo).replace(0, np.nan))


def alpha_vol_price_corr(df):
    """GTJA-style: 10-bar corr(close, volume) -- accumulation signature."""
    return _safe(df["close"].rolling(10).corr(df["volume"]))


def alpha_illiq(df):
    """Amihud illiquidity: mean(|ret| / volume) over 20 bars (scaled)."""
    ret = df["close"].pct_change().abs()
    return _safe((ret / df["volume"].replace(0, np.nan)).rolling(20).mean() * 1e6)


def alpha_vol_share(df):
    """Last-bar volume vs its 20-bar mean (thrust)."""
    return _safe(df["volume"] / df["volume"].rolling(20).mean().replace(0, np.nan))


def alpha_close_to_high(df):
    """(high - close)/(high - low): where in the bar it closed (0=at high)."""
    return _safe((df["high"] - df["close"]) / (df["high"] - df["low"]).replace(0, np.nan))


def alpha_updown_vol(df):
    """Up-volume minus down-volume share over 20 bars (buy pressure)."""
    up = np.sign(df["close"].diff()) * df["volume"]
    return _safe(up.rolling(20).sum() / df["volume"].rolling(20).sum().replace(0, np.nan))


def alpha_ret_skew(df):
    """20-bar return skew (tail asymmetry)."""
    return _safe(df["close"].pct_change().rolling(20).skew())


def alpha_vol_of_vol(df):
    """Volatility of volatility: std of 5-bar ATR-like range over 20 bars."""
    rng = (df["high"] - df["low"]) / df["close"].replace(0, np.nan)
    return _safe(rng.rolling(5).mean().rolling(20).std())


def alpha_ts_rank_close(df):
    """Time-series rank of close in trailing 20 bars (0..1)."""
    return _safe(_ts_rank(df["close"], 20))


def alpha_bollinger_pos(df):
    """Close position in 20-bar Bollinger band (z-score of close)."""
    m = df["close"].rolling(20).mean()
    s = df["close"].rolling(20).std().replace(0, np.nan)
    return _safe((df["close"] - m) / s)


def alpha_range_compression(df):
    """5-bar range vs 20-bar range -- squeeze detector (complements dense)."""
    r5 = (df["high"].rolling(5).max() - df["low"].rolling(5).min())
    r20 = (df["high"].rolling(20).max() - df["low"].rolling(20).min()).replace(0, np.nan)
    return _safe(r5 / r20)


def alpha_vwap_dev(df):
    """Deviation of close from 20-bar VWAP."""
    tp = (df["high"] + df["low"] + df["close"]) / 3
    vwap = (tp * df["volume"]).rolling(20).sum() / df["volume"].rolling(20).sum().replace(0, np.nan)
    return _safe(df["close"] / vwap - 1)


def alpha_obv_slope(df):
    """20-bar slope of On-Balance Volume (accumulation vs distribution).

    Cols: close, volume. Window: 20 bars ending at t (causal).
    """
    direction = np.sign(df["close"].diff()).fillna(0.0)
    obv = (direction * df["volume"]).cumsum()
    # raw slope; scale by recent |OBV| level so cross-symbol ranks stay meaningful
    slope = (obv - obv.shift(20)) / 20.0
    scale = obv.abs().rolling(20, min_periods=10).mean().replace(0, np.nan)
    return _safe(slope / scale)


def alpha_vol_dryup(df):
    """Dense-period volume / prior-48-bar mean volume (VSA dry-up).

    Dense window = last 8 bars with ma_spread_pct <= 0.0028 when that column
    exists (from add_indicators); otherwise plain last-8 volume mean.
    Baseline = mean volume of the 48 bars ending 8 bars ago (no overlap).
    Cols: volume [, ma_spread_pct]. Windows: 8 + 48 lookback only.
    """
    vol = df["volume"]
    baseline = vol.shift(8).rolling(48, min_periods=24).mean()
    if "ma_spread_pct" in df.columns:
        dense = (df["ma_spread_pct"] <= 0.0028).astype(float)
        dens_vol = (vol * dense).rolling(8, min_periods=1).sum()
        dens_n = dense.rolling(8, min_periods=1).sum().replace(0, np.nan)
        dense_mean = dens_vol / dens_n
        dense_mean = dense_mean.fillna(vol.rolling(8, min_periods=1).mean())
    else:
        dense_mean = vol.rolling(8, min_periods=1).mean()
    return _safe(dense_mean / baseline.replace(0, np.nan))


def alpha_taker_imbalance(df):
    """Approx buy-pressure: 20-bar mean of (close-low)/(high-low) * volume.

    Proxy for taker buy share when true taker volume is unavailable.
    Cols: high, low, close, volume. Window: 20 bars ending at t.
    """
    hl = (df["high"] - df["low"]).replace(0, np.nan)
    buy_share = (df["close"] - df["low"]) / hl  # 1 = close at high (buy pressure)
    buy_vol = buy_share * df["volume"]
    # normalize by mean volume so level is roughly a share in [0, 1]
    vol_mean = df["volume"].rolling(20, min_periods=10).mean().replace(0, np.nan)
    return _safe(buy_vol.rolling(20, min_periods=10).mean() / vol_mean)


def _ensure_emas(df: pd.DataFrame) -> pd.DataFrame:
    """Return a frame that has ema8..ema200; compute missing ones causally."""
    need = [s for s in (8, 13, 21, 34, 55, 144, 200) if f"ema{s}" not in df.columns]
    if not need:
        return df
    out = df.copy()
    for span in need:
        out[f"ema{span}"] = out["close"].ewm(span=span, adjust=False).mean()
    return out


def alpha_ma_order_score(df):
    """Bullish stack score for six EMAs (8>=13>=21>=34>=55>=144), range 0..5.

    Cols: close (or precomputed ema*). EMA is causal (no lookahead).
    """
    d = _ensure_emas(df)
    pairs = (
        ("ema8", "ema13"),
        ("ema13", "ema21"),
        ("ema21", "ema34"),
        ("ema34", "ema55"),
        ("ema55", "ema144"),
    )
    return sum((d[a] >= d[b]).astype(float) for a, b in pairs)


def alpha_convergence_speed(df):
    """Second difference of MA-bundle width (spread acceleration).

    Negative = spread shrinking faster (convergence accelerating).
    Cols: ma_spread_pct or close+ema*. Windows: two 4-bar first diffs.
    """
    if "ma_spread_pct" in df.columns:
        spread = df["ma_spread_pct"]
    else:
        d = _ensure_emas(df)
        close = d["close"].replace(0, np.nan)
        cluster = d[["ema8", "ema13", "ema21", "ema34", "ema55"]]
        spread = (cluster.max(axis=1) - cluster.min(axis=1)) / close
    d1 = spread - spread.shift(4)
    d2 = d1 - d1.shift(4)
    return _safe(d2)


def alpha_ma_bandwidth_pct(df):
    """Six-EMA bundle width / close (bandwidth of the dense stack).

    Cols: close or ema*. Instantaneous at bar t only.
    """
    d = _ensure_emas(df)
    close = d["close"].replace(0, np.nan)
    mas = d[["ema8", "ema13", "ema21", "ema34", "ema55", "ema144"]]
    return _safe((mas.max(axis=1) - mas.min(axis=1)) / close)


# registry: name -> callable. Add here; screening picks up automatically.
FACTORS = {
    "rev5": alpha_reversal_5, "mom20": alpha_mom_20, "hl_pos": alpha_hl_range_pos,
    "vp_corr": alpha_vol_price_corr, "illiq": alpha_illiq, "vol_share": alpha_vol_share,
    "close_to_high": alpha_close_to_high, "updown_vol": alpha_updown_vol,
    "ret_skew": alpha_ret_skew, "vol_of_vol": alpha_vol_of_vol,
    "ts_rank_close": alpha_ts_rank_close, "boll_pos": alpha_bollinger_pos,
    "range_compress": alpha_range_compression, "vwap_dev": alpha_vwap_dev,
    # H14/H17/H18 volume family
    "obv_slope": alpha_obv_slope,
    "vol_dryup": alpha_vol_dryup,
    "taker_imbalance": alpha_taker_imbalance,
    # H15 dense-quality second-order family
    "ma_order_score": alpha_ma_order_score,
    "convergence_speed": alpha_convergence_speed,
    "ma_bandwidth_pct": alpha_ma_bandwidth_pct,
}
