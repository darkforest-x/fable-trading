#!/usr/bin/env python3
"""Run the frozen two-year 4h BTC-reference morphology retrieval.

This one-shot experiment is descriptive. It reads completed historical shapes,
including 12 bars after each proposed release onset, so every output is forced
to ``training_eligible=false`` and ``production_eligible=false``. It never
reads or writes ACTIVE, thresholds, forward logs, order state, or raw kline
caches. Public OKX suffix rows are held in memory only.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from yoyo.contracts.holdout import HOLDOUT_START
from yoyo.layers.l1_detection.four_hour_similarity import (
    CHANNELS,
    ReferenceContract,
    SimilaritySpec,
    build_overview,
    build_reference_contract,
    candidate_anchor_indices,
    canonical_frame_sha256,
    channel_scales,
    coarse_distance,
    deduplicate_candidates,
    discover_universe,
    enrich_4h,
    event_id,
    fetch_recent_4h,
    merge_with_api_suffix,
    normalize_tensor,
    passes_reference_contract,
    raw_window_tensor,
    read_local_15m,
    render_review_chart,
    resample_complete_4h,
    sha256_file,
    split_dtw_distance,
    symbol_from_path,
    utc,
    write_json,
    write_jsonl,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXPERIMENT_ID = "exp-btc-4h-ma-launch-similarity-v1"
DEFAULT_SOURCE_DIR = ROOT / "data/kline_deep"
DEFAULT_EXPERIMENT_DIR = ROOT / "experiments/active" / DEFAULT_EXPERIMENT_ID
DEFAULT_PREREG = DEFAULT_EXPERIMENT_DIR / "preregistration.json"
DEFAULT_OUTPUT = DEFAULT_EXPERIMENT_DIR / "results"


def git_output(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=ROOT, text=True, stderr=subprocess.STDOUT
    ).strip()


def verify_builder_committed(paths: list[Path]) -> str:
    """Fail if this run would precede or differ from its committed builder."""

    if git_output("branch", "--show-current") != "main":
        raise RuntimeError("builder must run on main")
    relative = [str(path.relative_to(ROOT)) for path in paths]
    status = git_output("status", "--short", "--", *relative)
    if status:
        raise RuntimeError(f"builder/prereg paths are not committed:\n{status}")
    commit = git_output("log", "-1", "--format=%H", "--", relative[0])
    if len(commit) != 40:
        raise RuntimeError("could not resolve committed builder SHA")
    return commit


def validate_preregistration(
    path: Path,
) -> tuple[dict[str, Any], SimilaritySpec, str, int, int]:
    """Load the exact committed run identity, spec, universe and holdout use."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    experiment_id = str(payload.get("experiment_id", ""))
    if not experiment_id.startswith("exp-"):
        raise ValueError("preregistration experiment_id is missing or invalid")
    raw_spec = payload.get("spec")
    if not isinstance(raw_spec, dict):
        raise ValueError("preregistration spec is missing or invalid")
    spec = SimilaritySpec.from_jsonable(raw_spec)
    if payload.get("spec") != spec.to_jsonable():
        raise ValueError("preregistration spec does not match executable constants")
    expected_universe_size = int(payload.get("expected_universe_size", -1))
    if expected_universe_size <= 0:
        raise ValueError("preregistration universe size is invalid")
    holdout = payload.get("holdout", {})
    holdout_use_number = int(holdout.get("use_number_for_this_configuration", -1))
    if holdout_use_number <= 0 or holdout.get("read") is not True:
        raise ValueError("holdout read/use number is not frozen")
    if holdout.get("owner_authorized_in_conversation") is not True:
        raise ValueError("holdout authorization is not recorded")
    if not str(holdout.get("authorization_scope", "")).strip():
        raise ValueError("holdout authorization scope is empty")
    return (
        payload,
        spec,
        experiment_id,
        expected_universe_size,
        holdout_use_number,
    )


def repo_path(value: object) -> Path:
    """Resolve a preregistered repository path without allowing parent escape."""

    path = (ROOT / str(value)).resolve()
    try:
        path.relative_to(ROOT)
    except ValueError as exc:
        raise ValueError(f"path escapes repository: {value}") from exc
    return path


