#!/usr/bin/env python3
"""Build the blind 200-item Local Signal V2 positive-semantics review.

This is a data/semantic audit only. It reads the frozen R2 positive manifest and
the already-materialized pre-holdout R1/R2 canary events, then writes exactly
100 stratified positive-pool items plus 100 stratified canary items. Every
review image contains only bars at or before ``decision_bar``; no outcome,
future candle, model source, confidence, or recommended verdict is rendered in
the Owner UI.

Positive rows use the stored R2 image/label bytes and rebuild the review image
from prefix-limited OHLCV only through the stored window end. Canary rows use
the frozen disposable snapshot and the exact causal window from the selected
event. The small candidate box is an audit overlay, never a new training label.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import cv2
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
YOYO_REPO = Path.home() / "yoyo-trading"
for module_path in (ROOT, YOYO_REPO):
    if str(module_path) not in sys.path:
        sys.path.insert(0, str(module_path))

from yoyo.layers.l1_detection.data import add_mas  # noqa: E402
from yoyo.layers.l1_detection.render import MARGIN, render_chart  # noqa: E402

from scripts.backtest_owner_short_gold_center_recent import (  # noqa: E402
    HOLDOUT_START,
    load_snapshot,
    read_jsonl,
    sha256_file,
    write_jsonl,
)
from scripts.build_owner_eth_shortdelay_calibration import load_preholdout_prefix  # noqa: E402
from scripts.build_owner_short_hardneg_canary_review import (  # noqa: E402
    ORANGE,
    box_rect,
    draw_normalized_box,
    utc,
    write_image,
)


PROTOCOL = "local_signal_v2_positive_semantic_review200_v1_20260812"
SEED = 20260812
POSITIVE_TOTAL = 100
CANARY_TOTAL = 100
CANARY_QUOTAS = {
    "common_retained": 50,
    "r2_new": 25,
    "r1_suppressed": 25,
}
CONFIDENCE_BINS = (0.35, 0.55)

DEFAULT_POSITIVE_MANIFEST = (
    ROOT / "datasets/owner_short_gold_center_hardneg_r2_ownerconfirmed/positive_manifest.jsonl"
)
DEFAULT_R1_EVENTS = (
    ROOT
    / "analysis/output/owner_short_gold_center_preholdout_canary_20260503_pm_v1"
    / "merged_r1/events.jsonl"
)
DEFAULT_R2_EVENTS = (
    ROOT
    / "analysis/output/owner_short_gold_center_preholdout_canary_20260503_pm_v1"
    / "merged_r2/events.jsonl"
)
DEFAULT_SNAPSHOT = (
    ROOT
    / "analysis/output/owner_short_gold_center_preholdout_canary_20260503_pm_v1"
    / "kline_snapshot"
)
DEFAULT_SNAPSHOT_SUMMARY = DEFAULT_SNAPSHOT.parent / "fetch_summary.json"
DEFAULT_COMPARISON = DEFAULT_SNAPSHOT.parent / "r1_r2_comparison.json"
DEFAULT_OUT = ROOT / "analysis/output/local_signal_v2_positive_semantic_review200_v1"
R2_WEIGHTS = (
    ROOT
    / "analysis/output/lsv2_stageb"
    / "owner_lsv2_short_gold_center_hardneg_r2_ownerconfirmed_ft/weights/best.pt"
)


def stable_hash(*parts: object) -> str:
    return hashlib.sha256("|".join(map(str, parts)).encode("utf-8")).hexdigest()


def json_sha256(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def bucket(value: float, cuts: tuple[float, float]) -> str:
    if value < cuts[0]:
        return "low"
    if value < cuts[1]:
        return "mid"
    return "high"


def time_bucket(value: object, values: list[pd.Timestamp]) -> str:
    stamp = utc(value)
    q1 = values[len(values) // 3]
    q2 = values[(2 * len(values)) // 3]
    if stamp < q1:
        return "early"
    if stamp < q2:
        return "middle"
    return "late"


def tercile_bucket(value: float, values: list[float]) -> str:
    ordered = sorted(float(item) for item in values)
    q1 = ordered[len(ordered) // 3]
    q2 = ordered[(2 * len(ordered)) // 3]
    if value < q1:
        return "low"
    if value < q2:
        return "mid"
    return "high"


def frame_volatility(frame: pd.DataFrame) -> float:
    close = pd.to_numeric(frame["close"], errors="coerce")
    high = pd.to_numeric(frame["high"], errors="coerce")
    low = pd.to_numeric(frame["low"], errors="coerce")
    midpoint = close.abs().replace(0, np.nan)
    return float(((high - low).abs() / midpoint).median())


def read_positive_frame(row: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    frame, audit = load_preholdout_prefix(ROOT / str(row["source_csv"]), int(row["win_end"]))
    window = frame.iloc[int(row["win_start"]) : int(row["win_end"]) + 1].reset_index(drop=True)
    if len(window) != int(row["win_len"]):
        raise ValueError(f"positive window length mismatch: {row['sample_id']}")
    if utc(window["open_time"].iloc[-1]) != utc(row["end_time"]):
        raise ValueError(f"positive decision time mismatch: {row['sample_id']}")
    return add_mas(frame).iloc[int(row["win_start"]) : int(row["win_end"]) + 1].reset_index(drop=True), audit


def score_positive_pool(
    rows: list[dict[str, Any]],
    weights: Path,
    *,
    device: str,
    batch: int,
) -> dict[str, float]:
    """Score frozen positive images without changing the frozen conf/NMS contract."""
    from ultralytics import YOLO  # noqa: PLC0415

    model = YOLO(str(weights))
    scores: dict[str, float] = {}
    for start in range(0, len(rows), batch):
        chunk = rows[start : start + batch]
        results = model.predict(
            [str(ROOT / str(row["image_path"])) for row in chunk],
            conf=0.001,
            iou=0.70,
            imgsz=960,
            device=device,
            batch=batch,
            augment=False,
            save=False,
            verbose=False,
        )
        for row, result in zip(chunk, results):
            confidences = (
                result.boxes.conf.detach().cpu().numpy().astype(float).tolist()
                if result.boxes is not None and len(result.boxes)
                else []
            )
            scores[str(row["sample_id"])] = max(confidences, default=0.0)
        done = min(start + batch, len(rows))
        if done % 256 == 0 or done == len(rows):
            print(f"positive confidence audit [{done}/{len(rows)}]", flush=True)
    return scores


def positive_pool_volatility(rows: list[dict[str, Any]]) -> dict[str, float]:
    """Measure each positive's causal OHLC range without opening later rows."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["source_csv"])].append(row)
    result: dict[str, float] = {}
    for source_csv, cohort in sorted(grouped.items()):
        max_end = max(int(row["win_end"]) for row in cohort)
        frame, _audit = load_preholdout_prefix(ROOT / source_csv, max_end)
        for row in cohort:
            window = frame.iloc[int(row["win_start"]) : int(row["win_end"]) + 1]
            if len(window) != int(row["win_len"]):
                raise ValueError(f"positive volatility window mismatch: {row['sample_id']}")
            result[str(row["sample_id"])] = frame_volatility(window)
    if len(result) != len(rows):
        raise ValueError(f"positive volatility coverage mismatch: {len(result)} / {len(rows)}")
    return result


