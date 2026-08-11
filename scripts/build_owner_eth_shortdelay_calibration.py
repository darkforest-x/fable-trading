#!/usr/bin/env python3
"""Build 30 owner-review calibration crops for the delayed morphology target.

This is a geometry calibration artifact, not a training dataset.  Event identity
and the frozen train/validation boundary come from the repaired Local-Signal V2
Stage-A manifest.  The 5/7-bar core proposals come from the older owner W20--30
manifest, but are explicitly kept ``unreviewed`` because the Owner's ETH example
overrides legacy box geometry when they disagree.

Each crop is rebuilt from continuous 15m bars as::

    6--10 pre-core bars + 5/7 proposed core bars + exactly 3/4/5 post-core bars

There are ten crops for each post-core delay.  No later return, model score, val
image, val label, or holdout row participates in selection.  Raw CSV access is
prefix-limited to the final required pre-holdout bar for each selected event.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
YOYO_REPO = Path.home() / "yoyo-trading"
for path in (ROOT, YOYO_REPO):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from yoyo.data.loader import OHLCV_COLUMNS, list_series  # noqa: E402
from yoyo.layers.l1_detection.data import add_mas  # noqa: E402
from yoyo.layers.l1_detection.render import render_chart  # noqa: E402

from scripts.build_local_signal_v2_stageb import (  # noqa: E402
    BAR_MINUTES,
    HOLDOUT_START,
    PURGE_BARS,
    sha256_file,
)
from scripts.build_w20_midbox_dataset import yolo_box_from_bars  # noqa: E402


PROTOCOL = "owner_eth_shortdelay_calibration30_v1_20260811"
DEFAULT_STAGE_MANIFEST = (
    ROOT / "datasets/local_signal_v2_stagea_randomcrop_v1/w20_manifest.json"
)
DEFAULT_OWNER_MANIFEST = ROOT / "datasets/dense_owner_w20_midbox/w20_manifest.json"
DEFAULT_OUT = ROOT / "analysis/output/owner_eth_shortdelay_calibration30_v1"
POST_DELAYS = (3, 4, 5)
PRE_CONTEXTS = (6, 7, 8, 9, 10)
CORE_WIDTHS = (5, 7)
PER_DELAY = 10
COLOR_CORE = (38, 38, 235)  # red, BGR
COLOR_BOUNDARY = (180, 165, 0)  # teal, BGR


@dataclass(frozen=True)
class CalibrationPlan:
    calibration_id: str
    event_id: str
    source_stem: str
    symbol: str
    stage_split: str
    source_csv: str
    mid_global: int
    core_global: tuple[int, int]
    core_local: tuple[int, int]
    core_bars: int
    pre_bars: int
    post_bars: int
    win_start: int
    win_end: int
    win_len: int
    box_center_ratio: float
    expected_start_time: str
    expected_anchor_time: str
    expected_end_time: str
    time_bucket: str
    semantic_status: str
    geometry_status: str
    training_eligible: bool
    production_eligible: bool


class Skip(Exception):
    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        self.detail = detail or reason
        super().__init__(self.detail)


def stable_key(*parts: object) -> str:
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()


def _utc(value: object) -> pd.Timestamp:
    stamp = pd.to_datetime(value, utc=True, errors="raise")
    if not isinstance(stamp, pd.Timestamp):
        raise TypeError(f"expected scalar timestamp, got {type(stamp)}")
    return stamp


def _series_groups() -> dict[tuple[str, str], list[Path]]:
    groups: dict[tuple[str, str], list[Path]] = {}
    for directory in (ROOT / "data/kline_cache", ROOT / "data/kline_fetched"):
        if not directory.is_dir():
            continue
        for key, paths in list_series(cache_dir=directory, bar="15m").items():
            groups.setdefault(key, []).extend(paths)
    return groups


def source_path_for_symbol(
    symbol: str,
    groups: dict[tuple[str, str], list[Path]],
) -> Path | None:
    """Resolve one unambiguous CSV without opening market rows."""
    matches: list[tuple[int, str, list[Path]]] = []
    for (_source, candidate), paths in groups.items():
        exact = candidate == symbol
        swap_equivalent = candidate == f"{symbol}_SWAP" or candidate.replace("_SWAP", "") == symbol
        if exact or swap_equivalent:
            matches.append((0 if exact else 1, candidate, sorted(set(paths))))
    if not matches:
        return None
    matches.sort(key=lambda item: (item[0], len(item[1]), item[1]))
    best_priority = matches[0][0]
    best = [item for item in matches if item[0] == best_priority]
    paths = sorted({path for _priority, _candidate, items in best for path in items})
    return paths[0] if len(paths) == 1 else None


def load_preholdout_prefix(path: Path, required_end: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load only the CSV prefix needed through ``required_end``.

    ``required_end`` is a global index recovered by the original continuous
    series builder.  Limiting ``nrows`` prevents post-boundary rows from being
    materialized merely to draw an older calibration crop.
    """
    requested_rows = required_end + 1
    raw = pd.read_csv(path, nrows=requested_rows, encoding_errors="replace")
    if not set(OHLCV_COLUMNS).issubset(raw.columns):
        raise Skip("bad_source_schema", str(path))
    if "confirm" in raw.columns:
        raw = raw[raw["confirm"] != 0]
    frame = raw[OHLCV_COLUMNS].copy()
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True, errors="coerce")
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = (
        frame.dropna(subset=["open_time", "open", "high", "low", "close"])
        .drop_duplicates("open_time", keep="last")
        .sort_values("open_time")
        .reset_index(drop=True)
    )
    if len(frame) <= required_end:
        raise Skip(
            "prefix_index_mismatch",
            f"{path.name}: need index {required_end}, got {len(frame)} rows",
        )
    if not frame["open_time"].is_monotonic_increasing:
        raise Skip("non_monotonic_prefix", str(path))
    max_time = _utc(frame.iloc[required_end]["open_time"])
    if max_time >= HOLDOUT_START:
        raise Skip("prefix_touches_holdout", str(max_time))
    return frame, {
        "source_csv": str(path.relative_to(ROOT)),
        "csv_rows_requested": requested_rows,
        "rows_materialized": len(frame),
        "max_materialized_time": max_time.isoformat(),
        "holdout_rows_materialized": 0,
    }


