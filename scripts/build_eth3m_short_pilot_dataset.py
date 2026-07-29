#!/usr/bin/env python3
"""Build the owner-reviewed ETH 3m short-detector pilot dataset.

Sources and causal contract
---------------------------
The source table contains the 200 v10 candidates reviewed by the owner in
Label Studio project 53.  Positives are the owner's ``是`` rows whose proposed
entry is at least two completed 3-minute bars earlier than v10.  The proposal
is recomputed from OHLC as the first completed close below all six MAs inside
the owner-confirmed original box.  Negatives are only the owner's ``不是``
rows; no rule-generated easy negatives are added.

Every image contains exactly 200 completed bars ending at its causal anchor.
Positive boxes end at that anchor, span 5..12 causal bars, and obtain their
vertical geometry only from the six MAs visible in the same image.  Future
outcome columns in the source table are never used.  OHLC is truncated to rows
strictly before 2026-05-04 before indicators or samples are constructed.

Exact positive anchors are deduplicated.  Samples within 60 minutes form an
event and an event is kept wholly on one side of a chronological 75/25 split.
This is a small pilot dataset, not a promotion-grade gold set.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.detection.auto_label import (  # noqa: E402
    MAX_DENSE_BARS,
    MIN_DENSE_BARS,
    DenseSegment,
    segment_to_bbox,
    to_yolo_lines,
)
from src.detection.data import ALL_MA_COLS, add_mas, load_ohlcv_csv  # noqa: E402
from src.detection.render import render_chart  # noqa: E402

HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")
WINDOW = 200
BAR_MINUTES = 3
MIN_LEAD_BARS = 2
EVENT_GAP_MINUTES = 60
TRAIN_EVENT_FRAC = 0.75

DEFAULT_INPUT = PROJECT / "data/kline_fetched/okx_ETH_USDT_SWAP_3m_57705.csv"
DEFAULT_DETAIL = PROJECT / "analysis/output/eth3m_v10_label_timing/task_timing_metrics.csv"
DEFAULT_OUT = PROJECT / "datasets/eth_3m_short_pilot_v1"

FUTURE_ONLY_COLUMNS = {
    "future_end",
    "outcome_return_1h",
    "outcome_return_3h",
    "outcome_max_drop_3h",
    "outcome_max_rebound_3h",
    "remaining_drop_abs",
    "remaining_drop_atr",
    "consumed_exceeds_remaining",
    "consumed_share_of_observed_path",
    "outcome_max_drop_3h_recomputed",
    "outcome_max_drop_abs_error",
}


def load_dev_frame(path: Path) -> pd.DataFrame:
    """Load ETH 3m OHLC and physically discard holdout rows before indicators."""
    frame = load_ohlcv_csv(path)
    frame = frame.loc[frame["open_time"] < HOLDOUT_START].copy().reset_index(drop=True)
    if frame.empty or frame["open_time"].max() >= HOLDOUT_START:
        raise ValueError("development OHLC truncation failed")
    if frame["open_time"].duplicated().any():
        raise ValueError("duplicate OHLC timestamps")
    return frame


def _require_columns(detail: pd.DataFrame) -> None:
    required = {
        "task_id",
        "candidate_time",
        "v10_conf",
        "owner_is_target",
        "owner_label",
        "box_start_time",
        "first_below_all_mas_lag_bars",
    }
    missing = sorted(required - set(detail.columns))
    if missing:
        raise ValueError(f"detail table missing columns: {missing}")


def prepare_samples(detail: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Create deduplicated causal anchors without consulting future outcomes."""
    _require_columns(detail)
    work = detail[
        [
            "task_id",
            "candidate_time",
            "v10_conf",
            "owner_is_target",
            "owner_label",
            "box_start_time",
            "first_below_all_mas_lag_bars",
        ]
    ].copy()
    if FUTURE_ONLY_COLUMNS.intersection(work.columns):
        raise AssertionError("future-only columns entered sample preparation")
    work["candidate_time"] = pd.to_datetime(work["candidate_time"], utc=True)
    work["box_start_time"] = pd.to_datetime(work["box_start_time"], utc=True)
    work["owner_is_target"] = pd.to_numeric(work["owner_is_target"], errors="raise").astype(int)
    work["first_below_all_mas_lag_bars"] = pd.to_numeric(
        work["first_below_all_mas_lag_bars"], errors="coerce"
    )
    if not set(work["owner_is_target"].unique()).issubset({0, 1}):
        raise ValueError("owner_is_target must be binary")
    if (work["candidate_time"] >= HOLDOUT_START).any():
        raise ValueError("source candidate enters holdout")

    positives_all = work[work["owner_is_target"] == 1].copy()
    positives = positives_all[
        positives_all["first_below_all_mas_lag_bars"] >= MIN_LEAD_BARS
    ].copy()
    positives["lag_bars"] = positives["first_below_all_mas_lag_bars"].astype(int)
    positives["anchor_time"] = positives["candidate_time"] - pd.to_timedelta(
        positives["lag_bars"] * BAR_MINUTES, unit="m"
    )
    positives["raw_span_bars"] = (
        (positives["anchor_time"] - positives["box_start_time"])
        / pd.Timedelta(minutes=BAR_MINUTES)
    ).round().astype(int) + 1
    if (positives["raw_span_bars"] < 1).any():
        bad = positives.loc[positives["raw_span_bars"] < 1, "task_id"].tolist()
        raise ValueError(f"positive anchor before original box start: {bad[:10]}")

    before_dedup = len(positives)
    positives = (
        positives.sort_values(
            ["anchor_time", "raw_span_bars", "v10_conf", "task_id"],
            ascending=[True, False, False, True],
        )
        .drop_duplicates("anchor_time", keep="first")
        .copy()
    )
    positives["target"] = 1
    positives["sample_kind"] = "owner_yes_early_anchor"

    negatives = work[work["owner_is_target"] == 0].copy()
    negatives["anchor_time"] = negatives["candidate_time"]
    negatives["lag_bars"] = 0
    negatives["raw_span_bars"] = 0
    negatives["target"] = 0
    negatives["sample_kind"] = "owner_no_hard_negative"

    positive_anchors = set(positives["anchor_time"])
    negative_anchors = set(negatives["anchor_time"])
    exact_conflicts = positive_anchors.intersection(negative_anchors)
    if exact_conflicts:
        raise ValueError(f"positive/negative exact anchor conflicts: {sorted(exact_conflicts)[:5]}")

    samples = pd.concat([positives, negatives], ignore_index=True, sort=False)
    samples = samples.sort_values(["anchor_time", "target", "task_id"]).reset_index(drop=True)
    audit = {
        "source_owner_yes": int(len(positives_all)),
        "source_owner_no": int(len(negatives)),
        "timing_ineligible_owner_yes": int(len(positives_all) - before_dedup),
        "positive_rows_before_anchor_dedup": int(before_dedup),
        "positive_exact_anchor_duplicates_removed": int(before_dedup - len(positives)),
        "positive_after_dedup": int(len(positives)),
        "negative_owner_hard": int(len(negatives)),
        "exact_anchor_conflicts": int(len(exact_conflicts)),
    }
    return samples, audit