def normalize_positive_pool(
    rows: list[dict[str, Any]],
    scores: dict[str, float],
    volatilities: dict[str, float],
) -> list[dict[str, Any]]:
    stamps = sorted(utc(row["end_time"]) for row in rows)
    positions = sorted(float(row["yolo_box"][0]) for row in rows)
    volatility_values = sorted(volatilities.values())
    normalized: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        image_path = ROOT / str(row["image_path"])
        if not image_path.is_file() or sha256_file(image_path) != str(row["image_sha256"]):
            raise ValueError(f"positive image lineage mismatch: {row['sample_id']}")
        item.update(
            {
                "event_id": f"positive:{row['sample_id']}",
                "decision_time": str(row["end_time"]),
                "window_start_time": str(row["start_time"]),
                "window_len": int(row["win_len"]),
                "source_type_internal": "positive_pool",
                "source_dataset_internal": "owner_short_gold_center_hardneg_r2_ownerconfirmed",
                "source_model_internal": "R2",
                "model_confidence_internal": float(scores[str(row["sample_id"])]),
                "confidence_stratum_internal": bucket(
                    float(scores[str(row["sample_id"])]), CONFIDENCE_BINS
                ),
                "time_stratum_internal": time_bucket(row["end_time"], stamps),
                "position_stratum_internal": tercile_bucket(
                    float(row["yolo_box"][0]), positions
                ),
                "volatility_internal": float(volatilities[str(row["sample_id"])]),
                "volatility_stratum_internal": tercile_bucket(
                    float(volatilities[str(row["sample_id"])]), volatility_values
                ),
                "core_stratum_internal": str(int(row["core_bars"])),
                "split_stratum_internal": str(row["split"]),
            }
        )
        normalized.append(item)
    return normalized