def build_candidates(
    stage_manifest_path: Path,
    owner_manifest_path: Path,
    groups: dict[tuple[str, str], list[Path]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Join train identity to legacy core geometry without semantic scoring."""
    stage_rows = json.loads(stage_manifest_path.read_text())
    owner_rows = json.loads(owner_manifest_path.read_text())
    owner_by_stem = {str(row["stem"]): row for row in owner_rows}
    if len(owner_by_stem) != len(owner_rows):
        raise ValueError("owner manifest has duplicate stems")
    event_counts = Counter(str(row["event_id"]) for row in stage_rows)
    if any(count != 1 for count in event_counts.values()):
        raise ValueError("Stage-A event_id must be unique")

    val_rows = [row for row in stage_rows if row["split"] == "val"]
    if not val_rows:
        raise ValueError("repaired Stage-A manifest has no val boundary metadata")
    val_start_min = min(_utc(row["start_time"]) for row in val_rows)
    train_allowed_end = val_start_min - timedelta(minutes=PURGE_BARS * BAR_MINUTES)
    train_rows = [row for row in stage_rows if row["split"] == "train"]
    ordered_times = sorted(_utc(row["anchor_time"]) for row in train_rows)
    q1 = ordered_times[len(ordered_times) // 3]
    q2 = ordered_times[(2 * len(ordered_times)) // 3]

    def time_bucket(stamp: pd.Timestamp) -> str:
        if stamp < q1:
            return "early"
        if stamp < q2:
            return "middle"
        return "late"

    candidates: list[dict[str, Any]] = []
    skips: Counter[str] = Counter()
    for stage in train_rows:
        owner = owner_by_stem.get(str(stage["source_stem"]))
        if owner is None:
            skips["missing_owner_row"] += 1
            continue
        core_start, core_end = map(int, owner["small_bars"])
        core_bars = core_end - core_start + 1
        if core_bars not in CORE_WIDTHS:
            skips["legacy_core_width_outside_5_7"] += 1
            continue
        source_path = source_path_for_symbol(str(stage["symbol"]), groups)
        if source_path is None:
            skips["ambiguous_or_missing_source_csv"] += 1
            continue
        anchor = int(stage["mid_global"])
        anchor_time = _utc(stage["anchor_time"])
        # Metadata-only worst-case boundary check before any market CSV access.
        max_end_time = anchor_time + timedelta(
            minutes=(core_end + max(POST_DELAYS) - anchor) * BAR_MINUTES
        )
        if max_end_time > train_allowed_end:
            skips["dynamic_window_crosses_purge"] += 1
            continue
        if max_end_time >= HOLDOUT_START:
            skips["dynamic_window_touches_holdout"] += 1
            continue
        candidates.append(
            {
                "event_id": str(stage["event_id"]),
                "source_stem": str(stage["source_stem"]),
                "symbol": str(stage["symbol"]),
                "stage_split": "train",
                "source_csv": str(source_path.relative_to(ROOT)),
                "mid_global": anchor,
                "core_global": [core_start, core_end],
                "core_bars": core_bars,
                "anchor_time": anchor_time,
                "time_bucket": time_bucket(anchor_time),
            }
        )
    profile = {
        "stage_manifest_rows": len(stage_rows),
        "stage_train_rows": len(train_rows),
        "stage_val_metadata_rows_for_boundary_only": len(val_rows),
        "stage_val_images_read": 0,
        "stage_val_labels_read": 0,
        "val_start_min": val_start_min.isoformat(),
        "purge_bars": PURGE_BARS,
        "train_allowed_end": train_allowed_end.isoformat(),
        "candidate_rows": len(candidates),
        "candidate_width_counts": dict(Counter(row["core_bars"] for row in candidates)),
        "candidate_time_bucket_counts": dict(Counter(row["time_bucket"] for row in candidates)),
        "skip_reasons": dict(skips),
    }
    return candidates, profile


def select_plans(candidates: list[dict[str, Any]]) -> list[CalibrationPlan]:
    """Pick 30 distinct events/symbols across delay, width, context and time."""
    selected: list[CalibrationPlan] = []
    used_events: set[str] = set()
    used_symbols: set[str] = set()
    time_buckets = ("early", "middle", "late")
    for post_bars in POST_DELAYS:
        combinations = [
            (pre_bars, core_bars)
            for pre_bars in PRE_CONTEXTS
            for core_bars in CORE_WIDTHS
        ]
        for slot, (pre_bars, core_bars) in enumerate(combinations):
            target_bucket = time_buckets[(slot + post_bars) % len(time_buckets)]
            pool = [
                row
                for row in candidates
                if row["core_bars"] == core_bars
                and row["event_id"] not in used_events
                and row["symbol"] not in used_symbols
            ]
            pool.sort(
                key=lambda row: (
                    row["time_bucket"] != target_bucket,
                    stable_key(
                        PROTOCOL,
                        post_bars,
                        pre_bars,
                        core_bars,
                        row["event_id"],
                    ),
                )
            )
            if not pool:
                raise ValueError(
                    f"not enough unique-symbol candidates for post={post_bars}, "
                    f"pre={pre_bars}, core={core_bars}"
                )
            row = pool[0]
            core_start, core_end = map(int, row["core_global"])
            win_start = core_start - pre_bars
            win_end = core_end + post_bars
            win_len = win_end - win_start + 1
            core_local = (pre_bars, pre_bars + core_bars - 1)
            center = ((core_local[0] + core_local[1]) / 2) / max(win_len - 1, 1)
            anchor_time = _utc(row["anchor_time"])
            mid_global = int(row["mid_global"])
            calibration_id = f"post{post_bars}_{slot + 1:02d}_{row['event_id']}"
            selected.append(
                CalibrationPlan(
                    calibration_id=calibration_id,
                    event_id=str(row["event_id"]),
                    source_stem=str(row["source_stem"]),
                    symbol=str(row["symbol"]),
                    stage_split="train",
                    source_csv=str(row["source_csv"]),
                    mid_global=mid_global,
                    core_global=(core_start, core_end),
                    core_local=core_local,
                    core_bars=core_bars,
                    pre_bars=pre_bars,
                    post_bars=post_bars,
                    win_start=win_start,
                    win_end=win_end,
                    win_len=win_len,
                    box_center_ratio=center,
                    expected_start_time=(
                        anchor_time
                        + timedelta(minutes=(win_start - mid_global) * BAR_MINUTES)
                    ).isoformat(),
                    expected_anchor_time=anchor_time.isoformat(),
                    expected_end_time=(
                        anchor_time
                        + timedelta(minutes=(win_end - mid_global) * BAR_MINUTES)
                    ).isoformat(),
                    time_bucket=str(row["time_bucket"]),
                    semantic_status="unreviewed",
                    geometry_status="unreviewed_legacy_core_proposal",
                    training_eligible=False,
                    production_eligible=False,
                )
            )
            used_events.add(str(row["event_id"]))
            used_symbols.add(str(row["symbol"]))
    return selected


def _draw_review_overlay(
    image: np.ndarray,
    box: tuple[float, float, float, float],
    *,
    caption: str,
) -> np.ndarray:
    vis = image.copy()
    height, width = vis.shape[:2]
    xc, yc, box_w, box_h = box
    x1 = int(round((xc - box_w / 2) * width))
    x2 = int(round((xc + box_w / 2) * width))
    y1 = int(round((yc - box_h / 2) * height))
    y2 = int(round((yc + box_h / 2) * height))
    cv2.line(vis, (x1, 40), (x1, height - 1), COLOR_BOUNDARY, 2, cv2.LINE_AA)
    cv2.line(vis, (x2, 40), (x2, height - 1), COLOR_BOUNDARY, 2, cv2.LINE_AA)
    cv2.rectangle(vis, (x1, y1), (x2, y2), COLOR_CORE, 4, cv2.LINE_AA)
    cv2.rectangle(vis, (0, 0), (width, 42), (250, 250, 250), -1)
    cv2.putText(
        vis,
        caption,
        (12, 29),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.68,
        (18, 28, 34),
        2,
        cv2.LINE_AA,
    )
    return vis


def render_plan(plan: CalibrationPlan, output_dir: Path) -> dict[str, Any]:
    source_path = ROOT / plan.source_csv
    frame, read_audit = load_preholdout_prefix(source_path, plan.win_end)
    anchor_time = _utc(frame.iloc[plan.mid_global]["open_time"])
    if anchor_time != _utc(plan.expected_anchor_time):
        raise Skip(
            "anchor_index_mismatch",
            f"{plan.symbol}: {anchor_time} != {plan.expected_anchor_time}",
        )
    start_time = _utc(frame.iloc[plan.win_start]["open_time"])
    end_time = _utc(frame.iloc[plan.win_end]["open_time"])
    if start_time != _utc(plan.expected_start_time) or end_time != _utc(plan.expected_end_time):
        raise Skip(
            "window_time_mismatch",
            f"{plan.symbol}: {start_time}..{end_time}",
        )
    enriched = add_mas(frame)
    window = enriched.iloc[plan.win_start : plan.win_end + 1].reset_index(drop=True)
    if len(window) != plan.win_len:
        raise Skip("short_window", plan.calibration_id)
    image, transform = render_chart(window, out_path=None)
    box = yolo_box_from_bars(
        transform,
        window,
        plan.core_local[0],
        plan.core_local[1],
    )
    if box is None:
        raise Skip("empty_yolo_box", plan.calibration_id)
    caption = (
        f"{plan.symbol} | PRE {plan.pre_bars} | CORE {plan.core_bars} | "
        f"POST {plan.post_bars} | W{plan.win_len} | center {plan.box_center_ratio:.0%}"
    )
    overlay = _draw_review_overlay(image, box, caption=caption)
    image_path = output_dir / "images" / f"post_{plan.post_bars}" / f"{plan.calibration_id}.png"
    label_path = output_dir / "labels" / f"post_{plan.post_bars}" / f"{plan.calibration_id}.txt"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    label_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(image_path), overlay)
    label_path.write_text(
        f"0 {box[0]:.6f} {box[1]:.6f} {box[2]:.6f} {box[3]:.6f}\n"
    )
    row = asdict(plan)
    row["core_global"] = list(plan.core_global)
    row["core_local"] = list(plan.core_local)
    row["yolo_box"] = list(box)
    row["actual_start_time"] = start_time.isoformat()
    row["actual_anchor_time"] = anchor_time.isoformat()
    row["actual_end_time"] = end_time.isoformat()
    row["image_path"] = str(image_path.relative_to(ROOT))
    row["label_path"] = str(label_path.relative_to(ROOT))
    row["image_sha256"] = sha256_file(image_path)
    row["label_sha256"] = sha256_file(label_path)
    row["read_audit"] = read_audit
    return row


def build_contact_sheet(rows: list[dict[str, Any]], output_path: Path) -> None:
    if len(rows) != PER_DELAY:
        raise ValueError(f"contact sheet requires {PER_DELAY} rows, got {len(rows)}")
    card_w = 900
    card_chart_h = int(round(card_w * 742 / 1280))
    card_h = card_chart_h + 48
    sheet = np.full((80 + 5 * card_h, 2 * card_w, 3), 244, dtype=np.uint8)
    post_bars = int(rows[0]["post_bars"])
    title = (
        f"SHORT-DELAY CALIBRATION | POST {post_bars} BARS | "
        f"10 UNREVIEWED CORE PROPOSALS"
    )
    cv2.putText(
        sheet,
        title,
        (22, 52),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.9,
        (20, 34, 43),
        2,
        cv2.LINE_AA,
    )
    for index, row in enumerate(rows):
        image = cv2.imread(str(ROOT / row["image_path"]))
        if image is None:
            raise FileNotFoundError(row["image_path"])
        resized = cv2.resize(image, (card_w, card_chart_h), interpolation=cv2.INTER_AREA)
        card = np.full((card_h, card_w, 3), 255, dtype=np.uint8)
        card[:card_chart_h] = resized
        footer = (
            f"#{index + 1:02d} {row['event_id']} | {row['time_bucket']} | "
            f"red=core only, teal=owner boundary"
        )
        cv2.putText(
            card,
            footer,
            (12, card_chart_h + 31),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.52,
            (48, 60, 68),
            1,
            cv2.LINE_AA,
        )
        row_i, col_i = divmod(index, 2)
        y0 = 80 + row_i * card_h
        x0 = col_i * card_w
        sheet[y0 : y0 + card_h, x0 : x0 + card_w] = card
        cv2.rectangle(
            sheet,
            (x0, y0),
            (x0 + card_w - 1, y0 + card_h - 1),
            (205, 213, 218),
            2,
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_path), sheet)


def run(
    stage_manifest_path: Path,
    owner_manifest_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    groups = _series_groups()
    candidates, profile = build_candidates(stage_manifest_path, owner_manifest_path, groups)
    plans = select_plans(candidates)
    rows = [render_plan(plan, output_dir) for plan in plans]
    sheets: dict[str, str] = {}
    for post_bars in POST_DELAYS:
        cohort = [row for row in rows if row["post_bars"] == post_bars]
        sheet_path = output_dir / f"calibration_post{post_bars}_10.png"
        build_contact_sheet(cohort, sheet_path)
        sheets[f"post_{post_bars}"] = str(sheet_path.relative_to(ROOT))

    counts = Counter(int(row["post_bars"]) for row in rows)
    widths = Counter(int(row["core_bars"]) for row in rows)
    pre_counts = Counter(int(row["pre_bars"]) for row in rows)
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "protocol": PROTOCOL,
        "scope": "owner_visual_geometry_calibration_only",
        "stage_manifest": str(stage_manifest_path.relative_to(ROOT)),
        "stage_manifest_sha256": sha256_file(stage_manifest_path),
        "owner_manifest": str(owner_manifest_path.relative_to(ROOT)),
        "owner_manifest_sha256": sha256_file(owner_manifest_path),
        "profile": profile,
        "counts": {
            "total": len(rows),
            "by_post_bars": dict(sorted(counts.items())),
            "by_core_bars": dict(sorted(widths.items())),
            "by_pre_bars": dict(sorted(pre_counts.items())),
            "unique_events": len({row["event_id"] for row in rows}),
            "unique_symbols": len({row["symbol"] for row in rows}),
        },
        "window_len_observed": [min(row["win_len"] for row in rows), max(row["win_len"] for row in rows)],
        "box_center_ratio_observed": [
            min(row["box_center_ratio"] for row in rows),
            max(row["box_center_ratio"] for row in rows),
        ],
        "contact_sheets": sheets,
        "selection_inputs": {
            "later_return": False,
            "model_confidence": False,
            "handwritten_morphology_score": False,
            "stage_val_market_rows": False,
            "holdout_rows": False,
        },
        "quality_gates": {
            "exactly_30": len(rows) == 30,
            "ten_per_post_delay": counts == Counter({3: 10, 4: 10, 5: 10}),
            "pre_6_to_10_balanced": pre_counts == Counter({6: 6, 7: 6, 8: 6, 9: 6, 10: 6}),
            "core_5_7_balanced": widths == Counter({5: 15, 7: 15}),
            "unique_events": len({row["event_id"] for row in rows}) == 30,
            "unique_symbols": len({row["symbol"] for row in rows}) == 30,
            "train_only": all(row["stage_split"] == "train" for row in rows),
            "full_window_before_purge_boundary": all(
                _utc(row["actual_end_time"]) <= _utc(profile["train_allowed_end"])
                for row in rows
            ),
            "holdout_rows_materialized_zero": all(
                row["read_audit"]["holdout_rows_materialized"] == 0 for row in rows
            ),
            "no_training_labels_yet": all(not row["training_eligible"] for row in rows),
            "semantic_and_geometry_unreviewed": all(
                row["semantic_status"] == "unreviewed"
                and row["geometry_status"] == "unreviewed_legacy_core_proposal"
                for row in rows
            ),
        },
        "automatic_training_labels": False,
        "training_eligible": False,
        "production_eligible": False,
        "holdout_read": False,
    }
    if not all(summary["quality_gates"].values()):
        raise RuntimeError(f"calibration quality gate failed: {summary['quality_gates']}")
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "manifest.jsonl").open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage-manifest", type=Path, default=DEFAULT_STAGE_MANIFEST)
    parser.add_argument("--owner-manifest", type=Path, default=DEFAULT_OWNER_MANIFEST)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    summary = run(args.stage_manifest, args.owner_manifest, args.out)
    print(
        json.dumps(
            {
                "output": str(args.out),
                "counts": summary["counts"],
                "sheets": summary["contact_sheets"],
                "gates": summary["quality_gates"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
