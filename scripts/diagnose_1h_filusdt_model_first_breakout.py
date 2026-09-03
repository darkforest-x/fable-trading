#!/usr/bin/env python3
"""Diagnose YOLO-first then first-close breakout code on frozen FIL 1h output.

This Owner-authorized diagnostic performs no network read and no model
inference.  It reuses the exhaustive raw detections from checkpoint holdout
use #17, asserts that every raw box was structurally retained, and then applies
the deterministic two-bar breakout gate only at those model-proposed endpoints.

For a LONG proposal at endpoint ``t``, the code reads only closes and trailing
SMA/EMA 20/60/120 at ``t-1`` and ``t``.  It passes only when ``close[t]`` is
strictly above the entire bundle and ``close[t-1]`` was not.  SHORT is mirrored.
Future bars appear only in the review chart and a future-mutation check proves
that changing them cannot change any gate decision.
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
from yoyo.layers.l1_detection.model_first_breakout import (  # noqa: E402
    decisions_equal,
    evaluate_model_first_breakout,
)
from yoyo.layers.l1_detection.render import render_chart  # noqa: E402


EXPERIMENT_ID = "exp-1h-filusdt-model-first-breakout-gate-20260904-v1"
DEFAULT_PREREG = ROOT / "experiments" / "active" / EXPERIMENT_ID / "preregistration.json"
DEFAULT_OUT = ROOT / "experiments" / "active" / EXPERIMENT_ID / "results"
BAR_DELTA = pd.Timedelta(hours=1)


class ModelFirstDiagnosticError(RuntimeError):
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
    """Verify the preregistered source artifacts, rule, and safety switches."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("experiment_id") != EXPERIMENT_ID:
        raise ModelFirstDiagnosticError("experiment identity drifted")
    auth = payload.get("owner_authorization") or {}
    if auth.get("holdout_reuse_authorized") is not True:
        raise ModelFirstDiagnosticError("holdout reuse is not authorized")
    if int(auth.get("holdout_consumption_number_for_checkpoint", -1)) != 18:
        raise ModelFirstDiagnosticError("holdout consumption number drifted")
    if any(bool(value) for value in (payload.get("safety") or {}).values()):
        raise ModelFirstDiagnosticError("a safety mutation is enabled")
    expected_rule = {
        "pipeline_order": "model_proposal_then_deterministic_breakout_gate",
        "long_current": "close[t] > max(sma20,sma60,sma120,ema20,ema60,ema120)[t]",
        "long_previous": "close[t-1] <= max(sma20,sma60,sma120,ema20,ema60,ema120)[t-1]",
        "short_current": "close[t] < min(sma20,sma60,sma120,ema20,ema60,ema120)[t]",
        "short_previous": "close[t-1] >= min(sma20,sma60,sma120,ema20,ema60,ema120)[t-1]",
        "epsilon": 0.0,
        "lookback_rows": 2,
    }
    if payload.get("breakout_gate") != expected_rule:
        raise ModelFirstDiagnosticError("breakout rule drifted")
    for item in (payload.get("frozen_inputs") or {}).values():
        source = ROOT / str(item["path"])
        if not source.is_file() or sha256_file(source) != str(item["sha256"]):
            raise ModelFirstDiagnosticError(f"frozen input drifted: {source}")
    return payload


def verify_committed_sources(prereg: Path, payload: Mapping[str, Any]) -> str:
    """Require main and committed code/config before reading frozen holdout bytes."""

    branch = subprocess.check_output(
        ["git", "branch", "--show-current"], cwd=ROOT, text=True
    ).strip()
    if branch != "main":
        raise ModelFirstDiagnosticError("diagnostic must run on main")
    paths = [Path(__file__).resolve(), prereg]
    paths.extend(ROOT / str(item["path"]) for item in payload["implementation"].values())
    relative = [str(path.relative_to(ROOT)) for path in paths]
    dirty = subprocess.check_output(
        ["git", "status", "--short", "--", *relative], cwd=ROOT, text=True
    ).strip()
    if dirty:
        raise ModelFirstDiagnosticError(f"diagnostic sources must be committed:\n{dirty}")
    for item in payload["implementation"].values():
        source = ROOT / str(item["path"])
        if sha256_file(source) != str(item["sha256"]):
            raise ModelFirstDiagnosticError(f"implementation drifted: {source}")
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


def proposal_direction(class_name: object) -> str:
    """Map the frozen detector class name without guessing another direction."""

    name = str(class_name)
    if name == "dense_long":
        return "LONG"
    if name == "dense_short":
        return "SHORT"
    raise ModelFirstDiagnosticError(f"unsupported detector class: {name}")


