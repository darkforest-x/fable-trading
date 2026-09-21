"""Resumable, source-bounded mining of frozen Grade-A MA-launch events.

The Profit3R experiment may broaden *where* the original morphology is
looked for, but may not change its gates.  This module therefore reuses the
original close-MA morphology profiles and Perfect Filter calibration.  A
candidate with core end ``c`` reads OHLCV through ``c + 5`` only: that fifth
bar is the completed confirmation, and is also the right edge supplied to
``extract_profile``.  Outcome labels are deliberately outside this module.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from yoyo.datasets.fifteen_minute_launch_candidates import (
    add_candidate_features,
    read_preholdout_prefix,
    sha256_file,
)
from yoyo.datasets.ma_launch_owner_autofill10000 import (
    DEFAULT_PREREG as DEFAULT_AUTOFILL_PREREG,
    frame_arrays,
    load_reference_profiles,
)
from yoyo.datasets.ma_launch_owner_autofill_review import (
    FEATURE_NAMES,
    Profile,
    morphology_profile,
    passes_gate,
    profile_distance,
)
from yoyo.datasets.ma_launch_owner_perfect_filter import (
    DEFAULT_PREREG,
    HOLDOUT_START,
    PerfectFilterError,
    _load_pinned_rows,
    _profile_key,
    _repo_path,
    _score_all,
    extract_profile,
    read_json,
)
from yoyo.datasets.ma_launch_snapshot_scan import candidate_row, pick_4_5, source_event_nms


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_NAME = "source_scans"
RULE_DEPENDENCY_PATHS = (
    ROOT / "yoyo/datasets/ma_launch_snapshot_scan.py",
    ROOT / "yoyo/datasets/ma_launch_owner_perfect_filter.py",
    ROOT / "yoyo/datasets/ma_launch_owner_autofill10000.py",
    ROOT / "yoyo/datasets/ma_launch_owner_autofill_review.py",
    ROOT / "yoyo/datasets/fifteen_minute_launch_candidates.py",
)

# Spawned workers receive this immutable context once through their process
# initializer.  Keeping it module-level makes the worker entry point pickleable
# on macOS' spawn start method.
_WORKER_CONTEXT: ScoreContext | None = None
_WORKER_PLAN: Mapping[str, Any] | None = None
_WORKER_PLAN_SHA: str | None = None
_WORKER_OUTPUT_ROOT: Path | None = None


class ProfitMinerError(RuntimeError):
    """Raised when a frozen mining receipt cannot be reproduced safely."""


def _utc(value: object) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        raise ProfitMinerError(f"timestamp requires timezone: {value!r}")
    return stamp.tz_convert("UTC")


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError as exc:
        raise ProfitMinerError(f"path escapes repository: {path}") from exc


def _json_sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _rule_dependency_hashes() -> dict[str, str]:
    """Hash every imported rule implementation that can change candidate results."""

    return {_relative(path): sha256_file(path) for path in RULE_DEPENDENCY_PATHS}


def _write_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> str:
    temporary = path.with_suffix(path.suffix + ".part")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(temporary, path)
    return sha256_file(path)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_sources(path: Path) -> list[dict[str, Any]]:
    """Load the committed source manifest without inferring missing metadata."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload["sources"] if isinstance(payload, Mapping) else payload
    if not isinstance(rows, list) or not rows:
        raise ProfitMinerError("sources manifest must contain a non-empty sources list")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in rows:
        if not isinstance(raw, Mapping):
            raise ProfitMinerError("sources manifest contains a non-object entry")
        missing = {"source_path", "symbol", "venue", "bar_minutes", "sha256"} - set(raw)
        if missing:
            raise ProfitMinerError(f"source manifest entry missing {sorted(missing)}")
        source_path = _relative(_repo_path(raw["source_path"]))
        if source_path in seen:
            raise ProfitMinerError(f"duplicate source_path in manifest: {source_path}")
        seen.add(source_path)
        minutes = int(raw["bar_minutes"])
        if minutes <= 0:
            raise ProfitMinerError("bar_minutes must be positive")
        result.append({
            "source_path": source_path,
            "symbol": str(raw["symbol"]),
            "venue": str(raw["venue"]),
            "bar_minutes": minutes,
            "sha256": str(raw["sha256"]),
        })
    return sorted(result, key=lambda row: (row["symbol"], row["venue"], row["bar_minutes"], row["source_path"]))


