"""Scan a committed historical as-of window with the frozen Grade-A morphology.

This is a retrospective endpoint scan.  A candidate ending its five-bar
confirmation at a closed timestamp in the requested interval may read no bar
after that confirmation.  It reuses the original close-MA Perfect Filter gates
and reference calibration; it creates neither labels nor training data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

from yoyo.datasets.fifteen_minute_launch_candidates import add_candidate_features, read_preholdout_prefix, sha256_file
from yoyo.datasets.ma_launch_owner_autofill10000 import frame_arrays, load_reference_profiles
from yoyo.datasets.ma_launch_owner_autofill_review import FEATURE_NAMES, morphology_profile, passes_gate, profile_distance
from yoyo.datasets.ma_launch_owner_perfect_filter import (
    DEFAULT_PREREG, HOLDOUT_START, PerfectFilterError, _load_pinned_rows, _profile_key,
    _repo_path, _score_all, extract_profile, read_json, write_json, write_jsonl,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_AUTOFILL_PREREG = ROOT / "experiments/active/exp-15m-ma-launch-owner-autofill10000-v1/preregistration.json"


class SnapshotScanError(RuntimeError):
    """Raised when a frozen as-of scan cannot be reproduced safely."""


def utc(value: object) -> pd.Timestamp:
    result = pd.Timestamp(value)
    if result.tzinfo is None:
        raise SnapshotScanError(f"timestamp needs timezone: {value!r}")
    return result.tz_convert("UTC")


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def committed(paths: Iterable[Path]) -> str:
    if subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip() != "main":
        raise SnapshotScanError("scanner must run on main")
    names = [rel(path) for path in paths]
    dirty = subprocess.check_output(["git", "status", "--porcelain", "--", *names], cwd=ROOT, text=True).strip()
    if dirty:
        raise SnapshotScanError("commit scanner and frozen plan before scanning:\n" + dirty)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def closed_confirmation_indices(
    open_times: pd.Series, *, bar_minutes: int, start_utc: pd.Timestamp, end_utc: pd.Timestamp
) -> np.ndarray:
    """Return ``confirm_i`` whose candle close lies in the inclusive as-of interval."""

    closes = pd.to_datetime(open_times, utc=True) + pd.Timedelta(minutes=bar_minutes)
    return np.flatnonzero(((closes >= start_utc) & (closes <= end_utc)).to_numpy())


def source_frame(path: Path, *, bar_minutes: int, close_cutoff: pd.Timestamp) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Read one already-clipped canonical source and preserve its local chronology evidence."""

    end_exclusive = close_cutoff - pd.Timedelta(minutes=bar_minutes) + pd.Timedelta(milliseconds=1)
    frame, audit = read_preholdout_prefix(path, end_exclusive=end_exclusive, bar_minutes=bar_minutes)
    if frame.empty:
        raise SnapshotScanError(f"empty source: {path}")
    return add_candidate_features(frame), audit


def candidate_row(source: Mapping[str, Any], *, confirm_i: int, core_bars: int, direction: str, bar_minutes: int, distance: float, features: Mapping[str, float], frame: pd.DataFrame) -> dict[str, Any]:
    """Create exact 4/5-bar geometry ending before the five known confirmation bars."""

    c = int(confirm_i) - 5
    end_i, start_i = c, c - core_bars + 1
    if start_i < 0:
        raise SnapshotScanError("core before source start")
    candidate_id = f"scan::{source['venue']}::{source['symbol']}::{bar_minutes}m::{direction}::{confirm_i}::{core_bars}"
    return {
        "sample_id": candidate_id, "event_id": candidate_id,
        "profile_id": candidate_id,
        "source_path": source["path"], "symbol": source["symbol"], "venue": source["venue"], "bar_minutes": bar_minutes,
        "direction": direction, "core_bars": core_bars, "source_core_start_i": start_i,
        "source_core_end_i": end_i, "source_comparison_anchor_i": c + 2,
        "confirm_i": int(confirm_i), "box": {"h_norm": 0.0}, "similarity_distance": float(distance), "features": dict(features),
        "core_start_time": pd.Timestamp(frame["open_time"].iloc[start_i]).isoformat(),
        "core_end_time": pd.Timestamp(frame["open_time"].iloc[end_i]).isoformat(),
        "confirmation_close_utc": (pd.Timestamp(frame["open_time"].iloc[confirm_i]) + pd.Timedelta(minutes=bar_minutes)).isoformat(),
        "training_eligible": False, "production_eligible": False,
    }


