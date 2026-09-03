#!/usr/bin/env python3
"""Diagnose model-first then current MA-bundle position on frozen FIL 1h output.

This Owner-authorized diagnostic performs no network read and no model
inference.  It reuses the exhaustive raw detections from checkpoint holdout
use #17 and changes exactly one downstream condition from v1: a proposal only
needs to close beyond the full six-MA bundle at endpoint ``t``.  No condition
is imposed on ``t-1`` and no first-cross event is required.

For a LONG proposal, ``close[t]`` must be strictly above all trailing
SMA/EMA 20/60/120 values at ``t``.  SHORT is mirrored below the bundle.  Rows
after ``t`` appear only in the review chart, while a future-mutation check
proves that changing them cannot alter any decision.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import cv2
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from yoyo.layers.l1_detection.data import add_mas  # noqa: E402
from yoyo.layers.l1_detection.model_first_standing import (  # noqa: E402
    evaluate_model_first_standing,
    standing_decisions_equal,
)
from yoyo.layers.l1_detection.render import render_chart  # noqa: E402


EXPERIMENT_ID = "exp-1h-filusdt-model-first-standing-gate-20260904-v2"
DEFAULT_PREREG = ROOT / "experiments" / "active" / EXPERIMENT_ID / "preregistration.json"
DEFAULT_OUT = ROOT / "experiments" / "active" / EXPERIMENT_ID / "results"
BAR_DELTA = pd.Timedelta(hours=1)


class ModelFirstStandingDiagnosticError(RuntimeError):
    """Fail closed on contract, lineage, causality, or output drift."""


def sha256_file(path: Path) -> str:
    """Return a streaming SHA-256 identity."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc(value: object) -> pd.Timestamp:
    """Normalize one timestamp to UTC."""

    stamp = pd.Timestamp(value)
    return stamp.tz_convert("UTC") if stamp.tzinfo else stamp.tz_localize("UTC")


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Write stable UTF-8 JSON."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def verify_contract(path: Path) -> dict[str, Any]:
    """Verify frozen sources, the one-bar rule, and safety switches."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("experiment_id") != EXPERIMENT_ID:
        raise ModelFirstStandingDiagnosticError("experiment identity drifted")
    auth = payload.get("owner_authorization") or {}
    if auth.get("holdout_reuse_authorized") is not True:
        raise ModelFirstStandingDiagnosticError("holdout reuse is not authorized")
    if int(auth.get("holdout_consumption_number_for_checkpoint", -1)) != 19:
        raise ModelFirstStandingDiagnosticError("holdout consumption number drifted")
    if any(bool(value) for value in (payload.get("safety") or {}).values()):
        raise ModelFirstStandingDiagnosticError("a safety mutation is enabled")
    expected_rule = {
        "pipeline_order": "model_proposal_then_deterministic_bundle_position_gate",
        "long_current": "close[t] > max(sma20,sma60,sma120,ema20,ema60,ema120)[t]",
        "short_current": "close[t] < min(sma20,sma60,sma120,ema20,ema60,ema120)[t]",
        "prior_bar_condition": None,
        "first_cross_required": False,
        "epsilon": 0.0,
        "lookback_rows": 1,
    }
    if payload.get("position_gate") != expected_rule:
        raise ModelFirstStandingDiagnosticError("position rule drifted")
    for item in (payload.get("frozen_inputs") or {}).values():
        source = ROOT / str(item["path"])
        if not source.is_file() or sha256_file(source) != str(item["sha256"]):
            raise ModelFirstStandingDiagnosticError(f"frozen input drifted: {source}")
    return payload


def verify_committed_sources(prereg: Path, payload: Mapping[str, Any]) -> str:
    """Require main and committed code/config before reading frozen holdout bytes."""

    branch = subprocess.check_output(
        ["git", "branch", "--show-current"], cwd=ROOT, text=True
    ).strip()
    if branch != "main":
        raise ModelFirstStandingDiagnosticError("diagnostic must run on main")
    paths = [Path(__file__).resolve(), prereg]
    paths.extend(ROOT / str(item["path"]) for item in payload["implementation"].values())
    relative = sorted({str(item.relative_to(ROOT)) for item in paths})
    dirty = subprocess.check_output(
        ["git", "status", "--short", "--", *relative], cwd=ROOT, text=True
    ).strip()
    if dirty:
        raise ModelFirstStandingDiagnosticError(
            f"diagnostic sources must be committed:\n{dirty}"
        )
    for item in payload["implementation"].values():
        source = ROOT / str(item["path"])
        if sha256_file(source) != str(item["sha256"]):
            raise ModelFirstStandingDiagnosticError(f"implementation drifted: {source}")
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


def proposal_direction(class_name: object) -> str:
    """Map a frozen detector class without inferring another direction."""

    name = str(class_name)
    if name == "dense_long":
        return "LONG"
    if name == "dense_short":
        return "SHORT"
    raise ModelFirstStandingDiagnosticError(f"unsupported detector class: {name}")


def evaluate_proposals(
    enriched: pd.DataFrame, candidates: pd.DataFrame
) -> list[dict[str, Any]]:
    """Apply the one-bar position code only at model-proposed endpoints."""

    rows: list[dict[str, Any]] = []
    for _, candidate in candidates.iterrows():
        end = int(candidate["window_end_i"])
        direction = proposal_direction(candidate["class_name"])
        actual = evaluate_model_first_standing(
            enriched, proposal_end_i=end, direction=direction
        )
        flipped = evaluate_model_first_standing(
            enriched,
            proposal_end_i=end,
            direction="SHORT" if direction == "LONG" else "LONG",
        )
        rows.append(
            {
                "candidate_id": str(candidate["candidate_id"]),
                "class_name": str(candidate["class_name"]),
                "confidence": float(candidate["confidence"]),
                "window_len": int(candidate["window_len"]),
                "window_end_i": end,
                "window_end_time": utc(candidate["window_end_time"]).isoformat(),
                "available_at": (
                    utc(candidate["window_end_time"]) + BAR_DELTA
                ).isoformat(),
                "core_start_i": int(candidate["core_start_i"]),
                "core_end_i": int(candidate["core_end_i"]),
                **{f"gate_{key}": value for key, value in actual.to_dict().items()},
                "flipped_gate_pass": bool(flipped.passed),
            }
        )
    return rows


def count_overlap_episodes(rows: Iterable[Mapping[str, Any]]) -> int:
    """Deduplicate passing proposals by direction and overlapping core interval."""

    materialized = list(rows)
    episodes = 0
    for direction in ("LONG", "SHORT"):
        group = sorted(
            (row for row in materialized if str(row["gate_direction"]) == direction),
            key=lambda row: (int(row["core_start_i"]), int(row["core_end_i"])),
        )
        active_end: int | None = None
        for row in group:
            start = int(row["core_start_i"])
            end = int(row["core_end_i"])
            if active_end is None or start > active_end:
                episodes += 1
                active_end = end
            else:
                active_end = max(active_end, end)
    return episodes


def verify_future_mutation(
    raw: pd.DataFrame,
    enriched: pd.DataFrame,
    rows: Iterable[Mapping[str, Any]],
) -> int:
    """Mutate every OHLCV row after each endpoint and require decision parity."""

    passed = 0
    for row in rows:
        end = int(row["window_end_i"])
        direction = proposal_direction(row["class_name"])
        mutated = raw.copy()
        future = mutated.index > end
        multipliers = np.linspace(5.0, 50.0, int(future.sum()))
        for column in ("open", "high", "low", "close", "volume"):
            mutated.loc[future, column] = (
                mutated.loc[future, column].to_numpy(dtype=float) * multipliers
            )
        replay_frame = add_mas(mutated)
        original = evaluate_model_first_standing(
            enriched, proposal_end_i=end, direction=direction
        )
        replay = evaluate_model_first_standing(
            replay_frame, proposal_end_i=end, direction=direction
        )
        if not standing_decisions_equal(original, replay):
            raise ModelFirstStandingDiagnosticError(
                f"future mutation changed {row['candidate_id']}"
            )
        passed += 1
    return passed


def draw_marker(image: np.ndarray, x: int, color: tuple[int, int, int], width: int) -> None:
    """Draw a full-height causal endpoint marker."""

    cv2.line(image, (x, 92), (x, image.shape[0] - 36), color, width, cv2.LINE_AA)


def render_review(
    enriched: pd.DataFrame, decisions: list[Mapping[str, Any]]
) -> np.ndarray:
    """Render global context with the first model and full-gate availability."""

    start = max(0, len(enriched) - 240)
    context = enriched.iloc[start:].copy()
    chart, transform = render_chart(context, width=1920, height=1040, out_path=None)
    canvas = np.full((1160, 1920, 3), 247, dtype=np.uint8)
    canvas[92:1132] = chart
    cv2.putText(
        canvas,
        "FILUSDT.P 1h | YOLO proposal FIRST -> six-MA position code SECOND",
        (22, 35),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.72,
        (25, 25, 25),
        2,
        cv2.LINE_AA,
    )
    cv2.putText(
        canvas,
        "green=first raw YOLO proposal | magenta overlay=first full-gate pass | no prior-bar condition",
        (22, 70),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.52,
        (55, 55, 55),
        1,
        cv2.LINE_AA,
    )
    first_model = min(decisions, key=lambda row: int(row["window_end_i"]))
    model_i = int(first_model["window_end_i"])
    x = transform.x_at(model_i - start)
    draw_marker(canvas, x, (35, 150, 30), 8)
    passing = [row for row in decisions if bool(row["gate_passed"])]
    if passing:
        first_pass = min(passing, key=lambda row: int(row["window_end_i"]))
        pass_i = int(first_pass["window_end_i"])
        draw_marker(canvas, transform.x_at(pass_i - start), (175, 40, 175), 3)
    when = utc(first_model["available_at"]).tz_convert("Asia/Shanghai")
    label = "FIRST YOLO + CODE PASS" if bool(first_model["gate_passed"]) else "FIRST YOLO"
    cv2.putText(
        canvas,
        f"{label} {when:%m-%d %H:%M}",
        (max(10, x - 130), 140),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.46,
        (125, 30, 125) if bool(first_model["gate_passed"]) else (35, 150, 30),
        2,
        cv2.LINE_AA,
    )
    return canvas


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prereg", type=Path, default=DEFAULT_PREREG)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    prereg = args.prereg.resolve()
    out = args.out.resolve()
    building = out.with_name(out.name + ".building")
    if out.exists() or building.exists():
        raise FileExistsError(f"refusing to overwrite output: {out}")
    payload = verify_contract(prereg)
    source_commit = verify_committed_sources(prereg, payload)

    frozen = payload["frozen_inputs"]
    source_summary = json.loads(
        (ROOT / frozen["source_summary"]["path"]).read_text(encoding="utf-8")
    )
    v1_summary = json.loads(
        (ROOT / frozen["v1_summary"]["path"]).read_text(encoding="utf-8")
    )
    raw = pd.read_csv(ROOT / frozen["source_candles"]["path"])
    raw["open_time"] = pd.to_datetime(raw["open_time"], utc=True)
    candidates = pd.read_csv(ROOT / frozen["source_candidates"]["path"])
    stats = source_summary["detector"]["stats"]
    raw_boxes = int(stats["raw_boxes"])
    accepted = int(stats["accepted_structural_boxes"])
    if not raw_boxes == accepted == len(candidates):
        raise ModelFirstStandingDiagnosticError(
            "source candidate CSV is not an exhaustive raw-box ledger"
        )
    if int(stats["windows_scored"]) != 240:
        raise ModelFirstStandingDiagnosticError("source model-window count drifted")
    if set(candidates["class_name"].astype(str)) - {"dense_long", "dense_short"}:
        raise ModelFirstStandingDiagnosticError("unexpected source class")

    enriched = add_mas(raw)
    decisions = evaluate_proposals(enriched, candidates)
    future_passes = verify_future_mutation(raw, enriched, decisions)
    gate_passes = [row for row in decisions if bool(row["gate_passed"])]
    flipped_passes = [row for row in decisions if bool(row["flipped_gate_pass"])]
    episodes = count_overlap_episodes(gate_passes)

    building.mkdir(parents=True)
    try:
        shutil.copy2(prereg, building / "preregistration.json")
        pd.DataFrame(decisions).to_csv(
            building / "model_first_standing_decisions.csv", index=False
        )
        review_dir = building / "review"
        review_dir.mkdir()
        chart_path = review_dir / "FILUSDT_P_1h_model_first_standing_global.png"
        image = render_review(enriched, decisions)
        if not cv2.imwrite(str(chart_path), image, [cv2.IMWRITE_PNG_COMPRESSION, 4]):
            raise OSError("failed to write review chart")

        first_model = min(decisions, key=lambda row: int(row["window_end_i"]))
        first_pass = (
            min(gate_passes, key=lambda row: int(row["window_end_i"]))
            if gate_passes
            else None
        )
        summary = {
            "schema_version": 1,
            "experiment_id": EXPERIMENT_ID,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source_commit": source_commit,
            "holdout_consumption_number_for_checkpoint": 19,
            "execution": {"network_reads": 0, "model_inference_calls": 0},
            "source_replay": {
                "model_windows": int(stats["windows_scored"]),
                "raw_boxes": raw_boxes,
                "accepted_structural_boxes": accepted,
                "candidate_rows": len(candidates),
                "raw_box_ledger_is_exhaustive": True,
            },
            "pipeline": {
                "order": "model_proposal_then_deterministic_bundle_position_gate",
                "prior_bar_condition": None,
                "model_proposals": len(decisions),
                "actual_gate_passes": len(gate_passes),
                "flipped_direction_gate_passes": len(flipped_passes),
                "deduplicated_actionable_events": episodes,
                "conclusion": (
                    "model_proposals_stand_beyond_bundle"
                    if gate_passes
                    else "no_model_proposal_stands_beyond_bundle"
                ),
            },
            "timing": {
                "first_raw_model_endpoint": str(first_model["window_end_time"]),
                "first_raw_model_available_at": str(first_model["available_at"]),
                "first_raw_model_confidence": float(first_model["confidence"]),
                "first_gate_pass_endpoint": (
                    str(first_pass["window_end_time"]) if first_pass else None
                ),
                "first_gate_pass_available_at": (
                    str(first_pass["available_at"]) if first_pass else None
                ),
            },
            "comparison_to_v1": {
                "only_changed_condition": "removed_prior_bar_first_cross_requirement",
                "v1_actual_gate_passes": int(
                    v1_summary["pipeline"]["actual_gate_passes"]
                ),
                "v2_actual_gate_passes": len(gate_passes),
                "first_pipeline_availability_changed": False,
            },
            "causality": {
                "rows_read_per_gate": ["t"],
                "future_mutation_checks": len(decisions),
                "future_mutation_passes": future_passes,
                "future_rows_in_gate": 0,
                "review_chart_physically_separate": True,
            },
            "interpretation": {
                "known_profitable_case": True,
                "performance_claim_allowed": False,
                "one_case_can_diagnose_latency_but_not_precision_or_returns": True,
            },
            "safety": payload["safety"],
        }
        write_json(building / "summary.json", summary)
        gallery = f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>
<title>FILUSDT.P 1h model-first standing diagnostic</title>
<style>body{{font-family:system-ui;margin:24px;background:#f5f6f8;color:#181818}}img{{max-width:100%;border:1px solid #aaa;background:white}}code{{background:#eee;padding:2px 5px}}</style></head>
<body><h1>FILUSDT.P 1h：模型先检测，代码只检查当前站位</h1>
<p>模型原始提案 {len(decisions)}；代码通过 {len(gate_passes)}；重合核心去重后 {episodes} 个事件。已去掉前一根与“首次穿越”条件。</p>
<img src='{html.escape(chart_path.name)}'></body></html>"""
        (review_dir / "gallery.html").write_text(gallery, encoding="utf-8")
        building.replace(out)
    except Exception:
        shutil.rmtree(building, ignore_errors=True)
        raise

    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
