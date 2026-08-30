"""Pre-holdout 15m six-MA completed-launch candidate collection.

This dataset helper proposes review rows, not positive labels.  At decision bar
``t`` the inherited dense-start gate reads close-derived SMA/EMA 20/60/120,
Pine-RMA ATR14 and the frozen formation windows ending at ``t``.  Only the
descriptive completed-path ranking reads ``t+1..t+11``; six more rows are
rendered for review.  No training image or label is produced here.

Source CSV access is prefix bounded: the first boundary timestamp may be
inspected, while OHLCV values at or after the repository holdout are never
converted, materialized, hashed, charted or scored.
"""
from __future__ import annotations

import csv
import hashlib
import html
import json
import math
import re
from bisect import bisect_left
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import cv2
import numpy as np
import pandas as pd

from yoyo.data.loader import BLOCKED_BASES
from yoyo.data.universe import is_stockish
from yoyo.datasets.ma_rope_filter import SIX_MA_COLUMNS, add_six_mas
from yoyo.layers.l1_detection.render import render_chart
from yoyo.layers.l2_judgment.pine_dense_start import (
    DenseStartProfile,
    add_six_ma_dense_start_features,
    dense_start_gate_mask,
)
from yoyo.layers.l3_backtest.pine_allin_v7 import pine_rma


OHLCV = ("open_time", "open", "high", "low", "close", "volume")
FILENAME_RE = re.compile(r"^okx_(?P<symbol>.+)_15m_(?P<rows>\d+)\.csv$")


class CandidateCollectionError(ValueError):
    """Fail-closed candidate collection error."""


