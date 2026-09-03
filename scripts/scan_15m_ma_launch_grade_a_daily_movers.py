#!/usr/bin/env python3
"""Mine a frozen pre-holdout daily-mover board with the Grade-A YOLO.

The scanner reads only official Binance USD-M monthly 15-minute archives for
October 2025.  For each complete UTC day it freezes the five largest positive
and five largest negative open-to-close returns, then scores W18/W19 windows
with the existing Grade-A full40 native-1280 checkpoint.  Same-day rank is
known only after the day closes: it is an offline P1 discovery stratum, never a
causal selector, model feature, backtest, or trading signal.

Model inputs contain ``open/high/low/close`` plus causal SMA/EMA 20/60/120.
The inherited Pine-RMA ATR14 and frozen morphology gate are evaluated only
through each model endpoint.  A proposal is attributed to a board only when
its mapped core ends inside that UTC day.  Full-day context images are written
to a physically separate review directory and are never training inputs.

This script does not fetch network data, read holdout OHLCV, mutate labels or
datasets, train, tune, promote, deploy, write forward state, send messages, or
place orders.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import shutil
import subprocess
import sys
import time
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import cv2
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import scan_15m_ma_launch_t3_daily_movers as common  # noqa: E402
from scripts import scan_4h_ma_launch_yolo_latest as base  # noqa: E402
from scripts import scan_crypto_grade_a_yolo_mtf_latest as latest  # noqa: E402
from yoyo.datasets.fifteen_minute_launch_candidates import (  # noqa: E402
    add_candidate_features,
)
from yoyo.layers.l1_detection.data import ALL_MA_COLS  # noqa: E402
from yoyo.layers.l1_detection.render import ChartTransform, render_chart  # noqa: E402


EXPERIMENT_ID = "exp-15m-ma-launch-grade-a-daily-movers-202510-v1"
DEFAULT_PREREG = ROOT / "experiments" / "active" / EXPERIMENT_ID / "preregistration.json"
DEFAULT_OUT = ROOT / "experiments" / "active" / EXPERIMENT_ID / "results"
PARENT_GATE_PREREG = (
    ROOT
    / "experiments/active/exp-15m-ma-launch-owner-yolo-causal-semantic-gate-v1"
    / "preregistration.json"
)
BAR_DELTA = pd.Timedelta(minutes=15)
CSV_COLUMNS = ("open_time", "open", "high", "low", "close", "volume")
HOLDOUT_START = pd.Timestamp("2026-05-04T00:00:00Z")


class DailyMoversError(RuntimeError):
    """Fail closed on source, time, model, lineage, or artifact drift."""


def utc(value: object) -> pd.Timestamp:
    """Normalize one timestamp to UTC."""

    stamp = pd.Timestamp(value)
    return stamp.tz_convert("UTC") if stamp.tzinfo else stamp.tz_localize("UTC")


def sha256_file(path: Path) -> str:
    """Return a streaming SHA-256 digest."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pixel_sha256(image: np.ndarray) -> str:
    """Hash decoded BGR pixels, independent of PNG compression metadata."""

    return hashlib.sha256(np.ascontiguousarray(image).tobytes()).hexdigest()


def repo_relative(path: Path) -> str:
    """Serialize one repository-relative path."""

    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def stable_json(value: Any) -> str:
    """Serialize deterministic JSON for JSONL identities."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    """Write one readable deterministic JSON receipt."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    """Write deterministic JSON Lines."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(stable_json(dict(row)) + "\n")