def round_robin_stratified(
    rows: list[dict[str, Any]],
    total: int,
    *,
    stratum_fields: tuple[str, ...],
    salt: str,
) -> list[dict[str, Any]]:
    """Select deterministically across strata while capping repeated symbols."""
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = tuple(str(row.get(field, "")) for field in stratum_fields)
        groups[key].append(row)
    for key, cohort in groups.items():
        cohort.sort(key=lambda row: stable_hash(PROTOCOL, SEED, salt, key, row["event_id"]))
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    symbol_counts: Counter[str] = Counter()
    ordered_keys = sorted(groups, key=lambda key: stable_hash(PROTOCOL, SEED, salt, key))
    for symbol_cap in (1, 2, 3, 5, total):
        made_progress = True
        while len(selected) < total and made_progress:
            made_progress = False
            for key in ordered_keys:
                candidate = next(
                    (
                        row
                        for row in groups[key]
                        if row["event_id"] not in selected_ids
                        and symbol_counts[str(row["symbol"])] < symbol_cap
                    ),
                    None,
                )
                if candidate is None:
                    continue
                selected.append(candidate)
                selected_ids.add(str(candidate["event_id"]))
                symbol_counts[str(candidate["symbol"])] += 1
                made_progress = True
                if len(selected) == total:
                    break
    if len(selected) != total:
        raise ValueError(f"stratified selector produced {len(selected)} / {total}")
    return selected


def select_positive_rows(
    rows: list[dict[str, Any]],
    scores: dict[str, float],
    volatilities: dict[str, float],
) -> list[dict[str, Any]]:
    normalized = normalize_positive_pool(rows, scores, volatilities)
    return round_robin_stratified(
        normalized,
        POSITIVE_TOTAL,
        stratum_fields=(
            "split_stratum_internal",
            "time_stratum_internal",
            "position_stratum_internal",
            "confidence_stratum_internal",
            "volatility_stratum_internal",
            "core_stratum_internal",
        ),
        salt="positive",
    )


def pair_canary_events(
    r1_rows: list[dict[str, Any]],
    r2_rows: list[dict[str, Any]],
    *,
    gap_bars: int,
) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[tuple[float, int, str, str, int, int]] = []
    for left_index, left in enumerate(r1_rows):
        for right_index, right in enumerate(r2_rows):
            if str(left["symbol"]) != str(right["symbol"]):
                continue
            core_distance = abs(float(left["core_mid_i"]) - float(right["core_mid_i"]))
            if core_distance > gap_bars:
                continue
            decision_distance = abs(int(left["decision_i"]) - int(right["decision_i"]))
            candidates.append(
                (
                    core_distance,
                    decision_distance,
                    str(left["event_id"]),
                    str(right["event_id"]),
                    left_index,
                    right_index,
                )
            )
    used_left: set[int] = set()
    used_right: set[int] = set()
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for _core, _decision, _left_id, _right_id, left_index, right_index in sorted(candidates):
        if left_index in used_left or right_index in used_right:
            continue
        used_left.add(left_index)
        used_right.add(right_index)
        pairs.append((r1_rows[left_index], r2_rows[right_index]))
    return (
        pairs,
        [row for index, row in enumerate(r1_rows) if index not in used_left],
        [row for index, row in enumerate(r2_rows) if index not in used_right],
    )


