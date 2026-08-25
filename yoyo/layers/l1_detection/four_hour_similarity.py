"""Retrospective 4h MA-compression launch similarity retrieval.

The source is closed OKX USDT perpetual OHLCV. Local 15-minute snapshots are
resampled to UTC-aligned 4-hour bars, then a bounded public OKX 4h suffix is
merged in memory after exact overlap parity checks. The longest causal input is
the 120-bar SMA/EMA warm-up. A candidate window contains 30 pre-launch bars and
12 release bars; therefore this module is deliberately *retrospective* and must
never be presented as a tip detector or production signal. The 12 release bars
are used only to retrieve completed historical shapes.

Similarity uses five scale-free channels derived from ``open/high/low/close``
and ``volume``: signed log close path, signed six-MA centre path, six-MA spread,
signed candle body, and within-window log volume ratio. A fixed coarse distance
shortlists candidates and a Sakoe-Chiba constrained multivariate DTW distance
reranks them. SHORT candidates use the exact same tensor after a price-axis
mirror; no separate short thresholds are fitted.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

import cv2
import numpy as np
import pandas as pd

from yoyo.layers.l1_detection.data import ALL_MA_COLS, add_mas
from yoyo.layers.l1_detection.render import render_chart


OKX_HISTORY_URL = "https://www.okx.com/api/v5/market/history-candles"
OHLCV = ("open_time", "open", "high", "low", "close", "volume")
CHANNELS = (
    "signed_close_path_pct",
    "signed_ma_center_path_pct",
    "ma_spread_pct",
    "signed_body_pct",
    "log_volume_ratio",
)


@dataclass(frozen=True)
class SimilaritySpec:
    """Frozen search contract for one retrospective retrieval run."""

    protocol: str = "btc_4h_ma_launch_similarity_v1_20260825"
    scan_start: str = "2024-08-25T04:00:00+00:00"
    scan_end: str = "2026-08-25T04:00:00+00:00"
    reference_symbol: str = "BTC_USDT_SWAP"
    reference_anchor: str = "2026-08-19T12:00:00+00:00"
    pre_bars: int = 30
    release_bars: int = 12
    review_extra_bars: int = 6
    dedupe_bars: int = 18
    shortlist_per_side: int = 400
    top_per_side: int = 8
    dtw_radius: int = 2
    coarse_weight: float = 0.45
    dtw_weight: float = 0.55
    null_permutations: int = 200
    random_seed: int = 20260825
    api_limit: int = 300
    api_pause_seconds: float = 0.12
    spread_reference_mult: float = 3.0
    release_close_reference_mult: float = 0.25
    release_close_hard_min_pct: float = 3.0
    first3_reference_mult: float = 0.15
    first3_hard_min_pct: float = 0.75
    release_atr_reference_mult: float = 0.15
    release_atr_hard_min: float = 4.0
    pre_range_reference_mult: float = 2.0
    pre_range_hard_max_pct: float = 8.0
    anchor_to_bundle_reference_mult: float = 3.0
    anchor_to_bundle_hard_max_pct: float = 2.0
    channel_weights: tuple[float, ...] = (0.40, 0.20, 0.15, 0.10, 0.15)
    channel_scale_floors: tuple[float, ...] = (1.0, 1.0, 0.25, 0.50, 0.50)

    def __post_init__(self) -> None:
        if len(self.channel_weights) != len(CHANNELS):
            raise ValueError("channel_weights length must match CHANNELS")
        if len(self.channel_scale_floors) != len(CHANNELS):
            raise ValueError("channel_scale_floors length must match CHANNELS")
        if not math.isclose(sum(self.channel_weights), 1.0, abs_tol=1e-12):
            raise ValueError("channel_weights must sum to one")

    @property
    def scan_start_ts(self) -> pd.Timestamp:
        return utc(self.scan_start)

    @property
    def scan_end_ts(self) -> pd.Timestamp:
        return utc(self.scan_end)

    @property
    def reference_anchor_ts(self) -> pd.Timestamp:
        return utc(self.reference_anchor)

    @property
    def total_bars(self) -> int:
        return self.pre_bars + self.release_bars

    def to_jsonable(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["channel_weights"] = dict(zip(CHANNELS, self.channel_weights))
        payload["channel_scale_floors"] = dict(
            zip(CHANNELS, self.channel_scale_floors)
        )
        return payload


@dataclass(frozen=True)
class ReferenceContract:
    """Reference-derived limits frozen before the multi-symbol scan."""

    ma_spread_before_max_pct: float
    release_close_min_pct: float
    first3_close_min_pct: float
    release_favorable_atr_min: float
    pre_range_max_pct: float
    anchor_to_bundle_max_pct: float


def utc(value: object) -> pd.Timestamp:
    """Return one timezone-aware UTC timestamp."""

    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        return stamp.tz_localize("UTC")
    return stamp.tz_convert("UTC")


def sha256_file(path: Path) -> str:
    """Hash one source or artifact without loading it all at once."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_frame_sha256(frame: pd.DataFrame) -> str:
    """Hash the reviewed 4h OHLCV rows in a platform-stable CSV encoding."""

    canonical = frame[list(OHLCV)].copy()
    canonical["open_time"] = canonical["open_time"].map(
        lambda value: utc(value).isoformat()
    )
    payload = canonical.to_csv(index=False, float_format="%.10g", lineterminator="\n")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def symbol_from_path(path: Path) -> str:
    """Parse ``okx_BTC_USDT_SWAP_15m_158499.csv`` into ``BTC_USDT_SWAP``."""

    stem = path.stem
    if not stem.startswith("okx_") or "_15m_" not in stem:
        raise ValueError(f"not a canonical 15m OKX filename: {path}")
    return stem[len("okx_") :].split("_15m_", 1)[0]


