#!/usr/bin/env python3
"""Collect the frozen pre-holdout 15m six-MA review-candidate pool.

The causal proposal gate uses only OHLCV through completed bar ``t``.  The
descriptive ranking uses the completed 12-bar path ``t..t+11`` and the review
chart shows six additional bars, so every output remains a PENDING review
candidate with ``training_eligible=false`` and ``production_eligible=false``.

Source files are read line by line only until the first timestamp at or after
2026-05-04.  The boundary timestamp may be inspected, but its OHLCV cells are
never converted or materialized.  The script neither reads existing Owner
label manifests nor writes raw klines, training images, labels, models,
forward state, ACTIVE pointers or order state.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from yoyo.datasets.fifteen_minute_launch_candidates import (
    CandidateCollectionError,
    CandidateSpec,
    add_six_mas,
    audit_future_invariance,
    build_gallery,
    build_overview,
    collect_segment_candidates,
    deduplicate_candidates,
    discover_universe,
    read_preholdout_prefix,
    render_review_chart,
    select_balanced_candidates,
    sha256_file,
    utc,
    write_json,
    write_jsonl,
)
from yoyo.layers.l2_judgment.pine_dense_start import DenseStartProfile


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-15m-ma-launch-candidate1000-v1"
EXPERIMENT_DIR = ROOT / "experiments" / "active" / EXPERIMENT_ID
DEFAULT_PREREG = EXPERIMENT_DIR / "preregistration.json"
DEFAULT_OUTPUT = EXPERIMENT_DIR / "results"
BUILDER_PATHS = (
    ROOT / "yoyo" / "datasets" / "fifteen_minute_launch_candidates.py",
    Path(__file__).resolve(),
    ROOT / "tests" / "test_fifteen_minute_launch_candidates.py",
)


def git_output(*args: str) -> str:
    """Run a read-only git query from the repository root."""

    return subprocess.check_output(
        ["git", *args], cwd=ROOT, text=True, stderr=subprocess.STDOUT
    ).strip()


def verify_builder_committed(paths: Sequence[Path]) -> str:
    """Fail when the run would precede or differ from its committed builder."""

    if git_output("branch", "--show-current") != "main":
        raise RuntimeError("candidate builder must run on main")
    relative = [str(path.resolve().relative_to(ROOT)) for path in paths]
    status = git_output("status", "--short", "--", *relative)
    if status:
        raise RuntimeError(f"builder/prereg paths are not committed:\n{status}")
    commit = git_output("log", "-1", "--format=%H", "--", relative[0])
    if len(commit) != 40:
        raise RuntimeError("could not resolve the committed builder SHA")
    return commit


def repo_path(value: object) -> Path:
    """Resolve a preregistered path without allowing repository escape."""

    path = (ROOT / str(value)).resolve()
    try:
        path.relative_to(ROOT)
    except ValueError as exc:
        raise CandidateCollectionError(f"path escapes repository: {value}") from exc
    return path


def validate_preregistration(
    path: Path,
) -> tuple[dict[str, Any], CandidateSpec, DenseStartProfile, set[str]]:
    """Load the exact committed contract, dependency hashes and eval symbols."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("experiment_id") != EXPERIMENT_ID:
        raise CandidateCollectionError("unexpected experiment_id")
    spec = CandidateSpec.from_preregistration(payload)
    selection = payload["selection"]
    per_side = selection["per_side"]
    if int(per_side["LONG"]) != int(per_side["SHORT"]):
        raise CandidateCollectionError("LONG and SHORT target counts must match")
    if int(selection["total"]) != 2 * spec.target_per_side:
        raise CandidateCollectionError("total selection count does not match per-side counts")
    shape = payload["shape_contract"]
    if int(shape["review_window_bars"]) != spec.review_bars:
        raise CandidateCollectionError("review window arithmetic drifted")
    if int(shape["selection_future_rows_beyond_t"]) != spec.release_bars - 1:
        raise CandidateCollectionError("selection future-row arithmetic drifted")
    if int(payload["scope"]["holdout_ohlcv_rows_allowed"]) != 0:
        raise CandidateCollectionError("this run must allow zero holdout OHLCV rows")
    safety = payload["safety"]
    forbidden = (
        "model_training",
        "economic_evaluation",
        "raw_kline_write",
        "active_or_frozen_change",
        "forward_or_order_state_change",
        "holdout_read",
    )
    if any(safety.get(field) is not False for field in forbidden):
        raise CandidateCollectionError("one or more safety switches are not false")

    dependencies = payload["frozen_dependencies"]
    dense_prereg = repo_path(dependencies["dense_start_preregistration_path"])
    if sha256_file(dense_prereg) != dependencies["dense_start_preregistration_sha256"]:
        raise CandidateCollectionError("dense-start preregistration hash drifted")
    inherited = json.loads(dense_prereg.read_text(encoding="utf-8"))
    profile_id = str(dependencies["dense_profile_id"])
    inherited_profiles = {
        str(row["profile_id"]): row
        for row in inherited.get("ordered_strictness_profiles", [])
    }
    if profile_id not in inherited_profiles:
        raise CandidateCollectionError(f"inherited profile is missing: {profile_id}")
    profile = DenseStartProfile.from_mapping(dependencies["dense_profile"])
    inherited_profile = DenseStartProfile.from_mapping(inherited_profiles[profile_id])
    if profile != inherited_profile:
        raise CandidateCollectionError("embedded dense profile differs from its frozen source")

    eval_manifest_path = repo_path(dependencies["eval_manifest_path"])
    if sha256_file(eval_manifest_path) != dependencies["eval_manifest_sha256"]:
        raise CandidateCollectionError("frozen eval manifest hash drifted")
    eval_payload = json.loads(eval_manifest_path.read_text(encoding="utf-8"))
    eval_symbols = {str(symbol) for symbol in eval_payload["symbols"]}
    if not eval_symbols:
        raise CandidateCollectionError("frozen eval-symbol set is empty")
    return payload, spec, profile, eval_symbols


