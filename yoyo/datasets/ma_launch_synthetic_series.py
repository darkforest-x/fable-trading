"""Deterministic synthetic OHLC series for reviewing MA-launch chart styling.

These cases are deliberately invented price paths.  They supply a long, varied
pre-launch consolidation followed by a clean upward release so a renderer can
derive SMA/EMA overlays from genuine ``close`` values.  They do not contain
market data and cannot serve as return, model-quality, or generalization
evidence.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


ONSET_I = 900
_BASE_PRICE = 100.0
_FAMILIES: tuple[tuple[str, str], ...] = (
    ("quiet_range", "安静横盘后均线密集向上启动"),
    ("pullback_convergence", "回落修复后均线收拢向上启动"),
    ("small_wave_range", "小波浪横盘后均线收拢向上启动"),
    ("volatility_compression", "波动逐步收缩后均线收拢向上启动"),
    ("shallow_prelaunch_dip", "启动前轻微下探后均线收拢向上启动"),
)


def _smoothstep(progress: np.ndarray) -> np.ndarray:
    """Return a smooth 0..1 ramp without a mechanically straight trend."""

    clipped = np.clip(progress, 0.0, 1.0)
    return clipped * clipped * (3.0 - 2.0 * clipped)


def _correlated_noise(
    rng: np.random.Generator, size: int, scale: float, persistence: float = 0.72
) -> np.ndarray:
    """Make modest, serially related log-price noise for candle-to-candle texture."""

    shocks = rng.normal(0.0, scale, size=size)
    output = np.zeros(size, dtype=float)
    for index in range(1, size):
        output[index] = persistence * output[index - 1] + shocks[index]
    return output


def _prelaunch_log_path(
    case_id: int, pre_bars: int, rng: np.random.Generator
) -> np.ndarray:
    """Build the final consolidation, with one distinct morphology per family."""

    family_index = (case_id - 1) // 4
    x = np.linspace(0.0, 1.0, pre_bars, endpoint=False)
    phase = 0.37 * case_id
    # Every family ends calm.  The last 20 bars are additionally tapered so
    # SMA/EMA 20/60/120 converge from observed closes instead of injected MAs.
    quiet = _correlated_noise(rng, pre_bars, 0.00055 + 0.00005 * (case_id % 3))
    taper = 0.30 + 0.70 * (1.0 - _smoothstep(np.clip((x - 0.68) / 0.32, 0, 1)))

    if family_index == 0:
        shape = 0.0065 * np.sin(2.0 * np.pi * (1.25 * x + phase))
        shape += 0.0020 * np.sin(2.0 * np.pi * (4.2 * x + phase))
    elif family_index == 1:
        # A contained pullback early in the setup, then a gradual repair.
        pullback = -0.017 * np.exp(-((x - 0.30) / 0.19) ** 2)
        repair = 0.005 * _smoothstep(np.clip((x - 0.46) / 0.42, 0, 1))
        shape = pullback + repair + 0.0032 * np.sin(2.0 * np.pi * (2.3 * x + phase))
    elif family_index == 2:
        amplitude = 0.012 * (1.0 - 0.46 * x)
        shape = amplitude * np.sin(2.0 * np.pi * (3.0 * x + phase))
        shape += 0.0022 * np.sin(2.0 * np.pi * (7.3 * x + phase / 2.0))
    elif family_index == 3:
        amplitude = 0.018 - 0.014 * x
        shape = amplitude * np.sin(2.0 * np.pi * (2.0 * x + phase))
        shape += 0.0030 * np.sin(2.0 * np.pi * (6.0 * x + phase))
    else:
        # A late, shallow washout that resolves before the defined onset.
        late_dip = -0.0135 * np.exp(-((x - 0.78) / 0.105) ** 2)
        recovery = 0.0055 * _smoothstep(np.clip((x - 0.82) / 0.18, 0, 1))
        shape = late_dip + recovery + 0.0040 * np.sin(2.0 * np.pi * (1.7 * x + phase))

    path = shape + quiet * taper
    # A common endpoint makes the 120-bar MA cohort meet at the launch area.
    path -= np.mean(path[-18:])
    return path


def _postlaunch_log_path(
    case_id: int, post_bars: int, rng: np.random.Generator
) -> np.ndarray:
    """Create a stepped 12--25% upward release with pauses and occasional red bars."""

    x = np.linspace(0.0, 1.0, post_bars + 1)
    # Work in log-price space while keeping the displayed close-to-close move
    # in the requested 12--25 percent range.
    gain = np.log1p(0.13 + 0.114 * ((case_id - 1) / 19.0))
    # Two acceleration phases and a short consolidation keep the result from
    # looking like a straight, translated line across all twenty examples.
    release = gain * (0.10 * (1.0 - np.exp(-35.0 * x))
                      + 0.40 * _smoothstep(x / 0.45)
                      + 0.50 * _smoothstep((x - 0.17) / 0.83))
    pause_center = 0.41 + 0.045 * (case_id % 4)
    pause = -0.011 * np.exp(-((x - pause_center) / 0.075) ** 2)
    wave = 0.0032 * np.sin(2.0 * np.pi * ((2.0 + (case_id % 3) * 0.35) * x + case_id * 0.19))
    texture = _correlated_noise(rng, post_bars + 1, 0.00085, persistence=0.38)
    path = release + pause + wave + texture
    path -= path[0]
    return path


def _build_ohlcv(close: np.ndarray, rng: np.random.Generator) -> pd.DataFrame:
    """Derive internally coherent open/high/low/volume from synthetic closes."""

    count = len(close)
    opens = np.empty(count, dtype=float)
    opens[0] = close[0] * (1.0 + rng.normal(0.0, 0.00045))
    overnight = rng.normal(0.0, 0.00065, size=count - 1)
    opens[1:] = close[:-1] * (1.0 + overnight)
    body = np.abs(close - opens) / np.maximum(opens, 1e-12)
    wick = rng.uniform(0.0010, 0.0040, size=count) + 0.42 * body
    high = np.maximum(opens, close) * (1.0 + wick * rng.uniform(0.48, 1.0, size=count))
    low = np.minimum(opens, close) * (1.0 - wick * rng.uniform(0.48, 1.0, size=count))
    returns = np.abs(np.diff(np.log(close), prepend=np.log(close[0])))
    volume = 900.0 * (1.0 + 95.0 * returns + rng.uniform(0.0, 0.22, size=count))
    # ``ts`` is an artificial millisecond index, not a real-world timestamp.
    ts = np.arange(count, dtype=np.int64) * np.int64(60_000)
    return pd.DataFrame(
        {"ts": ts, "open": opens, "high": high, "low": low, "close": close, "volume": volume}
    )


def _ma_band_pct(close: pd.Series, end_i: int) -> float:
    """Measure SMA/EMA20/60/120 density at a completed internal bar."""

    values: list[float] = []
    for span in (20, 60, 120):
        values.append(float(close.rolling(span, min_periods=span).mean().iloc[end_i]))
        values.append(float(close.ewm(span=span, adjust=False, min_periods=span).mean().iloc[end_i]))
    return 100.0 * (max(values) - min(values)) / float(close.iloc[end_i])


def generate_case(case_id: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Return one deterministic, invented LONG MA-launch OHLC case.

    The first 900 bars are synthetic warmup.  The last 120--170 bars before
    ``onset_i`` form a family-specific consolidation; 35--55 subsequent bars
    release upward with small pullbacks, candle wicks, and non-linear pacing.
    The series exists only to confirm chart presentation.  It is neither real
    market history nor evidence for trading returns, detection quality, or
    generalization.
    """

    if not isinstance(case_id, int) or isinstance(case_id, bool) or not 1 <= case_id <= 20:
        raise ValueError("case_id must be an integer from 1 through 20")

    seed = 20_260_921 + case_id * 7_919
    rng = np.random.default_rng(seed)
    family, label_zh = _FAMILIES[(case_id - 1) // 4]
    pre_bars = 120 + ((case_id * 17) % 51)
    post_bars = 35 + ((case_id * 11) % 21)

    # The older warmup has a quiet, non-flat texture and is intentionally not
    # presented as the labelled formation.  It gives EMA120 a natural history.
    warmup_count = ONSET_I - pre_bars
    warmup_noise = _correlated_noise(rng, warmup_count, 0.00085, persistence=0.84)
    warmup_cycle = 0.007 * np.sin(np.linspace(0.0, 10.0 * np.pi, warmup_count) + case_id)
    warmup = warmup_noise + warmup_cycle
    warmup -= warmup[-1]

    prelaunch = _prelaunch_log_path(case_id, pre_bars, rng)
    postlaunch = _postlaunch_log_path(case_id, post_bars, rng)
    # Join the regimes at the actual preceding close; resetting to 100 would
    # create an artificial gap unrelated to the intended release morphology.
    postlaunch += prelaunch[-1]
    log_close = np.concatenate((warmup, prelaunch, postlaunch))
    close = _BASE_PRICE * np.exp(log_close)
    df = _build_ohlcv(close, rng)

    launch_band_pct = _ma_band_pct(df["close"], ONSET_I - 1)
    pre_range_pct = 100.0 * (
        df["high"].iloc[ONSET_I - pre_bars : ONSET_I].max()
        / df["low"].iloc[ONSET_I - pre_bars : ONSET_I].min()
        - 1.0
    )
    post_gain_pct = 100.0 * (df["close"].iloc[-1] / df["close"].iloc[ONSET_I] - 1.0)
    metadata: dict[str, Any] = {
        "seed": seed,
        "onset_i": ONSET_I,
        "pre_bars": pre_bars,
        "post_bars": post_bars,
        "family": family,
        "label_zh": label_zh,
        "direction": "LONG",
        "ts_is_synthetic_index": True,
        "launch_ma_band_pct": launch_band_pct,
        "pre_range_pct": pre_range_pct,
        "post_gain_pct": post_gain_pct,
    }
    return df, metadata


def validate_all_cases() -> list[dict[str, Any]]:
    """Check determinism, OHLC invariants, density, and release for all 20 cases."""

    results: list[dict[str, Any]] = []
    for case_id in range(1, 21):
        first, metadata = generate_case(case_id)
        second, repeated_metadata = generate_case(case_id)
        deterministic = first.equals(second) and metadata == repeated_metadata
        ohlc_valid = bool(
            (first["high"] >= first[["open", "close"]].max(axis=1)).all()
            and (first["low"] <= first[["open", "close"]].min(axis=1)).all()
            and (first[["open", "high", "low", "close", "volume"]] > 0).all().all()
            and first["ts"].is_monotonic_increasing
        )
        results.append(
            {
                "case_id": case_id,
                "deterministic": deterministic,
                "ohlc_valid": ohlc_valid,
                "ma_converged": metadata["launch_ma_band_pct"] < 1.5,
                "post_release": 12.0 <= metadata["post_gain_pct"] <= 25.5,
                **metadata,
            }
        )
    return results