def pick_4_5(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep one exact endpoint geometry, preferring the original autofill similarity distance."""

    groups: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(str(row["source_path"]), str(row["direction"]), int(row["confirm_i"]))].append(row)
    winners = []
    for group in groups.values():
        winners.append(min(group, key=lambda row: (float(row["similarity_distance"]), int(row["core_bars"]), str(row["sample_id"]))))
    return sorted(winners, key=lambda row: (str(row["source_path"]), str(row["direction"]), int(row["confirm_i"])))


def source_event_nms(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Original fixed-cluster 60-minute NMS, grouped by source/timeframe/direction."""

    groups: dict[tuple[str, int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(str(row["source_path"]), int(row["bar_minutes"]), str(row["direction"]))].append(row)
    kept: list[dict[str, Any]] = []
    for group in groups.values():
        clusters: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        for row in sorted(group, key=lambda item: utc(item["core_end_time"])):
            if current and utc(row["core_end_time"]) - utc(current[0]["core_end_time"]) >= pd.Timedelta(minutes=60):
                clusters.append(current); current = []
            current.append(row)
        if current: clusters.append(current)
        kept.extend(min(cluster, key=lambda item: (float(item["similarity_distance"]), utc(item["core_end_time"]), int(item["core_bars"]))) for cluster in clusters)
    return sorted(kept, key=lambda row: (utc(row["core_end_time"]), str(row["sample_id"])))


def crossvenue_4h_nms(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Original quality-first 4-hour cross-venue NMS, independently per timeframe."""

    groups: dict[tuple[str, int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(str(row["symbol"]), int(row["bar_minutes"]), str(row["direction"]))].append(row)
    kept: list[dict[str, Any]] = []
    radius = pd.Timedelta(minutes=240)
    for group in groups.values():
        chosen: list[dict[str, Any]] = []
        for row in sorted(group, key=lambda item: (-float(item["quality_score"]), str(item["sample_id"]))):
            current = utc(row["core_end_time"])
            if all(abs(current - utc(other["core_end_time"])) > radius for other in chosen):
                chosen.append(row)
        kept.extend(chosen)
    return sorted(kept, key=lambda row: (utc(row["confirmation_close_utc"]), str(row["sample_id"])))


def scan_weak_source(
    frame: pd.DataFrame, spec: Mapping[str, Any], *, start: pd.Timestamp, end: pd.Timestamp,
    autofill: Mapping[str, Any], references: list[Any],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Apply the original post-progress, morphology, and similarity pipeline at each endpoint."""

    gates, similarity = autofill["morphology_gate"], autofill["reference_family"]
    arrays, segment = frame_arrays(frame), frame["_segment_id"].to_numpy(dtype=int)
    feature_scales = np.asarray(similarity["feature_scales"], dtype=float)
    bar_minutes = int(spec["bar_minutes"])
    counts: Counter[str] = Counter()
    max_pre = max(int(v) for v in autofill["render"]["pre_core_context_bars"])
    weak: list[dict[str, Any]] = []
    for confirm_i in closed_confirmation_indices(frame["open_time"], bar_minutes=bar_minutes, start_utc=start, end_utc=end):
        c = int(confirm_i) - 5
        for direction, sign in (("LONG", 1.0), ("SHORT", -1.0)):
            for core_bars in (4, 5):
                start_i, anchor_i = c - core_bars + 1, c + 2
                if start_i < max_pre or anchor_i >= len(frame) or segment[start_i - max_pre] != segment[confirm_i]:
                    counts["gap_or_boundary"] += 1; continue
                atr = float(arrays["atr"][anchor_i])
                if not np.isfinite(atr) or atr <= 0:
                    counts["atr_invalid"] += 1; continue
                progress = [sign * (arrays["close"][c + offset] - arrays["close"][c]) / atr for offset in (1, 2, 3, 5)]
                if not all(value >= float(gates[f"min_post{offset}_progress_atr"]) for value, offset in zip(progress, (1, 2, 3, 5))):
                    counts[f"{direction.lower()}_coarse_reject"] += 1; continue
                counts[f"{direction.lower()}_coarse"] += 1
                profile = morphology_profile(arrays, anchor_i=anchor_i, direction=direction, core_start_offset=-core_bars - 1, core_end_offset=-2)
                if profile is None or not passes_gate(profile, gates):
                    counts[f"{direction.lower()}_gate_reject"] += 1; continue
                counts[f"{direction.lower()}_gate"] += 1
                distance = profile_distance(profile, references, feature_scales=feature_scales, feature_weight=float(similarity["feature_weight"]), sequence_weight=float(similarity["sequence_weight"]))
                if distance > float(similarity["max_distance"]):
                    counts[f"{direction.lower()}_distance_reject"] += 1; continue
                counts[f"{direction.lower()}_distance"] += 1
                weak.append(candidate_row(spec, confirm_i=int(confirm_i), core_bars=core_bars, direction=direction, bar_minutes=bar_minutes, distance=distance, features=dict(zip(FEATURE_NAMES, map(float, profile.features))), frame=frame))
    return weak, dict(counts)


def build(plan_path: Path) -> dict[str, Any]:
    """Execute a committed plan and emit raw, scored, rejected, and strict-match evidence."""

    plan_path = plan_path.resolve()
    plan = read_json(plan_path)
    start, end = utc(plan["scan_start_utc"]), utc(plan["scan_end_utc"])
    if start > end:
        raise SnapshotScanError("scan start is after scan end")
    output = ROOT / str(plan["output_dir"])
    if output.exists():
        raise FileExistsError(f"refusing existing scan output: {output}")
    sources = list(plan["sources"])
    if not sources:
        raise SnapshotScanError("plan has no sources")
    commit = committed((Path(__file__), ROOT / "yoyo/datasets/ma_launch_owner_perfect_filter.py", plan_path))
    prereg = read_json(DEFAULT_PREREG)
    autofill_path = ROOT / str(plan.get("autofill_preregistration", rel(DEFAULT_AUTOFILL_PREREG)))
    autofill = read_json(autofill_path)
    if plan.get("autofill_preregistration_sha256") and sha256_file(autofill_path) != str(plan["autofill_preregistration_sha256"]):
        raise SnapshotScanError("autofill preregistration SHA drift")
    scan_references, reference_audits = load_reference_profiles(autofill)
    _, references, family, _ = _load_pinned_rows(prereg)
    profiles = {}
    # References always reconstruct on original pre-May-2026 sources; only the
    # current endpoint scan uses the caller's physically clipped historical files.
    reference_frames: dict[str, pd.DataFrame] = {}
    for row in (*references, *family):
        key = _profile_key(row)
        if key in profiles:
            continue
        source_path = str(row["source_path"])
        if source_path not in reference_frames:
            raw_frame, _ = read_preholdout_prefix(_repo_path(source_path), end_exclusive=HOLDOUT_START, bar_minutes=15)
            reference_frames[source_path] = add_candidate_features(raw_frame)
        profiles[key] = extract_profile(reference_frames[source_path], row, bar_minutes=15)
    raw: list[dict[str, Any]] = []
    coverage = []
    for source_index, spec in enumerate(sources,1):
        if source_index % 250 == 0: print(f"scanned {source_index}/{len(sources)} sources; weak={len(raw)}", flush=True)
        for field in ("path", "symbol", "venue", "bar_minutes", "prefix_sha256"):
            if field not in spec:
                raise SnapshotScanError(f"source missing {field}")
        bar_minutes = int(spec["bar_minutes"])
        path = ROOT / str(spec["path"])
        if sha256_file(path) != str(spec["prefix_sha256"]): raise SnapshotScanError(f"source prefix SHA drift: {path}")
        frame, audit = source_frame(path, bar_minutes=bar_minutes, close_cutoff=end)
        endpoints = closed_confirmation_indices(frame["open_time"], bar_minutes=bar_minutes, start_utc=start, end_utc=end)
        rows, counts = scan_weak_source(frame, spec, start=start, end=end, autofill=autofill, references=scan_references)
        raw.extend(rows)
        coverage.append({"path": str(spec["path"]), "prefix_sha256": str(spec["prefix_sha256"]), "symbol": spec["symbol"], "venue": spec["venue"], "bar_minutes": bar_minutes, "rows": len(frame), "gaps": audit["non_bar_gaps"], "confirmation_endpoints": len(endpoints), "candidate_counts": counts, "weak_candidates": len(rows)})
    collapsed = pick_4_5(raw)
    source_nms = source_event_nms(collapsed)
    ready: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in source_nms: by_source[str(row["source_path"])].append(row)
    for source_path, rows in by_source.items():
        spec = next(source for source in sources if str(source["path"]) == source_path)
        bar_minutes = int(spec["bar_minutes"])
        frame, _ = source_frame(ROOT / source_path, bar_minutes=bar_minutes, close_cutoff=end)
        for row in rows:
            try:
                profiles[_profile_key(row)] = extract_profile(frame, row, bar_minutes=bar_minutes, visibility_end_exclusive=utc(row["confirmation_close_utc"]) + pd.Timedelta(nanoseconds=1))
                ready.append(row)
            except PerfectFilterError as error:
                rejected.append({**row, "profile_reject_reason": str(error)})
    scored, calibration = _score_all(ready, references, family, profiles, prereg)
    raw_grade_a = [row for row in scored if row["quality_tier"] == "PERFECT_CANDIDATE"]
    final_nms = crossvenue_4h_nms(raw_grade_a)
    matches = [row for row in final_nms if row["quality_tier"] == "PERFECT_CANDIDATE"]
    output.mkdir(parents=True)
    write_jsonl(output / "raw_candidates.jsonl", raw)
    write_jsonl(output / "scored_candidates.jsonl", scored)
    write_jsonl(output / "rejections.jsonl", rejected + [row for row in scored if row["quality_tier"] == "REJECT" or not row.get("reference_gate_pass")])
    write_jsonl(output / "endpoint_collapsed.jsonl", collapsed)
    write_jsonl(output / "source_nms_60m.jsonl", source_nms)
    write_jsonl(output / "crossvenue_nms_4h.jsonl", final_nms)
    write_jsonl(output / "grade_a_matches.jsonl", matches)
    write_json(output / "source_coverage.json", coverage)
    write_json(output / "calibration.json", calibration)
    summary = {"builder_commit": commit, "plan_sha256": sha256_file(plan_path), "autofill_preregistration_sha256": sha256_file(autofill_path), "scan_start_utc": start.isoformat(), "scan_end_utc": end.isoformat(), "raw_candidates": len(raw), "profile_ready": len(ready), "profile_rejected": len(rejected), "scored": len(scored), "grade_a_before_final_nms": len(raw_grade_a), "endpoint_collapsed": len(collapsed), "source_nms_60m": len(source_nms), "crossvenue_nms_4h": len(final_nms), "grade_a_matches": len(matches), "directions": dict(Counter(row["direction"] for row in scored)), "nms_sequence": "raw weak 4/5 rows -> same source/direction/confirmation collapse by minimum autofill similarity -> original fixed-cluster 60-minute source/timeframe NMS -> strict batch score -> quality-first 240-minute cross-venue NMS per symbol/timeframe/direction", "moving_average_price_source": "close", "training_eligible": False, "production_eligible": False, "reference_profile_source_audits": reference_audits}
    write_json(output / "summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.plan), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
