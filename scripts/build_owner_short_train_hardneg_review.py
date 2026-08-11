#!/usr/bin/env python3
"""Build a 200-event Owner review from train-time detector false-fire candidates.

The post-val 331-event review is used only as a labelled morphology reference:
254 Owner-rejected events define the false-fire neighbourhood and the 77
semantic positives (target + rebox) define what must not be mined blindly.
Every candidate itself comes from five continuous blocks ending before the
frozen train boundary. Candidate ranking uses only the causal detector window;
future bars are rendered later in a physically separate human-review image.

This script does not create YOLO labels, mutate the frozen validation set,
start training, read holdout, change thresholds, or mark rows training-eligible.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from scripts.backtest_owner_short_gold_center_recent import (
    HOLDOUT_START,
    load_snapshot,
    read_jsonl,
    sha256_file,
    write_jsonl,
)
from scripts.build_owner_short_hardneg_canary_review import (
    render_event,
    render_html,
    utc,
)
from yoyo.layers.l1_detection.data import ALL_MA_COLS, add_mas


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = "owner_short_train_hardneg_review200_v1_20260811"
BLOCK_ROOT = ROOT / "analysis/output/owner_short_train_hardneg_blocks_v1"
OUTPUT = ROOT / "analysis/output/owner_short_train_hardneg_review200_v1"
OUTPUT_HTML = (
    ROOT / "analysis/html/p2_owner_short_train_hardneg_review200_20260811.html"
)
REFERENCE_MANIFEST = (
    ROOT
    / "analysis/output/owner_short_gold_center_hardneg_canary_review331_v3"
    / "owner_review_labeled_manifest.jsonl"
)
REFERENCE_SNAPSHOT = (
    ROOT
    / "analysis/output/owner_short_gold_center_preholdout_canary_audit_20260503_v1"
    / "kline_snapshot"
)
OWNER_SHEET = ROOT / "analysis/output/owner_side_review/review_sheet.csv"
DATASET_SUMMARY = ROOT / "datasets/owner_short_gold_center_v1/summary.json"
WEIGHTS = (
    ROOT
    / "analysis/output/lsv2_stageb/owner_lsv2_short_gold_center_hardneg_r1_ft"
    / "weights/best.pt"
)
EXPECTED_WEIGHTS_SHA256 = (
    "029f80a52b5beda2e32f6bb5a188a39fd7f74fe0a3fef4dffa79ae620384f537"
)
BAR_MINUTES = 15
OWNER_GUARD_BARS = 12
FEATURE_POINTS = 19
K_NEIGHBOURS = 7
REVIEW_PER_BLOCK = 40
REVIEW_TOTAL = 200
BLOCKS = (
    ("B01_20250715", "2025-07-15T12:00:00Z"),
    ("B02_20250915", "2025-09-15T12:00:00Z"),
    ("B03_20251115", "2025-11-15T12:00:00Z"),
    ("B04_20260115", "2026-01-15T12:00:00Z"),
    ("B05_20260301", "2026-03-01T12:00:00Z"),
)
FEATURE_COLUMNS = ("open", "high", "low", "close", *ALL_MA_COLS)


def block_specs(root: Path = BLOCK_ROOT) -> list[dict[str, Any]]:
    """Return the frozen, multi-regime train-time scan contract."""
    specs = []
    for block_id, scan_end_value in BLOCKS:
        scan_end = utc(scan_end_value)
        audit_end = scan_end + pd.Timedelta(minutes=BAR_MINUTES * 48)
        base = root / block_id
        specs.append(
            {
                "block_id": block_id,
                "scan_end": scan_end,
                "audit_end": audit_end,
                "scan_snapshot": base / "scan_snapshot",
                "audit_snapshot": base / "audit_snapshot",
                "merged_scan": base / "merged",
            }
        )
    return specs


def causal_feature_vector(
    event: dict[str, Any],
    enriched: pd.DataFrame,
    *,
    points: int = FEATURE_POINTS,
) -> np.ndarray:
    """Describe the causal OHLC/MA morphology on a fixed normalized time grid.

    Columns used: open/high/low/close plus SMA/EMA 20/60/120. The slice ends at
    ``decision_time``; no row after the signal bar is accessed. Price level is
    removed by centering on the last causal close and scaling by the visible
    OHLC/MA span. Six geometry values encode only the predicted causal box and
    window coordinates.
    """
    times = pd.to_datetime(enriched["open_time"], utc=True)
    by_time = {utc(value): int(index) for index, value in enumerate(times)}
    start = by_time[utc(event["window_start_time"])]
    decision = by_time[utc(event["decision_time"])]
    window = enriched.iloc[start : decision + 1].reset_index(drop=True)
    if len(window) != int(event["window_len"]):
        raise ValueError(f"causal feature length mismatch: {event['event_id']}")
    matrix = window[list(FEATURE_COLUMNS)].astype(float).ffill().bfill().to_numpy()
    if not np.isfinite(matrix).all():
        raise ValueError(f"non-finite causal features: {event['event_id']}")
    center = float(window["close"].iloc[-1])
    span = max(float(matrix.max() - matrix.min()), abs(center) * 1e-6, 1e-12)
    normalized = (matrix - center) / span
    old_x = np.linspace(0.0, 1.0, len(window))
    new_x = np.linspace(0.0, 1.0, points)
    interpolated = np.column_stack(
        [np.interp(new_x, old_x, normalized[:, column]) for column in range(normalized.shape[1])]
    )
    geometry = np.asarray(
        [
            int(event["window_len"]) / 19.0,
            int(event["predicted_core_bars"]) / 10.0,
            int(event["decision_delay_bars"]) / 10.0,
            float(event["x1n"]),
            float(event["x2n"]),
            float(event["y2n"]) - float(event["y1n"]),
        ],
        dtype=float,
    )
    return np.concatenate([interpolated.ravel(), geometry])


def owner_forbidden_time_intervals(
    sheet: pd.DataFrame,
    *,
    guard_bars: int = OWNER_GUARD_BARS,
) -> dict[str, list[tuple[pd.Timestamp, pd.Timestamp]]]:
    """Convert every historical Owner box into a guarded UTC interval."""
    result: dict[str, list[tuple[pd.Timestamp, pd.Timestamp]]] = defaultdict(list)
    guard = pd.Timedelta(minutes=BAR_MINUTES * guard_bars)
    for row in sheet.to_dict("records"):
        end = utc(row["cut_time"])
        start = end - pd.Timedelta(minutes=BAR_MINUTES * (int(row["width_bars"]) - 1))
        result[str(row["symbol"])].append((start - guard, end + guard))
    return {symbol: sorted(intervals) for symbol, intervals in result.items()}


def touches_forbidden(
    event: dict[str, Any],
    forbidden: dict[str, list[tuple[pd.Timestamp, pd.Timestamp]]],
) -> bool:
    """Return whether the complete causal detector window touches an Owner box."""
    start = utc(event["window_start_time"])
    end = utc(event["decision_time"])
    return any(
        start <= blocked_end and end >= blocked_start
        for blocked_start, blocked_end in forbidden.get(str(event["symbol"]), [])
    )


def mean_knn_distance(
    query: np.ndarray,
    reference: np.ndarray,
    *,
    k: int = K_NEIGHBOURS,
) -> np.ndarray:
    """Compute deterministic mean Euclidean distance to the nearest references."""
    if len(reference) < k:
        raise ValueError(f"need at least {k} references, got {len(reference)}")
    result = np.empty(len(query), dtype=float)
    for start in range(0, len(query), 256):
        chunk = query[start : start + 256]
        squared = np.sum((chunk[:, None, :] - reference[None, :, :]) ** 2, axis=2)
        nearest = np.partition(squared, k - 1, axis=1)[:, :k]
        result[start : start + len(chunk)] = np.mean(np.sqrt(nearest), axis=1)
    return result


def select_diverse(
    rows: list[dict[str, Any]],
    *,
    per_block: int = REVIEW_PER_BLOCK,
) -> list[dict[str, Any]]:
    """Take an equal block allocation, preferring one event per symbol per block."""
    selected: list[dict[str, Any]] = []
    by_block: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_block[str(row["candidate_block"])].append(row)
    for block_id, _scan_end in BLOCKS:
        ranked = sorted(
            by_block.get(block_id, []),
            key=lambda row: (
                -float(row["hard_negative_affinity"]),
                -float(row["event_conf_max"]),
                str(row["decision_time"]),
                str(row["event_id"]),
            ),
        )
        chosen: list[dict[str, Any]] = []
        symbol_counts: Counter[str] = Counter()
        for cap in (1, 2, 3):
            for row in ranked:
                if row in chosen or symbol_counts[str(row["symbol"])] >= cap:
                    continue
                chosen.append(row)
                symbol_counts[str(row["symbol"])] += 1
                if len(chosen) == per_block:
                    break
            if len(chosen) == per_block:
                break
        if len(chosen) != per_block:
            raise ValueError(f"{block_id}: need {per_block} review candidates, have {len(chosen)}")
        selected.extend(chosen)
    return selected


def _load_enriched(path: Path) -> pd.DataFrame:
    return add_mas(load_snapshot(path))


def _reference_vectors(reference_rows: list[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray]:
    frames: dict[str, pd.DataFrame] = {}
    negatives: list[np.ndarray] = []
    positives: list[np.ndarray] = []
    for row in reference_rows:
        symbol = str(row["symbol"])
        if symbol not in frames:
            frames[symbol] = _load_enriched(REFERENCE_SNAPSHOT / f"{symbol}.csv")
        vector = causal_feature_vector(row, frames[symbol])
        if row["owner_decision"] == "hard_negative":
            negatives.append(vector)
        elif row["owner_decision"] in {"target", "rebox"}:
            positives.append(vector)
    return np.vstack(negatives), np.vstack(positives)


def _validate_block(
    spec: dict[str, Any],
    *,
    train_end: pd.Timestamp,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    merged = Path(spec["merged_scan"])
    audit = Path(spec["audit_snapshot"])
    scan_summary_path = merged / "scan_summary.json"
    events_path = merged / "events.jsonl"
    audit_summary_path = audit / "fetch_summary.json"
    scan_summary = json.loads(scan_summary_path.read_text(encoding="utf-8"))
    audit_summary = json.loads(audit_summary_path.read_text(encoding="utf-8"))
    if scan_summary.get("evaluation_scope") != "train_hardneg_mining":
        raise ValueError(f"{spec['block_id']}: scan scope is not train mining")
    if audit_summary.get("evaluation_scope") != "train_hardneg_mining":
        raise ValueError(f"{spec['block_id']}: audit scope is not train mining")
    if str(scan_summary.get("weights_sha256")) != EXPECTED_WEIGHTS_SHA256:
        raise ValueError(f"{spec['block_id']}: wrong detector weight")
    if utc(scan_summary["latest_bar"]) != utc(spec["scan_end"]):
        raise ValueError(f"{spec['block_id']}: scan endpoint drift")
    if utc(audit_summary["snapshot_end"]) != utc(spec["audit_end"]):
        raise ValueError(f"{spec['block_id']}: audit endpoint drift")
    if int(audit_summary.get("holdout_rows_materialized", -1)) != 0:
        raise ValueError(f"{spec['block_id']}: holdout proof missing")
    if utc(audit_summary["max_materialized_time"]) > train_end:
        raise ValueError(f"{spec['block_id']}: audit context exceeds frozen train")
    events = read_jsonl(events_path)
    return events, {
        "events": len(events),
        "symbols": int(scan_summary["symbols"]),
        "bar_endpoints": int(scan_summary["bar_endpoints"]),
        "window_exposures": int(scan_summary["window_exposures"]),
        "raw_detections": int(scan_summary["raw_detections"]),
        "scan_end": utc(spec["scan_end"]).isoformat(),
        "audit_end": utc(spec["audit_end"]).isoformat(),
        "weights_sha256": scan_summary["weights_sha256"],
        "scan_summary_sha256": sha256_file(scan_summary_path),
        "events_sha256": sha256_file(events_path),
        "audit_snapshot_summary_sha256": sha256_file(audit_summary_path),
    }


def build(
    *,
    block_root: Path = BLOCK_ROOT,
    output: Path = OUTPUT,
    output_html: Path = OUTPUT_HTML,
) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError(f"refusing to overwrite {output}")
    if sha256_file(WEIGHTS) != EXPECTED_WEIGHTS_SHA256:
        raise ValueError("local hard-negative model SHA drift")
    dataset_summary = json.loads(DATASET_SUMMARY.read_text(encoding="utf-8"))
    train_end = utc(dataset_summary["split_profile"]["train_end_max"])
    reference_rows = read_jsonl(REFERENCE_MANIFEST)
    reference_counts = Counter(str(row["owner_decision"]) for row in reference_rows)
    if reference_counts != Counter({"hard_negative": 254, "target": 66, "rebox": 11}):
        raise ValueError(f"reference decision drift: {reference_counts}")
    reference_negative, reference_positive = _reference_vectors(reference_rows)
    reference_joint = np.vstack([reference_negative, reference_positive])
    mean = reference_joint.mean(axis=0)
    scale = reference_joint.std(axis=0)
    scale[scale < 1e-8] = 1.0
    reference_negative = (reference_negative - mean) / scale
    reference_positive = (reference_positive - mean) / scale

    sheet = pd.read_csv(OWNER_SHEET)
    forbidden = owner_forbidden_time_intervals(sheet)
    pool: list[dict[str, Any]] = []
    vectors: list[np.ndarray] = []
    block_audit: dict[str, Any] = {}
    skip_counts: Counter[str] = Counter()
    specs = block_specs(block_root)
    for spec in specs:
        events, audit = _validate_block(spec, train_end=train_end)
        block_audit[str(spec["block_id"])] = audit
        frames: dict[str, pd.DataFrame] = {}
        for event in events:
            if utc(event["decision_time"]) > utc(spec["scan_end"]):
                skip_counts["decision_after_block"] += 1
                continue
            if touches_forbidden(event, forbidden):
                skip_counts["touches_owner_box_guard"] += 1
                continue
            symbol = str(event["symbol"])
            path = Path(spec["audit_snapshot"]) / "kline_snapshot" / f"{symbol}.csv"
            if not path.exists():
                skip_counts["missing_audit_symbol"] += 1
                continue
            if symbol not in frames:
                frames[symbol] = _load_enriched(path)
            try:
                vector = causal_feature_vector(event, frames[symbol])
            except (KeyError, ValueError):
                skip_counts["causal_feature_unavailable"] += 1
                continue
            pool.append(
                {
                    **event,
                    "candidate_block": str(spec["block_id"]),
                    "selection_future_used": False,
                    "selection_reference_protocol": str(reference_rows[0]["owner_review_protocol"]),
                    "owner_box_guard_bars": OWNER_GUARD_BARS,
                    "touches_owner_box_guard": False,
                    "training_eligible": False,
                }
            )
            vectors.append(vector)
    if not pool:
        raise ValueError("no train-time candidate survived safety filters")
    query = (np.vstack(vectors) - mean) / scale
    negative_distance = mean_knn_distance(query, reference_negative)
    positive_distance = mean_knn_distance(query, reference_positive)
    for row, neg_distance, pos_distance in zip(pool, negative_distance, positive_distance):
        row["nearest_owner_negative_distance"] = float(neg_distance)
        row["nearest_owner_positive_distance"] = float(pos_distance)
        row["hard_negative_affinity"] = float(pos_distance - neg_distance)
    selected = select_diverse(pool)
    selected.sort(
        key=lambda row: (
            -float(row["hard_negative_affinity"]),
            str(row["candidate_block"]),
            str(row["event_id"]),
        )
    )
    output.mkdir(parents=True, exist_ok=False)
    write_jsonl(output / "candidate_pool.jsonl", pool)
    selected_source: list[dict[str, Any]] = []
    for number, row in enumerate(selected, 1):
        item = dict(row)
        item["review_id"] = f"T{number:03d}"
        item["review_context"] = (
            f"训练块 {item['candidate_block']} · 误报相似度 {float(item['hard_negative_affinity']):.3f}"
        )
        selected_source.append(item)
    selected_path = output / "selected_candidates.jsonl"
    write_jsonl(selected_path, selected_source)

    review_rows: list[dict[str, Any]] = []
    render_frames: dict[tuple[str, str], pd.DataFrame] = {}
    spec_by_id = {str(spec["block_id"]): spec for spec in specs}
    for number, row in enumerate(selected_source, 1):
        key = (str(row["candidate_block"]), str(row["symbol"]))
        if key not in render_frames:
            spec = spec_by_id[key[0]]
            render_frames[key] = load_snapshot(
                Path(spec["audit_snapshot"]) / "kline_snapshot" / f"{key[1]}.csv"
            )
        rendered = render_event(
            row,
            render_frames[key],
            output,
            str(row["review_id"]),
        )
        rendered["training_eligibility_reason"] = "pending Owner decision; train-time and guard checks passed"
        review_rows.append(rendered)
        if number % 25 == 0 or number == len(selected_source):
            print(f"train hard-negative review render [{number}/{len(selected_source)}]", flush=True)
    review_manifest = output / "review_manifest.jsonl"
    write_jsonl(review_manifest, review_rows)
    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_html.write_text(
        render_html(
            review_rows,
            selected_path,
            output_html,
            protocol=PROTOCOL,
            title="训练区间难负例候选200张审核",
            heading="Owner-short · 训练区间难负例候选200张",
            description="五个历史训练块各40张；左图是模型当时可见输入，右图未来48根只供人工判断。",
            notice="形态和框都正确按1；形态正确但框偏按2；不是目标形态按3。全部默认未确认，不会自动写入训练集。",
        ),
        encoding="utf-8",
    )
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "protocol": PROTOCOL,
        "reference_manifest": str(REFERENCE_MANIFEST.relative_to(ROOT)),
        "reference_manifest_sha256": sha256_file(REFERENCE_MANIFEST),
        "reference_counts": dict(reference_counts),
        "weights_sha256": EXPECTED_WEIGHTS_SHA256,
        "train_end": train_end.isoformat(),
        "blocks": block_audit,
        "candidate_pool": len(pool),
        "candidate_skips": dict(skip_counts),
        "selected_review": len(review_rows),
        "selected_by_block": dict(Counter(str(row["candidate_block"]) for row in review_rows)),
        "selected_symbols": len({str(row["symbol"]) for row in review_rows}),
        "affinity": {
            "p10": float(np.quantile([float(row["hard_negative_affinity"]) for row in review_rows], 0.10)),
            "median": float(np.median([float(row["hard_negative_affinity"]) for row in review_rows])),
            "p90": float(np.quantile([float(row["hard_negative_affinity"]) for row in review_rows], 0.90)),
        },
        "future_used_for_selection": False,
        "holdout_read": False,
        "owner_decisions_preselected": 0,
        "training_eligible": 0,
        "labels_created": 0,
        "html": str(output_html.relative_to(ROOT)),
        "candidate_pool_sha256": sha256_file(output / "candidate_pool.jsonl"),
        "selected_candidates_sha256": sha256_file(selected_path),
        "review_manifest_sha256": sha256_file(review_manifest),
        "html_sha256": sha256_file(output_html),
        "quality_gates": {
            "exactly_200_unique_events": len(review_rows) == REVIEW_TOTAL
            and len({str(row["event_id"]) for row in review_rows}) == REVIEW_TOTAL,
            "exactly_40_per_block": set(Counter(str(row["candidate_block"]) for row in review_rows).values()) == {REVIEW_PER_BLOCK},
            "all_decisions_within_train": all(utc(row["decision_time"]) <= train_end for row in review_rows),
            "all_future_context_within_train": all(utc(row["future_review_end_time"]) <= train_end for row in review_rows),
            "no_owner_box_overlap": all(not row["touches_owner_box_guard"] for row in review_rows),
            "selection_is_causal": all(not row["selection_future_used"] for row in review_rows),
            "nothing_training_eligible": all(not row["training_eligible"] for row in review_rows),
            "no_label_directory": not (output / "labels").exists(),
            "strictly_preholdout": all(utc(row["future_review_end_time"]) < HOLDOUT_START for row in review_rows),
        },
    }
    if not all(summary["quality_gates"].values()):
        raise RuntimeError(summary["quality_gates"])
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--block-root", type=Path, default=BLOCK_ROOT)
    parser.add_argument("--out", type=Path, default=OUTPUT)
    parser.add_argument("--html", type=Path, default=OUTPUT_HTML)
    args = parser.parse_args()
    summary = build(block_root=args.block_root, output=args.out, output_html=args.html)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