def merge_counts(target: Counter[str], values: Mapping[str, int]) -> None:
    """Accumulate integer scan counters without losing zero-valued fields."""

    for key, value in values.items():
        target[str(key)] += int(value)


def deterministic_audit_ids(
    rows: Iterable[Mapping[str, Any]], *, count: int, seed: int
) -> set[str]:
    """Choose a stable cross-side causality audit sample by salted identity."""

    ordered = sorted(
        rows,
        key=lambda row: hashlib.sha256(
            f"{seed}|{row['event_id']}".encode("utf-8")
        ).hexdigest(),
    )
    if len(ordered) < count:
        raise CandidateCollectionError("not enough selected rows for causality audit")
    return {str(row["event_id"]) for row in ordered[:count]}


def selection_audit(
    selected: Mapping[str, Sequence[Mapping[str, Any]]], *, spec: CandidateSpec
) -> dict[str, Any]:
    """Verify exact counts, dedupe distance and frozen diversity quotas."""

    sides: dict[str, Any] = {}
    for side in ("LONG", "SHORT"):
        rows = list(selected[side])
        if len(rows) != spec.target_per_side:
            raise CandidateCollectionError(f"{side} selected count drifted")
        symbols = Counter(str(row["symbol"]) for row in rows)
        days = Counter(utc(row["anchor_time"]).strftime("%Y-%m-%d") for row in rows)
        if max(symbols.values()) > spec.max_per_symbol_per_side:
            raise CandidateCollectionError(f"{side} symbol quota failed")
        if max(days.values()) > spec.max_per_day_per_side:
            raise CandidateCollectionError(f"{side} UTC-day quota failed")
        by_symbol: dict[str, list[Any]] = defaultdict(list)
        for row in rows:
            by_symbol[str(row["symbol"])].append(utc(row["anchor_time"]))
        closest_bars: int | None = None
        for stamps in by_symbol.values():
            ordered = sorted(stamps)
            for left, right in zip(ordered, ordered[1:]):
                bars = int((right - left) / np.timedelta64(spec.bar_minutes, "m"))
                closest_bars = bars if closest_bars is None else min(closest_bars, bars)
                if bars <= spec.dedupe_bars:
                    raise CandidateCollectionError(
                        f"{side} same-symbol dedupe failed: {bars} <= {spec.dedupe_bars}"
                    )
        scores = np.asarray([float(row["completed_score"]) for row in rows])
        sides[side] = {
            "selected": len(rows),
            "unique_symbols": len(symbols),
            "unique_utc_days": len(days),
            "max_per_symbol": max(symbols.values()),
            "max_per_utc_day": max(days.values()),
            "minimum_same_symbol_gap_bars": closest_bars,
            "score_min": float(scores.min()),
            "score_median": float(np.median(scores)),
            "score_max": float(scores.max()),
        }
    return {"passed": True, "sides": sides}