def assign_event_split(samples: pd.DataFrame) -> tuple[pd.DataFrame, pd.Timestamp, int]:
    """Assign gap-clustered events to a strict chronological 75/25 split."""
    out = samples.sort_values("anchor_time").reset_index(drop=True).copy()
    new_event = out["anchor_time"].diff() > pd.Timedelta(minutes=EVENT_GAP_MINUTES)
    if len(new_event):
        new_event.iloc[0] = True
    out["event_id"] = new_event.cumsum().astype(int)
    events = (
        out.groupby("event_id", sort=True)["anchor_time"]
        .agg(["min", "max"])
        .reset_index()
    )
    if len(events) < 2:
        raise ValueError("need at least two independent events for a time split")
    n_train_events = int(math.floor(len(events) * TRAIN_EVENT_FRAC))
    n_train_events = min(max(n_train_events, 1), len(events) - 1)
    train_event_ids = set(events.iloc[:n_train_events]["event_id"].astype(int))
    last_train = pd.Timestamp(events.iloc[n_train_events - 1]["max"])
    first_val = pd.Timestamp(events.iloc[n_train_events]["min"])
    if not last_train < first_val:
        raise AssertionError("event chronology is not strictly ordered")
    cutoff = last_train + (first_val - last_train) / 2
    out["split"] = out["event_id"].map(
        lambda value: "train" if int(value) in train_event_ids else "val"
    )
    if out.loc[out["split"] == "train", "anchor_time"].max() >= out.loc[
        out["split"] == "val", "anchor_time"
    ].min():
        raise AssertionError("train/val timestamps overlap")
    split_counts = out.groupby("event_id")["split"].nunique()
    if int(split_counts.max()) != 1:
        raise AssertionError("an event crossed the train/val split")
    return out, cutoff, n_train_events