def evaluate_proposals(
    enriched: pd.DataFrame, candidates: pd.DataFrame
) -> list[dict[str, Any]]:
    """Apply code only at model-proposed endpoints and retain flipped controls."""

    rows: list[dict[str, Any]] = []
    for _, candidate in candidates.iterrows():
        end = int(candidate["window_end_i"])
        direction = proposal_direction(candidate["class_name"])
        actual = evaluate_model_first_breakout(
            enriched, proposal_end_i=end, direction=direction
        )
        flipped = evaluate_model_first_breakout(
            enriched,
            proposal_end_i=end,
            direction="SHORT" if direction == "LONG" else "LONG",
        )
        row = {
            "candidate_id": str(candidate["candidate_id"]),
            "class_name": str(candidate["class_name"]),
            "confidence": float(candidate["confidence"]),
            "window_len": int(candidate["window_len"]),
            "window_end_i": end,
            "window_end_time": utc(candidate["window_end_time"]).isoformat(),
            "available_at": (utc(candidate["window_end_time"]) + BAR_DELTA).isoformat(),
            "core_start_i": int(candidate["core_start_i"]),
            "core_end_i": int(candidate["core_end_i"]),
            **{f"gate_{key}": value for key, value in actual.to_dict().items()},
            "flipped_gate_pass": bool(flipped.passed),
        }
        rows.append(row)
    return rows


def code_only_references(
    enriched: pd.DataFrame, *, start_i: int, end_i: int
) -> list[dict[str, Any]]:
    """List LONG crossings for diagnosis only; these are not pipeline proposals."""

    rows: list[dict[str, Any]] = []
    for index in range(max(1, int(start_i)), min(len(enriched) - 1, int(end_i)) + 1):
        decision = evaluate_model_first_breakout(
            enriched, proposal_end_i=index, direction="LONG"
        )
        if decision.passed:
            opened = utc(enriched.iloc[index]["open_time"])
            rows.append(
                {
                    **decision.to_dict(),
                    "bar_open_time": opened.isoformat(),
                    "available_at": (opened + BAR_DELTA).isoformat(),
                    "pipeline_eligible": False,
                    "reason": "diagnostic_reference_without_model_proposal",
                }
            )
    return rows


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
        mutated_enriched = add_mas(mutated)
        original = evaluate_model_first_breakout(
            enriched, proposal_end_i=end, direction=direction
        )
        replay = evaluate_model_first_breakout(
            mutated_enriched, proposal_end_i=end, direction=direction
        )
        if not decisions_equal(original, replay):
            raise ModelFirstDiagnosticError(f"future mutation changed {row['candidate_id']}")
        passed += 1
    return passed


def draw_marker(image: np.ndarray, x: int, color: tuple[int, int, int], width: int) -> None:
    """Draw a full-height causal endpoint marker."""

    cv2.line(image, (x, 92), (x, image.shape[0] - 36), color, width, cv2.LINE_AA)


