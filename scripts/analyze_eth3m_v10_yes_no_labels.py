#!/usr/bin/env python3
"""Diagnose owner yes/no labels for the ETH 3m v10 prebox pack.

Sources and timing contract:

* Label Studio project 53 supplies only the owner's ``is_target`` choice.
* ``datasets/eth_3m_v10_prebox200/manifest.csv`` supplies the frozen v10 box
  and the already-generated three-hour outcome fields.
* ``data/kline_fetched/okx_ETH_USDT_SWAP_3m_57705.csv`` supplies OHLC bars.
  It is physically truncated to rows before 2026-05-04 before any indicator
  or diagnostic is calculated.

The timing diagnostics use only the 200 causal bars ending at the signal for
box geometry and ATR.  Future three-hour lows are outcome-only diagnostics;
they are never model features.  The script does not train, tune, or promote a
model and does not read the project holdout.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from scripts.build_eth_3m_dual_view_calibration import (  # noqa: E402
    FUTURE_BARS,
    HOLDOUT_START,
    box_geometry,
    load_dev_frame,
)
from scripts.ls_auto_import import api, session  # noqa: E402
from src.detection.data import ALL_MA_COLS, add_mas  # noqa: E402
from src.detection.render import make_chart_transform  # noqa: E402
from src.judgment.candidates import add_indicators  # noqa: E402
from src.judgment.yolo_candidates import WINDOW  # noqa: E402

DEFAULT_PROJECT_ID = 53
DEFAULT_MANIFEST = PROJECT / "datasets/eth_3m_v10_prebox200/manifest.csv"
DEFAULT_INPUT = PROJECT / "data/kline_fetched/okx_ETH_USDT_SWAP_3m_57705.csv"
DEFAULT_OUT = PROJECT / "analysis/output/eth3m_v10_label_timing"
BAR_MINUTES = 3


def owner_choice(task: dict[str, Any]) -> tuple[str, int, str]:
    """Return the one non-cancelled owner choice, annotation id, and timestamp."""
    found: list[tuple[str, int, str]] = []
    for annotation in task.get("annotations", []):
        if annotation.get("was_cancelled"):
            continue
        for result in annotation.get("result", []):
            if result.get("from_name") != "is_target":
                continue
            for choice in result.get("value", {}).get("choices", []):
                found.append(
                    (
                        str(choice),
                        int(annotation["id"]),
                        str(annotation.get("updated_at") or annotation.get("created_at") or ""),
                    )
                )
    if len(found) != 1:
        task_id = (task.get("data") or {}).get("task_id")
        raise ValueError(f"task {task_id}: expected one is_target choice, found {found}")
    choice, annotation_id, annotation_time = found[0]
    if choice not in {"是", "不是"}:
        raise ValueError(f"unsupported is_target choice: {choice!r}")
    return choice, annotation_id, annotation_time


def parse_label_export(tasks: list[dict[str, Any]]) -> pd.DataFrame:
    """Parse a Label Studio JSON export into one validated row per task."""
    rows: list[dict[str, Any]] = []
    for task in tasks:
        data = task.get("data") or {}
        choice, annotation_id, annotation_time = owner_choice(task)
        rows.append(
            {
                "task_id": int(data["task_id"]),
                "label_studio_task_id": int(task["id"]),
                "annotation_id": annotation_id,
                "annotation_time": annotation_time,
                "owner_label": choice,
                "owner_is_target": int(choice == "是"),
            }
        )
    out = pd.DataFrame(rows).sort_values("task_id").reset_index(drop=True)
    if out["task_id"].duplicated().any():
        dupes = out.loc[out["task_id"].duplicated(), "task_id"].tolist()
        raise ValueError(f"duplicate task ids in export: {dupes}")
    return out


def _pct(value: float) -> float:
    return round(float(value) * 100, 2)


def _finite(value: Any) -> float | None:
    number = float(value)
    return number if math.isfinite(number) else None


def _group_summary(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {"n": 0}
    return {
        "n": int(len(frame)),
        "box_bars_median": round(float(frame["box_bars"].median()), 2),
        "box_elapsed_min_median": round(float(frame["box_elapsed_min"].median()), 2),
        "box_elapsed_min_p75": round(float(frame["box_elapsed_min"].quantile(0.75)), 2),
        "consumed_atr_median": round(float(frame["consumed_atr"].median()), 3),
        "consumed_atr_p75": round(float(frame["consumed_atr"].quantile(0.75)), 3),
        "remaining_drop_atr_median": round(float(frame["remaining_drop_atr"].median()), 3),
        "future_return_3h_median_pct": round(float(frame["outcome_return_3h"].median() * 100), 3),
        "share_consumed_gt_0_5atr_pct": _pct((frame["consumed_atr"] > 0.5).mean()),
        "share_consumed_gt_1atr_pct": _pct((frame["consumed_atr"] > 1.0).mean()),
        "share_consumed_gt_2atr_pct": _pct((frame["consumed_atr"] > 2.0).mean()),
        "share_consumed_exceeds_remaining_pct": _pct(
            (frame["consumed_drop_abs"] > frame["remaining_drop_abs"]).mean()
        ),
        "share_tip_below_all_mas_pct": _pct(frame["tip_below_all_mas"].mean()),
    }


def _bucket_rows(detail: pd.DataFrame) -> list[dict[str, Any]]:
    bins = [-np.inf, 0.5, 1.0, 2.0, 3.0, np.inf]
    labels = ["≤0.5 ATR", "0.5–1 ATR", "1–2 ATR", "2–3 ATR", ">3 ATR"]
    bucketed = detail.assign(
        consumed_bucket=pd.cut(
            detail["consumed_atr"], bins=bins, labels=labels, include_lowest=True, right=True
        )
    )
    rows: list[dict[str, Any]] = []
    for bucket in labels:
        part = bucketed[bucketed["consumed_bucket"] == bucket]
        rows.append(
            {
                "consumed_bucket": bucket,
                "task_count": int(len(part)),
                "owner_yes_count": int(part["owner_is_target"].sum()),
                "owner_yes_rate_pct": round(float(part["owner_is_target"].mean() * 100), 2)
                if len(part)
                else None,
                "median_box_elapsed_min": round(float(part["box_elapsed_min"].median()), 2)
                if len(part)
                else None,
                "median_remaining_drop_atr": round(float(part["remaining_drop_atr"].median()), 3)
                if len(part)
                else None,
            }
        )
    return rows


def _confidence_rows(detail: pd.DataFrame) -> list[dict[str, Any]]:
    bins = [0.30, 0.40, 0.50, 0.65, np.inf]
    labels = ["0.30–0.40", "0.40–0.50", "0.50–0.65", "≥0.65"]
    bucketed = detail.assign(
        confidence_bucket=pd.cut(
            detail["v10_conf"], bins=bins, labels=labels, include_lowest=True, right=False
        )
    )
    rows: list[dict[str, Any]] = []
    for bucket in labels:
        part = bucketed[bucketed["confidence_bucket"] == bucket]
        rows.append(
            {
                "confidence_bucket": bucket,
                "task_count": int(len(part)),
                "owner_yes_count": int(part["owner_is_target"].sum()),
                "owner_yes_rate_pct": round(float(part["owner_is_target"].mean() * 100), 2)
                if len(part)
                else None,
                "median_consumed_atr": round(float(part["consumed_atr"].median()), 3)
                if len(part)
                else None,
            }
        )
    return rows


def build_detail(
    labels: pd.DataFrame,
    manifest: pd.DataFrame,
    frame: pd.DataFrame,
) -> pd.DataFrame:
    """Join labels to exact box geometry and causal/future timing diagnostics."""
    if len(labels) != len(manifest):
        raise ValueError(f"label/manifest count mismatch: {len(labels)} vs {len(manifest)}")
    merged = manifest.merge(labels, on="task_id", how="outer", validate="one_to_one", indicator=True)
    if set(merged["_merge"]) != {"both"}:
        bad = merged.loc[merged["_merge"] != "both", ["task_id", "_merge"]].to_dict("records")
        raise ValueError(f"unmatched task rows: {bad[:10]}")
    merged = merged.drop(columns="_merge").sort_values("task_id").reset_index(drop=True)

    merged["candidate_time"] = pd.to_datetime(merged["candidate_time"], utc=True)
    merged["future_end"] = pd.to_datetime(merged["future_end"], utc=True)
    if (merged["future_end"] >= HOLDOUT_START).any():
        bad = merged.loc[merged["future_end"] >= HOLDOUT_START, "task_id"].tolist()
        raise ValueError(f"holdout boundary violation in manifest tasks: {bad[:10]}")
    if frame["open_time"].max() >= HOLDOUT_START:
        raise ValueError("load_dev_frame contract broken: post-holdout row is present")

    ma_frame = add_mas(frame)
    indicator_frame = add_indicators(frame)
    positions = pd.Series(frame.index.to_numpy(), index=frame["open_time"])
    if positions.index.duplicated().any():
        raise ValueError("duplicate OHLC timestamps")

    metric_rows: list[dict[str, Any]] = []
    for row in merged.itertuples(index=False):
        if row.candidate_time not in positions.index:
            raise ValueError(f"task {row.task_id}: candidate timestamp missing from OHLC")
        idx = int(positions.loc[row.candidate_time])
        if idx < WINDOW - 1:
            raise ValueError(f"task {row.task_id}: insufficient causal bars")
        causal_start = idx - WINDOW + 1
        causal = ma_frame.iloc[causal_start : idx + 1].reset_index(drop=True)
        transform = make_chart_transform(causal)
        x0, x1, _, _ = box_geometry(
            (float(row.box_cx), float(row.box_cy), float(row.box_w), float(row.box_h)),
            transform,
        )
        box_start_i = causal_start + int(x0)
        box_end_i = causal_start + int(x1)
        if box_end_i > idx:
            raise ValueError(f"task {row.task_id}: box ends after causal tip")
        if int(x1) != int(row.box_right_bar):
            raise ValueError(
                f"task {row.task_id}: mapped right edge {x1} != manifest {row.box_right_bar}"
            )
        box_slice = indicator_frame.iloc[box_start_i : idx + 1]
        ma_box_slice = ma_frame.iloc[box_start_i : idx + 1]
        tip_close = float(indicator_frame["close"].iloc[idx])
        start_close = float(indicator_frame["close"].iloc[box_start_i])
        peak_close = float(box_slice["close"].max())
        peak_high = float(box_slice["high"].max())
        atr14 = float(indicator_frame["atr14"].iloc[idx])
        if not math.isfinite(atr14) or atr14 <= 0:
            raise ValueError(f"task {row.task_id}: invalid ATR14 at signal")

        consumed_drop_abs = max(0.0, peak_close - tip_close)
        remaining_drop_abs = max(0.0, tip_close * float(row.outcome_max_drop_3h))
        total_drop_path = consumed_drop_abs + remaining_drop_abs
        future = frame.iloc[idx + 1 : idx + FUTURE_BARS + 1]
        if len(future) != FUTURE_BARS:
            raise ValueError(f"task {row.task_id}: incomplete future 3h window")
        expected_future_end = pd.Timestamp(future["open_time"].iloc[-1])
        if expected_future_end != row.future_end:
            raise ValueError(
                f"task {row.task_id}: future_end {row.future_end} != {expected_future_end}"
            )
        recomputed_max_drop = 1 - float(future["low"].min()) / tip_close

        all_ma_valid = ma_box_slice[list(ALL_MA_COLS)].notna().all(axis=1)
        below_all = all_ma_valid & (
            ma_box_slice["close"] < ma_box_slice[list(ALL_MA_COLS)].min(axis=1)
        )
        first_below_global = int(below_all[below_all].index[0]) if below_all.any() else None
        tip_below_all = bool(
            ma_frame.loc[idx, list(ALL_MA_COLS)].notna().all()
            and tip_close < float(ma_frame.loc[idx, list(ALL_MA_COLS)].min())
        )

        metric_rows.append(
            {
                "task_id": int(row.task_id),
                "box_start_bar": int(x0),
                "box_end_bar": int(x1),
                "box_bars": int(x1 - x0 + 1),
                "box_elapsed_min": int((x1 - x0) * BAR_MINUTES),
                "box_start_time": pd.Timestamp(frame["open_time"].iloc[box_start_i]).isoformat(),
                "signal_close": tip_close,
                "box_start_close": start_close,
                "box_peak_close": peak_close,
                "box_peak_high": peak_high,
                "atr14": atr14,
                "start_close_to_tip_drop_pct": 1 - tip_close / start_close,
                "peak_close_to_tip_drop_pct": 1 - tip_close / peak_close,
                "peak_high_to_tip_drop_pct": 1 - tip_close / peak_high,
                "consumed_drop_abs": consumed_drop_abs,
                "consumed_atr": consumed_drop_abs / atr14,
                "remaining_drop_abs": remaining_drop_abs,
                "remaining_drop_atr": remaining_drop_abs / atr14,
                "outcome_max_drop_3h_recomputed": recomputed_max_drop,
                "outcome_max_drop_abs_error": abs(
                    recomputed_max_drop - float(row.outcome_max_drop_3h)
                ),
                "consumed_share_of_observed_path": consumed_drop_abs / total_drop_path
                if total_drop_path > 0
                else np.nan,
                "consumed_exceeds_remaining": int(consumed_drop_abs > remaining_drop_abs),
                "first_below_all_mas_lag_bars": idx - first_below_global
                if first_below_global is not None
                else np.nan,
                "first_below_all_mas_lag_min": (idx - first_below_global) * BAR_MINUTES
                if first_below_global is not None
                else np.nan,
                "tip_below_all_mas": int(tip_below_all),
            }
        )
    metrics = pd.DataFrame(metric_rows)
    return merged.merge(metrics, on="task_id", how="inner", validate="one_to_one")


def build_summary(detail: pd.DataFrame, *, project_id: int) -> dict[str, Any]:
    yes = detail[detail["owner_is_target"] == 1]
    no = detail[detail["owner_is_target"] == 0]
    sorted_time = detail.sort_values("candidate_time")
    event_count = int(
        1
        + (sorted_time["candidate_time"].diff() > pd.Timedelta(minutes=60)).fillna(False).sum()
    )
    width_consumed_spearman = detail["box_elapsed_min"].corr(
        detail["consumed_atr"], method="spearman"
    )
    confidence_yes_spearman = detail["v10_conf"].corr(
        detail["owner_is_target"], method="spearman"
    )
    yes_more_remaining = yes[yes["consumed_drop_abs"] <= yes["remaining_drop_abs"]]
    yes_more_consumed = yes[yes["consumed_drop_abs"] > yes["remaining_drop_abs"]]

    def timing_proxy_summary(part: pd.DataFrame) -> dict[str, Any]:
        return {
            "n": int(len(part)),
            "share_of_owner_yes_pct": _pct(len(part) / len(yes)) if len(yes) else 0.0,
            "consumed_atr_median": round(float(part["consumed_atr"].median()), 3),
            "remaining_drop_atr_median": round(float(part["remaining_drop_atr"].median()), 3),
            "future_3h_close_down_rate_pct": _pct((part["outcome_return_3h"] < 0).mean()),
            "future_rebound_pct_median": round(
                float(part["outcome_max_rebound_3h"].median() * 100), 3
            ),
        }

    summary = {
        "project_id": project_id,
        "holdout_start_utc": HOLDOUT_START.isoformat(),
        "holdout_consumed": False,
        "task_count": int(len(detail)),
        "task_time_start": detail["candidate_time"].min().isoformat(),
        "task_time_end": detail["candidate_time"].max().isoformat(),
        "owner_yes_count": int(len(yes)),
        "owner_no_count": int(len(no)),
        "owner_yes_rate_pct": _pct(detail["owner_is_target"].mean()),
        "one_hour_separated_event_count": event_count,
        "all_tasks": _group_summary(detail),
        "owner_yes": _group_summary(yes),
        "owner_no": _group_summary(no),
        "owner_yes_timing_proxy": {
            "remaining_not_less_than_consumed": timing_proxy_summary(yes_more_remaining),
            "consumed_exceeds_remaining": timing_proxy_summary(yes_more_consumed),
        },
        "relationships": {
            "box_elapsed_vs_consumed_atr_spearman": _finite(width_consumed_spearman),
            "v10_confidence_vs_owner_yes_spearman": _finite(confidence_yes_spearman),
        },
        "consumed_buckets": _bucket_rows(detail),
        "confidence_buckets": _confidence_rows(detail),
        "data_quality": {
            "unique_task_ids": int(detail["task_id"].nunique()),
            "all_box_right_edges_match_manifest": bool(
                (detail["box_end_bar"] == detail["box_right_bar"]).all()
            ),
            "box_right_edge_counts": {
                str(int(k)): int(v)
                for k, v in detail["box_end_bar"].value_counts().sort_index().items()
            },
            "max_outcome_recompute_abs_error": float(
                detail["outcome_max_drop_abs_error"].max()
            ),
            "max_future_end_utc": detail["future_end"].max().isoformat(),
            "missing_required_values": int(
                detail[
                    ["owner_label", "box_bars", "consumed_atr", "remaining_drop_atr"]
                ].isna().sum().sum()
            ),
        },
        "definitions": {
            "owner_yes": "Owner answered 是 to whether the red box is the desired short shape; this is not an entry-timing approval.",
            "box_elapsed_min": "Elapsed time from the mapped first box bar to the mapped last box bar; (bar_count - 1) × 3 minutes.",
            "consumed_atr": "Positive decline from the highest close inside the box through the signal close, divided by causal ATR14 at the signal.",
            "remaining_drop_atr": "Maximum additional decline during the fixed future 3h window, measured from the signal close and divided by causal ATR14.",
            "late_thresholds": "0.5/1/2 ATR cuts are sensitivity checks only, not accepted owner thresholds.",
        },
    }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", type=int, default=DEFAULT_PROJECT_ID)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    tasks = api(session(), "GET", f"/api/projects/{args.project_id}/export?exportType=JSON&download_all_tasks=false")
    labels = parse_label_export(tasks)
    manifest = pd.read_csv(args.manifest)
    frame = load_dev_frame(args.input)
    detail = build_detail(labels, manifest, frame)
    summary = build_summary(detail, project_id=args.project_id)

    args.out.mkdir(parents=True, exist_ok=True)
    labels.to_csv(args.out / "project_53_owner_choices.csv", index=False)
    detail.to_csv(args.out / "task_timing_metrics.csv", index=False)
    (args.out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    pd.DataFrame(summary["consumed_buckets"]).to_csv(
        args.out / "consumed_atr_buckets.csv", index=False
    )
    pd.DataFrame(summary["confidence_buckets"]).to_csv(
        args.out / "confidence_buckets.csv", index=False
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