def _assert_committed(paths: Sequence[Path]) -> str:
    """Require the formal scan's code, plan, and sources manifest on ``main``."""

    branch = subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip()
    if branch != "main":
        raise ProfitMinerError("formal mining must run on main")
    relative_paths = [_relative(path) for path in paths]
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain", "--", *relative_paths], cwd=ROOT, text=True
    ).strip()
    if dirty:
        raise ProfitMinerError("commit miner, plan, and sources manifest before formal run:\n" + dirty)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


@dataclass(frozen=True)
class ScoreContext:
    """Pinned reference calibration; candidate rows never contribute to it."""

    autofill: Mapping[str, Any]
    perfect_prereg: Mapping[str, Any]
    scan_references: list[Profile]
    references: list[dict[str, Any]]
    family: list[dict[str, Any]]
    profiles: dict[str, Any]
    reference_audits: list[dict[str, Any]]


def load_score_context() -> ScoreContext:
    """Reconstruct only the original pinned references and their calibration inputs."""

    autofill = read_json(DEFAULT_AUTOFILL_PREREG)
    scan_references, audits = load_reference_profiles(autofill)
    perfect = read_json(DEFAULT_PREREG)
    _, references, family, _ = _load_pinned_rows(perfect)
    profiles: dict[str, Any] = {}
    frames: dict[str, pd.DataFrame] = {}
    for row in (*references, *family):
        key = _profile_key(row)
        if key in profiles:
            continue
        source_path = str(row["source_path"])
        if source_path not in frames:
            raw, _ = read_preholdout_prefix(
                _repo_path(source_path), end_exclusive=HOLDOUT_START, bar_minutes=15
            )
            frames[source_path] = add_candidate_features(raw)
        profiles[key] = extract_profile(frames[source_path], row, bar_minutes=15)
    return ScoreContext(
        autofill=autofill,
        perfect_prereg=perfect,
        scan_references=scan_references,
        references=references,
        family=family,
        profiles=profiles,
        reference_audits=audits,
    )


def vectorized_coarse_candidates(
    frame: pd.DataFrame, *, bar_minutes: int, gates: Mapping[str, Any], cutoff: pd.Timestamp
) -> dict[tuple[str, int], np.ndarray]:
    """Return frozen coarse-gate core ends keyed by ``(direction, core_bars)``.

    This is the vectorized form of the original autofill scanner.  The ATR is
    intentionally anchored at ``c + 2`` and the final required continuation is
    ``c + 5``.  It adds no time-frame-specific padding.
    """

    arrays = frame_arrays(frame)
    segment = frame["_segment_id"].to_numpy(dtype=int)
    closes = arrays["close"]
    atr = arrays["atr"]
    max_pre = max(int(value) for value in gates.get("pre_core_context_bars", [12]))
    # The frozen prereg stores render context separately; support direct gates
    # only for unit-level callers without changing formal-run constants.
    n = len(frame)
    output: dict[tuple[str, int], np.ndarray] = {}
    known_close = pd.to_datetime(frame["open_time"], utc=True) + pd.Timedelta(minutes=bar_minutes)
    for core_bars in (4, 5):
        c = np.arange(core_bars - 1 + max_pre, n - 5, dtype=int)
        if not len(c):
            continue
        start = c - core_bars + 1
        anchor = c + 2
        confirmation = c + 5
        valid = (
            (segment[start - max_pre] == segment[confirmation])
            & np.isfinite(atr[anchor])
            & (atr[anchor] > 0.0)
            & (known_close.iloc[confirmation].to_numpy() < cutoff)
        )
        for direction, sign in (("LONG", 1.0), ("SHORT", -1.0)):
            post1 = sign * (closes[c + 1] - closes[c]) / atr[anchor]
            post2 = sign * (closes[c + 2] - closes[c]) / atr[anchor]
            post3 = sign * (closes[c + 3] - closes[c]) / atr[anchor]
            post5 = sign * (closes[c + 5] - closes[c]) / atr[anchor]
            mask = (
                valid
                & (post1 >= float(gates["min_post1_progress_atr"]))
                & (post2 >= float(gates["min_post2_progress_atr"]))
                & (post3 >= float(gates["min_post3_progress_atr"]))
                & (post5 >= float(gates["min_post5_progress_atr"]))
            )
            output[(direction, core_bars)] = c[mask]
    return output