def instrument_id(symbol: str) -> str:
    """Convert repository symbol spelling into OKX instrument spelling."""

    return symbol.replace("_", "-")


def discover_universe(source_dir: Path) -> list[Path]:
    """Choose the longest local 15m history for each USDT perpetual symbol."""

    best: dict[str, tuple[int, Path]] = {}
    for path in sorted(source_dir.glob("okx_*_USDT_SWAP_15m_*.csv")):
        try:
            declared_rows = int(path.stem.rsplit("_", 1)[-1])
            symbol = symbol_from_path(path)
        except ValueError:
            continue
        if symbol not in best or declared_rows > best[symbol][0]:
            best[symbol] = (declared_rows, path)
    return [item[1] for item in sorted(best.values(), key=lambda item: item[1].name)]


def read_local_15m(path: Path, *, source_start: pd.Timestamp) -> pd.DataFrame:
    """Read a bounded local OHLCV suffix; source files remain untouched."""

    header = pd.read_csv(path, nrows=0).columns
    usecols = [column for column in ("ts", *OHLCV) if column in header]
    raw = pd.read_csv(path, usecols=usecols, encoding_errors="replace")
    if "open_time" in raw:
        raw["open_time"] = pd.to_datetime(raw["open_time"], utc=True, errors="coerce")
    elif "ts" in raw:
        raw["open_time"] = pd.to_datetime(
            pd.to_numeric(raw["ts"], errors="coerce"), unit="ms", utc=True
        )
    else:
        raise ValueError(f"{path} has neither open_time nor ts")
    for column in ("open", "high", "low", "close", "volume"):
        raw[column] = pd.to_numeric(raw[column], errors="coerce")
    return (
        raw[list(OHLCV)]
        .dropna(subset=["open_time", "open", "high", "low", "close", "volume"])
        .drop_duplicates("open_time", keep="last")
        .sort_values("open_time")
        .loc[lambda frame: frame["open_time"] >= source_start]
        .reset_index(drop=True)
    )