def compact_segment(box_start_i: int, anchor_i: int) -> tuple[int, int]:
    """Keep the causal part of the owner box, bounded to 5..12 bars."""
    start = max(box_start_i, anchor_i - MAX_DENSE_BARS + 1)
    if anchor_i - start + 1 < MIN_DENSE_BARS:
        start = anchor_i - MIN_DENSE_BARS + 1
    span = anchor_i - start + 1
    if not MIN_DENSE_BARS <= span <= MAX_DENSE_BARS:
        raise AssertionError(f"compact span outside bounds: {span}")
    return start, anchor_i


def _recompute_positive_anchor(
    row: Any, ma_frame: pd.DataFrame, positions: pd.Series
) -> tuple[int, int, int]:
    """Recompute the first completed below-all-MA bar from causal OHLC."""
    candidate_i = int(positions.loc[pd.Timestamp(row.candidate_time)])
    box_start_i = int(positions.loc[pd.Timestamp(row.box_start_time)])
    region = ma_frame.iloc[box_start_i : candidate_i + 1]
    valid = region[list(ALL_MA_COLS)].notna().all(axis=1)
    below = valid & (region["close"] < region[list(ALL_MA_COLS)].min(axis=1))
    hits = below[below].index
    if len(hits) == 0:
        raise ValueError(f"task {row.task_id}: no below-all-MA bar in original box")
    anchor_i = int(hits[0])
    recorded_anchor = int(positions.loc[pd.Timestamp(row.anchor_time)])
    if anchor_i != recorded_anchor:
        raise ValueError(
            f"task {row.task_id}: recomputed anchor {anchor_i} != recorded {recorded_anchor}"
        )
    if candidate_i - anchor_i < MIN_LEAD_BARS:
        raise ValueError(f"task {row.task_id}: timing lead below pilot gate")
    return anchor_i, candidate_i, box_start_i