def relative_to_root(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def selected_by_source(
    selected: Mapping[str, Sequence[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for side in ("LONG", "SHORT"):
        for row in selected[side]:
            grouped[str(row["source_path"])].append(row)
    return dict(grouped)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prereg", type=Path, default=DEFAULT_PREREG)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    started = time.perf_counter()
    prereg_path = args.prereg.resolve()
    output = args.out.resolve()
    build_output = output.with_name(f"{output.name}.building")
    if output.exists():
        raise FileExistsError(f"refusing to overwrite output path: {output}")
    if build_output.exists():
        raise FileExistsError(f"refusing to overwrite build path: {build_output}")

    prereg, spec, profile, eval_symbols = validate_preregistration(prereg_path)
    builder_commit = verify_builder_committed([*BUILDER_PATHS, prereg_path])
    roots = [repo_path(value) for value in prereg["scope"]["source_roots"]]
    universe, universe_audit = discover_universe(roots, eval_symbols=eval_symbols)
    expected_fields = {
        "discovered_symbols": int(prereg["scope"]["expected_discovered_symbols"]),
        "eligible_filename_symbols": int(
            prereg["scope"]["expected_eligible_filename_symbols"]
        ),
        "deep_sources": int(prereg["scope"]["expected_deep_sources"]),
        "fetched_sources": int(prereg["scope"]["expected_fetched_sources"]),
    }
    drift = {
        field: {"expected": expected, "actual": int(universe_audit[field])}
        for field, expected in expected_fields.items()
        if int(universe_audit[field]) != expected
    }
    if drift:
        raise CandidateCollectionError(f"universe contract drifted: {drift}")

    candidates: list[dict[str, Any]] = []
    source_audits: list[dict[str, Any]] = []
    scan_counts: Counter[str] = Counter()
    materialized_symbols = 0
    total = len(universe)
    for number, (symbol, source_path) in enumerate(universe.items(), 1):
        frame, source_audit = read_preholdout_prefix(
            source_path, end_exclusive=spec.holdout_start_ts
        )
        source_audit["source_path"] = relative_to_root(source_path)
        source_audit["symbol"] = symbol
        source_audits.append(source_audit)
        scan_counts["source_rows_materialized"] += int(source_audit["rows_materialized"])
        scan_counts["boundary_timestamp_rows_inspected"] += int(
            source_audit["boundary_timestamp_rows_inspected"]
        )
        scan_counts["holdout_ohlcv_rows_materialized"] += int(
            source_audit["holdout_ohlcv_rows_materialized"]
        )
        if frame.empty:
            scan_counts["empty_preholdout_sources"] += 1
            continue
        materialized_symbols += 1
        relative_source = Path(relative_to_root(source_path))
        symbol_before = len(candidates)
        for _, segment in frame.groupby("_segment_id", sort=True):
            rows, counts = collect_segment_candidates(
                segment,
                symbol=symbol,
                source_path=relative_source,
                profile=profile,
                spec=spec,
            )
            candidates.extend(rows)
            merge_counts(scan_counts, counts)
            scan_counts["segments_scanned"] += 1
        if number == 1 or number % 10 == 0 or number == total:
            print(
                f"scan {number:03d}/{total} {symbol:<22} "
                f"rows={len(frame):>7} candidates+={len(candidates)-symbol_before:>4} "
                f"total={len(candidates):>7}",
                flush=True,
            )

    if scan_counts["holdout_ohlcv_rows_materialized"] != 0:
        raise AssertionError("a holdout OHLCV row was materialized")
    deduplicated = deduplicate_candidates(candidates, spec=spec)
    deduplicated_counts = Counter(str(row["direction"]) for row in deduplicated)
    selected = select_balanced_candidates(deduplicated, spec=spec)
    selected_audit = selection_audit(selected, spec=spec)
    all_selected = [*selected["LONG"], *selected["SHORT"]]
    causality_ids = deterministic_audit_ids(
        all_selected,
        count=spec.causality_audit_rows,
        seed=spec.causality_seed,
    )

    build_output.mkdir(parents=True)
    chart_dir = build_output / "review_charts"
    grouped = selected_by_source(selected)
    causality_rows: list[dict[str, Any]] = []
    rendered = 0
    for source_number, source_key in enumerate(sorted(grouped), 1):
        source_path = repo_path(source_key)
        frame, render_source_audit = read_preholdout_prefix(
            source_path, end_exclusive=spec.holdout_start_ts
        )
        if int(render_source_audit["holdout_ohlcv_rows_materialized"]) != 0:
            raise AssertionError("render reload materialized holdout OHLCV")
        segment_cache: dict[tuple[int, int], tuple[Any, Any]] = {}
        for row in sorted(
            grouped[source_key], key=lambda value: (value["direction"], int(value["rank"]))
        ):
            start_i = int(row["segment_start_i"])
            end_i = int(row["segment_end_i"])
            segment_key = (start_i, end_i)
            if segment_key not in segment_cache:
                raw_segment = frame[
                    (frame["_source_i"] >= start_i) & (frame["_source_i"] <= end_i)
                ].reset_index(drop=True)
                segment_cache[segment_key] = (raw_segment, add_six_mas(raw_segment))
            raw_segment, segment = segment_cache[segment_key]
            stamp = utc(row["anchor_time"]).strftime("%Y%m%d_%H%M")
            name = (
                f"{str(row['direction']).lower()}_{int(row['rank']):03d}_"
                f"{row['symbol']}_{stamp}.png"
            )
            build_path = chart_dir / name
            meta = render_review_chart(segment, row, spec=spec, output=build_path)
            row.update(meta)
            row["review_path"] = relative_to_root(output / "review_charts" / name)
            if str(row["event_id"]) in causality_ids:
                anchor_source_i = int(row["source_anchor_i"])
                causal_stop = anchor_source_i + spec.release_bars + spec.review_extra_bars
                audit_segment = raw_segment[raw_segment["_source_i"] < causal_stop]
                causality_rows.append(
                    audit_future_invariance(audit_segment, row, profile=profile)
                )
            rendered += 1
        if source_number == 1 or source_number % 10 == 0 or source_number == len(grouped):
            print(
                f"render {source_number:03d}/{len(grouped)} sources "
                f"charts={rendered:04d}/1000",
                flush=True,
            )

    if rendered != 2 * spec.target_per_side:
        raise CandidateCollectionError(f"rendered {rendered}, expected {2 * spec.target_per_side}")
    if len(causality_rows) != spec.causality_audit_rows:
        raise CandidateCollectionError(
            f"causality audit produced {len(causality_rows)} rows, "
            f"expected {spec.causality_audit_rows}"
        )

    manifest_rows = [*selected["LONG"], *selected["SHORT"]]
    write_jsonl(build_output / "review_manifest.jsonl", manifest_rows)
    write_json(build_output / "source_audit.json", source_audits)
    write_json(build_output / "causality_audit.json", causality_rows)
    overview_paths = [
        chart_dir / Path(str(row["review_path"])).name
        for side in ("LONG", "SHORT")
        for row in selected[side][:20]
    ]
    overview_path = build_output / "overview_top40.png"
    build_overview(overview_paths, output=overview_path)
    gallery_path = build_output / "index.html"
    build_gallery(manifest_rows, output=gallery_path)

    elapsed = time.perf_counter() - started
    boundary_last = [
        str(row["last_time"]) for row in source_audits if row.get("last_time") is not None
    ]
    summary = {
        "experiment_id": EXPERIMENT_ID,
        "protocol": spec.protocol,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "success_pending_owner_review",
        "builder_commit": builder_commit,
        "preregistration_path": relative_to_root(prereg_path),
        "preregistration_sha256": sha256_file(prereg_path),
        "elapsed_seconds": elapsed,
        "universe": {
            **universe_audit,
            "materialized_preholdout_symbols": materialized_symbols,
            "symbols": sorted(universe),
        },
        "scan": {
            **dict(sorted(scan_counts.items())),
            "eligible_before_dedupe_total": len(candidates),
            "eligible_before_dedupe_long": sum(
                row["direction"] == "LONG" for row in candidates
            ),
            "eligible_before_dedupe_short": sum(
                row["direction"] == "SHORT" for row in candidates
            ),
            "deduplicated_total": len(deduplicated),
            "deduplicated_long": int(deduplicated_counts["LONG"]),
            "deduplicated_short": int(deduplicated_counts["SHORT"]),
            "threshold_tuned_after_scan": False,
        },
        "selection_audit": selected_audit,
        "causality_null": {
            "method": "multiply every OHLC row after t by 7 and volume by 13 then recompute",
            "sample_rows": len(causality_rows),
            "sample_seed": spec.causality_seed,
            "passed": all(bool(row["passed"]) for row in causality_rows),
            "maximum_abs_difference": max(
                float(row["max_abs_difference"]) for row in causality_rows
            ),
            "audit_path": relative_to_root(output / "causality_audit.json"),
            "audit_sha256": sha256_file(build_output / "causality_audit.json"),
        },
        "holdout": {
            "start_exclusive": spec.holdout_start_ts.isoformat(),
            "ohlcv_rows_materialized": int(
                scan_counts["holdout_ohlcv_rows_materialized"]
            ),
            "boundary_timestamp_rows_inspected": int(
                scan_counts["boundary_timestamp_rows_inspected"]
            ),
            "latest_materialized_time": max(boundary_last) if boundary_last else None,
            "existing_owner_label_manifests_read": 0,
            "read": False,
        },
        "output": {
            "candidate_rows": len(manifest_rows),
            "review_charts": rendered,
            "training_images": 0,
            "labels": 0,
            "owner_verdict": "PENDING",
            "training_eligible": False,
            "production_eligible": False,
            "manifest_path": relative_to_root(output / "review_manifest.jsonl"),
            "manifest_sha256": sha256_file(build_output / "review_manifest.jsonl"),
            "source_audit_path": relative_to_root(output / "source_audit.json"),
            "source_audit_sha256": sha256_file(build_output / "source_audit.json"),
            "overview_path": relative_to_root(output / "overview_top40.png"),
            "overview_sha256": sha256_file(overview_path),
            "gallery_path": relative_to_root(output / "index.html"),
            "gallery_sha256": sha256_file(gallery_path),
        },
        "existing_pack": prereg["existing_pack_audit"],
        "model_trained": False,
        "economic_evaluation_run": False,
        "raw_klines_written": 0,
        "active_or_frozen_changed": False,
        "forward_or_order_state_changed": False,
    }
    write_json(build_output / "scan_summary.json", summary)
    readme = f"""# {EXPERIMENT_ID} results

- status: **success, PENDING Owner review**
- scan: **{spec.scan_start_ts.isoformat()}** to **{spec.scan_end_ts.isoformat()}** (exclusive)
- selected: **{len(selected['LONG'])} LONG + {len(selected['SHORT'])} SHORT**
- proposal gate: inherited **{profile.profile_id}**, causal through completed bar t
- completed-path ranking: **12 bars**, plus **6 review-only bars**
- holdout OHLCV materialized: **0 rows**
- training images / labels / models: **0 / 0 / 0**
- training / production eligible: **false / false**

Open `index.html` for the searchable 1,000-chart gallery.  Blue marks completed
bar t and gray marks the end of the 12-bar completed path.  These are machine
proposals, not automatically accepted positives.
"""
    (build_output / "README.md").write_text(readme, encoding="utf-8")
    build_output.rename(output)
    print(
        f"selected LONG={len(selected['LONG'])} SHORT={len(selected['SHORT'])} "
        f"charts={rendered} holdout_ohlcv=0 elapsed={elapsed:.1f}s out={output}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
