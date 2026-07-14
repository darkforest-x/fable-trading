"""Beginner-friendly explore payload: candles + EMAs + dense-MA boxes.

Uses the same dense-run rules as auto_label (fast/full spread) so boxes match
what YOLO was taught, without requiring GPU inference for interactive browse.

Dense-segment math is inlined here (no import of detection/render) so the
dashboard VPS does not need opencv/cv2.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from fastapi import HTTPException

from src.data.loader import load_series
from src.judgment.candidates import ALL_MAS, CLUSTER_EMAS, add_indicators
from src.webapp.dashboard_payloads import series_groups
from src.webapp.dashboard_cache import DEFAULT_UNIVERSE, universe_spec

# Keep in sync with src.detection.auto_label defaults
FAST_SPREAD_MAX = 0.0028
FULL_SPREAD_MAX = 0.0055
MIN_DENSE_BARS = 5
MERGE_GAP_BARS = 2
MAX_DENSE_BARS = 12

# Popular swap symbols for the beginner dropdown (shown if present on disk).
POPULAR_SWAPS = [
    "BTC_USDT_SWAP",
    "ETH_USDT_SWAP",
    "SOL_USDT_SWAP",
    "XRP_USDT_SWAP",
    "DOGE_USDT_SWAP",
    "BNB_USDT_SWAP",
    "ADA_USDT_SWAP",
    "LINK_USDT_SWAP",
    "AVAX_USDT_SWAP",
    "DOT_USDT_SWAP",
    "SUI_USDT_SWAP",
    "PEPE_USDT_SWAP",
]


@dataclass(frozen=True)
class _Seg:
    start: int
    end: int


def _tightest_window(full_spread: np.ndarray, start: int, end: int, max_bars: int) -> _Seg:
    length = end - start + 1
    if length <= max_bars:
        return _Seg(start, end)
    best_i, best_score = start, float("inf")
    for i in range(start, end - max_bars + 2):
        window = full_spread[i : i + max_bars]
        if np.isnan(window).all():
            continue
        score = float(np.nanmean(window))
        if score < best_score:
            best_score = score
            best_i = i
    return _Seg(best_i, best_i + max_bars - 1)


def find_dense_segments(df: pd.DataFrame) -> list[_Seg]:
    """Same rule family as auto_label.find_dense_segments (cv2-free copy)."""
    full_spread = pd.to_numeric(df["full_spread"], errors="coerce").to_numpy()
    dense = (
        (pd.to_numeric(df["fast_spread"], errors="coerce") <= FAST_SPREAD_MAX)
        & (full_spread <= FULL_SPREAD_MAX)
    ).to_numpy()
    idx = np.flatnonzero(dense)
    if len(idx) == 0:
        return []
    runs: list[list[int]] = [[int(idx[0]), int(idx[0])]]
    for i in idx[1:]:
        if int(i) - runs[-1][1] <= MERGE_GAP_BARS + 1:
            runs[-1][1] = int(i)
        else:
            runs.append([int(i), int(i)])
    out: list[_Seg] = []
    for s, e in runs:
        if e - s + 1 < MIN_DENSE_BARS:
            continue
        out.append(_tightest_window(full_spread, s, e, MAX_DENSE_BARS))
    return out


def explore_catalog(universe: str = DEFAULT_UNIVERSE) -> dict:
    spec = universe_spec(universe)
    groups = series_groups(spec)
    available = {sym for (_, sym) in groups}
    popular = [s for s in POPULAR_SWAPS if s in available]
    # fill with other liquid names if popular list short
    extras = sorted(available - set(popular))[:40]
    return {
        "universe": spec.key,
        "universe_label": spec.label,
        "popular": [{"source": "okx", "symbol": s} for s in popular],
        "all": [{"source": src, "symbol": sym} for (src, sym) in sorted(groups.keys())][:400],
        "ranges": [
            {"id": "7d", "label": "近 7 天", "bars": 7 * 96},
            {"id": "14d", "label": "近 14 天", "bars": 14 * 96},
            {"id": "30d", "label": "近 30 天", "bars": 30 * 96},
            {"id": "90d", "label": "近 90 天", "bars": 90 * 96},
        ],
        "howto": [
            "① 选一个币种（建议先从 BTC / ETH 开始）",
            "② 选要看的时间长度",
            "③ 看图：蜡烛 + 彩色均线 + 半透明框 = 均线密集区",
            "④ 框里是「多条均线缠在一起」的地方——系统盯的就是这类形态",
            "⑤ 真正开仓还要过判断模型分数与趋势过滤（见「信号浏览」）",
        ],
        "extras_hint": f"另有 {len(extras)} 个币种可在输入框搜索",
    }


def explore_chart_payload(
    source: str,
    symbol: str,
    bars: int = 2880,
    universe: str = DEFAULT_UNIVERSE,
) -> dict:
    spec = universe_spec(universe)
    groups = series_groups(spec)
    key = (source, symbol)
    if key not in groups:
        raise HTTPException(404, f"unknown series {source}:{symbol}")
    n = int(min(max(bars, 200), 20_000))
    frame = load_series(groups[key]).tail(n).reset_index(drop=True)
    if frame.empty:
        raise HTTPException(404, "empty series")
    frame = add_indicators(frame)

    ts = (
        (frame["open_time"] - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta(seconds=1)
    ).astype(int)
    candles = [
        {
            "time": int(t),
            "open": float(o),
            "high": float(h),
            "low": float(l),
            "close": float(c),
            "volume": float(v) if np.isfinite(v) else 0.0,
        }
        for t, o, h, l, c, v in zip(
            ts, frame["open"], frame["high"], frame["low"], frame["close"], frame["volume"]
        )
    ]
    emas = {
        name: [
            {"time": int(t), "value": float(v)}
            for t, v in zip(ts, frame[name].round(8))
            if pd.notna(v)
        ]
        for name in ALL_MAS
        if name in frame.columns
    }

    segs = find_dense_segments(frame)
    boxes = []
    for i, seg in enumerate(segs, start=1):
        region = frame.iloc[seg.start : seg.end + 1]
        ma_vals = []
        for col in CLUSTER_EMAS:
            if col in region.columns:
                ma_vals.extend(float(x) for x in region[col] if pd.notna(x))
        if not ma_vals:
            continue
        hi, lo = max(ma_vals), min(ma_vals)
        pad = max((hi - lo) * 0.35, float(region["close"].median()) * 0.001)
        t0 = int(ts.iloc[seg.start])
        t1 = int(ts.iloc[seg.end])
        n_bars = int(seg.end - seg.start + 1)
        spread = float(region["full_spread"].mean()) if "full_spread" in region else None
        boxes.append(
            {
                "id": i,
                "t0": t0,
                "t1": t1,
                "hi": round(hi + pad, 8),
                "lo": round(lo - pad, 8),
                "bars": n_bars,
                "mid_time": int((t0 + t1) // 2),
                "start_iso": str(region["open_time"].iloc[0])[:16].replace("T", " "),
                "end_iso": str(region["open_time"].iloc[-1])[:16].replace("T", " "),
                "mean_full_spread": round(spread, 6) if spread is not None and np.isfinite(spread) else None,
            }
        )

    bar_lens = [b["bars"] for b in boxes]
    dens_per_day = round(len(boxes) / max(len(candles) / 96, 1e-9), 2) if candles else 0.0
    stats = {
        "n_boxes": len(boxes),
        "avg_bars": round(float(np.mean(bar_lens)), 1) if bar_lens else 0.0,
        "max_bars": int(max(bar_lens)) if bar_lens else 0,
        "boxes_per_day": dens_per_day,
        "coverage_pct": round(
            100.0 * sum(bar_lens) / max(len(candles), 1), 1
        ),
    }

    return {
        "source": source,
        "symbol": symbol,
        "universe": spec.key,
        "bar": "15m",
        "n_candles": len(candles),
        "n_boxes": len(boxes),
        "stats": stats,
        "candles": candles,
        "emas": emas,
        "dense_boxes": boxes,
        "legend": {
            "candles": "K 线（绿涨红跌）",
            "ema_fast": "EMA 8–55 快带（蓝系）",
            "ema_slow": "EMA 144 / 200 慢锚（紫/粉）",
            "box": "半透明框 = 均线密集区（规则扫描，与检测层同源）",
        },
        "tip": (
            f"本窗口共标出 {len(boxes)} 个密集框"
            f"（约 {stats['boxes_per_day']} 个/天，覆盖 K 线 {stats['coverage_pct']}%）。"
            "点击下方列表或图上的框可放大该段；真正开仓还要过判断层分数。"
        ),
    }