def load_preregistration(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load and enforce the exact pre-holdout, no-mutation scan contract."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("experiment_id") != EXPERIMENT_ID:
        raise DailyMoversError("unexpected experiment_id")
    start = utc(payload["calendar"]["start_inclusive"])
    end = utc(payload["calendar"]["end_exclusive"])
    if start != pd.Timestamp("2025-10-01T00:00:00Z"):
        raise DailyMoversError("calendar start drifted")
    if end != pd.Timestamp("2025-11-01T00:00:00Z") or end >= HOLDOUT_START:
        raise DailyMoversError("calendar end drifted or reached holdout")
    if int(payload["calendar"]["complete_days"]) != 31:
        raise DailyMoversError("complete-day count drifted")

    ranking = payload["ranking"]
    if int(ranking["top_gainers_per_day"]) != 5:
        raise DailyMoversError("gainer count drifted")
    if int(ranking["top_losers_per_day"]) != 5:
        raise DailyMoversError("loser count drifted")
    if ranking.get("causality") != "post_hoc_same_day_ranking_for_candidate_mining_only":
        raise DailyMoversError("ranking causality disclosure drifted")

    detector = payload["detector"]
    expected = {
        "weights_sha256": base.EXPECTED_WEIGHT_SHA256,
        "confidence": base.CONFIDENCE,
        "nms_iou": base.NMS_IOU,
        "imgsz": base.IMAGE_SIZE,
    }
    for key, value in expected.items():
        if detector.get(key) != value:
            raise DailyMoversError(f"detector {key} drifted")
    if tuple(map(int, detector["window_lengths"])) != base.WINDOW_LENGTHS:
        raise DailyMoversError("window lengths drifted")
    if set(map(int, detector["mapped_core_length_bars_allowed"])) != set(base.ALLOWED_CORES):
        raise DailyMoversError("core lengths drifted")
    if set(map(int, detector["mapped_confirmation_bars_allowed"])) != set(
        base.ALLOWED_CONFIRMATIONS
    ):
        raise DailyMoversError("confirmation lengths drifted")
    if int(detector["scan_endpoint_extension_after_day_bars"]) != 9:
        raise DailyMoversError("endpoint extension drifted")
    if int(detector["minimum_contiguous_history_bars_at_endpoint"]) != 140:
        raise DailyMoversError("history floor drifted")
    if detector.get("threshold_or_window_retuning_after_results") is not False:
        raise DailyMoversError("retuning must remain disabled")

    if any(bool(value) for value in payload["safety"].values()):
        raise DailyMoversError("one or more safety mutation switches are enabled")
    if payload["owner_authorization"].get("holdout_read_authorized") is not False:
        raise DailyMoversError("this experiment must remain pre-holdout")

    parent = json.loads(PARENT_GATE_PREREG.read_text(encoding="utf-8"))
    gates = dict(parent["treatment"]["frozen_morphology_gate"])
    if gates != dict(payload["semantic_gate"]["frozen_morphology_gate"]):
        raise DailyMoversError("semantic gate differs from frozen parent")
    return payload, gates


def verify_immutable_sources(prereg_path: Path, prereg: Mapping[str, Any]) -> str:
    """Require main, committed builder/prereg, and every pinned source hash."""

    branch = subprocess.check_output(
        ["git", "branch", "--show-current"], cwd=ROOT, text=True
    ).strip()
    if branch != "main":
        raise DailyMoversError("official scan must run on main")
    script = Path(__file__).resolve()
    paths = [script.relative_to(ROOT), prereg_path.resolve().relative_to(ROOT)]
    dirty = subprocess.check_output(
        ["git", "status", "--short", "--", *map(str, paths)], cwd=ROOT, text=True
    ).strip()
    if dirty:
        raise DailyMoversError(f"builder and preregistration must be committed:\n{dirty}")
    source_commit = subprocess.check_output(
        ["git", "log", "-1", "--format=%H", "--", str(paths[0])],
        cwd=ROOT,
        text=True,
    ).strip()
    if source_commit != str(prereg.get("source_commit")):
        raise DailyMoversError(
            f"builder commit {source_commit} differs from preregistration binding"
        )

    pinned = (
        (Path(prereg["data"]["archive_fetch_summary"]), prereg["data"]["archive_fetch_summary_sha256"]),
        (Path(prereg["data"]["admitted_symbols"]), prereg["data"]["admitted_symbols_sha256"]),
        (Path(prereg["detector"]["training_manifest"]), prereg["detector"]["training_manifest_sha256"]),
        (Path(prereg["semantic_gate"]["parent"]), prereg["semantic_gate"]["parent_sha256"]),
        (Path(prereg["detector"]["weights"]), prereg["detector"]["weights_sha256"]),
    )
    for relative, expected in pinned:
        path = ROOT / relative
        if not path.is_file():
            raise DailyMoversError(f"pinned source missing: {relative}")
        actual = sha256_file(path)
        if actual != expected:
            raise DailyMoversError(f"pinned source SHA drift: {relative}")
    return source_commit


def month_archive_path(root: Path, symbol: str, month: str) -> Path:
    """Return the canonical monthly Binance archive path."""

    return root / symbol / f"{symbol}-15m-{month}.zip"


def read_month_archive(path: Path, *, symbol: str, month: str) -> pd.DataFrame:
    """Read and validate one frozen official monthly 15-minute archive."""

    if not path.is_file():
        raise FileNotFoundError(path)
    with zipfile.ZipFile(path) as bundle:
        names = [name for name in bundle.namelist() if name.lower().endswith(".csv")]
        if len(names) != 1:
            raise DailyMoversError(f"{path} must contain exactly one CSV")
        with bundle.open(names[0]) as handle:
            frame = pd.read_csv(handle, usecols=list(CSV_COLUMNS))
    if frame.empty:
        raise DailyMoversError(f"empty archive: {path}")
    raw_time = pd.to_numeric(frame["open_time"], errors="raise")
    epoch_unit = "us" if float(raw_time.max()) >= 1e14 else "ms"
    frame["open_time"] = pd.to_datetime(raw_time.astype("int64"), unit=epoch_unit, utc=True)
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    numeric = frame[["open", "high", "low", "close", "volume"]].to_numpy(dtype=float)
    if not bool(np.isfinite(numeric).all()):
        raise DailyMoversError(f"non-finite OHLCV: {path}")
    if bool((frame[["open", "high", "low", "close"]] <= 0.0).any().any()):
        raise DailyMoversError(f"non-positive OHLC: {path}")
    if bool((frame["high"] < frame[["open", "close"]].max(axis=1)).any()):
        raise DailyMoversError(f"high below candle body: {path}")
    if bool((frame["low"] > frame[["open", "close"]].min(axis=1)).any()):
        raise DailyMoversError(f"low above candle body: {path}")
    times = frame["open_time"]
    if times.duplicated().any() or not times.is_monotonic_increasing:
        raise DailyMoversError(f"duplicate or descending timestamps: {path}")
    expected_month = pd.Period(month, freq="M")
    actual_months = set(times.dt.tz_localize(None).dt.to_period("M"))
    if actual_months != {expected_month}:
        raise DailyMoversError(f"archive month drift for {symbol}: {actual_months}")
    if utc(times.iloc[-1]) >= HOLDOUT_START:
        raise DailyMoversError(f"archive reached holdout: {path}")
    frame["exchange_symbol"] = symbol
    return frame.loc[:, [*CSV_COLUMNS, "exchange_symbol"]]


def _is_exact_day(frame: pd.DataFrame, day: pd.Timestamp) -> bool:
    """Return whether a group is the exact 96-bar UTC day."""

    if len(frame) != 96:
        return False
    expected = pd.date_range(day, periods=96, freq=BAR_DELTA)
    actual = pd.DatetimeIndex(pd.to_datetime(frame["open_time"], utc=True))
    return bool(actual.equals(expected))


def select_daily_board(
    rows: Sequence[Mapping[str, Any]], *, gainers: int, losers: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Select deterministic positive and negative return tails for one day."""

    top = sorted(
        (dict(row) for row in rows if float(row["daily_return"]) > 0.0),
        key=lambda row: (-float(row["daily_return"]), str(row["exchange_symbol"])),
    )[:gainers]
    bottom = sorted(
        (dict(row) for row in rows if float(row["daily_return"]) < 0.0),
        key=lambda row: (float(row["daily_return"]), str(row["exchange_symbol"])),
    )[:losers]
    return top, bottom


def build_daily_rankings(
    prereg: Mapping[str, Any], *, archive_root: Path
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Reconstruct every October symbol-day and freeze balanced mover boards."""

    admitted = json.loads((ROOT / prereg["data"]["admitted_symbols"]).read_text(encoding="utf-8"))
    symbols = sorted({str(row["symbol"]) for row in admitted})
    start = utc(prereg["calendar"]["start_inclusive"])
    end = utc(prereg["calendar"]["end_exclusive"])
    target_days = list(pd.date_range(start, end - pd.Timedelta(days=1), freq="D"))
    universe_rows: list[dict[str, Any]] = []
    archive_rows: list[dict[str, Any]] = []
    for index, symbol in enumerate(symbols, 1):
        path = month_archive_path(archive_root, symbol, "2025-10")
        if not path.is_file():
            continue
        frame = read_month_archive(path, symbol=symbol, month="2025-10")
        archive_rows.append(
            {
                "role": "ranking_month",
                "symbol": symbol,
                "month": "2025-10",
                "path": repo_relative(path),
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
                "rows": len(frame),
                "first_bar_open": utc(frame.iloc[0]["open_time"]).isoformat(),
                "last_bar_open": utc(frame.iloc[-1]["open_time"]).isoformat(),
            }
        )
        for day, part in frame.groupby(frame["open_time"].dt.floor("D"), sort=True):
            day = utc(day)
            if day < start or day >= end or not _is_exact_day(part, day):
                continue
            open_px = float(part.iloc[0]["open"])
            close_px = float(part.iloc[-1]["close"])
            universe_rows.append(
                {
                    "day": day.isoformat(),
                    "exchange_symbol": symbol,
                    "daily_return": close_px / open_px - 1.0,
                    "open": open_px,
                    "close": close_px,
                    "base_volume": float(part["volume"].sum()),
                    "bars": 96,
                    "source_zip_sha256": archive_rows[-1]["sha256"],
                }
            )
        if index % 100 == 0 or index == len(symbols):
            print(
                f"ranking archives {index}/{len(symbols)} complete_symbol_days={len(universe_rows)}",
                flush=True,
            )

    by_day: defaultdict[pd.Timestamp, list[dict[str, Any]]] = defaultdict(list)
    for row in universe_rows:
        by_day[utc(row["day"])].append(row)
    rankings: list[dict[str, Any]] = []
    gain_n = int(prereg["ranking"]["top_gainers_per_day"])
    loss_n = int(prereg["ranking"]["top_losers_per_day"])
    for day in target_days:
        rows = by_day.get(utc(day), [])
        gainers, losers = select_daily_board(rows, gainers=gain_n, losers=loss_n)
        if len(gainers) != gain_n or len(losers) != loss_n:
            raise DailyMoversError(f"{day:%Y-%m-%d} lacks five gainers or losers")
        for bucket, selected in (("gainer", gainers), ("loser", losers)):
            for bucket_rank, source in enumerate(selected, 1):
                row = dict(source)
                row.update(
                    {
                        "mover_bucket": bucket,
                        "bucket_rank": bucket_rank,
                        "rank_label": f"{'G' if bucket == 'gainer' else 'L'}{bucket_rank}",
                        "board_order": bucket_rank if bucket == "gainer" else gain_n + bucket_rank,
                        "rank": bucket_rank if bucket == "gainer" else gain_n + bucket_rank,
                        "eligible_symbol_days": len(rows),
                    }
                )
                rankings.append(row)
        print(
            f"{day:%Y-%m-%d} universe={len(rows)} G5={gainers[-1]['daily_return']*100:+.2f}% "
            f"L5={losers[-1]['daily_return']*100:+.2f}%",
            flush=True,
        )
    if len(rankings) != 31 * (gain_n + loss_n):
        raise DailyMoversError("ranked symbol-day count drifted")
    universe_rows.sort(key=lambda row: (str(row["day"]), str(row["exchange_symbol"])))
    rankings.sort(key=lambda row: (str(row["day"]), int(row["board_order"])))
    archive_rows.sort(key=lambda row: str(row["symbol"]))
    return universe_rows, rankings, archive_rows


def load_selected_frames(
    prereg: Mapping[str, Any],
    *,
    archive_root: Path,
    symbols: Sequence[str],
) -> tuple[dict[str, pd.DataFrame], list[dict[str, Any]]]:
    """Load only selected symbols and bounded adjacent-month causal context."""

    start = utc(prereg["calendar"]["start_inclusive"]) - pd.Timedelta(days=3)
    end = utc(prereg["calendar"]["end_exclusive"]) + pd.Timedelta(days=1)
    months = list(map(str, prereg["data"]["context_months"]))
    frames: dict[str, pd.DataFrame] = {}
    audits: list[dict[str, Any]] = []
    for index, symbol in enumerate(sorted(set(symbols)), 1):
        pieces: list[pd.DataFrame] = []
        for month in months:
            path = month_archive_path(archive_root, symbol, month)
            if not path.is_file():
                continue
            piece = read_month_archive(path, symbol=symbol, month=month)
            pieces.append(piece)
            audits.append(
                {
                    "role": "selected_symbol_context",
                    "symbol": symbol,
                    "month": month,
                    "path": repo_relative(path),
                    "sha256": sha256_file(path),
                    "size_bytes": path.stat().st_size,
                    "rows": len(piece),
                    "first_bar_open": utc(piece.iloc[0]["open_time"]).isoformat(),
                    "last_bar_open": utc(piece.iloc[-1]["open_time"]).isoformat(),
                }
            )
        if not pieces:
            raise DailyMoversError(f"selected symbol has no context archives: {symbol}")
        frame = pd.concat(pieces, ignore_index=True)
        frame = frame.loc[(frame["open_time"] >= start) & (frame["open_time"] < end)].copy()
        frame = frame.drop_duplicates("open_time", keep="last").sort_values("open_time").reset_index(drop=True)
        if frame.empty or utc(frame.iloc[-1]["open_time"]) >= HOLDOUT_START:
            raise DailyMoversError(f"invalid selected frame: {symbol}")
        frames[symbol] = add_candidate_features(frame)
        if index % 50 == 0 or index == len(set(symbols)):
            print(f"context {index}/{len(set(symbols))} {symbol} rows={len(frame)}", flush=True)
    audits.sort(key=lambda row: (str(row["symbol"]), str(row["month"])))
    return frames, audits


class MoverTaskSequence(Sequence[tuple[np.ndarray, ChartTransform, dict[str, Any]]]):
    """Lazily render bounded symbol-day model inputs one inference batch at a time."""

    def __init__(
        self,
        frames: Mapping[str, pd.DataFrame],
        specs: Sequence[Mapping[str, Any]],
    ) -> None:
        self._frames = frames
        self._specs = tuple(dict(spec) for spec in specs)

    def __len__(self) -> int:
        return len(self._specs)

    def __getitem__(
        self, index: int | slice
    ) -> tuple[np.ndarray, ChartTransform, dict[str, Any]] | list[
        tuple[np.ndarray, ChartTransform, dict[str, Any]]
    ]:
        if isinstance(index, slice):
            return [self[item] for item in range(*index.indices(len(self)))]
        spec = dict(self._specs[index])
        frame = self._frames[str(spec["symbol"])]
        start_i = int(spec["window_start_i"])
        end_i = int(spec["window_end_i"])
        image, transform = render_chart(frame.iloc[start_i : end_i + 1], out_path=None)
        spec["window_end_time"] = utc(frame.iloc[end_i]["open_time"]).isoformat()
        spec["input_pixel_sha256"] = pixel_sha256(image)
        return image, transform, spec


def build_tasks(
    prereg: Mapping[str, Any],
    *,
    frames: Mapping[str, pd.DataFrame],
    rankings: Sequence[Mapping[str, Any]],
) -> tuple[MoverTaskSequence, list[dict[str, Any]]]:
    """Build causal W18/W19 tasks and explicit unscannable-board audits."""

    extension = int(prereg["detector"]["scan_endpoint_extension_after_day_bars"])
    history = int(prereg["detector"]["minimum_contiguous_history_bars_at_endpoint"])
    specs: list[dict[str, Any]] = []
    board_audits: list[dict[str, Any]] = []
    for board in rankings:
        symbol = str(board["exchange_symbol"])
        frame = frames[symbol]
        times = pd.DatetimeIndex(pd.to_datetime(frame["open_time"], utc=True))
        bad = np.r_[0, (times[1:] - times[:-1] != BAR_DELTA).astype(np.int64)]
        bad_prefix = np.cumsum(bad)
        day = utc(board["day"])
        endpoint_end = day + pd.Timedelta(days=1) + extension * BAR_DELTA
        endpoints = np.flatnonzero((times >= day) & (times < endpoint_end))
        skip: Counter[str] = Counter()
        before = len(specs)
        for endpoint_raw in endpoints:
            endpoint = int(endpoint_raw)
            history_start = endpoint - history + 1
            if history_start < 0:
                skip["insufficient_history"] += len(base.WINDOW_LENGTHS)
                continue
            gap_count = int(bad_prefix[endpoint] - bad_prefix[history_start])
            if gap_count:
                skip["non_contiguous_history"] += len(base.WINDOW_LENGTHS)
                continue
            for window_len in base.WINDOW_LENGTHS:
                window_start = endpoint - int(window_len) + 1
                if window_start < 0:
                    skip["insufficient_window"] += 1
                    continue
                window = frame.iloc[window_start : endpoint + 1]
                required = [*ALL_MA_COLS, "atr"]
                if window.loc[:, required].isna().any().any():
                    skip["non_finite_features"] += 1
                    continue
                specs.append(
                    {
                        "symbol": symbol,
                        "exchange_symbol": symbol,
                        "day": day.isoformat(),
                        "mover_bucket": str(board["mover_bucket"]),
                        "bucket_rank": int(board["bucket_rank"]),
                        "rank_label": str(board["rank_label"]),
                        "board_order": int(board["board_order"]),
                        "daily_return": float(board["daily_return"]),
                        "eligible_symbol_days": int(board["eligible_symbol_days"]),
                        "window_len": int(window_len),
                        "window_start_i": window_start,
                        "window_end_i": endpoint,
                    }
                )
        board_audits.append(
            {
                "day": day.isoformat(),
                "exchange_symbol": symbol,
                "rank_label": str(board["rank_label"]),
                "endpoint_bars_present": len(endpoints),
                "model_windows": len(specs) - before,
                "scannable": len(specs) > before,
                "skip_counts": dict(sorted(skip.items())),
            }
        )
    return MoverTaskSequence(frames, specs), board_audits


def load_training_index(prereg: Mapping[str, Any]) -> dict[str, Any]:
    """Index exact training windows and unique positive events without mutation."""

    manifest = ROOT / prereg["detector"]["training_manifest"]
    exact: defaultdict[tuple[str, str, str, int], list[dict[str, Any]]] = defaultdict(list)
    positives: defaultdict[tuple[str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    first_val: pd.Timestamp | None = None
    rows = 0
    with manifest.open("r", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            rows += 1
            if str(row.get("venue")) != "binance_um":
                continue
            key = (
                str(row.get("exchange_symbol")),
                utc(row["window_start_time"]).isoformat(),
                utc(row["window_end_time"]).isoformat(),
                int(row["window_bars"]),
            )
            exact[key].append(
                {
                    "sample_kind": str(row["sample_kind"]),
                    "split": str(row["split"]),
                    "dataset_sample_id": str(row["dataset_sample_id"]),
                    "event_id": str(row.get("event_id") or ""),
                    "image_path": str(row["image_path"]),
                    "image_sha256": str(row["image_sha256"]),
                }
            )
            if str(row["split"]) == "val":
                candidate = utc(row["window_start_time"])
                first_val = candidate if first_val is None else min(first_val, candidate)
            if str(row["sample_kind"]) == "positive":
                direction = str(row["direction"]).upper()
                positives[(str(row["exchange_symbol"]), direction)][str(row["event_id"])] = {
                    "event_id": str(row["event_id"]),
                    "core_end_time": utc(row["core_end_time"]).isoformat(),
                    "split": str(row["split"]),
                }
    expected_first = utc(prereg["detector"]["existing_validation_first_window_start"])
    if first_val != expected_first:
        raise DailyMoversError(f"training validation boundary drifted: {first_val}")
    return {
        "rows": rows,
        "exact": dict(exact),
        "positive_events": {key: list(value.values()) for key, value in positives.items()},
        "first_validation_window_start": first_val.isoformat(),
    }


def _training_image_pixel_hash(relative: str) -> str:
    """Decode one frozen training PNG and hash its BGR pixels."""

    path = ROOT / "datasets/ma_launch_owner_grade_a8000_yolo_neg24000_v1" / relative
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise DailyMoversError(f"could not decode training image: {relative}")
    return pixel_sha256(image)


def annotate_training_overlap(
    candidates: Sequence[Mapping[str, Any]], training: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Mark exact input reuse and same-event proximity for every proposal."""

    output: list[dict[str, Any]] = []
    decoded_cache: dict[str, str] = {}
    exact_index = training["exact"]
    positive_index = training["positive_events"]
    for source in candidates:
        row = dict(source)
        exact_key = (
            str(row["exchange_symbol"]),
            utc(pd.Timestamp(row["window_end_time"]) - (int(row["window_len"]) - 1) * BAR_DELTA).isoformat(),
            utc(row["window_end_time"]).isoformat(),
            int(row["window_len"]),
        )
        coordinate_matches = list(exact_index.get(exact_key, []))
        pixel_matches: list[dict[str, Any]] = []
        for match in coordinate_matches:
            image_path = str(match["image_path"])
            if image_path not in decoded_cache:
                decoded_cache[image_path] = _training_image_pixel_hash(image_path)
            if decoded_cache[image_path] == str(row["input_pixel_sha256"]):
                pixel_matches.append(match)

        direction = "LONG" if int(row["class_id"]) == 0 else "SHORT"
        core_end = utc(row["core_end_time"])
        nearby: list[tuple[int, dict[str, Any]]] = []
        for event in positive_index.get((str(row["exchange_symbol"]), direction), []):
            delta_bars = int(abs((core_end - utc(event["core_end_time"])) / BAR_DELTA))
            if delta_bars < int(base.EVENT_GAP_BARS):
                nearby.append((delta_bars, dict(event)))
        nearby.sort(key=lambda item: (item[0], str(item[1]["event_id"])))
        if pixel_matches:
            novelty = "exact_training_input"
        elif nearby:
            novelty = "same_training_positive_event"
        else:
            novelty = "new_event_review"
        row.update(
            {
                "exact_training_coordinate_matches": len(coordinate_matches),
                "exact_training_input_matches": len(pixel_matches),
                "exact_training_sample_kinds": sorted({item["sample_kind"] for item in pixel_matches}),
                "exact_training_splits": sorted({item["split"] for item in pixel_matches}),
                "exact_training_sample_ids": sorted({item["dataset_sample_id"] for item in pixel_matches}),
                "near_training_positive_event": bool(nearby),
                "nearest_training_positive_event_id": nearby[0][1]["event_id"] if nearby else None,
                "nearest_training_positive_core_end_distance_bars": nearby[0][0] if nearby else None,
                "novelty_status": novelty,
            }
        )
        output.append(row)
    return output


def deduplicate_review_events(
    decisions: Sequence[Mapping[str, Any]], *, gap_bars: int
) -> list[dict[str, Any]]:
    """Collapse adjacent W18/W19 proposals into one symbol-day review event."""

    grouped: defaultdict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for decision in decisions:
        grouped[(str(decision["day"]), str(decision["exchange_symbol"]))].append(dict(decision))
    events: list[dict[str, Any]] = []
    for (day, symbol), rows in sorted(grouped.items()):
        peaks = common.deduplicate_hits(rows, gap_bars=gap_bars)
        for peak in peaks:
            related = [
                row
                for row in rows
                if abs(int(row["core_end_i"]) - int(peak["core_end_i"])) < gap_bars
            ]
            semantic = [row for row in related if bool(row["semantic_gate_pass"])]
            representative_pool = semantic or related
            last_endpoint = max(utc(row["window_end_time"]) for row in representative_pool)
            representative = max(
                (row for row in representative_pool if utc(row["window_end_time"]) == last_endpoint),
                key=lambda row: (float(row["confidence"]), -int(row["window_len"])),
            )
            event = dict(representative)
            any_exact = any(int(row["exact_training_input_matches"]) > 0 for row in related)
            any_near = any(bool(row["near_training_positive_event"]) for row in related)
            novelty = (
                "exact_training_input"
                if any_exact
                else "same_training_positive_event"
                if any_near
                else "new_event_review"
            )
            first_endpoint = min(utc(row["window_end_time"]) for row in related)
            event.update(
                {
                    "semantic_gate_pass": bool(semantic),
                    "review_bucket": "candidate_positive" if semantic else "candidate_hard_negative",
                    "first_detection_bar_open_time": first_endpoint.isoformat(),
                    "first_available_at": (first_endpoint + BAR_DELTA).isoformat(),
                    "last_detection_bar_open_time": last_endpoint.isoformat(),
                    "event_peak_confidence": max(float(row["confidence"]) for row in representative_pool),
                    "candidate_count": len(related),
                    "semantic_candidate_count": len(semantic),
                    "classes_observed": sorted({str(row["class_name"]) for row in related}),
                    "event_has_exact_training_input": any_exact,
                    "event_near_training_positive": any_near,
                    "novelty_status": novelty,
                    "ranking_is_post_hoc": True,
                }
            )
            direction = "LONG" if int(event["class_id"]) == 0 else "SHORT"
            event["model_direction"] = direction
            event["direction_matches_completed_day"] = (
                str(event["mover_bucket"]) == "gainer" and direction == "LONG"
            ) or (str(event["mover_bucket"]) == "loser" and direction == "SHORT")
            events.append(event)

    novelty_order = {
        "new_event_review": 0,
        "same_training_positive_event": 1,
        "exact_training_input": 2,
    }
    events.sort(
        key=lambda row: (
            novelty_order[str(row["novelty_status"])],
            0 if bool(row["semantic_gate_pass"]) else 1,
            -float(row["confidence"]),
            str(row["day"]),
            str(row["exchange_symbol"]),
            int(row["core_end_i"]),
        )
    )
    for rank, event in enumerate(events, 1):
        event["review_rank"] = rank
        event["event_id"] = (
            f"mover_202510_{rank:04d}_{event['exchange_symbol']}_"
            f"{utc(event['core_end_time']):%Y%m%dT%H%M}"
        )
    return events


def _put_text(
    image: np.ndarray,
    text: str,
    origin: tuple[int, int],
    *,
    color: tuple[int, int, int] = (35, 35, 35),
    scale: float = 0.48,
    thickness: int = 1,
) -> None:
    cv2.putText(
        image,
        text,
        origin,
        cv2.FONT_HERSHEY_SIMPLEX,
        scale,
        color,
        thickness,
        cv2.LINE_AA,
    )


def render_exact_model_input(event: Mapping[str, Any], frame: pd.DataFrame) -> np.ndarray:
    """Render only the exact causal model input and unchanged raw rectangle."""

    start = int(event["window_start_i"])
    end = int(event["window_end_i"])
    clean, _ = render_chart(frame.iloc[start : end + 1], out_path=None)
    if pixel_sha256(clean) != str(event["input_pixel_sha256"]):
        raise DailyMoversError(f"model input pixel drift: {event['event_id']}")
    overlay = clean.copy()
    x0, y0, x1, y1 = base.normalized_box_corners(event)
    color = common.CLASS_COLORS[int(event["class_id"])]
    cv2.rectangle(overlay, (x0, y0), (x1, y1), color, 4, cv2.LINE_AA)
    header = np.full((86, overlay.shape[1], 3), 247, dtype=np.uint8)
    state = "SEM-PASS" if bool(event["semantic_gate_pass"]) else "SEM-REJECT"
    _put_text(
        header,
        f"#{int(event['review_rank']):04d} {event['exchange_symbol']} {event['rank_label']} "
        f"{event['model_direction']} conf={float(event['confidence']):.3f} {state}",
        (14, 30),
        scale=0.62,
        thickness=2,
    )
    _put_text(
        header,
        f"{str(event['day'])[:10]} return={float(event['daily_return'])*100:+.2f}% "
        f"W{int(event['window_len'])} core{int(event['core_length_bars'])} "
        f"post{int(event['confirmation_bars'])} | {event['novelty_status']}",
        (14, 62),
        color=(70, 70, 70),
        scale=0.50,
    )
    return np.vstack((header, overlay))


def build_day_sheet(
    day: pd.Timestamp,
    rankings: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
    frames: Mapping[str, pd.DataFrame],
) -> np.ndarray:
    """Render all ten selected symbol-days as future-containing review context."""

    board = sorted(
        (row for row in rankings if utc(row["day"]) == day),
        key=lambda row: int(row["board_order"]),
    )
    by_symbol: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        if utc(event["day"]) == day:
            by_symbol[str(event["exchange_symbol"])].append(dict(event))
    panels = [
        common.render_symbol_day_panel(
            frames[str(row["exchange_symbol"])],
            day_row={**dict(row), "symbol": str(row["exchange_symbol"]), "rank": int(row["board_order"])},
            hits=by_symbol[str(row["exchange_symbol"])],
        )
        for row in board
    ]
    columns = 2
    banner_h = 92
    cell_h = max(panel.shape[0] for panel in panels)
    cell_w = max(panel.shape[1] for panel in panels)
    rows = math.ceil(len(panels) / columns)
    canvas = np.full((banner_h + rows * cell_h, columns * cell_w, 3), 244, dtype=np.uint8)
    count = sum(len(value) for value in by_symbol.values())
    _put_text(
        canvas,
        f"{day:%Y-%m-%d} UTC | Top5 gainers + Top5 losers | Grade-A events {count}",
        (16, 34),
        scale=0.78,
        thickness=2,
    )
    _put_text(
        canvas,
        "POST-HOC FULL-DAY REVIEW CONTEXT - physically separate from exact model inputs - not training data",
        (16, 68),
        color=(35, 70, 185),
        scale=0.55,
        thickness=2,
    )
    for index, panel in enumerate(panels):
        row_i, column_i = divmod(index, columns)
        y = banner_h + row_i * cell_h
        x = column_i * cell_w
        canvas[y : y + panel.shape[0], x : x + panel.shape[1]] = panel
    return canvas


def build_overview(
    rankings: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
) -> np.ndarray:
    """Build a compact all-days yield and novelty overview."""

    days = sorted({utc(row["day"]) for row in rankings})
    width, height = 1800, 1320
    canvas = np.full((height, width, 3), 248, dtype=np.uint8)
    _put_text(
        canvas,
        "Grade-A 1280 | October 2025 daily Top5 gainers + Top5 losers | P1 review mining",
        (28, 48),
        scale=0.90,
        thickness=2,
    )
    _put_text(
        canvas,
        "POST-HOC board; pre-holdout; not validation, causal selection, return evidence, Gold, or trading signal",
        (28, 84),
        color=(35, 70, 185),
        scale=0.58,
        thickness=2,
    )
    by_day: Counter[str] = Counter(str(row["day"]) for row in events)
    pass_day: Counter[str] = Counter(
        str(row["day"]) for row in events if bool(row["semantic_gate_pass"])
    )
    novel_day: Counter[str] = Counter(
        str(row["day"]) for row in events if row["novelty_status"] == "new_event_review"
    )
    columns = 3
    card_w, card_h = 570, 106
    for index, day in enumerate(days):
        row_i, column_i = divmod(index, columns)
        x = 24 + column_i * 590
        y = 112 + row_i * card_h
        key = day.isoformat()
        cv2.rectangle(canvas, (x, y), (x + card_w, y + 88), (222, 227, 232), 2)
        _put_text(canvas, f"{day:%Y-%m-%d}", (x + 12, y + 30), scale=0.58, thickness=2)
        _put_text(
            canvas,
            f"events {by_day[key]:>3} | semantic {pass_day[key]:>3} | novel {novel_day[key]:>3}",
            (x + 12, y + 64),
            scale=0.52,
        )
    return canvas


def build_gallery(
    path: Path,
    *,
    events: Sequence[Mapping[str, Any]],
    summary: Mapping[str, Any],
    day_images: Sequence[Mapping[str, Any]],
) -> None:
    """Write a filterable local review gallery with exact inputs and day context."""

    table_rows: list[str] = []
    cards: list[str] = []
    for event in events:
        side = str(event["model_direction"])
        semantic = "pass" if bool(event["semantic_gate_pass"]) else "reject"
        novelty = str(event["novelty_status"])
        chart = str(event["model_input_chart"])
        day_chart = str(event["day_context_chart"])
        symbol = html.escape(str(event["exchange_symbol"]))
        table_rows.append(
            "<tr>"
            f"<td>{int(event['review_rank'])}</td><td>{str(event['day'])[:10]}</td>"
            f"<td>{html.escape(str(event['rank_label']))}</td><td>{symbol}</td>"
            f"<td>{float(event['daily_return'])*100:+.2f}%</td><td>{side}</td>"
            f"<td>{float(event['confidence']):.3f}</td><td>{semantic}</td>"
            f"<td>{html.escape(novelty)}</td>"
            f"<td><a href='{html.escape(chart)}'>input</a> · <a href='{html.escape(day_chart)}'>day</a></td>"
            "</tr>"
        )
        cards.append(
            f"<article class='card' data-side='{side}' data-sem='{semantic}' "
            f"data-novelty='{html.escape(novelty)}' data-symbol='{symbol.lower()}'>"
            f"<a href='{html.escape(chart)}'><img loading='lazy' src='{html.escape(chart)}' alt='{symbol}'></a>"
            f"<div><b>#{int(event['review_rank']):04d} {symbol}</b> · {event['rank_label']} · "
            f"{side} · conf {float(event['confidence']):.3f}</div>"
            f"<small>{str(event['day'])[:10]} · {semantic} · {html.escape(novelty)} · "
            f"<a href='{html.escape(day_chart)}'>full-day review context</a></small></article>"
        )
    day_links = "".join(
        f"<a href='{html.escape(str(row['path']))}'>{html.escape(str(row['day'])[:10])}</a>"
        for row in day_images
    )
    counts = summary["counts"]
    receipt = html.escape(stable_json(summary))
    path.write_text(
        f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Grade-A daily movers P1 review queue</title><style>
:root{{--bg:#0f141a;--panel:#18212a;--line:#33404c;--text:#e7edf3;--muted:#9eabb7;--accent:#6db6ff}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.55 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}}
main{{max-width:1540px;margin:auto;padding:24px}}a{{color:#83c4ff}}.warn{{border-left:4px solid #d59b36;background:#252016;padding:12px 16px}}
.kpis{{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin:18px 0}}.kpi{{background:var(--panel);border:1px solid var(--line);padding:12px;border-radius:8px}}.kpi b{{display:block;font-size:1.45rem}}
.controls{{position:sticky;top:0;background:#0f141aee;padding:10px 0;z-index:3;display:flex;gap:8px;flex-wrap:wrap}}button,input{{background:#1d2832;color:var(--text);border:1px solid var(--line);padding:8px 11px;border-radius:6px}}button.active{{border-color:var(--accent)}}
.days{{display:flex;gap:9px;flex-wrap:wrap;margin:12px 0}}.tablewrap{{overflow:auto;max-height:540px;border:1px solid var(--line)}}table{{width:100%;border-collapse:collapse;white-space:nowrap}}th,td{{padding:7px 9px;border-bottom:1px solid #293640}}th{{position:sticky;top:0;background:#202b35}}
.grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;margin-top:20px}}.card{{background:var(--panel);border:1px solid var(--line);padding:10px;border-radius:8px}}.card img{{display:block;width:100%;height:auto}}small{{color:var(--muted)}}.hidden{{display:none}}
@media(max-width:900px){{.grid{{grid-template-columns:1fr}}.kpis{{grid-template-columns:repeat(2,1fr)}}}}
</style></head><body><main><h1>2025-10 每日 Top5 涨 / Top5 跌 · Grade-A 模型挖样本</h1>
<p class='warn'><b>事后榜单，只用于 P1 人审候选。</b> 本页不是现有模型验证，不是提前选币能力、收益证据、Gold 或交易信号；全天图含事后走势，已与精确模型输入物理分目录。</p>
<div class='kpis'><div class='kpi'><b>{counts['ranked_symbol_days']}</b>币种日</div><div class='kpi'><b>{counts['windows_scored']}</b>窗口</div><div class='kpi'><b>{counts['structural_boxes']}</b>结构框</div><div class='kpi'><b>{counts['semantic_events']}</b>语义事件</div><div class='kpi'><b>{counts['novel_events']}</b>新事件待审</div></div>
<div class='controls'><button class='active' data-filter='all'>全部</button><button data-filter='pass'>语义通过</button><button data-filter='reject'>语义拒绝</button><button data-filter='LONG'>LONG</button><button data-filter='SHORT'>SHORT</button><button data-filter='new_event_review'>仅新事件</button><input id='search' placeholder='搜索币种'></div>
<h2>每日完整榜单图（事后上下文）</h2><div class='days'>{day_links}</div>
<h2>候选账本</h2><div class='tablewrap'><table><thead><tr><th>#</th><th>日期</th><th>榜位</th><th>币种</th><th>日涨跌</th><th>模型方向</th><th>conf</th><th>语义门</th><th>训练重合</th><th>图</th></tr></thead><tbody>{''.join(table_rows)}</tbody></table></div>
<h2>精确模型输入</h2><div class='grid'>{''.join(cards)}</div>
<script type='application/json' id='receipt'>{receipt}</script><script>
const cards=[...document.querySelectorAll('.card')],buttons=[...document.querySelectorAll('button[data-filter]')],search=document.querySelector('#search');let filter='all';
function apply(){{const q=search.value.trim().toLowerCase();cards.forEach(c=>{{const ok=(filter==='all'||c.dataset.sem===filter||c.dataset.side===filter||c.dataset.novelty===filter)&&(!q||c.dataset.symbol.includes(q));c.classList.toggle('hidden',!ok)}})}}
buttons.forEach(b=>b.onclick=()=>{{filter=b.dataset.filter;buttons.forEach(x=>x.classList.toggle('active',x===b));apply()}});search.oninput=apply;
</script></main></body></html>""",
        encoding="utf-8",
    )


def _csv_ready(rows: Sequence[Mapping[str, Any]]) -> pd.DataFrame:
    """Flatten list/dict values for inspectable CSV output."""

    frame = pd.DataFrame([dict(row) for row in rows])
    for column in frame.columns:
        if frame[column].map(lambda value: isinstance(value, (list, dict, tuple))).any():
            frame[column] = frame[column].map(
                lambda value: stable_json(value) if isinstance(value, (list, dict, tuple)) else value
            )
    return frame


def run_scan(
    prereg: Mapping[str, Any],
    gates: Mapping[str, Any],
    *,
    out: Path,
    source_commit: str,
    device: str,
    batch_size: int,
) -> dict[str, Any]:
    """Execute the frozen ranking, model, semantic, lineage, and render chain."""

    if out.exists() or out.with_name(f"{out.name}.building").exists():
        raise FileExistsError(f"refusing to overwrite official output: {out}")
    building = out.with_name(f"{out.name}.building")
    building.mkdir(parents=True)
    started = time.perf_counter()
    try:
        archive_root = ROOT / prereg["data"]["archive_root"]
        universe_rows, rankings, ranking_archives = build_daily_rankings(
            prereg, archive_root=archive_root
        )
        _csv_ready(universe_rows).to_csv(building / "universe_daily_returns.csv", index=False)
        _csv_ready(rankings).to_csv(building / "daily_rankings.csv", index=False)

        selected_symbols = sorted({str(row["exchange_symbol"]) for row in rankings})
        frames, context_archives = load_selected_frames(
            prereg, archive_root=archive_root, symbols=selected_symbols
        )
        tasks, board_audits = build_tasks(prereg, frames=frames, rankings=rankings)
        if not tasks:
            raise DailyMoversError("no model tasks were built")
        _csv_ready(board_audits).to_csv(building / "board_scanability.csv", index=False)

        from ultralytics import YOLO

        weights = ROOT / prereg["detector"]["weights"]
        model = YOLO(str(weights))
        names = {int(key): str(value) for key, value in model.names.items()}
        if names != common.CLASS_NAMES:
            raise DailyMoversError(f"class map drifted: {names}")
        print(f"inference tasks={len(tasks)} device={device} batch={batch_size}", flush=True)
        structural_raw, inference_stats = base.infer(
            model,
            tasks,
            frames=frames,
            device=device,
            batch_size=batch_size,
        )
        structural: list[dict[str, Any]] = []
        outside_day = 0
        for row in structural_raw:
            if utc(row["core_end_time"]).floor("D") != utc(row["day"]):
                outside_day += 1
                continue
            structural.append(dict(row))
        inference_stats["reject_core_outside_ranked_day"] += outside_day

        training = load_training_index(prereg)
        structural = annotate_training_overlap(structural, training)
        decisions = latest.evaluate_semantic_candidates(
            structural, frames, gates, timeframe="15m"
        )
        write_jsonl(building / "semantic_decisions.jsonl", decisions)
        flattened = [latest.flatten_semantic_candidate(row) for row in decisions]
        _csv_ready(flattened).to_csv(building / "structural_candidates.csv", index=False)

        events = deduplicate_review_events(
            decisions,
            gap_bars=int(prereg["detector"]["same_symbol_day_event_gap_bars"]),
        )
        exact_dir = building / "model_inputs"
        day_dir = building / "day_context"
        exact_dir.mkdir()
        day_dir.mkdir()
        day_images: list[dict[str, Any]] = []
        for day in sorted({utc(row["day"]) for row in rankings}):
            image = build_day_sheet(day, rankings, events, frames)
            relative = Path("day_context") / f"day_{day:%Y%m%d}_top5_up_down.png"
            path = building / relative
            if not cv2.imwrite(str(path), image, [cv2.IMWRITE_PNG_COMPRESSION, 4]):
                raise DailyMoversError(f"could not write {path}")
            day_images.append(
                {
                    "day": day.isoformat(),
                    "path": relative.as_posix(),
                    "sha256": sha256_file(path),
                    "width": int(image.shape[1]),
                    "height": int(image.shape[0]),
                }
            )
        day_path_by_day = {str(row["day"]): str(row["path"]) for row in day_images}
        for event in events:
            image = render_exact_model_input(event, frames[str(event["exchange_symbol"])])
            relative = Path("model_inputs") / f"{int(event['review_rank']):04d}_{event['exchange_symbol']}_{event['model_direction']}.png"
            path = building / relative
            if not cv2.imwrite(str(path), image, [cv2.IMWRITE_PNG_COMPRESSION, 4]):
                raise DailyMoversError(f"could not write {path}")
            event["model_input_chart"] = relative.as_posix()
            event["model_input_chart_sha256"] = sha256_file(path)
            event["day_context_chart"] = day_path_by_day[str(event["day"])]

        _csv_ready(events).to_csv(building / "review_queue.csv", index=False)
        write_jsonl(building / "review_queue.jsonl", events)
        overview = build_overview(rankings, events)
        if not cv2.imwrite(str(building / "overview.png"), overview, [cv2.IMWRITE_PNG_COMPRESSION, 4]):
            raise DailyMoversError("could not write overview")

        archive_map: dict[str, dict[str, Any]] = {}
        for row in [*ranking_archives, *context_archives]:
            key = str(row["path"])
            merged = dict(row)
            roles = set(str(archive_map.get(key, {}).get("role", "")).split("|"))
            roles.add(str(row["role"]))
            merged["role"] = "|".join(sorted(role for role in roles if role))
            archive_map[key] = merged
        source_manifest = {
            "schema_version": 1,
            "experiment_id": EXPERIMENT_ID,
            "source_commit": source_commit,
            "network_reads": 0,
            "holdout_ohlcv_rows_materialized": 0,
            "archives": [archive_map[key] for key in sorted(archive_map)],
        }
        write_json(building / "source_manifest.json", source_manifest)

        class_counts = Counter(str(row["model_direction"]) for row in events)
        semantic_events = sum(bool(row["semantic_gate_pass"]) for row in events)
        novel_events = sum(row["novelty_status"] == "new_event_review" for row in events)
        alignment_events = sum(bool(row["direction_matches_completed_day"]) for row in events)
        paired_null = latest.paired_direction_null(decisions)
        counts = {
            "universe_complete_symbol_days": len(universe_rows),
            "ranked_symbol_days": len(rankings),
            "selected_unique_symbols": len(selected_symbols),
            "scannable_symbol_days": sum(bool(row["scannable"]) for row in board_audits),
            "unscannable_symbol_days": sum(not bool(row["scannable"]) for row in board_audits),
            "windows_scored": int(inference_stats["windows_scored"]),
            "raw_boxes": int(inference_stats["raw_boxes"]),
            "structural_boxes": len(decisions),
            "semantic_boxes": sum(bool(row["semantic_gate_pass"]) for row in decisions),
            "review_events": len(events),
            "semantic_events": semantic_events,
            "hard_negative_review_events": len(events) - semantic_events,
            "novel_events": novel_events,
            "exact_training_input_events": sum(row["novelty_status"] == "exact_training_input" for row in events),
            "same_training_positive_events": sum(row["novelty_status"] == "same_training_positive_event" for row in events),
            "direction_aligned_events": alignment_events,
        }
        summary = {
            "schema_version": 1,
            "experiment_id": EXPERIMENT_ID,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source_commit": source_commit,
            "model": prereg["detector"]["display_name"],
            "weights_sha256": prereg["detector"]["weights_sha256"],
            "device": device,
            "calendar": dict(prereg["calendar"]),
            "ranking": dict(prereg["ranking"]),
            "counts": counts,
            "class_counts": dict(sorted(class_counts.items())),
            "inference_stats": dict(sorted(inference_stats.items())),
            "paired_direction_null": paired_null,
            "direction_alignment_is_descriptive_only": True,
            "direction_alignment_fraction": alignment_events / len(events) if events else None,
            "training_manifest_rows": int(training["rows"]),
            "first_validation_window_start": training["first_validation_window_start"],
            "scan_ends_before_existing_validation": utc(prereg["calendar"]["end_exclusive"])
            < utc(training["first_validation_window_start"]),
            "ranking_is_post_hoc": True,
            "economic_backtest": False,
            "automatic_gold_or_label_mutation": False,
            "holdout_consumed": False,
            "holdout_ohlcv_rows_materialized": 0,
            "network_reads": 0,
            "trained": False,
            "threshold_or_weight_changed": False,
            "promoted": False,
            "active_or_frozen_changed": False,
            "forward_state_changed": False,
            "deployed": False,
            "telegram_sent": False,
            "orders_placed": False,
            "training_eligible": False,
            "production_eligible": False,
            "artifacts": {
                "daily_rankings": "daily_rankings.csv",
                "universe_daily_returns": "universe_daily_returns.csv",
                "board_scanability": "board_scanability.csv",
                "semantic_decisions": "semantic_decisions.jsonl",
                "structural_candidates": "structural_candidates.csv",
                "review_queue": "review_queue.csv",
                "review_queue_jsonl": "review_queue.jsonl",
                "source_manifest": "source_manifest.json",
                "overview": "overview.png",
                "gallery": "gallery.html",
            },
            "day_images": day_images,
            "wall_seconds": round(time.perf_counter() - started, 3),
        }
        build_gallery(building / "gallery.html", events=events, summary=summary, day_images=day_images)
        write_json(building / "summary.json", summary)
        shutil.make_archive(
            str(building / "grade_a_daily_movers_202510_review_pack"),
            "zip",
            root_dir=building,
            base_dir="model_inputs",
        )
        building.replace(out)
        print(
            f"complete windows={counts['windows_scored']} structural={counts['structural_boxes']} "
            f"events={counts['review_events']} novel={counts['novel_events']} -> {out}",
            flush=True,
        )
        return summary
    except Exception as exc:
        write_json(
            building / "failure_receipt.json",
            {
                "experiment_id": EXPERIMENT_ID,
                "failed_at": datetime.now(timezone.utc).isoformat(),
                "error": f"{type(exc).__name__}:{exc}",
                "holdout_consumed": False,
                "network_reads": 0,
            },
        )
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prereg", type=Path, default=DEFAULT_PREREG)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--device", default=None)
    parser.add_argument("--batch-size", type=int, default=16)
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    prereg_path = args.prereg.resolve()
    prereg, gates = load_preregistration(prereg_path)
    source_commit = verify_immutable_sources(prereg_path, prereg)
    run_scan(
        prereg,
        gates,
        out=args.out.resolve(),
        source_commit=source_commit,
        device=base.choose_device(args.device),
        batch_size=args.batch_size,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