def build_dataset(
    *, input_path: Path, detail_path: Path, out: Path
) -> tuple[dict[str, Any], pd.DataFrame]:
    """Build images, YOLO labels, manifest, and an auditable metadata file."""
    if out.exists() and any(out.iterdir()):
        raise FileExistsError(f"refusing to overwrite non-empty output: {out}")
    for split in ("train", "val"):
        (out / "images" / split).mkdir(parents=True, exist_ok=True)
        (out / "labels" / split).mkdir(parents=True, exist_ok=True)

    detail = pd.read_csv(detail_path)
    samples, audit = prepare_samples(detail)
    samples, cutoff, n_train_events = assign_event_split(samples)
    frame = load_dev_frame(input_path)
    ma_frame = add_mas(frame)
    positions = pd.Series(frame.index.to_numpy(), index=frame["open_time"])

    manifest_rows: list[dict[str, Any]] = []
    for row in samples.itertuples(index=False):
        anchor_time = pd.Timestamp(row.anchor_time)
        if anchor_time >= HOLDOUT_START or anchor_time not in positions.index:
            raise ValueError(f"task {row.task_id}: invalid anchor {anchor_time}")
        anchor_i = int(positions.loc[anchor_time])
        if anchor_i < WINDOW - 1:
            raise ValueError(f"task {row.task_id}: insufficient causal history")
        if int(row.target) == 1:
            anchor_i, candidate_i, original_box_start_i = _recompute_positive_anchor(
                row, ma_frame, positions
            )
        else:
            candidate_i = anchor_i
            original_box_start_i = -1

        causal_start_i = anchor_i - WINDOW + 1
        causal = ma_frame.iloc[causal_start_i : anchor_i + 1].reset_index(drop=True)
        if len(causal) != WINDOW:
            raise AssertionError("causal window length changed")
        stamp = anchor_time.strftime("%Y%m%dT%H%M%SZ")
        prefix = "pos" if int(row.target) == 1 else "neg"
        stem = f"{prefix}_eth3m_{stamp}_t{int(row.task_id):03d}"
        image_rel = Path("images") / row.split / f"{stem}.png"
        label_rel = Path("labels") / row.split / f"{stem}.txt"
        _, transform = render_chart(causal, out_path=out / image_rel)

        bbox = None
        box_start_i = None
        box_end_i = None
        box_span_bars = 0
        if int(row.target) == 1:
            box_start_i, box_end_i = compact_segment(original_box_start_i, anchor_i)
            box_span_bars = box_end_i - box_start_i + 1
            local_start = box_start_i - causal_start_i
            bbox = segment_to_bbox(
                causal, DenseSegment(start=local_start, end=WINDOW - 1), transform
            )
            if bbox is None:
                raise ValueError(f"task {row.task_id}: compact box could not be rendered")
            label_text = to_yolo_lines([bbox])
        else:
            label_text = ""
        (out / label_rel).write_text(label_text, encoding="utf-8")

        manifest_rows.append(
            {
                "sample_id": stem,
                "source_task_id": int(row.task_id),
                "owner_label": str(row.owner_label),
                "target": int(row.target),
                "sample_kind": str(row.sample_kind),
                "split": str(row.split),
                "event_id": int(row.event_id),
                "anchor_time": anchor_time.isoformat(),
                "original_v10_time": pd.Timestamp(row.candidate_time).isoformat(),
                "lead_bars": int(row.lag_bars),
                "lead_minutes": int(row.lag_bars) * BAR_MINUTES,
                "causal_start_time": pd.Timestamp(
                    frame["open_time"].iloc[causal_start_i]
                ).isoformat(),
                "box_start_time": pd.Timestamp(frame["open_time"].iloc[box_start_i]).isoformat()
                if box_start_i is not None
                else "",
                "box_end_time": pd.Timestamp(frame["open_time"].iloc[box_end_i]).isoformat()
                if box_end_i is not None
                else "",
                "box_span_bars": int(box_span_bars),
                "box_xc": float(bbox[0]) if bbox else "",
                "box_yc": float(bbox[1]) if bbox else "",
                "box_w": float(bbox[2]) if bbox else "",
                "box_h": float(bbox[3]) if bbox else "",
                "image_rel": image_rel.as_posix(),
                "label_rel": label_rel.as_posix(),
            }
        )

    manifest = pd.DataFrame(manifest_rows).sort_values(
        ["anchor_time", "target", "source_task_id"]
    )
    manifest.to_csv(out / "manifest.csv", index=False, quoting=csv.QUOTE_MINIMAL)
    (out / "data.yaml").write_text(
        "# ETH 3m short detector pilot: owner yes/no, causal tip anchors.\n"
        f"path: {out.resolve().as_posix()}\n"
        "train: images/train\n"
        "val: images/val\n"
        "names:\n  0: short_start\n"
        "nc: 1\n",
        encoding="utf-8",
    )

    counts: dict[str, Any] = {}
    for split in ("train", "val"):
        part = manifest[manifest["split"] == split]
        counts[split] = {
            "total": int(len(part)),
            "positive": int(part["target"].sum()),
            "negative": int((part["target"] == 0).sum()),
            "events": int(part["event_id"].nunique()),
        }
    meta = {
        "dataset": out.name,
        "source_detail": str(detail_path),
        "source_ohlc": str(input_path),
        "holdout_start": HOLDOUT_START.isoformat(),
        "holdout_consumed": False,
        "causal_window_bars": WINDOW,
        "bar_minutes": BAR_MINUTES,
        "positive_rule": (
            "owner yes; first completed close below all six MAs inside the original "
            "owner-confirmed v10 box; at least two bars earlier; exact anchors deduplicated"
        ),
        "negative_rule": "owner no at original v10 candidate; empty YOLO label",
        "box_rule": (
            "right edge equals causal tip; preserve causal original-box prefix; "
            f"clamp to {MIN_DENSE_BARS}..{MAX_DENSE_BARS} bars; MA-only vertical geometry"
        ),
        "future_columns_used": [],
        "unreviewed_easy_negatives_added": 0,
        "event_gap_minutes": EVENT_GAP_MINUTES,
        "train_event_fraction": TRAIN_EVENT_FRAC,
        "event_count": int(manifest["event_id"].nunique()),
        "train_event_count": int(n_train_events),
        "split_cutoff_utc": cutoff.isoformat(),
        "time_start": str(manifest["anchor_time"].min()),
        "time_end": str(manifest["anchor_time"].max()),
        "counts": counts,
        "totals": {
            "total": int(len(manifest)),
            "positive": int(manifest["target"].sum()),
            "negative": int((manifest["target"] == 0).sum()),
        },
        "source_audit": audit,
        "owner_timing_calibration": {
            "reviewed_independent_events": 30,
            "timely": 30,
            "scope": "stratified timing-rule calibration sample, not 93-row exhaustive gold",
        },
        "risks": [
            "Only 76 unique positive anchors: pilot-scale and variance is high.",
            "All examples originate from the v10 candidate pool, so selection bias remains.",
            "Compact box geometry is causal reconstruction; the owner approved timing on 30 events, not every final box pixel.",
            "Development validation is not holdout and cannot authorize promotion or live use.",
        ],
        "status": "pilot_dataset_only_not_promoted",
    }
    (out / "build_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return meta, manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--detail", type=Path, default=DEFAULT_DETAIL)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    meta, _ = build_dataset(input_path=args.input, detail_path=args.detail, out=args.out)
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