def scan_weak_source(
    frame: pd.DataFrame, spec: Mapping[str, Any], *, cutoff: pd.Timestamp, context: ScoreContext
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Apply the unchanged profile gate after vectorized continuation screening."""

    gates = context.autofill["morphology_gate"]
    # Keep the original render-context boundary as part of the frozen gate.
    gates = {**gates, "pre_core_context_bars": context.autofill["render"]["pre_core_context_bars"]}
    similarity = context.autofill["reference_family"]
    feature_scales = np.asarray(similarity["feature_scales"], dtype=float)
    arrays = frame_arrays(frame)
    counts: Counter[str] = Counter()
    weak: list[dict[str, Any]] = []
    coarse = vectorized_coarse_candidates(frame, bar_minutes=int(spec["bar_minutes"]), gates=gates, cutoff=cutoff)
    candidate_source = {"path": spec["source_path"], "symbol": spec["symbol"], "venue": spec["venue"]}
    for (direction, core_bars), ends in coarse.items():
        counts[f"{direction.lower()}_coarse"] += len(ends)
        for c in ends:
            c = int(c)
            profile = morphology_profile(
                arrays,
                anchor_i=c + 2,
                direction=direction,
                core_start_offset=-core_bars - 1,
                core_end_offset=-2,
            )
            if profile is None or not passes_gate(profile, gates):
                counts[f"{direction.lower()}_gate_reject"] += 1
                continue
            counts[f"{direction.lower()}_gate"] += 1
            distance = profile_distance(
                profile,
                context.scan_references,
                feature_scales=feature_scales,
                feature_weight=float(similarity["feature_weight"]),
                sequence_weight=float(similarity["sequence_weight"]),
            )
            if distance > float(similarity["max_distance"]):
                counts[f"{direction.lower()}_distance_reject"] += 1
                continue
            counts[f"{direction.lower()}_distance"] += 1
            weak.append(candidate_row(
                candidate_source,
                confirm_i=c + 5,
                core_bars=core_bars,
                direction=direction,
                bar_minutes=int(spec["bar_minutes"]),
                distance=distance,
                features=dict(zip(FEATURE_NAMES, map(float, profile.features))),
                frame=frame,
            ))
    return weak, dict(counts)


def _source_output_dir(output_root: Path, spec: Mapping[str, Any]) -> Path:
    # Deliberately omit the expected content SHA from the directory key.  A
    # changed source must encounter its old receipt and be rejected as stale,
    # rather than silently selecting a fresh directory name.
    key = _json_sha({
        "source_path": spec["source_path"], "symbol": spec["symbol"],
        "venue": spec["venue"], "bar_minutes": int(spec["bar_minutes"]),
    })[:16]
    return output_root / f"{spec['venue']}_{spec['symbol']}_{int(spec['bar_minutes'])}m_{key}"


def _receipt_binding(plan_sha: str, spec: Mapping[str, Any]) -> dict[str, Any]:
    source = _repo_path(spec["source_path"])
    actual = sha256_file(source)
    if actual != str(spec["sha256"]):
        raise ProfitMinerError(f"source SHA drift: {spec['source_path']}")
    dependencies = _rule_dependency_hashes()
    return {
        "source_path": str(spec["source_path"]),
        "source_sha256": actual,
        "source_spec_sha256": _json_sha(spec),
        "plan_sha256": plan_sha,
        "miner_sha256": sha256_file(Path(__file__)),
        "rule_dependency_sha256": dependencies,
        "code_sha256": _json_sha({"miner": sha256_file(Path(__file__)), "rules": dependencies}),
    }


def _resume_or_reject(output: Path, binding: Mapping[str, Any]) -> dict[str, Any] | None:
    if not output.exists():
        return None
    receipt_path = output / "receipt.json"
    if not receipt_path.exists():
        raise ProfitMinerError(f"incomplete prior source output cannot resume: {output}")
    receipt = read_json(receipt_path)
    if receipt.get("binding") != dict(binding):
        raise ProfitMinerError(f"stale source output binding: {output}")
    if receipt.get("status") == "failed":
        return receipt
    if receipt.get("status") != "completed":
        raise ProfitMinerError(f"unknown source receipt status: {output}")
    for name, expected in receipt.get("artifacts", {}).items():
        artifact = output / name
        if not artifact.exists() or sha256_file(artifact) != expected:
            raise ProfitMinerError(f"stale or damaged source artifact: {artifact}")
    return receipt


def process_source(
    spec: Mapping[str, Any], *, plan: Mapping[str, Any], plan_sha: str, output_root: Path, context: ScoreContext
) -> dict[str, Any]:
    """Run or resume one source and publish its independent strict evidence."""

    cutoff = _utc(plan["discovery"]["data_end_exclusive"])
    binding = _receipt_binding(plan_sha, spec)
    output = _source_output_dir(output_root, spec)
    prior = _resume_or_reject(output, binding)
    if prior is not None:
        return {
            **prior["summary"],
            "status": str(prior["status"]),
            "resumed": True,
            "output_dir": _relative(output),
        }

    # ``read_preholdout_prefix`` is generic despite its historical name.  The
    # experiment cutoff is the only source boundary; its interval length is
    # carried by the explicit source manifest.
    frame, audit = read_preholdout_prefix(
        _repo_path(spec["source_path"]), end_exclusive=cutoff, bar_minutes=int(spec["bar_minutes"])
    )
    if frame.empty:
        raise ProfitMinerError(f"empty source before plan cutoff: {spec['source_path']}")
    enriched = add_candidate_features(frame)
    weak, counts = scan_weak_source(enriched, spec, cutoff=cutoff, context=context)
    collapsed = pick_4_5(weak)
    source_nms = source_event_nms(collapsed)
    profiles = dict(context.profiles)
    ready: list[dict[str, Any]] = []
    profile_rejections: list[dict[str, Any]] = []
    for row in source_nms:
        confirm_i = int(row["confirm_i"])
        # The strict profile has no handle to bars beyond confirmation.  This
        # slice is stronger than a timestamp assertion and protects callers
        # from accidental future additions to extract_profile.
        visible = enriched.iloc[: confirm_i + 1]
        try:
            profiles[_profile_key(row)] = extract_profile(
                visible,
                row,
                bar_minutes=int(spec["bar_minutes"]),
                visibility_end_exclusive=_utc(row["confirmation_close_utc"]) + pd.Timedelta(nanoseconds=1),
            )
            ready.append(row)
        except PerfectFilterError as exc:
            profile_rejections.append({**row, "profile_reject_reason": str(exc)})
    scored, calibration = _score_all(ready, context.references, context.family, profiles, context.perfect_prereg)
    # _score_all calibrates only the supplied pinned reference/family rows.
    if len(calibration["scored_references"]) != len(context.references):
        raise AssertionError("candidate rows altered frozen calibration references")
    rejected = profile_rejections + [
        row for row in scored
        if row["quality_tier"] == "REJECT" or not bool(row.get("reference_gate_pass"))
    ]
    strict_grade_a = [
        row for row in scored
        if row["quality_tier"] == "PERFECT_CANDIDATE" and bool(row["reference_gate_pass"])
    ]

    output.mkdir(parents=True, exist_ok=False)
    artifacts = {
        "raw_weak_candidates.jsonl": _write_jsonl(output / "raw_weak_candidates.jsonl", weak),
        "endpoint_collapsed.jsonl": _write_jsonl(output / "endpoint_collapsed.jsonl", collapsed),
        "source_nms_60m.jsonl": _write_jsonl(output / "source_nms_60m.jsonl", source_nms),
        "strict_scores.jsonl": _write_jsonl(output / "strict_scores.jsonl", scored),
        "rejections.jsonl": _write_jsonl(output / "rejections.jsonl", rejected),
        "strictGradeA.jsonl": _write_jsonl(output / "strictGradeA.jsonl", strict_grade_a),
        "calibration.json": "",
    }
    _write_json(output / "calibration.json", calibration)
    artifacts["calibration.json"] = sha256_file(output / "calibration.json")
    summary = {
        "source_path": spec["source_path"], "symbol": spec["symbol"], "venue": spec["venue"],
        "bar_minutes": int(spec["bar_minutes"]), "rows_materialized": len(frame),
        "non_bar_gaps": int(audit["non_bar_gaps"]), "raw_weak_candidates": len(weak),
        "endpoint_collapsed": len(collapsed), "source_nms_60m": len(source_nms),
        "profile_rejections": len(profile_rejections), "strict_scored": len(scored),
        "strict_grade_a": len(strict_grade_a), "coarse_counts": counts,
        "calibration_reference_rows": len(calibration["scored_references"]),
        "candidate_rows_used_for_calibration": 0,
    }
    _write_json(
        output / "receipt.json",
        {"status": "completed", "binding": binding, "summary": summary, "artifacts": artifacts},
    )
    return {**summary, "status": "completed", "resumed": False, "output_dir": _relative(output)}


def _failed_binding(plan_sha: str, spec: Mapping[str, Any]) -> dict[str, Any]:
    """Record the attempted input identity even when source validation fails."""

    source = _repo_path(spec["source_path"])
    actual = sha256_file(source) if source.exists() else None
    dependencies = _rule_dependency_hashes()
    return {
        "source_path": str(spec["source_path"]),
        "source_sha256": actual,
        "source_sha256_expected": str(spec["sha256"]),
        "source_spec_sha256": _json_sha(spec),
        "plan_sha256": plan_sha,
        "miner_sha256": sha256_file(Path(__file__)),
        "rule_dependency_sha256": dependencies,
        "code_sha256": _json_sha({"miner": sha256_file(Path(__file__)), "rules": dependencies}),
    }


def _record_failed_source(
    spec: Mapping[str, Any], *, plan_sha: str, output_root: Path, error: Exception
) -> dict[str, Any]:
    """Persist a source-local failure without damaging a completed prior receipt."""

    output = _source_output_dir(output_root, spec)
    existed = output.exists()
    output.mkdir(parents=True, exist_ok=True)
    binding = _failed_binding(plan_sha, spec)
    receipt = {
        "status": "failed",
        "binding": binding,
        "summary": {
            "source_path": str(spec["source_path"]),
            "symbol": str(spec["symbol"]),
            "venue": str(spec["venue"]),
            "bar_minutes": int(spec["bar_minutes"]),
            "error_type": type(error).__name__,
            "error": str(error),
        },
        "artifacts": {},
    }
    receipt_path = output / "receipt.json"
    if existed:
        # A stale or incomplete input must not be converted into a successful
        # or resumable receipt.  Its failure is separately named by this
        # attempt's complete binding.
        receipt_path = output / f"failed_receipt_{_json_sha(binding)[:16]}.json"
    _write_json(receipt_path, receipt)
    return {
        **receipt["summary"],
        "status": "failed",
        "resumed": False,
        "output_dir": _relative(output),
        "receipt_path": _relative(receipt_path),
    }


def _process_source_safely(
    spec: Mapping[str, Any], *, plan: Mapping[str, Any], plan_sha: str,
    output_root: Path, context: ScoreContext,
) -> dict[str, Any]:
    try:
        return process_source(
            spec, plan=plan, plan_sha=plan_sha, output_root=output_root, context=context
        )
    except Exception as exc:  # Source-level failures must not stop other sources.
        return _record_failed_source(spec, plan_sha=plan_sha, output_root=output_root, error=exc)


def _initialize_worker(
    context: ScoreContext, plan: Mapping[str, Any], plan_sha: str, output_root: Path
) -> None:
    """Install the once-pickled reference state for one process-pool worker."""

    global _WORKER_CONTEXT, _WORKER_PLAN, _WORKER_PLAN_SHA, _WORKER_OUTPUT_ROOT
    _WORKER_CONTEXT = context
    _WORKER_PLAN = plan
    _WORKER_PLAN_SHA = plan_sha
    _WORKER_OUTPUT_ROOT = output_root


def _process_source_worker(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Pickleable process-pool entry point; configuration arrives via initializer."""

    if any(value is None for value in (
        _WORKER_CONTEXT, _WORKER_PLAN, _WORKER_PLAN_SHA, _WORKER_OUTPUT_ROOT
    )):
        raise RuntimeError("profit miner worker was not initialized")
    return _process_source_safely(
        spec,
        plan=_WORKER_PLAN,
        plan_sha=_WORKER_PLAN_SHA,
        output_root=_WORKER_OUTPUT_ROOT,
        context=_WORKER_CONTEXT,
    )


def build(plan_path: Path, sources_path: Path, *, workers: int = 4) -> dict[str, Any]:
    """Run committed source batches; cross-timeframe outcome dedup remains external."""

    if not 1 <= workers <= 4:
        raise ProfitMinerError("workers must be between 1 and 4")
    plan_path, sources_path = plan_path.resolve(), sources_path.resolve()
    plan = read_json(plan_path)
    if plan.get("experiment_id") != "exp-ma-profit3r-20260922-v1":
        raise ProfitMinerError("wrong experiment plan")
    if not bool(plan["owner_authorization"]["all_historical_data_authorized"]):
        raise ProfitMinerError("plan does not authorize historical source scans")
    sources = load_sources(sources_path)
    commit = _assert_committed((Path(__file__), *RULE_DEPENDENCY_PATHS, plan_path, sources_path))
    output_root = plan_path.parent / DEFAULT_OUTPUT_NAME
    output_root.mkdir(exist_ok=True)
    plan_sha = sha256_file(plan_path)
    sources_sha = sha256_file(sources_path)
    master_path = output_root / f"master_{sources_path.stem}.json"
    if master_path.exists():
        existing = read_json(master_path)
        if existing.get("sources_manifest_sha256") != sources_sha:
            raise ProfitMinerError(
                f"refusing to overwrite master for a different sources manifest: {master_path}"
            )
    context = load_score_context()
    if workers == 1:
        summaries = []
        for index, spec in enumerate(sources, 1):
            summaries.append(_process_source_safely(
                spec, plan=plan, plan_sha=plan_sha, output_root=output_root, context=context
            ))
            if index == 1 or index % 10 == 0 or index == len(sources):
                print(f"profit miner completed {index}/{len(sources)} sources", flush=True)
    else:
        summaries = []
        with ProcessPoolExecutor(
            max_workers=workers,
            initializer=_initialize_worker,
            initargs=(context, plan, plan_sha, output_root),
        ) as executor:
            futures = [executor.submit(_process_source_worker, spec) for spec in sources]
            for index, future in enumerate(as_completed(futures), 1):
                # The worker catches source errors.  A pool-level exception is
                # still surfaced because no trustworthy per-source receipt can
                # then be claimed for the interrupted source.
                summaries.append(future.result())
                if index == 1 or index % 10 == 0 or index == len(sources):
                    print(f"profit miner completed {index}/{len(sources)} sources", flush=True)
    summaries.sort(key=lambda row: (row["symbol"], row["venue"], row["bar_minutes"], row["source_path"]))
    completed = [row for row in summaries if row["status"] == "completed"]
    failed = [row for row in summaries if row["status"] == "failed"]
    master = {
        "builder_commit": commit, "plan_sha256": plan_sha, "sources_manifest_sha256": sources_sha,
        "miner_sha256": sha256_file(Path(__file__)), "rule_dependency_sha256": _rule_dependency_hashes(),
        "attempted_sources": len(summaries), "completed_sources": len(completed), "failed_sources": len(failed),
        "workers": workers,
        "raw_weak_candidates": sum(int(row["raw_weak_candidates"]) for row in completed),
        "source_nms_60m": sum(int(row["source_nms_60m"]) for row in completed),
        "strict_grade_a_before_cross_timeframe_dedup": sum(int(row["strict_grade_a"]) for row in completed),
        "cross_timeframe_profit_dedup": "not performed; assigned to the outcome-label stage",
        "source_coverage_complete": not failed and len(completed) == len(sources),
        "training_gate_pass": not failed and len(completed) == len(sources),
        "training_eligible": False, "production_eligible": False,
        "source_summaries": completed, "errors": failed,
    }
    _write_json(master_path, master)
    return master


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--sources", required=True, type=Path)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    print(json.dumps(build(args.plan, args.sources, workers=args.workers), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