def resample_complete_4h(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    """Resample UTC 15m bars and retain only buckets with all 16 source bars."""

    indexed = frame.set_index("open_time").sort_index()
    grouped = indexed.resample("4h", label="left", closed="left", origin="epoch")
    bars = grouped.agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        source_count=("close", "count"),
    )
    total_buckets = len(bars)
    bars = bars[bars["source_count"] == 16].drop(columns="source_count").reset_index()
    return bars, {
        "four_hour_buckets_total": total_buckets,
        "four_hour_buckets_complete": len(bars),
        "four_hour_buckets_dropped_incomplete": total_buckets - len(bars),
    }


def fetch_recent_4h(
    symbol: str,
    *,
    limit: int,
    pause_seconds: float = 0.0,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Fetch one bounded public 4h page; credentials and local writes are absent."""

    query = urllib.parse.urlencode(
        {"instId": instrument_id(symbol), "bar": "4H", "limit": str(limit)}
    )
    request = urllib.request.Request(
        f"{OKX_HISTORY_URL}?{query}",
        headers={"User-Agent": "fable-trading-research/1.0"},
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        document = json.load(response)
    if str(document.get("code")) != "0":
        raise RuntimeError(f"OKX history-candles failed for {symbol}: {document}")
    columns = (
        "ts",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "vol_ccy",
        "vol_quote",
        "confirm",
    )
    api = pd.DataFrame(document.get("data", []), columns=columns)
    if api.empty:
        raise RuntimeError(f"OKX returned no 4h rows for {symbol}")
    api["open_time"] = pd.to_datetime(
        pd.to_numeric(api["ts"], errors="coerce"), unit="ms", utc=True
    )
    for column in ("open", "high", "low", "close", "volume"):
        api[column] = pd.to_numeric(api[column], errors="coerce")
    api = (
        api[api["confirm"].astype(str) == "1"][list(OHLCV)]
        .dropna()
        .drop_duplicates("open_time", keep="last")
        .sort_values("open_time")
        .reset_index(drop=True)
    )
    if pause_seconds:
        time.sleep(pause_seconds)
    return api, {
        "api_calls": 1,
        "api_rows_confirmed": len(api),
        "api_first_time": api["open_time"].min().isoformat(),
        "api_last_time": api["open_time"].max().isoformat(),
        "api_unclosed_rows_discarded": int(
            (pd.DataFrame(document.get("data", []), columns=columns)["confirm"] == "0").sum()
        ),
    }


def merge_with_api_suffix(
    local: pd.DataFrame,
    api: pd.DataFrame,
    *,
    scan_end: pd.Timestamp,
    min_overlap: int = 6,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Require exact local/API OHLC parity, then prefer native API rows in overlap."""

    overlap = local.merge(api, on="open_time", suffixes=("_local", "_api"))
    if len(overlap) < min_overlap:
        raise ValueError(f"local/API 4h overlap too short: {len(overlap)} < {min_overlap}")
    max_abs: dict[str, float] = {}
    max_rel: dict[str, float] = {}
    for column in ("open", "high", "low", "close"):
        difference = (overlap[f"{column}_local"] - overlap[f"{column}_api"]).abs()
        denominator = overlap[f"{column}_api"].abs().replace(0, np.nan)
        max_abs[column] = float(difference.max())
        max_rel[column] = float((difference / denominator).max())
    if max(max_rel.values()) > 1e-10:
        raise ValueError(f"local/API 4h OHLC parity failed: {max_rel}")
    api_first = utc(api["open_time"].min())
    merged = pd.concat(
        [local[local["open_time"] < api_first], api], ignore_index=True
    )
    merged = (
        merged.drop_duplicates("open_time", keep="last")
        .sort_values("open_time")
        .loc[lambda frame: frame["open_time"] <= scan_end]
        .reset_index(drop=True)
    )
    return merged, {
        "overlap_rows": len(overlap),
        "overlap_first_time": overlap["open_time"].min().isoformat(),
        "overlap_last_time": overlap["open_time"].max().isoformat(),
        "overlap_max_abs_ohlc_delta": max_abs,
        "overlap_max_relative_ohlc_delta": max_rel,
    }


def enrich_4h(frame: pd.DataFrame) -> pd.DataFrame:
    """Add causal six-MA and simple rolling ATR14 columns to closed 4h bars."""

    out = add_mas(frame)
    previous_close = out["close"].shift(1)
    true_range = pd.concat(
        [
            out["high"] - out["low"],
            (out["high"] - previous_close).abs(),
            (out["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    out["atr14"] = true_range.rolling(14, min_periods=14).mean()
    return out


def is_contiguous_4h(times: Iterable[object]) -> bool:
    """Return whether timestamps form an exact, duplicate-free 4h grid."""

    stamps = pd.DatetimeIndex([utc(value) for value in times])
    if len(stamps) <= 1:
        return True
    return bool((stamps[1:] - stamps[:-1] == pd.Timedelta(hours=4)).all())


def raw_window_tensor(
    frame: pd.DataFrame,
    anchor_i: int,
    direction: int,
    spec: SimilaritySpec,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Build one signed scale-free tensor and its transparent gate metrics."""

    if direction not in (-1, 1):
        raise ValueError("direction must be -1 or +1")
    start = anchor_i - spec.pre_bars
    stop = anchor_i + spec.release_bars
    if start < 0 or stop > len(frame):
        raise IndexError("candidate window exceeds available bars")
    window = frame.iloc[start:stop]
    if len(window) != spec.total_bars or not is_contiguous_4h(window["open_time"]):
        raise ValueError("candidate window is not a continuous 4h sequence")
    if not np.isfinite(window[list(ALL_MA_COLS)].to_numpy(dtype=float)).all():
        raise ValueError("candidate moving averages are not finite")
    anchor_open = float(frame.iloc[anchor_i]["open"])
    if not np.isfinite(anchor_open) or anchor_open <= 0:
        raise ValueError("candidate anchor open is invalid")
    pre = frame.iloc[start:anchor_i]
    release = frame.iloc[anchor_i:stop]
    ma_values = window[list(ALL_MA_COLS)].to_numpy(dtype=float)
    ma_center = ma_values.mean(axis=1)
    close = window["close"].to_numpy(dtype=float)
    open_ = window["open"].to_numpy(dtype=float)
    volume = window["volume"].to_numpy(dtype=float)
    pre_volume_median = max(float(np.median(pre["volume"].to_numpy(dtype=float))), 1e-12)
    tensor = np.column_stack(
        [
            direction * 100.0 * np.log(close / anchor_open),
            direction * 100.0 * np.log(ma_center / anchor_open),
            100.0 * (ma_values.max(axis=1) - ma_values.min(axis=1)) / close,
            direction * 100.0 * np.log(close / open_),
            np.log(np.maximum(volume, 1e-12) / pre_volume_median),
        ]
    )
    prior = frame.iloc[anchor_i - 1]
    prior_mas = prior[list(ALL_MA_COLS)].to_numpy(dtype=float)
    prior_close = float(prior["close"])
    ma_center_prior = float(np.mean(prior_mas))
    atr = float(prior["atr14"])
    release_close_signed_pct = float(
        direction * 100.0 * np.log(float(release.iloc[-1]["close"]) / anchor_open)
    )
    first3_close_signed_pct = float(
        direction * 100.0 * np.log(float(release.iloc[2]["close"]) / anchor_open)
    )
    if direction == 1:
        favorable_price = float(release["high"].max())
        release_favorable_pct = (favorable_price / anchor_open - 1.0) * 100.0
        release_favorable_abs = favorable_price - anchor_open
    else:
        favorable_price = float(release["low"].min())
        release_favorable_pct = (anchor_open / favorable_price - 1.0) * 100.0
        release_favorable_abs = anchor_open - favorable_price
    metrics = {
        "window_start_i": start,
        "anchor_i": anchor_i,
        "release_end_i": stop - 1,
        "window_start_time": utc(window.iloc[0]["open_time"]).isoformat(),
        "anchor_time": utc(frame.iloc[anchor_i]["open_time"]).isoformat(),
        "release_end_time": utc(frame.iloc[stop - 1]["open_time"]).isoformat(),
        "direction": "LONG" if direction == 1 else "SHORT",
        "anchor_open": anchor_open,
        "ma_spread_before_pct": float(
            (prior_mas.max() - prior_mas.min()) / prior_close * 100.0
        ),
        "anchor_to_bundle_pct": float(abs(anchor_open / ma_center_prior - 1.0) * 100.0),
        "pre_range_pct": float((pre["high"].max() / pre["low"].min() - 1.0) * 100.0),
        "pre_net_signed_pct": float(
            direction * 100.0 * np.log(float(pre.iloc[-1]["close"]) / float(pre.iloc[0]["open"]))
        ),
        "first3_close_signed_pct": first3_close_signed_pct,
        "release_close_signed_pct": release_close_signed_pct,
        "release_favorable_pct": float(release_favorable_pct),
        "release_favorable_atr": float(release_favorable_abs / atr),
        "atr14_before": atr,
        "feature_future_bars": spec.release_bars,
        "production_eligible": False,
        "training_eligible": False,
    }
    if not np.isfinite(tensor).all() or not all(
        np.isfinite(float(metrics[key]))
        for key in (
            "ma_spread_before_pct",
            "anchor_to_bundle_pct",
            "pre_range_pct",
            "first3_close_signed_pct",
            "release_close_signed_pct",
            "release_favorable_atr",
        )
    ):
        raise ValueError("candidate tensor or metrics contain non-finite values")
    return tensor, metrics


def channel_scales(reference_tensor: np.ndarray, spec: SimilaritySpec) -> np.ndarray:
    """Scale every channel by the reference RMS with preregistered floors."""

    rms = np.sqrt(np.mean(np.square(reference_tensor), axis=0))
    return np.maximum(rms, np.asarray(spec.channel_scale_floors, dtype=float))


def normalize_tensor(
    tensor: np.ndarray,
    scales: np.ndarray,
    spec: SimilaritySpec,
) -> np.ndarray:
    """Apply reference scales and square-root channel weights."""

    return tensor / scales * np.sqrt(np.asarray(spec.channel_weights, dtype=float))


def coarse_distance(candidate: np.ndarray, reference: np.ndarray) -> float:
    """Fixed-length weighted root-mean-square distance."""

    if candidate.shape != reference.shape:
        raise ValueError("candidate/reference tensor shapes differ")
    return float(np.sqrt(np.mean(np.square(candidate - reference))))


def multivariate_dtw_distance(
    candidate: np.ndarray,
    reference: np.ndarray,
    *,
    radius: int,
) -> float:
    """Exact constrained DTW with per-row mean-squared multivariate cost."""

    if candidate.ndim != 2 or reference.ndim != 2:
        raise ValueError("DTW expects two 2D arrays")
    if candidate.shape[1] != reference.shape[1]:
        raise ValueError("DTW channel counts differ")
    n, m = len(candidate), len(reference)
    if radius < abs(n - m):
        raise ValueError("DTW radius cannot connect the two sequence lengths")
    costs = np.full((n + 1, m + 1), np.inf, dtype=float)
    steps = np.zeros((n + 1, m + 1), dtype=np.int32)
    costs[0, 0] = 0.0
    for i in range(1, n + 1):
        lower = max(1, i - radius)
        upper = min(m, i + radius)
        for j in range(lower, upper + 1):
            predecessors = (
                (costs[i - 1, j], steps[i - 1, j]),
                (costs[i, j - 1], steps[i, j - 1]),
                (costs[i - 1, j - 1], steps[i - 1, j - 1]),
            )
            previous_cost, previous_steps = min(predecessors, key=lambda item: item[0])
            row_cost = float(np.mean(np.square(candidate[i - 1] - reference[j - 1])))
            costs[i, j] = previous_cost + row_cost
            steps[i, j] = previous_steps + 1
    if not np.isfinite(costs[n, m]) or steps[n, m] <= 0:
        raise ValueError("DTW path is unreachable")
    return float(np.sqrt(costs[n, m] / steps[n, m]))


def split_dtw_distance(
    candidate: np.ndarray,
    reference: np.ndarray,
    spec: SimilaritySpec,
) -> float:
    """Keep the launch boundary fixed while allowing ±radius warp within each side."""

    pre = multivariate_dtw_distance(
        candidate[: spec.pre_bars],
        reference[: spec.pre_bars],
        radius=spec.dtw_radius,
    )
    release = multivariate_dtw_distance(
        candidate[spec.pre_bars :],
        reference[spec.pre_bars :],
        radius=spec.dtw_radius,
    )
    return float(0.35 * pre + 0.65 * release)


def build_reference_contract(
    reference_metrics: dict[str, Any],
    spec: SimilaritySpec,
) -> ReferenceContract:
    """Derive preregistered broad recall gates from only the supplied reference."""

    return ReferenceContract(
        ma_spread_before_max_pct=float(reference_metrics["ma_spread_before_pct"])
        * spec.spread_reference_mult,
        release_close_min_pct=max(
            spec.release_close_hard_min_pct,
            float(reference_metrics["release_close_signed_pct"])
            * spec.release_close_reference_mult,
        ),
        first3_close_min_pct=max(
            spec.first3_hard_min_pct,
            float(reference_metrics["first3_close_signed_pct"])
            * spec.first3_reference_mult,
        ),
        release_favorable_atr_min=max(
            spec.release_atr_hard_min,
            float(reference_metrics["release_favorable_atr"])
            * spec.release_atr_reference_mult,
        ),
        pre_range_max_pct=min(
            spec.pre_range_hard_max_pct,
            float(reference_metrics["pre_range_pct"])
            * spec.pre_range_reference_mult,
        ),
        anchor_to_bundle_max_pct=min(
            spec.anchor_to_bundle_hard_max_pct,
            float(reference_metrics["anchor_to_bundle_pct"])
            * spec.anchor_to_bundle_reference_mult,
        ),
    )


def passes_reference_contract(
    metrics: dict[str, Any],
    contract: ReferenceContract,
) -> bool:
    """Apply the broad, direction-symmetric recall contract."""

    return bool(
        float(metrics["ma_spread_before_pct"]) <= contract.ma_spread_before_max_pct
        and float(metrics["release_close_signed_pct"]) >= contract.release_close_min_pct
        and float(metrics["first3_close_signed_pct"]) >= contract.first3_close_min_pct
        and float(metrics["release_favorable_atr"])
        >= contract.release_favorable_atr_min
        and float(metrics["pre_range_pct"]) <= contract.pre_range_max_pct
        and float(metrics["anchor_to_bundle_pct"])
        <= contract.anchor_to_bundle_max_pct
    )


def candidate_anchor_indices(
    frame: pd.DataFrame,
    *,
    direction: int,
    contract: ReferenceContract,
    spec: SimilaritySpec,
) -> tuple[list[int], dict[str, int]]:
    """Vectorize the broad recall gate before building detailed tensors.

    All release statistics intentionally look through ``release_bars`` because
    this is a completed-shape retrieval surface. The output remains ineligible
    for causal detection or execution.
    """

    if direction not in (-1, 1):
        raise ValueError("direction must be -1 or +1")
    close = frame["close"].astype(float)
    open_ = frame["open"].astype(float)
    high = frame["high"].astype(float)
    low = frame["low"].astype(float)
    prior_close = close.shift(1)
    prior_mas = frame[list(ALL_MA_COLS)].shift(1)
    prior_spread = (prior_mas.max(axis=1) - prior_mas.min(axis=1)) / prior_close * 100.0
    prior_center = prior_mas.mean(axis=1)
    anchor_to_bundle = (open_ / prior_center - 1.0).abs() * 100.0
    pre_high = high.shift(1).rolling(spec.pre_bars, min_periods=spec.pre_bars).max()
    pre_low = low.shift(1).rolling(spec.pre_bars, min_periods=spec.pre_bars).min()
    pre_range = (pre_high / pre_low - 1.0) * 100.0
    release_end_close = close.shift(-(spec.release_bars - 1))
    first3_close = close.shift(-2)
    release_high = (
        high.iloc[::-1]
        .rolling(spec.release_bars, min_periods=spec.release_bars)
        .max()
        .iloc[::-1]
    )
    release_low = (
        low.iloc[::-1]
        .rolling(spec.release_bars, min_periods=spec.release_bars)
        .min()
        .iloc[::-1]
    )
    release_close_signed = direction * 100.0 * np.log(release_end_close / open_)
    first3_signed = direction * 100.0 * np.log(first3_close / open_)
    if direction == 1:
        favorable_abs = release_high - open_
    else:
        favorable_abs = open_ - release_low
    favorable_atr = favorable_abs / frame["atr14"].shift(1)
    window_start_time = frame["open_time"].shift(spec.pre_bars)
    release_end_time = frame["open_time"].shift(-(spec.release_bars - 1))
    expected_span = pd.Timedelta(
        hours=4 * (spec.pre_bars + spec.release_bars - 1)
    )
    contiguous = (release_end_time - window_start_time) == expected_span
    anchor_times = pd.to_datetime(frame["open_time"], utc=True)
    in_scope = (anchor_times >= spec.scan_start_ts) & (anchor_times <= spec.scan_end_ts)
    finite_start_mas = frame[list(ALL_MA_COLS)].shift(spec.pre_bars).notna().all(axis=1)
    finite_prior_atr = frame["atr14"].shift(1).notna()
    mask = (
        in_scope
        & contiguous
        & finite_start_mas
        & finite_prior_atr
        & (prior_spread <= contract.ma_spread_before_max_pct)
        & (release_close_signed >= contract.release_close_min_pct)
        & (first3_signed >= contract.first3_close_min_pct)
        & (favorable_atr >= contract.release_favorable_atr_min)
        & (pre_range <= contract.pre_range_max_pct)
        & (anchor_to_bundle <= contract.anchor_to_bundle_max_pct)
    ).fillna(False)
    indices = [int(index) for index in frame.index[mask]]
    return indices, {
        "anchors_in_scope": int(in_scope.sum()),
        "anchors_contiguous_with_full_window": int((in_scope & contiguous).sum()),
        "anchors_passing_broad_gate": len(indices),
    }


def event_id(protocol: str, symbol: str, direction: str, anchor_time: str) -> str:
    """Return a stable ID for one completed historical window."""

    payload = f"{protocol}|{symbol}|{direction}|{anchor_time}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def deduplicate_candidates(
    rows: list[dict[str, Any]],
    *,
    distance_field: str,
    gap_bars: int,
) -> list[dict[str, Any]]:
    """Greedily keep the best candidate within each symbol/side exclusion zone."""

    kept: list[dict[str, Any]] = []
    occupied: dict[tuple[str, str], list[int]] = {}
    for row in sorted(rows, key=lambda item: float(item[distance_field])):
        key = (str(row["symbol"]), str(row["direction"]))
        anchor_i = int(row["anchor_i"])
        if any(abs(anchor_i - prior) <= gap_bars for prior in occupied.get(key, [])):
            continue
        kept.append(row)
        occupied.setdefault(key, []).append(anchor_i)
    return kept


def render_review_chart(
    frame: pd.DataFrame,
    row: dict[str, Any],
    *,
    spec: SimilaritySpec,
    output: Path,
    rank_label: str,
) -> dict[str, Any]:
    """Render candles plus six MAs with frozen anchor/release boundaries."""

    anchor_i = int(row["anchor_i"])
    start = anchor_i - spec.pre_bars
    stop = min(
        len(frame),
        anchor_i + spec.release_bars + spec.review_extra_bars,
    )
    review = frame.iloc[start:stop].reset_index(drop=True)
    chart, transform = render_chart(review, width=1280, height=680)
    canvas = np.full((770, 1280, 3), 255, dtype=np.uint8)
    canvas[70:750] = chart
    anchor_x = transform.x_at(spec.pre_bars)
    release_end_local = min(spec.pre_bars + spec.release_bars - 1, len(review) - 1)
    release_x = transform.x_at(release_end_local)
    cv2.line(canvas, (anchor_x, 70), (anchor_x, 750), (210, 115, 32), 2, cv2.LINE_AA)
    cv2.line(canvas, (release_x, 70), (release_x, 750), (110, 110, 110), 1, cv2.LINE_AA)
    direction = str(row["direction"])
    color = (170, 92, 25) if direction == "LONG" else (70, 70, 190)
    title = (
        f"{rank_label}  {row['symbol']}  {direction}  "
        f"anchor {utc(row['anchor_time']).strftime('%Y-%m-%d %H:%M UTC')}"
    )
    cv2.putText(
        canvas,
        title,
        (18, 30),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.72,
        color,
        2,
        cv2.LINE_AA,
    )
    metrics = (
        f"distance {float(row.get('final_distance', 0.0)):.4f} | "
        f"release close {float(row['release_close_signed_pct']):+.2f}% | "
        f"MFE {float(row['release_favorable_pct']):.2f}% | "
        f"MA spread {float(row['ma_spread_before_pct']):.3f}%"
    )
    cv2.putText(
        canvas,
        metrics,
        (18, 58),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.58,
        (55, 62, 69),
        1,
        cv2.LINE_AA,
    )
    cv2.putText(
        canvas,
        "blue line = release start | gray line = 12-bar match end | extra bars = review only",
        (18, 767),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.46,
        (80, 80, 80),
        1,
        cv2.LINE_AA,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), canvas):
        raise OSError(f"failed to write review chart: {output}")
    return {
        "review_path": str(output),
        "review_sha256": sha256_file(output),
        "review_bars": len(review),
        "review_extra_bars_available": max(
            0, len(review) - spec.pre_bars - spec.release_bars
        ),
    }


def build_overview(
    image_paths: list[Path],
    *,
    output: Path,
    columns: int = 4,
    thumb_width: int = 640,
) -> None:
    """Build a compact Telegram-friendly contact sheet from rendered evidence."""

    if not image_paths:
        raise ValueError("overview needs at least one image")
    images: list[np.ndarray] = []
    for path in image_paths:
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None:
            raise OSError(f"could not reopen rendered chart: {path}")
        scale = thumb_width / image.shape[1]
        images.append(
            cv2.resize(
                image,
                (thumb_width, int(round(image.shape[0] * scale))),
                interpolation=cv2.INTER_AREA,
            )
        )
    thumb_height = max(image.shape[0] for image in images)
    rows = math.ceil(len(images) / columns)
    canvas = np.full((rows * thumb_height, columns * thumb_width, 3), 242, dtype=np.uint8)
    for index, image in enumerate(images):
        row, column = divmod(index, columns)
        y = row * thumb_height
        x = column * thumb_width
        canvas[y : y + image.shape[0], x : x + image.shape[1]] = image
    output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), canvas):
        raise OSError(f"failed to write overview: {output}")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
