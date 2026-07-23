"""Rich causal feature pack for owner-side technique disclosure.

Owner critique (2026-07-23): the narrow FEATURE_COLUMNS reuse of add_indicators /
add_features was an artificial ceiling. This module factorizes as much as is
reproducible from local OHLCV alone — no paid feeds, no heavy deps.

All columns are causal (bars <= i only). Box geometry is listed separately and
must never enter a trading rule.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.detection.data import add_mas
from src.judgment.candidates import add_indicators
from src.judgment.features import FEATURE_COLUMNS, add_features

# Disclosure-only (needs a labeled box). Never part of causal trading rules.
BOX_FEATS = ("box_width_bars", "box_height_pct", "box_right_frac")

DENSE_FAST_MAX = 0.0028


def _safe_div(a: pd.Series, b: pd.Series) -> pd.Series:
    return a / b.replace(0, np.nan)


def _run_len(cond: pd.Series) -> pd.Series:
    """Consecutive True bars ending at i (0 when False)."""
    x = cond.fillna(False).astype(int)
    grp = (x == 0).cumsum()
    return x.groupby(grp).cumsum()


def _swing_structure(high: pd.Series, low: pd.Series, close: pd.Series, w: int = 48) -> pd.DataFrame:
    """Coarse HH/HL/LH/LL bias over trailing window vs prior window (causal)."""
    # Mid-point of last w/2 vs previous w/2 inside the trailing w bars.
    half = max(4, w // 2)
    hh = high.rolling(half).max()
    ll = low.rolling(half).min()
    hh_prev = high.shift(half).rolling(half).max()
    ll_prev = low.shift(half).rolling(half).min()
    # +1 higher-high, -1 lower-high; same for lows
    hi_tag = np.sign((hh - hh_prev).fillna(0).to_numpy())
    lo_tag = np.sign((ll - ll_prev).fillna(0).to_numpy())
    # Classic: HH+HL = +2 bull structure; LH+LL = -2 bear
    bias = hi_tag + lo_tag
    out = pd.DataFrame(index=close.index)
    out["struct_hh"] = (hi_tag > 0).astype(float)
    out["struct_hl"] = (lo_tag > 0).astype(float)
    out["struct_lh"] = (hi_tag < 0).astype(float)
    out["struct_ll"] = (lo_tag < 0).astype(float)
    out["struct_bias"] = bias.astype(float)
    return out


def add_rich_features(frame: pd.DataFrame) -> pd.DataFrame:
    """OHLCV → judgment indicators + narrow features + expanded factor pack."""
    # Same base chain as narrow verdict so shared columns stay comparable.
    out = add_features(add_indicators(add_mas(frame)))
    close = out["close"].replace(0, np.nan)
    high = out["high"]
    low = out["low"]
    open_ = out["open"]
    volume = out["volume"].astype(float)
    ret1 = close.pct_change()

    # ---- multi-period MA family (gaps / slopes / crosses / bandwidth) ----
    for p in (5, 10, 20, 60, 120):
        if f"sma{p}" not in out.columns:
            out[f"sma{p}"] = close.rolling(p, min_periods=max(3, p // 2)).mean()
        out[f"close_vs_sma{p}"] = _safe_div(close, out[f"sma{p}"]) - 1

    # Extra EMAs beyond judgment set
    for p in (5, 10, 20, 60, 120):
        col = f"ema{p}"
        if col not in out.columns:
            out[col] = close.ewm(span=p, adjust=False).mean()
        out[f"close_vs_ema{p}"] = _safe_div(close, out[col]) - 1

    out["gap_ema8_21"] = _safe_div(out["ema8"] - out["ema21"], close)
    out["gap_ema21_55"] = _safe_div(out["ema21"] - out["ema55"], close)
    out["gap_ema55_200"] = _safe_div(out["ema55"] - out["ema200"], close)
    out["gap_sma20_60"] = _safe_div(out["sma20"] - out["sma60"], close)
    out["gap_sma60_120"] = _safe_div(out["sma60"] - out["sma120"], close)

    for name, span, lag in (
        ("ema8_slope8", "ema8", 8),
        ("ema21_slope12", "ema21", 12),
        ("ema55_slope24", "ema55", 24),
        ("ema200_slope24", "ema200", 24),
        ("sma60_slope12", "sma60", 12),
    ):
        out[name] = out[span].pct_change(lag)

    out["cross_ema8_21"] = (out["ema8"] > out["ema21"]).astype(float)
    out["cross_ema21_55"] = (out["ema21"] > out["ema55"]).astype(float)
    out["cross_ema55_200"] = (out["ema55"] > out["ema200"]).astype(float)
    # Entanglement: inverse of fast spread (high = packed)
    out["ma_entangle"] = 1.0 / (1.0 + out["fast_spread"].clip(lower=0) * 200.0)
    # Bandwidth around mid of fast cluster
    mid = (out["cluster_max"] + out["cluster_min"]) / 2.0
    out["ma_bandwidth"] = _safe_div(out["cluster_max"] - out["cluster_min"], mid)

    # Bollinger-like width on close (20)
    sma20 = out["sma20"]
    std20 = close.rolling(20, min_periods=10).std()
    out["bb_width20"] = _safe_div(2.0 * std20, sma20)

    # ---- dense / discrete dynamics ----
    fs = out["fast_spread"]
    fl = out["full_spread"]
    out["fast_spread_chg4"] = fs - fs.shift(4)
    out["fast_spread_chg16"] = fs - fs.shift(16)
    out["full_spread_chg8"] = fl - fl.shift(8)
    out["full_spread_chg24"] = fl - fl.shift(24)
    # Min-max position ≈ percentile proxy (avoids slow rolling.apply rank)
    fs_lo = fs.rolling(96, min_periods=48).min()
    fs_hi = fs.rolling(96, min_periods=48).max()
    out["fast_spread_rank96"] = (fs - fs_lo) / (fs_hi - fs_lo).replace(0, np.nan)
    fl_lo = fl.rolling(96, min_periods=48).min()
    fl_hi = fl.rolling(96, min_periods=48).max()
    out["full_spread_rank96"] = (fl - fl_lo) / (fl_hi - fl_lo).replace(0, np.nan)
    expanding = (fs > fs.shift(1)).fillna(False)
    contracting = (fs < fs.shift(1)).fillna(False)
    out["spread_expand_run"] = _run_len(expanding)
    out["spread_contract_run"] = _run_len(contracting)
    dense = (fs <= DENSE_FAST_MAX).fillna(False)
    out["dense_run_len_fast"] = _run_len(dense)
    # Just left dense: was dense recently, now expanding
    out["exit_dense_expand"] = (
        (out["dense_run_len_fast"].shift(1) >= 5) & (out["spread_chg8"] > 0)
    ).astype(float)

    # ---- momentum / trend ----
    for n in (2, 8, 16, 64, 96):
        out[f"ret_{n}"] = close.pct_change(n)
    for n in (12, 48):
        out[f"roc_{n}"] = _safe_div(close - close.shift(n), close.shift(n))

    for w in (24, 48, 96):
        hh = high.rolling(w, min_periods=w // 2).max()
        ll = low.rolling(w, min_periods=w // 2).min()
        out[f"dist_high_{w}"] = _safe_div(close, hh) - 1
        out[f"dist_low_{w}"] = _safe_div(close, ll) - 1
        # Breakout distance vs prior-window extreme (shift 1 = no lookahead)
        out[f"break_up_{w}"] = _safe_div(close, hh.shift(1)) - 1
        out[f"break_dn_{w}"] = _safe_div(close, ll.shift(1)) - 1
        rng = (hh - ll).replace(0, np.nan)
        out[f"range_pos_{w}"] = (close - ll) / rng

    # ---- volatility ----
    prev_c = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_c).abs(), (low - prev_c).abs()], axis=1
    ).max(axis=1)
    out["atr7_pct"] = tr.ewm(alpha=1 / 7, adjust=False).mean() / close
    out["atr28_pct"] = tr.ewm(alpha=1 / 28, adjust=False).mean() / close
    out["atr_ratio_7_28"] = _safe_div(out["atr7_pct"], out["atr28_pct"])
    out["rvol_24"] = ret1.rolling(24, min_periods=12).std()
    out["rvol_96"] = ret1.rolling(96, min_periods=48).std()
    out["rvol_ratio_24_96"] = _safe_div(out["rvol_24"], out["rvol_96"])

    body = (close - open_).abs()
    rng_bar = (high - low).replace(0, np.nan)
    upper_wick = high - pd.concat([open_, close], axis=1).max(axis=1)
    lower_wick = pd.concat([open_, close], axis=1).min(axis=1) - low
    out["body_ratio"] = body / rng_bar
    out["wick_up_ratio"] = upper_wick.clip(lower=0) / rng_bar
    out["wick_dn_ratio"] = lower_wick.clip(lower=0) / rng_bar
    out["body_ratio_mean8"] = out["body_ratio"].rolling(8, min_periods=4).mean()
    out["atr_chg24"] = out["atr_pct"] - out["atr_pct"].shift(24)

    # ---- volume ----
    vol_ma20 = volume.rolling(20, min_periods=5).mean().replace(0, np.nan)
    vol_ma96 = volume.rolling(96, min_periods=20).mean().replace(0, np.nan)
    out["vol_ratio_20"] = volume / vol_ma20
    out["vol_ratio_96"] = volume / vol_ma96
    out["vol_z_24"] = (
        (volume - volume.rolling(24, min_periods=10).mean())
        / volume.rolling(24, min_periods=10).std().replace(0, np.nan)
    )
    # Price-volume divergence approx: corr(ret, Δvol) trailing
    dvol = volume.pct_change().replace([np.inf, -np.inf], np.nan)
    out["pv_corr_24"] = ret1.rolling(24, min_periods=12).corr(dvol)
    # Volume at breakout vs mean
    new_high = close >= high.rolling(24, min_periods=12).max().shift(1)
    out["breakout_vol_ratio"] = np.where(
        new_high.fillna(False),
        (volume / vol_ma20).to_numpy(),
        np.nan,
    )
    out["breakout_vol_ratio"] = (
        pd.Series(out["breakout_vol_ratio"], index=out.index)
        .ffill(limit=3)
        .fillna(out["vol_ratio_20"])
    )
    # Up-bar volume share
    up = (close > open_).astype(float)
    out["up_vol_share_24"] = (
        (volume * up).rolling(24, min_periods=12).sum()
        / volume.rolling(24, min_periods=12).sum().replace(0, np.nan)
    )

    # ---- structure ----
    struct = _swing_structure(high, low, close, w=48)
    for c in struct.columns:
        out[c] = struct[c]
    # Distance to prior swing extremes (96)
    prior_high = high.shift(1).rolling(96, min_periods=48).max()
    prior_low = low.shift(1).rolling(96, min_periods=48).min()
    out["dist_prior_high_96"] = _safe_div(close, prior_high) - 1
    out["dist_prior_low_96"] = _safe_div(close, prior_low) - 1

    # ---- time (UTC from open_time) ----
    ts = pd.to_datetime(out["open_time"], utc=True)
    hour = ts.dt.hour.astype(float)
    dow = ts.dt.dayofweek.astype(float)
    out["hour_sin"] = np.sin(2 * np.pi * hour / 24.0)
    out["hour_cos"] = np.cos(2 * np.pi * hour / 24.0)
    out["dow"] = dow
    out["dow_sin"] = np.sin(2 * np.pi * dow / 7.0)
    out["dow_cos"] = np.cos(2 * np.pi * dow / 7.0)

    # Keep judgment down_order for short-side interpretability
    if "down_order_score" not in out.columns:
        out["down_order_score"] = (
            (out["ema8"] <= out["ema13"]).astype(int)
            + (out["ema13"] <= out["ema21"]).astype(int)
            + (out["ema21"] <= out["ema34"]).astype(int)
            + (out["ema34"] <= out["ema55"]).astype(int)
        )

    return out.replace([np.inf, -np.inf], np.nan)


def rich_feature_columns(featured: pd.DataFrame | None = None) -> list[str]:
    """Market-feature list used for LGBM / causal rules (excludes box + raw OHLC)."""
    # Seed with known narrow set + expansions; if a frame is given, intersect.
    base = list(FEATURE_COLUMNS)
    extras = [
        # MA family
        "close_vs_sma20",
        "close_vs_sma60",
        "close_vs_sma120",
        "close_vs_ema5",
        "close_vs_ema10",
        "close_vs_ema20",
        "close_vs_ema60",
        "close_vs_ema120",
        "gap_ema8_21",
        "gap_ema21_55",
        "gap_ema55_200",
        "gap_sma20_60",
        "gap_sma60_120",
        "ema8_slope8",
        "ema21_slope12",
        "ema55_slope24",
        "ema200_slope24",
        "sma60_slope12",
        "cross_ema8_21",
        "cross_ema21_55",
        "cross_ema55_200",
        "ma_entangle",
        "ma_bandwidth",
        "bb_width20",
        "down_order_score",
        "trend_order_score",
        # dense / discrete
        "fast_spread",
        "full_spread",
        "fast_spread_chg4",
        "fast_spread_chg16",
        "full_spread_chg8",
        "full_spread_chg24",
        "fast_spread_rank96",
        "full_spread_rank96",
        "spread_expand_run",
        "spread_contract_run",
        "dense_run_len_fast",
        "exit_dense_expand",
        # momentum
        "ret_2",
        "ret_8",
        "ret_16",
        "ret_64",
        "ret_96",
        "roc_12",
        "roc_48",
        "dist_high_24",
        "dist_low_24",
        "dist_high_48",
        "dist_low_48",
        "dist_high_96",
        "dist_low_96",
        "break_up_24",
        "break_dn_24",
        "break_up_48",
        "break_dn_48",
        "break_up_96",
        "break_dn_96",
        "range_pos_24",
        "range_pos_48",
        "range_pos_96",
        # vol
        "atr7_pct",
        "atr28_pct",
        "atr_ratio_7_28",
        "rvol_24",
        "rvol_96",
        "rvol_ratio_24_96",
        "body_ratio",
        "wick_up_ratio",
        "wick_dn_ratio",
        "body_ratio_mean8",
        "atr_chg24",
        # volume
        "vol_ratio_20",
        "vol_ratio_96",
        "vol_z_24",
        "pv_corr_24",
        "breakout_vol_ratio",
        "up_vol_share_24",
        # structure
        "struct_hh",
        "struct_hl",
        "struct_lh",
        "struct_ll",
        "struct_bias",
        "dist_prior_high_96",
        "dist_prior_low_96",
        # time
        "hour_sin",
        "hour_cos",
        "dow",
        "dow_sin",
        "dow_cos",
    ]
    cols = list(dict.fromkeys(base + extras))  # preserve order, unique
    if featured is not None:
        cols = [c for c in cols if c in featured.columns]
    return cols


def feature_group(name: str) -> str:
    """Map a feature name to a coarse group for reporting."""
    if name.startswith("box_"):
        return "box_geometry"
    if name in FEATURE_COLUMNS and name.startswith(("ma_spread", "full_", "fast_", "spread_", "dense_")):
        return "dense_spread"
    if name.startswith(
        (
            "gap_",
            "cross_",
            "ema",
            "sma",
            "close_vs_",
            "ma_",
            "bb_",
            "order_",
            "down_order",
            "trend_order",
            "slow_slope",
        )
    ) or name in ("ext_up", "close_vs_ema55", "close_vs_ema200"):
        return "ma_family"
    if name.startswith(("ret_", "roc_", "dist_", "break_", "range_pos", "drawdown", "pre_range", "runup")):
        return "momentum_structure"
    if name.startswith(("atr", "rvol", "body_", "wick_", "vol_ratio", "volume_", "vol_", "pv_", "breakout_vol", "up_vol")):
        if name.startswith(("volume_", "vol_", "pv_", "breakout_vol", "up_vol")):
            return "volume"
        return "volatility"
    if name.startswith(("hour_", "dow")):
        return "time"
    if name.startswith("struct_"):
        return "structure"
    if name.startswith(("spread_", "fast_spread", "full_spread", "dense_", "exit_dense")):
        return "dense_spread"
    return "other"