def validate_expansion_contract(
    prereg: dict[str, Any], spec: SimilaritySpec
) -> dict[str, Any]:
    """Prove an optional expansion changes only the frozen Top-N field."""

    contract = prereg.get("expansion_contract")
    if contract is None:
        return {"enabled": False}
    if not isinstance(contract, dict):
        raise ValueError("expansion_contract must be a mapping")
    baseline_prereg = repo_path(contract.get("baseline_preregistration_path"))
    baseline_summary = repo_path(contract.get("baseline_summary_path"))
    expected_prereg_hash = str(contract.get("baseline_preregistration_sha256", ""))
    expected_summary_hash = str(contract.get("baseline_summary_sha256", ""))
    if sha256_file(baseline_prereg) != expected_prereg_hash:
        raise ValueError("baseline preregistration hash drifted")
    if sha256_file(baseline_summary) != expected_summary_hash:
        raise ValueError("baseline summary hash drifted")
    baseline_payload = json.loads(baseline_prereg.read_text(encoding="utf-8"))
    baseline_spec = SimilaritySpec.from_jsonable(baseline_payload["spec"])
    baseline_json = baseline_spec.to_jsonable()
    current_json = spec.to_jsonable()
    changed_fields = sorted(
        key
        for key in set(baseline_json) | set(current_json)
        if baseline_json.get(key) != current_json.get(key)
    )
    if changed_fields != ["top_per_side"]:
        raise ValueError(
            f"expansion must change only top_per_side, got {changed_fields}"
        )
    previous_top = int(contract.get("previous_top_per_side", -1))
    expanded_top = int(contract.get("expanded_top_per_side", -1))
    if baseline_spec.top_per_side != previous_top or spec.top_per_side != expanded_top:
        raise ValueError("expansion Top-N values do not match preregistration")
    if expanded_top <= previous_top:
        raise ValueError("expanded Top-N must exceed the baseline")
    return {
        "enabled": True,
        "single_changed_field": "top_per_side",
        "previous_top_per_side": previous_top,
        "expanded_top_per_side": expanded_top,
        "baseline_preregistration_path": str(baseline_prereg.relative_to(ROOT)),
        "baseline_preregistration_sha256": expected_prereg_hash,
        "baseline_summary_path": str(baseline_summary.relative_to(ROOT)),
        "baseline_summary_sha256": expected_summary_hash,
    }


def audit_expansion_rank_prefix(
    prereg: dict[str, Any], selected: dict[str, list[dict[str, Any]]]
) -> dict[str, Any]:
    """Fail closed unless the expanded ranking begins with the old exact Top-N."""

    contract = prereg.get("expansion_contract")
    if contract is None:
        return {"required": False}
    baseline_path = repo_path(contract["baseline_summary_path"])
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    previous_top = int(contract["previous_top_per_side"])
    exact_fields = ("event_id", "symbol", "direction", "anchor_time")
    numeric_fields = ("coarse_distance", "dtw_distance", "final_distance")
    side_audit: dict[str, Any] = {}
    for side in ("LONG", "SHORT"):
        expected = baseline["selected"][side]
        actual = selected[side][:previous_top]
        if len(expected) != previous_top or len(actual) != previous_top:
            raise ValueError(f"{side} baseline prefix length drifted")
        for rank, (old, new) in enumerate(zip(expected, actual), 1):
            for field in exact_fields:
                if old[field] != new[field]:
                    raise ValueError(f"{side} rank {rank} {field} drifted")
            for field in numeric_fields:
                if not math.isclose(
                    float(old[field]), float(new[field]), rel_tol=0.0, abs_tol=1e-12
                ):
                    raise ValueError(f"{side} rank {rank} {field} drifted")
        side_audit[side] = {
            "prefix_count": previous_top,
            "event_ids": [row["event_id"] for row in actual],
            "exact_identity_and_distance_match": True,
        }
    return {"required": True, "passed": True, "sides": side_audit}