def render_review(
    enriched: pd.DataFrame,
    decisions: list[Mapping[str, Any]],
    references: list[Mapping[str, Any]],
) -> np.ndarray:
    """Render global context with model-first and code-only-reference timing."""

    start = max(0, len(enriched) - 240)
    context = enriched.iloc[start:].copy()
    chart, transform = render_chart(context, width=1920, height=1040, out_path=None)
    canvas = np.full((1160, 1920, 3), 247, dtype=np.uint8)
    canvas[92:1132] = chart
    cv2.putText(
        canvas,
        "FILUSDT.P 1h | YOLO proposal FIRST -> first-close-above-six-MA code SECOND",
        (22, 35),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.72,
        (25, 25, 25),
        2,
        cv2.LINE_AA,
    )
    cv2.putText(
        canvas,
        "blue=code-only reference (not a signal) | green=first raw YOLO proposal | magenta=full gate pass",
        (22, 70),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.52,
        (55, 55, 55),
        1,
        cv2.LINE_AA,
    )
    if references:
        ref_i = int(references[0]["proposal_end_i"])
        draw_marker(canvas, transform.x_at(ref_i - start), (205, 115, 35), 3)
        when = utc(references[0]["available_at"]).tz_convert("Asia/Shanghai")
        cv2.putText(
            canvas,
            f"CODE-ONLY {when:%m-%d %H:%M}",
            (max(10, transform.x_at(ref_i - start) - 90), 118),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (205, 115, 35),
            1,
            cv2.LINE_AA,
        )
    if decisions:
        first = min(decisions, key=lambda row: int(row["window_end_i"]))
        model_i = int(first["window_end_i"])
        draw_marker(canvas, transform.x_at(model_i - start), (35, 150, 30), 4)
        when = utc(first["available_at"]).tz_convert("Asia/Shanghai")
        cv2.putText(
            canvas,
            f"FIRST YOLO {when:%m-%d %H:%M}",
            (max(10, transform.x_at(model_i - start) - 90), 148),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (35, 150, 30),
            2,
            cv2.LINE_AA,
        )
    for row in decisions:
        if bool(row["gate_passed"]):
            draw_marker(
                canvas,
                transform.x_at(int(row["window_end_i"]) - start),
                (175, 40, 175),
                6,
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
    raw = pd.read_csv(ROOT / frozen["source_candles"]["path"])
    raw["open_time"] = pd.to_datetime(raw["open_time"], utc=True)
    candidates = pd.read_csv(ROOT / frozen["source_candidates"]["path"])
    stats = source_summary["detector"]["stats"]
    raw_boxes = int(stats["raw_boxes"])
    accepted = int(stats["accepted_structural_boxes"])
    if not raw_boxes == accepted == len(candidates):
        raise ModelFirstDiagnosticError(
            "source candidate CSV is not an exhaustive raw-box ledger"
        )
    if int(stats["windows_scored"]) != 240:
        raise ModelFirstDiagnosticError("source model-window count drifted")
    if set(candidates["class_name"].astype(str)) - {"dense_long", "dense_short"}:
        raise ModelFirstDiagnosticError("unexpected source class")

    enriched = add_mas(raw)
    decisions = evaluate_proposals(enriched, candidates)
    earliest_model_end = min(int(row["window_end_i"]) for row in decisions)
    earliest_core_start = min(int(row["core_start_i"]) for row in decisions)
    references = code_only_references(
        enriched, start_i=earliest_core_start, end_i=earliest_model_end
    )
    future_passes = verify_future_mutation(raw, enriched, decisions)
    gate_passes = [row for row in decisions if bool(row["gate_passed"])]
    flipped_passes = [row for row in decisions if bool(row["flipped_gate_pass"])]

    building.mkdir(parents=True)
    try:
        shutil.copy2(prereg, building / "preregistration.json")
        pd.DataFrame(decisions).to_csv(building / "model_first_decisions.csv", index=False)
        pd.DataFrame(references).to_csv(building / "code_only_references.csv", index=False)
        review_dir = building / "review"
        review_dir.mkdir()
        chart_path = review_dir / "FILUSDT_P_1h_model_first_breakout_global.png"
        image = render_review(enriched, decisions, references)
        if not cv2.imwrite(str(chart_path), image, [cv2.IMWRITE_PNG_COMPRESSION, 4]):
            raise OSError("failed to write review chart")

        first_model = min(decisions, key=lambda row: int(row["window_end_i"]))
        first_reference = references[0] if references else None
        conclusion = (
            "model_first_gate_passed"
            if gate_passes
            else "no_model_proposal_coincided_with_first_close_beyond_bundle"
        )
        summary = {
            "schema_version": 1,
            "experiment_id": EXPERIMENT_ID,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source_commit": source_commit,
            "holdout_consumption_number_for_checkpoint": 18,
            "execution": {"network_reads": 0, "model_inference_calls": 0},
            "source_replay": {
                "model_windows": int(stats["windows_scored"]),
                "raw_boxes": raw_boxes,
                "accepted_structural_boxes": accepted,
                "candidate_rows": len(candidates),
                "raw_box_ledger_is_exhaustive": True,
            },
            "pipeline": {
                "order": "model_proposal_then_deterministic_breakout_gate",
                "model_proposals": len(decisions),
                "actual_gate_passes": len(gate_passes),
                "flipped_direction_gate_passes": len(flipped_passes),
                "deduplicated_actionable_events": len(gate_passes),
                "conclusion": conclusion,
            },
            "timing": {
                "first_raw_model_endpoint": str(first_model["window_end_time"]),
                "first_raw_model_available_at": str(first_model["available_at"]),
                "first_raw_model_confidence": float(first_model["confidence"]),
                "first_code_only_reference_endpoint": (
                    str(first_reference["bar_open_time"]) if first_reference else None
                ),
                "first_code_only_reference_available_at": (
                    str(first_reference["available_at"]) if first_reference else None
                ),
                "reference_is_not_pipeline_signal": True,
            },
            "causality": {
                "rows_read_per_gate": ["t-1", "t"],
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
<title>FILUSDT.P 1h model-first breakout diagnostic</title>
<style>body{{font-family:system-ui;margin:24px;background:#f5f6f8;color:#181818}}img{{max-width:100%;border:1px solid #aaa;background:white}}code{{background:#eee;padding:2px 5px}}</style></head>
<body><h1>FILUSDT.P 1h：模型先检测，代码再确认站上线</h1>
<p>模型原始提案 {len(decisions)}；代码门通过 {len(gate_passes)}。蓝线只是代码单独成立的诊断参考，不是流水线信号。</p>
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