def select_canary_rows(
    r1_rows: list[dict[str, Any]],
    r2_rows: list[dict[str, Any]],
    snapshot_dir: Path,
    *,
    gap_bars: int,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    pairs, r1_only, r2_only = pair_canary_events(r1_rows, r2_rows, gap_bars=gap_bars)
    cohorts: dict[str, list[dict[str, Any]]] = {
        "common_retained": [dict(right) for _left, right in pairs],
        "r2_new": [dict(row) for row in r2_only],
        "r1_suppressed": [dict(row) for row in r1_only],
    }
    expected = {"common_retained": 163, "r2_new": 32, "r1_suppressed": 60}
    actual = {key: len(value) for key, value in cohorts.items()}
    if actual != expected:
        raise ValueError(f"canary decomposition drift: expected={expected} actual={actual}")
    frames: dict[str, pd.DataFrame] = {}
    selected: list[dict[str, Any]] = []
    for cohort, rows in cohorts.items():
        stamps = sorted(utc(row["decision_time"]) for row in rows)
        for row in rows:
            symbol = str(row["symbol"])
            if symbol not in frames:
                frames[symbol] = load_snapshot(snapshot_dir / f"{symbol}.csv")
            frame = frames[symbol]
            start = int(row["window_start_i"])
            end = int(row["decision_i"])
            window = frame.iloc[start : end + 1]
            if len(window) != int(row["window_len"]):
                raise ValueError(f"canary window mismatch: {row['event_id']}")
            volatility = frame_volatility(window)
            row.update(
                {
                    "source_type_internal": "canary_candidate",
                    "source_dataset_internal": "owner_short_gold_center_preholdout_canary_20260503_pm_v1",
                    "source_model_internal": "R2" if cohort != "r1_suppressed" else "R1",
                    "model_confidence_internal": float(row["event_conf_max"]),
                    "canary_cohort_internal": cohort,
                    "confidence_stratum_internal": bucket(float(row["event_conf_max"]), CONFIDENCE_BINS),
                    "volatility_internal": volatility,
                    "time_stratum_internal": time_bucket(row["decision_time"], stamps),
                }
            )
        volatility_values = [float(row["volatility_internal"]) for row in rows]
        for row in rows:
            row["volatility_stratum_internal"] = tercile_bucket(
                float(row["volatility_internal"]), volatility_values
            )
        selected.extend(
            round_robin_stratified(
                rows,
                CANARY_QUOTAS[cohort],
                stratum_fields=(
                    "confidence_stratum_internal",
                    "time_stratum_internal",
                    "volatility_stratum_internal",
                ),
                salt=f"canary:{cohort}",
            )
        )
    if len(selected) != CANARY_TOTAL:
        raise ValueError(f"expected {CANARY_TOTAL} canary selections, got {len(selected)}")
    return selected, actual


def draw_decision_boundary(image: np.ndarray) -> None:
    """Mark the rightmost visible bar as the causal decision boundary."""
    x = image.shape[1] - MARGIN
    cv2.line(image, (x, MARGIN), (x, image.shape[0] - MARGIN), (185, 90, 20), 4, cv2.LINE_AA)
    cv2.putText(
        image,
        "DECISION",
        (max(MARGIN, x - 122), 31),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        (185, 90, 20),
        2,
        cv2.LINE_AA,
    )


def render_positive(row: dict[str, Any], output: Path, review_id: str) -> dict[str, Any]:
    window, read_audit = read_positive_frame(row)
    image, _transform = render_chart(window, out_path=None)
    stored = cv2.imread(str(ROOT / str(row["image_path"])), cv2.IMREAD_COLOR)
    if stored is None or not np.array_equal(stored, image):
        raise ValueError(f"positive rerender drift: {row['sample_id']}")
    annotated = image.copy()
    xc, yc, width, height = map(float, row["yolo_box"])
    draw_normalized_box(
        annotated,
        {"x1n": xc - width / 2, "x2n": xc + width / 2, "y1n": yc - height / 2, "y2n": yc + height / 2},
    )
    draw_decision_boundary(annotated)
    path = output / "images" / f"{review_id}.png"
    write_image(path, annotated)
    return {
        "source_type": "positive_pool",
        "event_id": str(row["event_id"]),
        "symbol": str(row["symbol"]),
        "anchor_bar": int(row["core_global"][0]),
        "decision_bar": int(row["win_end"]),
        "visible_end_bar": int(row["win_end"]),
        "window_start_bar": int(row["win_start"]),
        "window_length": int(row["win_len"]),
        "box_start_bar": int(row["core_global"][0]),
        "box_end_bar": int(row["core_global"][1]),
        "decision_time": str(row["end_time"]),
        "model_confidence": float(row["model_confidence_internal"]),
        "source_model": "R2",
        "source_dataset": str(row["source_dataset_internal"]),
        "source_manifest_reference": str(DEFAULT_POSITIVE_MANIFEST.relative_to(ROOT)),
        "label_sha256": str(row["label_sha256"]),
        "source_image_sha256": str(row["image_sha256"]),
        "image_path": str(path.relative_to(ROOT)),
        "image_sha256": sha256_file(path),
        "sampling_strata": {
            "split": row["split_stratum_internal"],
            "time": row["time_stratum_internal"],
            "position": row["position_stratum_internal"],
            "confidence": row["confidence_stratum_internal"],
            "volatility": row["volatility_stratum_internal"],
            "core_bars": row["core_stratum_internal"],
        },
        "source_read_audit": read_audit,
    }


def render_canary(row: dict[str, Any], frame: pd.DataFrame, output: Path, review_id: str) -> dict[str, Any]:
    enriched = add_mas(frame)
    start = int(row["window_start_i"])
    decision = int(row["decision_i"])
    window = enriched.iloc[start : decision + 1].reset_index(drop=True)
    if len(window) != int(row["window_len"]):
        raise ValueError(f"canary render window mismatch: {row['event_id']}")
    if utc(window["open_time"].iloc[-1]) != utc(row["decision_time"]):
        raise ValueError(f"canary decision time mismatch: {row['event_id']}")
    image, _transform = render_chart(window, out_path=None)
    annotated = image.copy()
    draw_normalized_box(annotated, row)
    draw_decision_boundary(annotated)
    path = output / "images" / f"{review_id}.png"
    write_image(path, annotated)
    return {
        "source_type": "canary_candidate",
        "event_id": str(row["event_id"]),
        "symbol": str(row["symbol"]),
        "anchor_bar": int(row["core_start_i"]),
        "decision_bar": decision,
        "visible_end_bar": decision,
        "window_start_bar": start,
        "window_length": int(row["window_len"]),
        "box_start_bar": int(row["core_start_i"]),
        "box_end_bar": int(row["core_end_i"]),
        "decision_time": str(row["decision_time"]),
        "model_confidence": float(row["model_confidence_internal"]),
        "source_model": str(row["source_model_internal"]),
        "source_dataset": str(row["source_dataset_internal"]),
        "source_manifest_reference": (
            str(DEFAULT_R1_EVENTS.relative_to(ROOT))
            if row["source_model_internal"] == "R1"
            else str(DEFAULT_R2_EVENTS.relative_to(ROOT))
        ),
        "label_sha256": None,
        "source_image_sha256": None,
        "image_path": str(path.relative_to(ROOT)),
        "image_sha256": sha256_file(path),
        "canary_cohort": str(row["canary_cohort_internal"]),
        "sampling_strata": {
            "confidence": row["confidence_stratum_internal"],
            "time": row["time_stratum_internal"],
            "volatility": row["volatility_stratum_internal"],
        },
    }


def review_order(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda row: stable_hash(PROTOCOL, SEED, "blind-order", row["event_id"]))


def image_href(path: str, output_html: Path) -> str:
    return Path(os.path.relpath(ROOT / path, output_html.parent)).as_posix()


def render_review_html(rows: list[dict[str, Any]], output_html: Path) -> str:
    public = [
        {
            "review_id": row["review_id"],
            "symbol": row["symbol"],
            "image": image_href(row["image_path"], output_html),
        }
        for row in rows
    ]
    payload = json.dumps(public, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><link rel=\"icon\" href=\"data:,\"><title>Local Signal V2 · Owner YES / NO</title>
<style>:root{{--bg:#eef2f4;--ink:#15232d;--muted:#667784;--yes:#16864b;--no:#cf3d3d;--skip:#8b6b12;--blue:#1769aa}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}}header{{background:#fff;border-bottom:1px solid #d7e0e6;padding:14px 20px}}h1{{margin:0 0 5px;font-size:23px}}header p{{margin:4px 0;color:#4e616f}}main{{max-width:1320px;margin:16px auto;padding:0 16px}}.panel{{background:#fff;border-radius:13px;overflow:hidden;box-shadow:0 3px 14px #0002}}.top{{display:flex;justify-content:space-between;padding:12px 15px;border-bottom:1px solid #e0e6ea;font-weight:800}}#chart{{display:block;width:100%;height:auto;background:#fff}}.choices{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;padding:13px}}button{{padding:14px;border:1px solid #bdc9d1;border-radius:9px;background:#fff;font-size:18px;font-weight:800;cursor:pointer}}button[data-v=yes].active{{background:var(--yes);color:#fff}}button[data-v=no].active{{background:var(--no);color:#fff}}button[data-v=skip].active{{background:var(--skip);color:#fff}}.nav{{display:flex;gap:8px;padding:0 13px 13px}}.nav button{{padding:8px 12px;font-size:14px}}#status{{margin-left:auto;padding:8px 0;color:var(--muted);font-weight:800}}.notice{{background:#fff6d9;border:1px solid #e5c462;border-radius:10px;padding:10px 13px;margin-bottom:12px}}.error{{color:#b21d1d}}@media(max-width:700px){{.choices{{grid-template-columns:1fr}}}}</style></head>
<body><header><h1>Local Signal V2 · SHORT 语义审核</h1><p>假设时间停在图中最后一根 K：这是不是你要的“启动前沿”？橙框只是候选核心。</p><p><b>快捷键：</b>Y=YES · N=NO · S=SKIP · ←/→=上一张/下一张。判断后自动保存并前进。</p></header><main><div class=\"notice\"><b>只看当时：</b>图中没有 decision 之后的 K 线、未来收益、模型来源、置信度或推荐答案。</div><section class=\"panel\"><div class=\"top\"><span id=\"item\"></span><span id=\"progress\"></span></div><img id=\"chart\" alt=\"causal review chart\"><div class=\"choices\"><button data-v=\"yes\" onclick=\"choose('YES')\">Y · YES</button><button data-v=\"no\" onclick=\"choose('NO')\">N · NO</button><button data-v=\"skip\" onclick=\"choose('SKIP')\">S · SKIP</button></div><div class=\"nav\"><button onclick=\"move(-1)\">← 上一张</button><button onclick=\"move(1)\">下一张 →</button><span id=\"status\"></span></div></section></main>
<script>const ITEMS={payload};let index=0,state={{}};const status=document.getElementById('status');
async function loadState(){{try{{const r=await fetch('/api/state');if(!r.ok)throw new Error(await r.text());state=(await r.json()).verdicts||{{}}}}catch(e){{status.innerHTML='<span class=\"error\">请用 README 命令启动审核服务，不能直接双击 HTML。</span>'}}render()}}
function render(){{const it=ITEMS[index];document.getElementById('chart').src=it.image;document.getElementById('item').textContent=it.review_id+' · '+it.symbol;document.getElementById('progress').textContent=(index+1)+' / '+ITEMS.length;document.querySelectorAll('[data-v]').forEach(b=>b.classList.toggle('active',b.dataset.v.toUpperCase()===state[it.review_id]));const done=Object.keys(state).length;status.textContent='已保存 '+done+' / '+ITEMS.length}}
async function choose(v){{const it=ITEMS[index];const r=await fetch('/api/verdict',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{review_id:it.review_id,owner_verdict:v}})}});if(!r.ok){{status.textContent='保存失败：'+await r.text();return}}state[it.review_id]=v;if(index<ITEMS.length-1)index++;render()}}
function move(delta){{index=Math.max(0,Math.min(ITEMS.length-1,index+delta));render()}}
document.addEventListener('keydown',e=>{{if(e.metaKey||e.ctrlKey||e.altKey)return;const k=e.key.toLowerCase();if(k==='y')choose('YES');else if(k==='n')choose('NO');else if(k==='s')choose('SKIP');else if(e.key==='ArrowLeft')move(-1);else if(e.key==='ArrowRight')move(1)}});loadState();</script></body></html>"""


def distributions(rows: Iterable[dict[str, Any]], field: str) -> dict[str, int]:
    return dict(sorted(Counter(str(row[field]) for row in rows).items()))


def build(
    positive_manifest: Path,
    r1_events: Path,
    r2_events: Path,
    snapshot_dir: Path,
    snapshot_summary_path: Path,
    comparison_path: Path,
    output: Path,
    *,
    device: str,
    batch: int,
    frozen_main_commit: str,
) -> dict[str, Any]:
    snapshot_summary = json.loads(snapshot_summary_path.read_text(encoding="utf-8"))
    comparison = json.loads(comparison_path.read_text(encoding="utf-8"))
    subprocess.run(
        ["git", "cat-file", "-e", f"{frozen_main_commit}^{{commit}}"],
        cwd=ROOT,
        check=True,
    )
    builder_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    if int(snapshot_summary.get("holdout_rows_materialized", -1)) != 0:
        raise ValueError("snapshot contains holdout rows")
    if utc(snapshot_summary["max_materialized_time"]) >= HOLDOUT_START:
        raise ValueError("snapshot touches holdout")
    if comparison.get("contract", {}).get("confidence") != 0.25:
        raise ValueError("canary confidence drift")
    if comparison.get("contract", {}).get("nms_iou") != 0.70:
        raise ValueError("canary NMS drift")
    positives = read_jsonl(positive_manifest)
    if len(positives) != 1345:
        raise ValueError(f"expected 1345 positives, got {len(positives)}")
    positive_scores = score_positive_pool(positives, R2_WEIGHTS, device=device, batch=batch)
    positive_volatilities = positive_pool_volatility(positives)
    positive_selected = select_positive_rows(
        positives, positive_scores, positive_volatilities
    )
    canary_selected, canary_decomposition = select_canary_rows(
        read_jsonl(r1_events),
        read_jsonl(r2_events),
        snapshot_dir,
        gap_bars=int(comparison["contract"]["event_gap_bars"]),
    )

    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"refusing to overwrite non-empty review output: {output}")
    output.mkdir(parents=True, exist_ok=True)
    rendered: list[dict[str, Any]] = []
    for number, row in enumerate(positive_selected, 1):
        rendered.append(render_positive(row, output, f"P{number:03d}"))
    frames: dict[str, pd.DataFrame] = {}
    for number, row in enumerate(canary_selected, 1):
        symbol = str(row["symbol"])
        if symbol not in frames:
            frames[symbol] = load_snapshot(snapshot_dir / f"{symbol}.csv")
        rendered.append(render_canary(row, frames[symbol], output, f"C{number:03d}"))

    blinded = review_order(rendered)
    for number, row in enumerate(blinded, 1):
        row.update(
            {
                "review_id": f"S{number:03d}",
                "future_bars": 0,
                "owner_verdict": None,
                "reviewed_at": None,
                "training_eligible": False,
                "production_eligible": False,
                "holdout_read": False,
            }
        )
    manifest = output / "review_manifest.jsonl"
    write_jsonl(manifest, blinded)
    html_path = output / "index.html"
    html_path.write_text(render_review_html(blinded, html_path), encoding="utf-8")
    readme = output / "README.md"
    readme.write_text(
        """# Local Signal V2 Owner YES / NO 审核