def load_symbol(
    path: Path,
    *,
    spec: SimilaritySpec,
    source_start: pd.Timestamp,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Build one audited, closed 4h series from a local snapshot plus API suffix."""

    symbol = symbol_from_path(path)
    local_15m = read_local_15m(path, source_start=source_start)
    local_4h, resample_audit = resample_complete_4h(local_15m)
    api_4h, api_audit = fetch_recent_4h(
        symbol,
        limit=spec.api_limit,
        pause_seconds=spec.api_pause_seconds,
    )
    merged, parity_audit = merge_with_api_suffix(
        local_4h,
        api_4h,
        scan_end=spec.scan_end_ts,
    )
    merged = enrich_4h(merged)
    in_scope = merged[
        (merged["open_time"] >= spec.scan_start_ts)
        & (merged["open_time"] <= spec.scan_end_ts)
    ]
    audit = {
        "symbol": symbol,
        "local_path": str(path.relative_to(ROOT)),
        "local_sha256": sha256_file(path),
        "local_15m_rows_bounded": len(local_15m),
        "local_15m_first_time": local_15m["open_time"].min().isoformat(),
        "local_15m_last_time": local_15m["open_time"].max().isoformat(),
        **resample_audit,
        **api_audit,
        **parity_audit,
        "merged_4h_rows_with_warmup": len(merged),
        "scan_scope_4h_rows": len(in_scope),
        "scan_scope_first_time": in_scope["open_time"].min().isoformat(),
        "scan_scope_last_time": in_scope["open_time"].max().isoformat(),
        "merged_series_sha256": canonical_frame_sha256(merged),
        "raw_klines_written_locally": 0,
    }
    return merged, audit


def is_reference_exclusion(row: dict[str, Any], spec: SimilaritySpec) -> bool:
    if row["symbol"] != spec.reference_symbol or row["direction"] != "LONG":
        return False
    gap = abs(utc(row["anchor_time"]) - spec.reference_anchor_ts)
    return gap <= pd.Timedelta(hours=4 * spec.dedupe_bars)


def event_top_mean(
    distances: np.ndarray,
    rows: list[dict[str, Any]],
    *,
    spec: SimilaritySpec,
    count: int,
) -> float:
    """Mean the best non-overlapping distances for one null/reference query."""

    occupied: dict[tuple[str, str], list[int]] = {}
    chosen: list[float] = []
    for index in np.argsort(distances):
        row = rows[int(index)]
        if is_reference_exclusion(row, spec):
            continue
        key = (str(row["symbol"]), str(row["direction"]))
        anchor_i = int(row["anchor_i"])
        if any(abs(anchor_i - prior) <= spec.dedupe_bars for prior in occupied.get(key, [])):
            continue
        occupied.setdefault(key, []).append(anchor_i)
        chosen.append(float(distances[int(index)]))
        if len(chosen) == count:
            break
    if len(chosen) != count:
        raise ValueError(f"only {len(chosen)} non-overlapping candidates for top-{count}")
    return float(np.mean(chosen))


def phase_scramble_null(
    reference: np.ndarray,
    rows: list[dict[str, Any]],
    *,
    spec: SimilaritySpec,
) -> dict[str, Any]:
    """Compare real top-event distance with release-order permutations.

    Every null keeps the exact same 30-bar prelude and the same 12 release rows;
    only their temporal order is permuted. This preserves amplitudes, channel
    values, and the broad-gate candidate population while removing the launch
    sequence that the similarity search claims to retrieve.
    """

    if not rows:
        raise ValueError("null control has no eligible candidate rows")
    matrix = np.stack([row["_tensor"] for row in rows]).astype(np.float32)
    observed_distances = np.sqrt(
        np.mean(np.square(matrix - reference.astype(np.float32)), axis=(1, 2))
    )
    observed = event_top_mean(
        observed_distances,
        rows,
        spec=spec,
        count=spec.top_per_side,
    )
    rng = np.random.default_rng(spec.random_seed)
    null_values: list[float] = []
    release_indices = np.arange(spec.pre_bars, spec.total_bars)
    for _ in range(spec.null_permutations):
        permuted = reference.copy()
        permutation = rng.permutation(release_indices)
        permuted[release_indices] = reference[permutation]
        distances = np.sqrt(
            np.mean(
                np.square(matrix - permuted.astype(np.float32)),
                axis=(1, 2),
            )
        )
        null_values.append(
            event_top_mean(distances, rows, spec=spec, count=spec.top_per_side)
        )
    null_array = np.asarray(null_values, dtype=float)
    return {
        "method": "release-row phase scramble; prelude and release row multiset fixed",
        "distance_stage": "coarse weighted RMSE before DTW",
        "permutations": spec.null_permutations,
        "random_seed": spec.random_seed,
        "top_event_count": spec.top_per_side,
        "observed_top_mean_distance": observed,
        "null_mean": float(null_array.mean()),
        "null_median": float(np.median(null_array)),
        "null_p05": float(np.quantile(null_array, 0.05)),
        "null_p95": float(np.quantile(null_array, 0.95)),
        "one_sided_p_lower_is_better": float(
            (1 + int((null_array <= observed).sum()))
            / (spec.null_permutations + 1)
        ),
    }


def public_row(row: dict[str, Any]) -> dict[str, Any]:
    """Drop in-memory tensors before serializing a candidate."""

    return {key: value for key, value in row.items() if not key.startswith("_")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--prereg", type=Path, default=DEFAULT_PREREG)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    source_dir = args.source_dir.resolve()
    prereg_path = args.prereg.resolve()
    output = args.out.resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"refusing to overwrite non-empty output: {output}")
    (
        prereg,
        spec,
        experiment_id,
        expected_universe_size,
        holdout_use_number,
    ) = validate_preregistration(prereg_path)
    expansion_contract_audit = validate_expansion_contract(prereg, spec)
    builder_commit = verify_builder_committed(
        [
            ROOT / "yoyo/layers/l1_detection/four_hour_similarity.py",
            Path(__file__).resolve(),
            ROOT / "tests/test_four_hour_similarity.py",
            prereg_path,
        ]
    )
    paths = discover_universe(source_dir)
    if len(paths) != expected_universe_size:
        raise ValueError(
            f"universe size drifted: {len(paths)} != {expected_universe_size}"
        )
    symbol_paths = {symbol_from_path(path): path for path in paths}
    if spec.reference_symbol not in symbol_paths:
        raise ValueError("reference symbol is absent from the frozen universe")

    source_start = spec.scan_start_ts - pd.Timedelta(
        hours=4 * (120 + spec.pre_bars + 5)
    )
    frames: dict[str, pd.DataFrame] = {}
    source_audits: list[dict[str, Any]] = []
    reference_frame, reference_audit = load_symbol(
        symbol_paths[spec.reference_symbol],
        spec=spec,
        source_start=source_start,
    )
    frames[spec.reference_symbol] = reference_frame
    source_audits.append(reference_audit)
    reference_matches = reference_frame.index[
        reference_frame["open_time"] == spec.reference_anchor_ts
    ]
    if len(reference_matches) != 1:
        raise ValueError("reference anchor is missing or duplicated")
    reference_anchor_i = int(reference_matches[0])
    reference_raw, reference_metrics = raw_window_tensor(
        reference_frame,
        reference_anchor_i,
        1,
        spec,
    )
    scales = channel_scales(reference_raw, spec)
    reference_tensor = normalize_tensor(reference_raw, scales, spec)
    contract = build_reference_contract(reference_metrics, spec)
    if not passes_reference_contract(reference_metrics, contract):
        raise ValueError("reference does not recover under its frozen broad gate")

    candidates: dict[str, list[dict[str, Any]]] = {"LONG": [], "SHORT": []}
    scan_counts: dict[str, dict[str, int]] = {}
    holdout_rows_read = 0
    for symbol in sorted(symbol_paths):
        if symbol == spec.reference_symbol:
            frame = reference_frame
            audit = reference_audit
        else:
            frame, audit = load_symbol(
                symbol_paths[symbol],
                spec=spec,
                source_start=source_start,
            )
            frames[symbol] = frame
            source_audits.append(audit)
        holdout_rows_read += int(
            (
                (frame["open_time"] >= utc(HOLDOUT_START))
                & (frame["open_time"] <= spec.scan_end_ts)
            ).sum()
        )
        for direction in (1, -1):
            side = "LONG" if direction == 1 else "SHORT"
            indices, counts = candidate_anchor_indices(
                frame,
                direction=direction,
                contract=contract,
                spec=spec,
            )
            scan_counts[f"{symbol}:{side}"] = counts
            for anchor_i in indices:
                raw_tensor, metrics = raw_window_tensor(
                    frame,
                    anchor_i,
                    direction,
                    spec,
                )
                if not passes_reference_contract(metrics, contract):
                    raise AssertionError("vectorized/scalar gate parity failed")
                tensor = normalize_tensor(raw_tensor, scales, spec)
                row = {
                    **metrics,
                    "event_id": event_id(
                        spec.protocol,
                        symbol,
                        side,
                        str(metrics["anchor_time"]),
                    ),
                    "symbol": symbol,
                    "timeframe": "4h",
                    "coarse_distance": coarse_distance(tensor, reference_tensor),
                    "owner_reference_sample": bool(
                        symbol == spec.reference_symbol
                        and side == "LONG"
                        and utc(metrics["anchor_time"]) == spec.reference_anchor_ts
                    ),
                    "sample_owner_confirmed_semantics": False,
                    "owner_verdict": "PENDING",
                    "codex_visual_review": "NOT_REVIEWED",
                    "_tensor": tensor.astype(np.float32),
                }
                candidates[side].append(row)
        print(
            f"loaded {symbol:<20} rows={audit['scan_scope_4h_rows']:<5} "
            f"long={scan_counts[f'{symbol}:LONG']['anchors_passing_broad_gate']:<4} "
            f"short={scan_counts[f'{symbol}:SHORT']['anchors_passing_broad_gate']:<4}"
        )

    reference_rows = [
        row
        for row in candidates["LONG"]
        if bool(row["owner_reference_sample"])
    ]
    if len(reference_rows) != 1:
        raise ValueError(f"reference recovery count is {len(reference_rows)}, expected 1")
    reference_row = reference_rows[0]
    reference_row.update(
        {
            "sample_owner_confirmed_semantics": True,
            "owner_verdict": "OWNER_REFERENCE",
            "codex_visual_review": "OWNER_REFERENCE",
            "dtw_distance": 0.0,
            "final_distance": 0.0,
            "rank": 0,
        }
    )

    selected: dict[str, list[dict[str, Any]]] = {}
    null_controls: dict[str, dict[str, Any]] = {}
    for side in ("LONG", "SHORT"):
        eligible = [row for row in candidates[side] if not is_reference_exclusion(row, spec)]
        shortlist = sorted(eligible, key=lambda row: float(row["coarse_distance"]))[
            : spec.shortlist_per_side
        ]
        for row in shortlist:
            row["dtw_distance"] = split_dtw_distance(
                row["_tensor"], reference_tensor, spec
            )
            row["final_distance"] = (
                spec.coarse_weight * float(row["coarse_distance"])
                + spec.dtw_weight * float(row["dtw_distance"])
            )
        deduplicated = deduplicate_candidates(
            shortlist,
            distance_field="final_distance",
            gap_bars=spec.dedupe_bars,
        )
        if len(deduplicated) < spec.top_per_side:
            raise ValueError(
                f"{side} has only {len(deduplicated)} deduplicated candidates"
            )
        selected[side] = deduplicated[: spec.top_per_side]
        for rank, row in enumerate(selected[side], 1):
            row["rank"] = rank
            row["codex_visual_review"] = "SUGGESTED_MATCH_PENDING_OWNER"
        null_controls[side] = phase_scramble_null(
            reference_tensor,
            candidates[side],
            spec=spec,
        )
    expansion_prefix_audit = audit_expansion_rank_prefix(prereg, selected)

    output.mkdir(parents=True, exist_ok=True)
    chart_dir = output / "charts"
    chart_paths: list[Path] = []
    reference_path = chart_dir / "000_owner_reference_BTC_20260819_1200.png"
    reference_meta = render_review_chart(
        frames[reference_row["symbol"]],
        reference_row,
        spec=spec,
        output=reference_path,
        rank_label="OWNER REFERENCE",
    )
    reference_row.update(reference_meta)
    reference_row["review_path"] = str(reference_path.relative_to(ROOT))
    chart_paths.append(reference_path)
    public_candidates: list[dict[str, Any]] = []
    for side in ("LONG", "SHORT"):
        for row in selected[side]:
            stamp = utc(row["anchor_time"]).strftime("%Y%m%d_%H%M")
            path = chart_dir / (
                f"{side.lower()}_{int(row['rank']):02d}_{row['symbol']}_{stamp}.png"
            )
            meta = render_review_chart(
                frames[row["symbol"]],
                row,
                spec=spec,
                output=path,
                rank_label=f"{side} #{int(row['rank'])}",
            )
            row.update(meta)
            row["review_path"] = str(path.relative_to(ROOT))
            chart_paths.append(path)
            public_candidates.append(public_row(row))
    overview_path = output / "overview.png"
    build_overview(chart_paths, output=overview_path)

    manifest_rows = [public_row(reference_row), *public_candidates]
    write_jsonl(output / "review_manifest.jsonl", manifest_rows)
    write_json(output / "source_audit.json", source_audits)
    reference_payload = {
        "spec": spec.to_jsonable(),
        "reference_metrics": public_row(reference_metrics),
        "reference_contract": asdict(contract),
        "channel_scales": dict(zip(CHANNELS, [float(value) for value in scales])),
        "reference_event_id": reference_row["event_id"],
    }
    write_json(output / "reference_contract.json", reference_payload)
    review_manifest_sha = sha256_file(output / "review_manifest.jsonl")
    summary = {
        "experiment_id": experiment_id,
        "protocol": spec.protocol,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "builder_commit": builder_commit,
        "preregistration_path": str(prereg_path.relative_to(ROOT)),
        "preregistration_sha256": sha256_file(prereg_path),
        "source": {
            "system": "OKX public market data",
            "local_source_dir": str(source_dir.relative_to(ROOT)),
            "local_files": len(paths),
            "api_calls": sum(int(row["api_calls"]) for row in source_audits),
            "raw_klines_written_locally": 0,
            "all_overlap_ohlc_exact": all(
                max(row["overlap_max_relative_ohlc_delta"].values()) <= 1e-10
                for row in source_audits
            ),
            "audited_symbols": len(source_audits),
            "scan_scope_rows_total": sum(
                int(row["scan_scope_4h_rows"]) for row in source_audits
            ),
            "source_audit_path": str((output / "source_audit.json").relative_to(ROOT)),
            "source_audit_sha256": sha256_file(output / "source_audit.json"),
        },
        "scan_start": spec.scan_start_ts.isoformat(),
        "scan_end": spec.scan_end_ts.isoformat(),
        "reference_anchor": spec.reference_anchor_ts.isoformat(),
        "universe_symbols": sorted(symbol_paths),
        "universe_size": len(symbol_paths),
        "scan_counts": {
            "anchors_in_scope_per_side_total": sum(
                counts["anchors_in_scope"] for counts in scan_counts.values()
            ),
            "broad_gate_long": len(candidates["LONG"]),
            "broad_gate_short": len(candidates["SHORT"]),
            "shortlist_per_side": spec.shortlist_per_side,
            "deduplicated_selected_long": len(selected["LONG"]),
            "deduplicated_selected_short": len(selected["SHORT"]),
        },
        "null_controls": null_controls,
        "expansion_contract_audit": expansion_contract_audit,
        "expansion_rank_prefix_audit": expansion_prefix_audit,
        "holdout": {
            "read": True,
            "four_hour_symbol_rows_read": holdout_rows_read,
            "use_number_for_this_configuration": holdout_use_number,
            "owner_authorized_in_conversation": True,
            "authorization_scope": prereg["holdout"]["authorization_scope"],
        },
        "lookahead_contract": {
            "pre_bars": spec.pre_bars,
            "historical_release_bars_used_for_similarity": spec.release_bars,
            "review_extra_bars": spec.review_extra_bars,
            "causal_live_signal_claim": False,
        },
        "threshold_tuned_after_scan": False,
        "model_trained": False,
        "economic_evaluation_run": False,
        "training_eligible": False,
        "production_eligible": False,
        "reference_contract": asdict(contract),
        "reference_metrics": public_row(reference_metrics),
        "review_manifest_path": str((output / "review_manifest.jsonl").relative_to(ROOT)),
        "review_manifest_sha256": review_manifest_sha,
        "overview_path": str(overview_path.relative_to(ROOT)),
        "overview_sha256": sha256_file(overview_path),
        "selected": {
            side: [public_row(row) for row in selected[side]]
            for side in ("LONG", "SHORT")
        },
    }
    write_json(output / "scan_summary.json", summary)
    readme = f"""# {experiment_id} results

- universe: **{len(symbol_paths)}** long-history OKX USDT perpetuals
- scan: **{spec.scan_start_ts.isoformat()}** to **{spec.scan_end_ts.isoformat()}**
- selected: **{len(selected['LONG'])} LONG + {len(selected['SHORT'])} SHORT**
- holdout: this configuration use **#{holdout_use_number}**, authorized by the Owner's informed continuation
- model/economic evaluation: **none / none**
- training/production eligible: **false / false**

Open `overview.png` for the visual summary. Blue vertical lines mark the proposed
release onset; gray lines mark the end of the 12-bar completed-shape match.
"""
    (output / "README.md").write_text(readme, encoding="utf-8")
    print(
        f"selected long={len(selected['LONG'])} short={len(selected['SHORT'])} "
        f"holdout_rows={holdout_rows_read} out={output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