@dataclass(frozen=True)
class CandidateSpec:
    """Executable subset of the committed candidate-collection contract."""

    protocol: str = "ma6_dense_start_15m_completed_pool_v1_20260825"
    scan_start: str = "2024-05-04T00:00:00Z"
    scan_end_exclusive: str = "2026-05-04T00:00:00Z"
    holdout_start: str = "2026-05-04T00:00:00Z"
    bar_minutes: int = 15
    causal_input_bars: int = 200
    pre_bars: int = 30
    release_bars: int = 12
    review_extra_bars: int = 6
    review_marker_offset_bars: int = 0
    dedupe_bars: int = 224
    target_per_side: int = 500
    max_per_symbol_per_side: int = 8
    max_per_day_per_side: int = 8
    formation_weight: float = 0.65
    future_weight: float = 0.35
    first3_weight: float = 0.25
    close12_weight: float = 0.45
    mfe_weight: float = 0.30
    first3_reference_atr: float = 1.5
    close12_reference_atr: float = 4.0
    mfe_reference_atr: float = 6.0
    causality_audit_rows: int = 32
    causality_seed: int = 20260825

    def __post_init__(self) -> None:
        if self.bar_minutes != 15:
            raise CandidateCollectionError("candidate collection is fixed to 15m")
        if self.scan_end_ts != self.holdout_start_ts:
            raise CandidateCollectionError("scan end must equal the exclusive holdout boundary")
        for name in (
            "causal_input_bars",
            "pre_bars",
            "release_bars",
            "review_extra_bars",
            "dedupe_bars",
            "target_per_side",
            "max_per_symbol_per_side",
            "max_per_day_per_side",
            "causality_audit_rows",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise CandidateCollectionError(f"{name} must be a positive integer")
        if self.causal_input_bars < self.pre_bars:
            raise CandidateCollectionError("causal input must cover the review prelude")
        if not isinstance(self.review_marker_offset_bars, int) or isinstance(
            self.review_marker_offset_bars, bool
        ):
            raise CandidateCollectionError("review_marker_offset_bars must be an integer")
        marker_index = self.pre_bars + self.review_marker_offset_bars
        if marker_index < 0 or marker_index >= self.review_bars:
            raise CandidateCollectionError("review marker falls outside the rendered window")
        if not math.isclose(self.formation_weight + self.future_weight, 1.0, abs_tol=1e-12):
            raise CandidateCollectionError("completed score weights must sum to one")
        if not math.isclose(
            self.first3_weight + self.close12_weight + self.mfe_weight,
            1.0,
            abs_tol=1e-12,
        ):
            raise CandidateCollectionError("future score weights must sum to one")
        if min(
            self.first3_reference_atr,
            self.close12_reference_atr,
            self.mfe_reference_atr,
        ) <= 0:
            raise CandidateCollectionError("future ATR references must be positive")

    @property
    def scan_start_ts(self) -> pd.Timestamp:
        return utc(self.scan_start)

    @property
    def scan_end_ts(self) -> pd.Timestamp:
        return utc(self.scan_end_exclusive)

    @property
    def holdout_start_ts(self) -> pd.Timestamp:
        return utc(self.holdout_start)

    @property
    def review_bars(self) -> int:
        return self.pre_bars + self.release_bars + self.review_extra_bars

    @property
    def dedupe_timedelta(self) -> pd.Timedelta:
        return pd.Timedelta(minutes=self.bar_minutes * self.dedupe_bars)

    @classmethod
    def from_preregistration(cls, payload: Mapping[str, Any]) -> "CandidateSpec":
        """Build the executable spec from the exact committed JSON fields."""

        scope = payload["scope"]
        shape = payload["shape_contract"]
        ranking = payload["ranking"]
        selection = payload["selection"]
        controls = payload["controls"]
        completed_weights = ranking["completed_score_weights"]
        future_weights = ranking["future_score_weights"]
        return cls(
            scan_start=str(scope["scan_start"]),
            scan_end_exclusive=str(scope["scan_end_exclusive"]),
            holdout_start=str(scope["holdout_start"]),
            bar_minutes=int(scope["bar_minutes"]),
            causal_input_bars=int(shape["causal_input_bars"]),
            pre_bars=int(shape["review_pre_bars"]),
            release_bars=int(shape["completed_release_bars"]),
            review_extra_bars=int(shape["review_extra_bars"]),
            review_marker_offset_bars=int(shape.get("review_marker_offset_bars", 0)),
            dedupe_bars=int(selection["same_symbol_side_dedupe_bars"]),
            target_per_side=int(selection["per_side"]["LONG"]),
            max_per_symbol_per_side=int(selection["max_per_symbol_per_side"]),
            max_per_day_per_side=int(selection["max_per_utc_day_per_side"]),
            formation_weight=float(completed_weights["causal_formation"]),
            future_weight=float(completed_weights["future_release"]),
            first3_weight=float(future_weights["first3"]),
            close12_weight=float(future_weights["close12"]),
            mfe_weight=float(future_weights["mfe12"]),
            first3_reference_atr=float(ranking["future_first3_reference_atr"]),
            close12_reference_atr=float(ranking["future_close12_reference_atr"]),
            mfe_reference_atr=float(ranking["future_mfe_reference_atr"]),
            causality_audit_rows=int(controls.get("causality_audit_rows", 32)),
        )


def utc(value: object) -> pd.Timestamp:
    """Return a timezone-aware UTC timestamp."""

    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def sha256_file(path: Path) -> str:
    """Hash one file without loading it all at once."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def event_id(protocol: str, symbol: str, direction: str, anchor_time: object) -> str:
    """Stable identity for one symbol, side and completed decision bar."""

    raw = f"{protocol}|{symbol}|{direction}|{utc(anchor_time).isoformat()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def discover_universe(
    roots: Sequence[Path],
    *,
    eval_symbols: set[str],
) -> tuple[dict[str, Path], dict[str, int]]:
    """Choose one longest snapshot per symbol and apply frozen taxonomy exclusions."""

    best: dict[str, tuple[tuple[int, int, str], Path]] = {}
    discovered: set[str] = set()
    for root in roots:
        root_priority = 1 if root.name == "kline_deep" else 0
        for path in sorted(root.glob("okx_*_USDT_SWAP_15m_*.csv")):
            matched = FILENAME_RE.match(path.name)
            if matched is None:
                continue
            symbol = matched.group("symbol")
            declared = int(matched.group("rows"))
            discovered.add(symbol)
            key = (declared, root_priority, str(path))
            if symbol not in best or key > best[symbol][0]:
                best[symbol] = (key, path)
    taxonomy = {
        symbol: value
        for symbol, value in best.items()
        if symbol.split("_", 1)[0] not in BLOCKED_BASES and not is_stockish(symbol)
    }
    eligible = {
        symbol: value[1]
        for symbol, value in taxonomy.items()
        if symbol not in eval_symbols
    }
    return dict(sorted(eligible.items())), {
        "discovered_symbols": len(discovered),
        "after_taxonomy": len(taxonomy),
        "blocked_by_taxonomy": len(best) - len(taxonomy),
        "frozen_eval_symbols_excluded": len(taxonomy) - len(eligible),
        "eligible_filename_symbols": len(eligible),
        "deep_sources": sum(path.parent.name == "kline_deep" for path in eligible.values()),
        "fetched_sources": sum(path.parent.name == "kline_fetched" for path in eligible.values()),
    }


def _parse_source_time(raw: str, *, is_epoch_ms: bool) -> pd.Timestamp:
    if is_epoch_ms:
        return pd.to_datetime(int(raw), unit="ms", utc=True)
    return pd.to_datetime(raw, utc=True, errors="raise")


def read_preholdout_prefix(
    path: Path,
    *,
    end_exclusive: pd.Timestamp,
    bar_minutes: int = 15,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Materialize OHLCV only before ``end_exclusive`` from an ascending CSV.

    The first boundary row is parsed only for its timestamp.  A unit test puts
    deliberately non-numeric strings in its OHLCV cells to prove they are not
    converted or materialized.
    """

    end_exclusive = utc(end_exclusive)
    rows: list[dict[str, Any]] = []
    digest = hashlib.sha256()
    boundary_timestamps_inspected = 0
    with path.open("r", newline="", encoding="utf-8", errors="replace") as handle:
        header_line = handle.readline()
        if not header_line:
            raise CandidateCollectionError(f"empty source file: {path}")
        header = next(csv.reader([header_line]))
        indexes = {name: i for i, name in enumerate(header)}
        # Prefer epoch milliseconds when both representations exist.  This
        # lets the hot loop compare an integer boundary and vectorize the
        # approved-prefix conversion after reading, while still never touching
        # boundary-row OHLCV cells.
        time_name = (
            "ts"
            if "ts" in indexes
            else "open_time"
            if "open_time" in indexes
            else None
        )
        missing = [name for name in ("open", "high", "low", "close", "volume") if name not in indexes]
        if time_name is None or missing:
            raise CandidateCollectionError(f"source schema missing time/OHLCV: {path} {missing}")
        digest.update(header_line.encode("utf-8"))
        is_epoch_ms = time_name == "ts"
        boundary_epoch_ms = int(end_exclusive.value // 1_000_000)
        for raw_line in handle:
            values = next(csv.reader([raw_line]))
            if len(values) != len(header):
                raise CandidateCollectionError(f"malformed CSV row in {path}")
            raw_time = values[indexes[time_name]]
            if is_epoch_ms:
                approved_time: Any = int(raw_time)
                at_or_after_boundary = approved_time >= boundary_epoch_ms
            else:
                approved_time = _parse_source_time(raw_time, is_epoch_ms=False)
                at_or_after_boundary = approved_time >= end_exclusive
            if at_or_after_boundary:
                boundary_timestamps_inspected += 1
                break
            rows.append(
                {
                    "open_time": approved_time,
                    "open": float(values[indexes["open"]]),
                    "high": float(values[indexes["high"]]),
                    "low": float(values[indexes["low"]]),
                    "close": float(values[indexes["close"]]),
                    "volume": float(values[indexes["volume"]]),
                }
            )
            digest.update(raw_line.encode("utf-8"))

    frame = pd.DataFrame(rows, columns=OHLCV)
    if is_epoch_ms and not frame.empty:
        frame["open_time"] = pd.to_datetime(frame["open_time"], unit="ms", utc=True)
    audit: dict[str, Any] = {
        "source_path": str(path),
        "bounded_prefix_sha256": digest.hexdigest(),
        "rows_materialized": len(frame),
        "boundary_timestamp_rows_inspected": boundary_timestamps_inspected,
        "holdout_ohlcv_rows_materialized": 0,
    }
    if frame.empty:
        audit.update({"first_time": None, "last_time": None, "non_bar_gaps": 0})
        return frame, audit
    times = pd.to_datetime(frame["open_time"], utc=True)
    numeric = frame[["open", "high", "low", "close", "volume"]].to_numpy(dtype=float)
    if not np.isfinite(numeric).all():
        raise CandidateCollectionError(f"non-finite OHLCV in approved prefix: {path}")
    if bool((frame[["open", "high", "low", "close"]] <= 0.0).any().any()):
        raise CandidateCollectionError(f"non-positive OHLC in approved prefix: {path}")
    if times.duplicated().any() or not times.is_monotonic_increasing:
        raise CandidateCollectionError(f"duplicate or descending timestamp in {path}")
    if bool((frame["high"] < frame[["open", "close"]].max(axis=1)).any()):
        raise CandidateCollectionError(f"high below candle body in {path}")
    if bool((frame["low"] > frame[["open", "close"]].min(axis=1)).any()):
        raise CandidateCollectionError(f"low above candle body in {path}")
    if times.max() >= end_exclusive:
        raise AssertionError("holdout boundary truncation failed")
    # Segment boundaries are the bar spacing of THIS series, not a constant.
    # Reading a 5m file with the 15m spacing marked every bar as a gap, so every
    # segment had length one and the scanner returned zero candidates while
    # reporting no error at all.
    bar_delta = pd.Timedelta(minutes=int(bar_minutes))
    gaps = int((times.diff().dropna() != bar_delta).sum())
    audit.update(
        {
            "first_time": times.iloc[0].isoformat(),
            "last_time": times.iloc[-1].isoformat(),
            "non_bar_gaps": gaps,
            "bar_minutes": int(bar_minutes),
        }
    )
    frame["_source_i"] = np.arange(len(frame), dtype=int)
    frame["_segment_id"] = times.diff().ne(bar_delta).cumsum().astype(int)
    return frame, audit


def add_candidate_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Add exact renderer MAs, inherited Pine-RMA ATR14 and dense-start features."""

    out = add_six_mas(frame)
    high = out["high"].to_numpy(dtype=float)
    low = out["low"].to_numpy(dtype=float)
    close = out["close"].to_numpy(dtype=float)
    previous = np.r_[np.nan, close[:-1]]
    true_range = np.nanmax(
        np.vstack((high - low, np.abs(high - previous), np.abs(low - previous))),
        axis=0,
    )
    out["atr"] = pine_rma(true_range, 14)
    return add_six_ma_dense_start_features(out)


def _score_component(value: float, reference: float) -> float:
    return float(np.clip(value / reference, 0.0, 1.0))


def collect_segment_candidates(
    segment: pd.DataFrame,
    *,
    symbol: str,
    source_path: Path,
    profile: DenseStartProfile,
    spec: CandidateSpec,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Collect LONG and SHORT completed-shape proposals from one contiguous segment."""

    counts = Counter()
    minimum = spec.causal_input_bars + spec.release_bars + spec.review_extra_bars
    if len(segment) < minimum:
        counts["segments_too_short"] += 1
        return [], dict(counts)
    featured = add_candidate_features(segment.reset_index(drop=True))
    segment_source_start = int(segment["_source_i"].iloc[0])
    segment_source_end = int(segment["_source_i"].iloc[-1])
    rows: list[dict[str, Any]] = []
    times = pd.to_datetime(featured["open_time"], utc=True)
    for side, direction in (("LONG", 1), ("SHORT", -1)):
        gate = dense_start_gate_mask(featured, profile, side=side.lower())
        counts[f"{side.lower()}_gate_hits_all"] += int(gate.sum())
        for local_i in np.flatnonzero(gate.to_numpy(dtype=bool)):
            local_i = int(local_i)
            counts[f"{side.lower()}_gate_hits_considered"] += 1
            if local_i < spec.causal_input_bars - 1 or local_i < spec.pre_bars:
                counts["rejected_warmup"] += 1
                continue
            review_stop = local_i + spec.release_bars + spec.review_extra_bars
            if review_stop > len(featured):
                counts["rejected_incomplete_review"] += 1
                continue
            anchor_time = times.iloc[local_i]
            if anchor_time < spec.scan_start_ts or anchor_time >= spec.scan_end_ts:
                counts["rejected_outside_scan"] += 1
                continue
            review_last_time = times.iloc[review_stop - 1]
            if review_last_time >= spec.holdout_start_ts:
                counts["rejected_review_touches_holdout"] += 1
                continue
            atr = float(featured["atr"].iloc[local_i])
            anchor_open = float(featured["open"].iloc[local_i])
            if not np.isfinite(atr) or atr <= 0.0 or anchor_open <= 0.0:
                counts["rejected_bad_atr_or_open"] += 1
                continue
            release = featured.iloc[local_i : local_i + spec.release_bars]
            first3_close_atr = direction * (
                float(release["close"].iloc[2]) - anchor_open
            ) / atr
            close12_atr = direction * (
                float(release["close"].iloc[-1]) - anchor_open
            ) / atr
            if direction == 1:
                favorable_price = float(release["high"].max())
                mfe_atr = (favorable_price - anchor_open) / atr
                favorable_pct = (favorable_price / anchor_open - 1.0) * 100.0
            else:
                favorable_price = float(release["low"].min())
                mfe_atr = (anchor_open - favorable_price) / atr
                favorable_pct = (anchor_open / favorable_price - 1.0) * 100.0
            first3_score = _score_component(first3_close_atr, spec.first3_reference_atr)
            close12_score = _score_component(close12_atr, spec.close12_reference_atr)
            mfe_score = _score_component(mfe_atr, spec.mfe_reference_atr)
            future_score = (
                spec.first3_weight * first3_score
                + spec.close12_weight * close12_score
                + spec.mfe_weight * mfe_score
            )
            formation_score = float(featured[f"dense_start_score_{side.lower()}"].iloc[local_i])
            completed_score = (
                spec.formation_weight * formation_score + spec.future_weight * future_score
            )
            prior_mas = featured.loc[local_i - 1, list(SIX_MA_COLUMNS)].to_numpy(dtype=float)
            prior_close = float(featured["close"].iloc[local_i - 1])
            source_i = int(featured["_source_i"].iloc[local_i])
            row = {
                "event_id": event_id(spec.protocol, symbol, side, anchor_time),
                "symbol": symbol,
                "direction": side,
                "source_path": str(source_path),
                "source_anchor_i": source_i,
                "segment_start_i": segment_source_start,
                "segment_end_i": segment_source_end,
                "anchor_time": anchor_time.isoformat(),
                "review_last_time": review_last_time.isoformat(),
                "anchor_open": anchor_open,
                "atr14_signal": atr,
                "ma_spread_before_pct": float(
                    (prior_mas.max() - prior_mas.min()) / prior_close * 100.0
                ),
                "formation_score": formation_score,
                "future_release_score": float(future_score),
                "completed_score": float(completed_score),
                "retrieval_distance": float(1.0 - completed_score),
                "first3_close_signed_atr": float(first3_close_atr),
                "release_close_signed_atr": float(close12_atr),
                "release_close_signed_pct": float(
                    direction * 100.0 * np.log(float(release["close"].iloc[-1]) / anchor_open)
                ),
                "release_favorable_atr": float(mfe_atr),
                "release_favorable_pct": float(favorable_pct),
                "feature_future_rows": 0,
                "selection_future_rows_beyond_t": spec.release_bars - 1,
                "review_only_extra_rows": spec.review_extra_bars,
                "owner_verdict": "PENDING",
                "training_eligible": False,
                "production_eligible": False,
            }
            rows.append(row)
            counts[f"{side.lower()}_eligible_before_dedupe"] += 1
    return rows, dict(counts)


def deduplicate_candidates(
    rows: Iterable[dict[str, Any]],
    *,
    spec: CandidateSpec,
) -> list[dict[str, Any]]:
    """Keep the highest score inside each same-symbol/side 56-hour neighborhood."""

    ordered = sorted(
        rows,
        key=lambda row: (
            -float(row["completed_score"]),
            str(row["symbol"]),
            str(row["anchor_time"]),
        ),
    )
    occupied: dict[tuple[str, str], list[pd.Timestamp]] = defaultdict(list)
    kept: list[dict[str, Any]] = []
    for row in ordered:
        key = (str(row["symbol"]), str(row["direction"]))
        stamp = utc(row["anchor_time"])
        stamps = occupied[key]
        insert_at = bisect_left(stamps, stamp)
        neighbors = stamps[max(0, insert_at - 1) : insert_at + 1]
        if any(abs(stamp - prior) <= spec.dedupe_timedelta for prior in neighbors):
            continue
        stamps.insert(insert_at, stamp)
        kept.append(row)
    return kept


def select_balanced_candidates(
    rows: Iterable[dict[str, Any]],
    *,
    spec: CandidateSpec,
    existing_rows: Iterable[Mapping[str, Any]] = (),
) -> dict[str, list[dict[str, Any]]]:
    """Select exact balanced additions under frozen union-pool quotas.

    ``existing_rows`` are immutable prior selections.  They seed symbol/day
    quotas and same-symbol/side exclusion intervals, so an expansion cannot
    silently duplicate or overcrowd the already delivered pool.
    """

    existing = [dict(row) for row in existing_rows]
    selected: dict[str, list[dict[str, Any]]] = {}
    for side in ("LONG", "SHORT"):
        side_rows = sorted(
            (row for row in rows if row["direction"] == side),
            key=lambda row: (
                -float(row["completed_score"]),
                str(row["symbol"]),
                str(row["anchor_time"]),
            ),
        )
        seeded = [row for row in existing if str(row["direction"]) == side]
        symbol_counts: Counter[str] = Counter(str(row["symbol"]) for row in seeded)
        day_counts: Counter[str] = Counter(
            utc(row["anchor_time"]).strftime("%Y-%m-%d") for row in seeded
        )
        occupied: dict[str, list[pd.Timestamp]] = defaultdict(list)
        for row in seeded:
            occupied[str(row["symbol"])].append(utc(row["anchor_time"]))
        for stamps in occupied.values():
            stamps.sort()
        chosen: list[dict[str, Any]] = []
        for row in side_rows:
            symbol = str(row["symbol"])
            stamp = utc(row["anchor_time"])
            day = utc(row["anchor_time"]).strftime("%Y-%m-%d")
            if symbol_counts[symbol] >= spec.max_per_symbol_per_side:
                continue
            if day_counts[day] >= spec.max_per_day_per_side:
                continue
            stamps = occupied[symbol]
            insert_at = bisect_left(stamps, stamp)
            neighbors = stamps[max(0, insert_at - 1) : insert_at + 1]
            if any(abs(stamp - prior) <= spec.dedupe_timedelta for prior in neighbors):
                continue
            chosen.append(dict(row))
            symbol_counts[symbol] += 1
            day_counts[day] += 1
            stamps.insert(insert_at, stamp)
            if len(chosen) == spec.target_per_side:
                break
        if len(chosen) != spec.target_per_side:
            raise CandidateCollectionError(
                f"{side} has only {len(chosen)} rows after frozen quotas; "
                f"requested {spec.target_per_side}"
            )
        for rank, row in enumerate(chosen, 1):
            row["rank"] = rank
        selected[side] = chosen
    return selected


def canonical_window_sha256(frame: pd.DataFrame) -> str:
    """Hash an already-bounded OHLCV window in a platform-stable encoding."""

    canonical = frame[list(OHLCV)].copy()
    canonical["open_time"] = pd.to_datetime(canonical["open_time"], utc=True).map(
        lambda value: value.isoformat()
    )
    payload = canonical.to_csv(index=False, float_format="%.17g", lineterminator="\n")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def audit_future_invariance(
    segment: pd.DataFrame,
    row: Mapping[str, Any],
    *,
    profile: DenseStartProfile,
) -> dict[str, Any]:
    """Prove every proposal feature through ``t`` ignores mutated future rows.

    The input must start at the same contiguous-segment boundary used by the
    scan.  OHLC and volume strictly after the selected anchor are changed by a
    large deterministic factor, then every MA, ATR and dense-start feature is
    recomputed.  Exact equality is required for every row through ``t`` and
    for both gate decisions.  Completed-path ranking fields are intentionally
    outside this causal null because they are review-only future descriptors.
    """

    original_input = segment.reset_index(drop=True).copy()
    matches = original_input.index[
        pd.to_datetime(original_input["open_time"], utc=True) == utc(row["anchor_time"])
    ]
    if len(matches) != 1:
        raise CandidateCollectionError(
            f"causality anchor missing/duplicated: {row['event_id']}"
        )
    anchor_i = int(matches[0])
    if anchor_i + 1 >= len(original_input):
        raise CandidateCollectionError(f"causality audit has no future: {row['event_id']}")

    mutated_input = original_input.copy()
    future = mutated_input.index > anchor_i
    for column in ("open", "high", "low", "close"):
        mutated_input.loc[future, column] = (
            mutated_input.loc[future, column].astype(float) * 7.0
        )
    mutated_input.loc[future, "volume"] = (
        mutated_input.loc[future, "volume"].astype(float) * 13.0 + 1.0
    )

    original = add_candidate_features(original_input)
    mutated = add_candidate_features(mutated_input)
    feature_columns = [
        *SIX_MA_COLUMNS,
        "atr",
        *(column for column in original.columns if column.startswith("dense_")),
    ]
    unequal: list[str] = []
    max_abs_difference = 0.0
    for column in feature_columns:
        before_series = original[column].iloc[: anchor_i + 1]
        after_series = mutated[column].iloc[: anchor_i + 1]
        before = before_series.to_numpy()
        after = after_series.to_numpy()
        if not before_series.equals(after_series):
            unequal.append(column)
            before_number = np.asarray(before, dtype=float)
            after_number = np.asarray(after, dtype=float)
            finite = np.isfinite(before_number) & np.isfinite(after_number)
            if finite.any():
                max_abs_difference = max(
                    max_abs_difference,
                    float(np.max(np.abs(before_number[finite] - after_number[finite]))),
                )
    gate_equal: dict[str, bool] = {}
    for side in ("long", "short"):
        before_gate = dense_start_gate_mask(original, profile, side=side).iloc[
            : anchor_i + 1
        ]
        after_gate = dense_start_gate_mask(mutated, profile, side=side).iloc[
            : anchor_i + 1
        ]
        gate_equal[side] = bool(before_gate.equals(after_gate))
    if unequal or not all(gate_equal.values()):
        raise CandidateCollectionError(
            f"future mutation changed causal features for {row['event_id']}: "
            f"columns={unequal} gates={gate_equal}"
        )
    return {
        "event_id": str(row["event_id"]),
        "prefix_rows_compared": anchor_i + 1,
        "future_rows_mutated": len(original_input) - anchor_i - 1,
        "feature_columns_compared": len(feature_columns),
        "unequal_columns": [],
        "gate_equal": gate_equal,
        "max_abs_difference": max_abs_difference,
        "passed": True,
    }


def render_review_chart(
    segment: pd.DataFrame,
    row: Mapping[str, Any],
    *,
    spec: CandidateSpec,
    output: Path,
) -> dict[str, Any]:
    """Render one 48-bar completed-shape review image with explicit boundaries."""

    source = segment.reset_index(drop=True)
    featured = (
        source.copy()
        if set(SIX_MA_COLUMNS).issubset(source.columns)
        else add_six_mas(source)
    )
    matches = featured.index[
        pd.to_datetime(featured["open_time"], utc=True) == utc(row["anchor_time"])
    ]
    if len(matches) != 1:
        raise CandidateCollectionError(f"anchor missing/duplicated during render: {row['event_id']}")
    anchor_i = int(matches[0])
    start = anchor_i - spec.pre_bars
    stop = anchor_i + spec.release_bars + spec.review_extra_bars
    review = featured.iloc[start:stop].reset_index(drop=True)
    causal = featured.iloc[anchor_i - spec.causal_input_bars + 1 : anchor_i + 1].reset_index(drop=True)
    if len(review) != spec.review_bars or len(causal) != spec.causal_input_bars:
        raise CandidateCollectionError(f"short render/causal window: {row['event_id']}")
    if utc(review["open_time"].iloc[-1]) >= spec.holdout_start_ts:
        raise CandidateCollectionError(f"review window touches holdout: {row['event_id']}")

    chart, transform = render_chart(review, width=1280, height=680)
    canvas = np.full((770, 1280, 3), 255, dtype=np.uint8)
    canvas[70:750] = chart
    marker_local_i = spec.pre_bars + spec.review_marker_offset_bars
    marker_x = transform.x_at(marker_local_i)
    anchor_x = transform.x_at(spec.pre_bars)
    release_end_x = transform.x_at(spec.pre_bars + spec.release_bars - 1)
    cv2.line(canvas, (marker_x, 70), (marker_x, 750), (210, 115, 32), 2, cv2.LINE_AA)
    if spec.review_marker_offset_bars != 0:
        for top in range(70, 750, 14):
            cv2.line(
                canvas,
                (anchor_x, top),
                (anchor_x, min(top + 7, 750)),
                (0, 145, 235),
                1,
                cv2.LINE_AA,
            )
    cv2.line(canvas, (release_end_x, 70), (release_end_x, 750), (100, 100, 100), 1, cv2.LINE_AA)
    side = str(row["direction"])
    color = (170, 92, 25) if side == "LONG" else (70, 70, 190)
    rank_width = max(3, len(str(spec.target_per_side)))
    title = (
        f"{side} #{int(row['rank']):0{rank_width}d}  {row['symbol']}  15m  "
        f"{utc(row['anchor_time']).strftime('%Y-%m-%d %H:%M UTC')}"
    )
    metrics = (
        f"score {float(row['completed_score']):.3f} | formation {float(row['formation_score']):.3f} | "
        f"12-bar close {float(row['release_close_signed_atr']):+.2f} ATR | "
        f"MFE {float(row['release_favorable_atr']):.2f} ATR | rope {float(row['ma_spread_before_pct']):.3f}%"
    )
    cv2.putText(canvas, title, (18, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.72, color, 2, cv2.LINE_AA)
    cv2.putText(canvas, metrics, (18, 58), cv2.FONT_HERSHEY_SIMPLEX, 0.53, (55, 62, 69), 1, cv2.LINE_AA)
    marker_note = (
        "blue = completed release bar t"
        if spec.review_marker_offset_bars == 0
        else f"blue = requested review marker t{spec.review_marker_offset_bars:+d} | orange dash = selection bar t"
    )
    cv2.putText(
        canvas,
        f"{marker_note} | gray = 12-bar path end | REVIEW ONLY; no training image/label generated",
        (18, 767),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.43,
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
        "review_window_sha256": canonical_window_sha256(review),
        "causal_input_sha256": canonical_window_sha256(causal),
        "review_bars": len(review),
        "causal_input_bars": len(causal),
        "review_marker_offset_bars": spec.review_marker_offset_bars,
        "review_marker_time": utc(review["open_time"].iloc[marker_local_i]).isoformat(),
        "review_marker_source_i": int(row["source_anchor_i"])
        + spec.review_marker_offset_bars,
        "review_marker_is_training_label": False,
    }


def build_overview(
    image_paths: Sequence[Path],
    *,
    output: Path,
    columns: int = 5,
    thumb_width: int = 384,
) -> None:
    """Build a compact top-candidate contact sheet."""

    if not image_paths:
        raise CandidateCollectionError("overview needs images")
    images: list[np.ndarray] = []
    for path in image_paths:
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None:
            raise OSError(f"could not reopen {path}")
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
        row_i, column_i = divmod(index, columns)
        y, x = row_i * thumb_height, column_i * thumb_width
        canvas[y : y + image.shape[0], x : x + image.shape[1]] = image
    output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), canvas):
        raise OSError(f"failed to write overview: {output}")


def build_gallery(rows: Sequence[Mapping[str, Any]], *, output: Path) -> None:
    """Write a local lazy-loading gallery for all PENDING review candidates."""

    cards: list[str] = []
    rank_width = max(3, len(str(max((int(row["rank"]) for row in rows), default=0))))
    for row in rows:
        # The public manifest stores repository-relative paths.  The gallery
        # sits beside ``review_charts/``, so it must not depend on the caller's
        # current working directory or on an absolute repository path.
        relative_image = Path("review_charts") / Path(str(row["review_path"])).name
        side = html.escape(str(row["direction"]))
        symbol = html.escape(str(row["symbol"]))
        cards.append(
            f'''<article class="card" data-side="{side}" data-text="{symbol.lower()} {side.lower()}">
  <img loading="lazy" src="{html.escape(relative_image.as_posix())}" alt="{side} {symbol}">
  <div class="meta"><b>{side} #{int(row['rank']):0{rank_width}d} · {symbol}</b><br>
  {html.escape(str(row['anchor_time']))}<br>
  score {float(row['completed_score']):.3f} · close {float(row['release_close_signed_atr']):+.2f} ATR · MFE {float(row['release_favorable_atr']):.2f} ATR<br>
  <span>PENDING · candidate, not positive label</span></div>
</article>'''
        )
    document = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>15m MA launch candidates {len(rows)}</title>
<style>body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:0;background:#f5f7fa;color:#18212b}}
header{{position:sticky;top:0;z-index:2;background:#fff;padding:14px 20px;border-bottom:1px solid #dfe5ec}}h1{{margin:0 0 8px;font-size:22px}}
.note{{font-size:13px;color:#5b6673}}.filters{{display:flex;gap:8px;margin-top:10px}}button,input{{padding:8px 12px;border:1px solid #cbd5df;border-radius:8px;background:#fff}}
main{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;padding:14px}}.card{{background:#fff;border:1px solid #dfe5ec;border-radius:10px;overflow:hidden}}
.card img{{display:block;width:100%;height:auto}}.meta{{padding:9px 11px;font-size:12px;line-height:1.5}}.meta span{{color:#a04416}}.hidden{{display:none}}
@media(max-width:1000px){{main{{grid-template-columns:1fr}}}}</style></head><body>
<header><h1>15m 六均线密集启动候选池 · {len(rows)}</h1><div class="note">LONG {sum(str(row['direction']) == 'LONG' for row in rows)} + SHORT {sum(str(row['direction']) == 'SHORT' for row in rows)}；全部 PENDING。蓝线是审核标记，灰线是 12 根路径结束；未来仅供审核，没有生成训练图或标签。</div>
<div class="filters"><button onclick="filterSide('ALL')">全部</button><button onclick="filterSide('LONG')">LONG</button><button onclick="filterSide('SHORT')">SHORT</button><input id="q" placeholder="搜索币种" oninput="apply()"></div></header>
<main>{''.join(cards)}</main><script>let side='ALL';function filterSide(v){{side=v;apply()}}function apply(){{const q=document.getElementById('q').value.toLowerCase();document.querySelectorAll('.card').forEach(c=>c.classList.toggle('hidden',(side!=='ALL'&&c.dataset.side!==side)||(q&&!c.dataset.text.includes(q))))}}</script>
</body></html>'''
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def spec_json(spec: CandidateSpec) -> dict[str, Any]:
    """Expose the executable defaults for unit-test and report audits."""

    return asdict(spec)