启动：

```bash
cd /Users/zhangzc/fable-trading
PYTHONPATH=.:/Users/zhangzc/yoyo-trading .venv/bin/python scripts/serve_local_signal_v2_semantic_review.py
```

浏览器打开 `http://127.0.0.1:8766/`。Y=YES，N=NO，S=SKIP，左右方向键前后移动。
每次判断立即写入本目录的 `owner_verdicts.jsonl`；可中断后继续，也可修改上一张。
主图严格止于 decision bar，不含未来 K、收益、TP/SL、模型来源和置信度。
审核完成前不要运行任何训练；完成后只生成解盲诊断报告。
""",
        encoding="utf-8",
    )

    causality_rows = []
    for row in blinded:
        image = ROOT / str(row["image_path"])
        causality_rows.append(
            {
                "review_id": row["review_id"],
                "visible_end_bar": row["visible_end_bar"],
                "decision_bar": row["decision_bar"],
                "future_bars": row["future_bars"],
                "image_exists": image.is_file(),
                "image_sha_matches": image.is_file() and sha256_file(image) == row["image_sha256"],
                "pass": (
                    int(row["visible_end_bar"]) == int(row["decision_bar"])
                    and int(row["future_bars"]) == 0
                    and image.is_file()
                    and sha256_file(image) == row["image_sha256"]
                ),
            }
        )
    causality_audit = {
        "protocol": PROTOCOL,
        "rows": len(causality_rows),
        "visible_end_equals_decision": sum(
            int(row["visible_end_bar"]) == int(row["decision_bar"]) for row in blinded
        ),
        "future_bars_zero": sum(int(row["future_bars"]) == 0 for row in blinded),
        "all_pass": all(row["pass"] for row in causality_rows),
        "items": causality_rows,
        "holdout_read": False,
    }
    (output / "causality_audit.json").write_text(
        json.dumps(causality_audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    sampling_audit = {
        "protocol": PROTOCOL,
        "seed": SEED,
        "positive_pool": {
            "population": len(positives),
            "selected": len(positive_selected),
            "symbols": len({row["symbol"] for row in positive_selected}),
            "split": distributions(positive_selected, "split_stratum_internal"),
            "time": distributions(positive_selected, "time_stratum_internal"),
            "position": distributions(positive_selected, "position_stratum_internal"),
            "confidence": distributions(positive_selected, "confidence_stratum_internal"),
            "volatility": distributions(positive_selected, "volatility_stratum_internal"),
            "core_bars": distributions(positive_selected, "core_stratum_internal"),
            "selection_rule": "deterministic round-robin over split/time/position/confidence/volatility/core strata with symbol caps",
        },
        "canary": {
            "population_decomposition": canary_decomposition,
            "selected": len(canary_selected),
            "selected_by_internal_cohort": distributions(canary_selected, "canary_cohort_internal"),
            "symbols": len({row["symbol"] for row in canary_selected}),
            "confidence": distributions(canary_selected, "confidence_stratum_internal"),
            "time": distributions(canary_selected, "time_stratum_internal"),
            "volatility": distributions(canary_selected, "volatility_stratum_internal"),
            "selection_rule": "frozen 50 common / 25 R2-new / 25 R1-suppressed, deterministic round-robin by confidence/time/volatility with symbol caps",
        },
        "owner_ui_blinded_fields": ["source_type", "source_model", "model_confidence", "canary_cohort"],
        "owner_verdicts_preselected": 0,
    }
    (output / "sampling_audit.json").write_text(
        json.dumps(sampling_audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    freeze = {
        "protocol": PROTOCOL,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "audit_start_main_commit": frozen_main_commit,
        "builder_commit": builder_commit,
        "weights": {
            "stage_a": {
                "path": "analysis/output/lsv2_stagea/owner_lsv2_stagea_randomcrop_v1_cold/weights/best.pt",
                "sha256": "c0e94f47df125e298b044d9f10acd0b8e4f525ccd6143ce34f8d174af802bf1a",
            },
            "stage_b_cold": {
                "path": "analysis/output/lsv2_stageb/owner_lsv2_stageb_cold/weights/best.pt",
                "sha256": "de80173ed05962d70bb19ae50539ff08309579a9cae205403c4645e88b13b362",
            },
            "positive_baseline": {
                "path": "analysis/output/lsv2_stageb/owner_lsv2_short_gold_center_v1_ft/weights/best.pt",
                "sha256": "da278820f2d96a64006d9ff6358b7c98faec52249ec8a6f4fe6bf55254fc65b4",
            },
            "r1": {
                "path": "analysis/output/lsv2_stageb/owner_lsv2_short_gold_center_hardneg_r1_ft/weights/best.pt",
                "sha256": "029f80a52b5beda2e32f6bb5a188a39fd7f74fe0a3fef4dffa79ae620384f537",
            },
            "r2": {
                "path": "analysis/output/lsv2_stageb/owner_lsv2_short_gold_center_hardneg_r2_ownerconfirmed_ft/weights/best.pt",
                "sha256": "52cd38fda253f052c3c8eb712d93557c0125dceb336fb4cd58136236dca32afe",
            },
        },
        "input_sha256": {
            str(positive_manifest.relative_to(ROOT)): sha256_file(positive_manifest),
            str(r1_events.relative_to(ROOT)): sha256_file(r1_events),
            str(r2_events.relative_to(ROOT)): sha256_file(r2_events),
            str(snapshot_summary_path.relative_to(ROOT)): sha256_file(snapshot_summary_path),
            str(comparison_path.relative_to(ROOT)): sha256_file(comparison_path),
            str(R2_WEIGHTS.relative_to(ROOT)): sha256_file(R2_WEIGHTS),
        },
        "active_pointer": {
            "path": "models/ACTIVE",
            "sha256": sha256_file(ROOT / "models/ACTIVE") if (ROOT / "models/ACTIVE").is_file() else None,
            "value": (ROOT / "models/ACTIVE").read_text(encoding="utf-8").strip()
            if (ROOT / "models/ACTIVE").is_file()
            else None,
        },
        "acceptance_decision_sha256": sha256_file(ROOT / "reports/ACCEPTANCE_DECISION.json"),
        "canary": {
            "confidence": comparison["contract"]["confidence"],
            "nms_iou": comparison["contract"]["nms_iou"],
            "event_gap_bars": comparison["contract"]["event_gap_bars"],
            "window_lengths": comparison["contract"]["window_lengths"],
            "latest_bar": comparison["contract"]["latest_bar"],
        },
        "holdout": {
            "start": HOLDOUT_START.isoformat(),
            "use_number": 0,
            "rows_materialized": 0,
            "max_materialized_time": snapshot_summary["max_materialized_time"],
        },
        "prohibitions_observed": {
            "new_model_training": False,
            "r3_or_r4_created": False,
            "weights_modified": False,
            "confidence_modified": False,
            "nms_modified": False,
            "active_modified": False,
            "deployed": False,
            "orders": False,
            "forward_log_cleared": False,
            "new_holdout_read": False,
        },
    }
    for value in freeze["weights"].values():
        path = ROOT / value["path"]
        if not path.is_file() or sha256_file(path) != value["sha256"]:
            raise ValueError(f"frozen weight mismatch: {path}")
    (output / "freeze_receipt.json").write_text(
        json.dumps(freeze, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    summary = {
        "protocol": PROTOCOL,
        "rows": len(blinded),
        "positive_pool": sum(row["source_type"] == "positive_pool" for row in blinded),
        "canary_candidate": sum(row["source_type"] == "canary_candidate" for row in blinded),
        "unique_review_ids": len({row["review_id"] for row in blinded}),
        "unique_event_ids": len({row["event_id"] for row in blinded}),
        "unique_image_sha256": len({row["image_sha256"] for row in blinded}),
        "owner_verdicts_preselected": sum(row["owner_verdict"] is not None for row in blinded),
        "causality_pass": causality_audit["all_pass"],
        "training_eligible": 0,
        "production_eligible": False,
        "holdout_read": False,
        "manifest": str(manifest.relative_to(ROOT)),
        "manifest_sha256": sha256_file(manifest),
        "image_tree_sha256": json_sha256(sorted(row["image_sha256"] for row in blinded)),
        "sampling_audit_sha256": sha256_file(output / "sampling_audit.json"),
        "causality_audit_sha256": sha256_file(output / "causality_audit.json"),
        "freeze_receipt_sha256": sha256_file(output / "freeze_receipt.json"),
        "review_html": str(html_path.relative_to(ROOT)),
        "review_html_sha256": sha256_file(html_path),
        "readme_sha256": sha256_file(readme),
        "quality_gates": {
            "exactly_200": len(blinded) == 200,
            "positive_100": sum(row["source_type"] == "positive_pool" for row in blinded) == 100,
            "canary_100": sum(row["source_type"] == "canary_candidate" for row in blinded) == 100,
            "independent_images_200": len({row["image_path"] for row in blinded}) == 200,
            "unique_image_hashes_200": len({row["image_sha256"] for row in blinded}) == 200,
            "no_owner_default": all(row["owner_verdict"] is None for row in blinded),
            "causality_all_green": causality_audit["all_pass"],
            "holdout_clean": not any(row["holdout_read"] for row in blinded),
        },
    }
    if not all(summary["quality_gates"].values()):
        raise RuntimeError(summary["quality_gates"])
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--positive-manifest", type=Path, default=DEFAULT_POSITIVE_MANIFEST)
    parser.add_argument("--r1-events", type=Path, default=DEFAULT_R1_EVENTS)
    parser.add_argument("--r2-events", type=Path, default=DEFAULT_R2_EVENTS)
    parser.add_argument("--snapshot-dir", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--snapshot-summary", type=Path, default=DEFAULT_SNAPSHOT_SUMMARY)
    parser.add_argument("--comparison", type=Path, default=DEFAULT_COMPARISON)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--device", default="mps")
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--frozen-main-commit", required=True)
    args = parser.parse_args()
    summary = build(
        args.positive_manifest,
        args.r1_events,
        args.r2_events,
        args.snapshot_dir,
        args.snapshot_summary,
        args.comparison,
        args.out,
        device=args.device,
        batch=args.batch,
        frozen_main_commit=args.frozen_main_commit,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
