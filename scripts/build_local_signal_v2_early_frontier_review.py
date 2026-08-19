#!/usr/bin/env python3
"""Build a blind 300-item review for the early SHORT semantic frontier.

The retrieval reference is the 11 YES versus 89 NO Owner verdicts from the
completed causal Canary review.  Candidate events are the previously
unreviewed remainder of ten frozen pre-holdout R1 scan blocks.  All 700 events
shown in earlier Owner pages are excluded before ranking. Candidate ranking
uses only OHLC and SMA/EMA 20/60/120 values through each decision bar;
the following 48 bars are loaded only after selection and rendered into a
physically separate Owner-review image.

This is a discovery review, not an independent model evaluation.  It creates
no training labels, never makes a row training-eligible, does not read holdout,
and does not mutate weights, confidence, NMS, ACTIVE, deployment, or trading.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from yoyo.layers.l1_detection.data import add_mas  # noqa: E402

from scripts.backtest_owner_short_gold_center_recent import (  # noqa: E402
    HOLDOUT_START,
    load_snapshot,
    read_jsonl,
    sha256_file,
    write_jsonl,
)
from scripts.build_local_signal_v2_semantic_review import (  # noqa: E402
    DEFAULT_R1_EVENTS,
    DEFAULT_R2_EVENTS,
    DEFAULT_SNAPSHOT,
    json_sha256,
    portable_artifact_path,
    render_canary,
    render_review_html,
    stable_hash,
)
from scripts.build_owner_short_train_hardneg_review import (  # noqa: E402
    causal_feature_vector,
    mean_knn_distance,
    utc,
)


PROTOCOL = "local_signal_v2_early_frontier_review300_v1_20260812"
SEED = 20260812
REVIEW_TOTAL = 300
YES_LIKE_TOTAL = 150
SIMILAR_NO_TOTAL = 150
FUTURE_REVIEW_BARS = 48
EXPECTED_R1_SHA256 = "029f80a52b5beda2e32f6bb5a188a39fd7f74fe0a3fef4dffa79ae620384f537"
R1_WEIGHTS = (
    ROOT
    / "analysis/output/lsv2_stageb"
    / "owner_lsv2_short_gold_center_hardneg_r1_ft/weights/best.pt"
)
REFERENCE_JOINED = (
    ROOT
    / "analysis/output/local_signal_v2_positive_semantic_review200_v2"
    / "owner_review_joined.jsonl"
)
BOUNDARY_FEATURES = (
    ROOT
    / "analysis/output/local_signal_v2_semantic_boundary_diagnosis_20260812"
    / "boundary_features.jsonl"
)
DEFAULT_OUT = ROOT / "analysis/output/local_signal_v2_early_frontier_review300_v1"
BLOCKS = (
    ("B01_20250715", "2025-07-15T12:00:00Z"),
    ("B02_20250915", "2025-09-15T12:00:00Z"),
    ("B03_20251115", "2025-11-15T12:00:00Z"),
    ("B04_20260115", "2026-01-15T12:00:00Z"),
    ("B05_20260301", "2026-03-01T12:00:00Z"),
    ("C01_20250615", "2025-06-15T12:00:00Z"),
    ("C02_20250815", "2025-08-15T12:00:00Z"),
    ("C03_20251015", "2025-10-15T12:00:00Z"),
    ("C04_20251215", "2025-12-15T12:00:00Z"),
    ("C05_20260215", "2026-02-15T12:00:00Z"),
)
BLOCK_ROOT_V1 = ROOT / "analysis/output/owner_short_train_hardneg_blocks_v1"
BLOCK_ROOT_V2 = ROOT / "analysis/output/owner_short_train_hardneg_blocks_v2"
SOURCE_POOLS = (
    ROOT / "analysis/output/owner_short_train_hardneg_review200_v1/candidate_pool.jsonl",
    ROOT / "analysis/output/owner_short_train_hardneg_newblocks200_v3/candidate_pool.jsonl",
)
PRIOR_REVIEW_MANIFESTS = (
    ROOT / "analysis/output/owner_short_train_hardneg_review200_v1/review_manifest.jsonl",
    ROOT / "analysis/output/owner_short_train_positive_retrieval100_v1/review_manifest.jsonl",
    ROOT / "analysis/output/owner_short_train_hardneg_expansion200_v2/review_manifest.jsonl",
    ROOT / "analysis/output/owner_short_train_hardneg_newblocks200_v3/review_manifest.jsonl",
)


def block_specs(root: Path | None = None) -> list[dict[str, Any]]:
    """Return the ten frozen pre-holdout scan block contracts."""
    specs: list[dict[str, Any]] = []
    for block_id, scan_end_value in BLOCKS:
        scan_end = utc(scan_end_value)
        audit_end = scan_end + pd.Timedelta(minutes=15 * FUTURE_REVIEW_BARS)
        base = (
            root / block_id
            if root is not None
            else (BLOCK_ROOT_V1 if block_id.startswith("B") else BLOCK_ROOT_V2) / block_id
        )
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


def allocate_block_quotas(
    available: dict[str, int],
    *,
    total: int = REVIEW_TOTAL,
) -> dict[str, int]:
    """Allocate nearly equal block quotas and redistribute sparse shortfalls."""
    ordered = [block_id for block_id, _end in BLOCKS]
    base = total // len(ordered)
    quotas = {block_id: min(base, int(available.get(block_id, 0))) for block_id in ordered}
    remaining = total - sum(quotas.values())
    while remaining:
        progressed = False
        for block_id in ordered:
            if quotas[block_id] >= int(available.get(block_id, 0)):
                continue
            quotas[block_id] += 1
            remaining -= 1
            progressed = True
            if not remaining:
                break
        if not progressed:
            raise ValueError(f"need {total} candidates, only {sum(available.values())} available")
    return quotas


def allocate_stratum_quotas(block_quotas: dict[str, int]) -> dict[str, dict[str, int]]:
    """Split block quotas into exactly 150 YES-like and 150 boundary rows."""
    yes = {block_id: quota // 2 for block_id, quota in block_quotas.items()}
    remaining = YES_LIKE_TOTAL - sum(yes.values())
    for block_id, _end in BLOCKS:
        if not remaining:
            break
        if yes[block_id] < block_quotas[block_id]:
            yes[block_id] += 1
            remaining -= 1
    if remaining:
        raise ValueError("could not allocate exact YES-like quota")
    result = {
        block_id: {
            "yes_like": yes[block_id],
            "similar_no_boundary": block_quotas[block_id] - yes[block_id],
        }
        for block_id in block_quotas
    }
    if sum(value["similar_no_boundary"] for value in result.values()) != SIMILAR_NO_TOTAL:
        raise ValueError("could not allocate exact similar-NO quota")
    return result


def _take_diverse(
    ranked: list[dict[str, Any]],
    quota: int,
    *,
    excluded: set[str],
) -> list[dict[str, Any]]:
    """Take deterministic rows while limiting one symbol from dominating a block."""
    if quota == 0:
        return []
    chosen: list[dict[str, Any]] = []
    chosen_ids: set[str] = set()
    symbol_counts: Counter[str] = Counter()
    for cap in (1, 2, 3, 5, max(quota, 1)):
        for row in ranked:
            event_id = str(row["event_id"])
            symbol = str(row["symbol"])
            if event_id in excluded or event_id in chosen_ids or symbol_counts[symbol] >= cap:
                continue
            chosen.append(row)
            chosen_ids.add(event_id)
            symbol_counts[symbol] += 1
            if len(chosen) == quota:
                return chosen
    raise ValueError(f"diverse selector produced {len(chosen)} / {quota}")


def select_review_rows(
    rows: list[dict[str, Any]],
    *,
    total: int = REVIEW_TOTAL,
) -> tuple[list[dict[str, Any]], dict[str, int], dict[str, dict[str, int]]]:
    """Select balanced YES-like enrichment plus YES-adjacent NO-side rows."""
    by_block: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_block[str(row["candidate_block"])].append(row)
    block_quotas = allocate_block_quotas(
        {block_id: len(values) for block_id, values in by_block.items()}, total=total
    )
    stratum_quotas = allocate_stratum_quotas(block_quotas)
    selected: list[dict[str, Any]] = []
    for block_id, _end in BLOCKS:
        cohort = by_block[block_id]
        yes_ranked = sorted(
            cohort,
            key=lambda row: (
                -float(row["owner_yes_affinity"]),
                float(row["nearest_owner_yes_distance"]),
                str(row["event_id"]),
            ),
        )
        yes_rows = _take_diverse(
            yes_ranked,
            stratum_quotas[block_id]["yes_like"],
            excluded=set(),
        )
        used = {str(row["event_id"]) for row in yes_rows}
        no_side = [
            row
            for row in cohort
            if float(row["owner_yes_affinity"]) <= 0 and str(row["event_id"]) not in used
        ]
        no_side.sort(
            key=lambda row: (
                float(row["nearest_owner_yes_distance"]),
                abs(float(row["owner_yes_affinity"])),
                str(row["event_id"]),
            )
        )
        fallback = sorted(
            (row for row in cohort if str(row["event_id"]) not in used),
            key=lambda row: (
                abs(float(row["owner_yes_affinity"])),
                float(row["nearest_owner_yes_distance"]),
                str(row["event_id"]),
            ),
        )
        ranked_boundary = no_side + [
            row for row in fallback if str(row["event_id"]) not in {str(item["event_id"]) for item in no_side}
        ]
        boundary_rows = _take_diverse(
            ranked_boundary,
            stratum_quotas[block_id]["similar_no_boundary"],
            excluded=used,
        )
        for row in yes_rows:
            row["retrieval_stratum_internal"] = "yes_like"
        for row in boundary_rows:
            row["retrieval_stratum_internal"] = "similar_no_boundary"
        selected.extend(yes_rows + boundary_rows)
    if len(selected) != total or len({str(row["event_id"]) for row in selected}) != total:
        raise ValueError("review selection is not exactly unique total")
    counts = Counter(str(row["retrieval_stratum_internal"]) for row in selected)
    if counts != Counter({"yes_like": YES_LIKE_TOTAL, "similar_no_boundary": SIMILAR_NO_TOTAL}):
        raise ValueError(f"retrieval stratum count drift: {counts}")
    return selected, block_quotas, stratum_quotas


def _load_enriched(path: Path) -> pd.DataFrame:
    return add_mas(load_snapshot(path))


def _reference_vectors() -> tuple[np.ndarray, np.ndarray, dict[str, int]]:
    joined = read_jsonl(REFERENCE_JOINED)
    r1 = {str(row["event_id"]): row for row in read_jsonl(DEFAULT_R1_EVENTS)}
    r2 = {str(row["event_id"]): row for row in read_jsonl(DEFAULT_R2_EVENTS)}
    frames: dict[str, pd.DataFrame] = {}
    yes: list[np.ndarray] = []
    no: list[np.ndarray] = []
    for review in joined:
        if str(review["source_type"]) != "canary_candidate":
            continue
        verdict = str(review["owner_verdict"])
        if verdict not in {"YES", "NO"}:
            continue
        original = (r1 if str(review["source_model"]) == "R1" else r2)[str(review["event_id"])]
        symbol = str(original["symbol"])
        if symbol not in frames:
            frames[symbol] = _load_enriched(DEFAULT_SNAPSHOT / f"{symbol}.csv")
        vector = causal_feature_vector(original, frames[symbol])
        (yes if verdict == "YES" else no).append(vector)
    if (len(yes), len(no)) != (11, 89):
        raise ValueError(f"Canary semantic reference drift: {(len(yes), len(no))}")
    return np.vstack(yes), np.vstack(no), {"YES": len(yes), "NO": len(no)}


def _validate_block(spec: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    scan_summary_path = Path(spec["merged_scan"]) / "scan_summary.json"
    events_path = Path(spec["merged_scan"]) / "events.jsonl"
    scan_snapshot_summary_path = Path(spec["scan_snapshot"]) / "fetch_summary.json"
    audit_snapshot_summary_path = Path(spec["audit_snapshot"]) / "fetch_summary.json"
    scan = json.loads(scan_summary_path.read_text(encoding="utf-8"))
    scan_snapshot = json.loads(scan_snapshot_summary_path.read_text(encoding="utf-8"))
    audit_snapshot = json.loads(audit_snapshot_summary_path.read_text(encoding="utf-8"))
    if scan.get("evaluation_scope") != "train_hardneg_mining":
        raise ValueError(f"{spec['block_id']}: scan scope drift")
    if scan_snapshot.get("evaluation_scope") != "train_hardneg_mining":
        raise ValueError(f"{spec['block_id']}: scan snapshot scope drift")
    if audit_snapshot.get("evaluation_scope") != "train_hardneg_mining":
        raise ValueError(f"{spec['block_id']}: audit snapshot scope drift")
    if str(scan.get("weights_sha256")) != EXPECTED_R1_SHA256:
        raise ValueError(f"{spec['block_id']}: R1 weight drift")
    if utc(scan["latest_bar"]) != utc(spec["scan_end"]):
        raise ValueError(f"{spec['block_id']}: scan endpoint drift")
    if utc(scan_snapshot["snapshot_end"]) != utc(spec["scan_end"]):
        raise ValueError(f"{spec['block_id']}: scan snapshot endpoint drift")
    if utc(audit_snapshot["snapshot_end"]) != utc(spec["audit_end"]):
        raise ValueError(f"{spec['block_id']}: audit endpoint drift")
    if int(scan_snapshot.get("holdout_rows_materialized", -1)) != 0:
        raise ValueError(f"{spec['block_id']}: scan snapshot holdout proof missing")
    if int(audit_snapshot.get("holdout_rows_materialized", -1)) != 0:
        raise ValueError(f"{spec['block_id']}: audit snapshot holdout proof missing")
    if utc(audit_snapshot["max_materialized_time"]) >= HOLDOUT_START:
        raise ValueError(f"{spec['block_id']}: audit snapshot touches holdout")
    events = read_jsonl(events_path)
    return events, {
        "events": len(events),
        "symbols": int(scan["symbols"]),
        "bar_endpoints": int(scan["bar_endpoints"]),
        "window_exposures": int(scan["window_exposures"]),
        "raw_detections": int(scan["raw_detections"]),
        "scan_end": utc(spec["scan_end"]).isoformat(),
        "audit_end": utc(spec["audit_end"]).isoformat(),
        "events_sha256": sha256_file(events_path),
        "scan_summary_sha256": sha256_file(scan_summary_path),
        "scan_snapshot_summary_sha256": sha256_file(scan_snapshot_summary_path),
        "audit_snapshot_summary_sha256": sha256_file(audit_snapshot_summary_path),
    }


def _candidate_pool(
    specs: list[dict[str, Any]],
    yes_reference: np.ndarray,
    no_reference: np.ndarray,
    reviewed_ids: set[str],
) -> tuple[list[dict[str, Any]], dict[str, Any], Counter[str]]:
    joint = np.vstack([yes_reference, no_reference])
    mean = joint.mean(axis=0)
    scale = joint.std(axis=0)
    scale[scale < 1e-8] = 1.0
    yes_scaled = (yes_reference - mean) / scale
    no_scaled = (no_reference - mean) / scale
    pool: list[dict[str, Any]] = []
    vectors: list[np.ndarray] = []
    block_audit: dict[str, Any] = {}
    skips: Counter[str] = Counter()
    seen_events: set[str] = set()
    source_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for path in SOURCE_POOLS:
        for row in read_jsonl(path):
            source_rows[str(row["candidate_block"])].append(row)
    for spec in specs:
        _events, audit = _validate_block(spec)
        block_id = str(spec["block_id"])
        events = source_rows.get(block_id, [])
        audit["source_candidate_rows"] = len(events)
        block_audit[block_id] = audit
        frames: dict[str, pd.DataFrame] = {}
        for event in events:
            event_id = str(event["event_id"])
            if event_id in reviewed_ids:
                skips["previously_reviewed"] += 1
                continue
            if event_id in seen_events:
                skips["duplicate_event_id"] += 1
                continue
            seen_events.add(event_id)
            if utc(event["decision_time"]) > utc(spec["scan_end"]):
                skips["decision_after_scan_end"] += 1
                continue
            symbol = str(event["symbol"])
            path = Path(spec["scan_snapshot"]) / "kline_snapshot" / f"{symbol}.csv"
            if not path.is_file():
                skips["missing_scan_symbol"] += 1
                continue
            if symbol not in frames:
                frames[symbol] = _load_enriched(path)
            try:
                vector = causal_feature_vector(event, frames[symbol])
            except (KeyError, ValueError):
                skips["causal_feature_unavailable"] += 1
                continue
            pool.append(
                {
                    **event,
                    "candidate_block": block_id,
                    "source_model_internal": "R1",
                    "source_dataset_internal": "owner_short_train_hardneg_unreviewed_remainder",
                    "model_confidence_internal": float(event["event_conf_max"]),
                    "future_review_bars_internal": FUTURE_REVIEW_BARS,
                    "selection_future_used": False,
                    "selection_max_visible_time": str(event["decision_time"]),
                    "training_eligible": False,
                    "holdout_read": False,
                }
            )
            vectors.append(vector)
    if not pool:
        raise ValueError("no early-frontier candidate survived validation")
    query = (np.vstack(vectors) - mean) / scale
    yes_distance = mean_knn_distance(query, yes_scaled, k=5)
    no_distance = mean_knn_distance(query, no_scaled, k=5)
    for row, yes_d, no_d in zip(pool, yes_distance, no_distance):
        row["nearest_owner_yes_distance"] = float(yes_d)
        row["nearest_owner_no_distance"] = float(no_d)
        row["owner_yes_affinity"] = float(no_d - yes_d)
    return pool, block_audit, skips


def _write_review_artifacts(
    selected: list[dict[str, Any]],
    specs: list[dict[str, Any]],
    output: Path,
) -> list[dict[str, Any]]:
    blind = sorted(
        selected,
        key=lambda row: stable_hash(PROTOCOL, SEED, "blind", row["candidate_block"], row["event_id"]),
    )
    spec_by_id = {str(spec["block_id"]): spec for spec in specs}
    frames: dict[tuple[str, str], pd.DataFrame] = {}
    rendered: list[dict[str, Any]] = []
    for number, row in enumerate(blind, 1):
        review_id = f"S{number:03d}"
        block_id = str(row["candidate_block"])
        symbol = str(row["symbol"])
        key = (block_id, symbol)
        if key not in frames:
            path = Path(spec_by_id[block_id]["audit_snapshot"]) / "kline_snapshot" / f"{symbol}.csv"
            frames[key] = load_snapshot(path)
        frame = frames[key]
        decision = int(row["decision_i"])
        if utc(frame["open_time"].iloc[decision]) != utc(row["decision_time"]):
            raise ValueError(f"audit snapshot index drift: {block_id} {row['event_id']}")
        if decision + FUTURE_REVIEW_BARS >= len(frame):
            raise ValueError(f"missing 48-bar future context: {block_id} {row['event_id']}")
        item = dict(row)
        item.update(
            {
                "canary_cohort_internal": str(row["retrieval_stratum_internal"]),
                "confidence_stratum_internal": "hidden",
                "time_stratum_internal": block_id,
                "volatility_stratum_internal": "hidden",
            }
        )
        review = render_canary(item, frame, output, review_id)
        review.update(
            {
                "review_id": review_id,
                "source_type": "early_frontier_candidate",
                "source_dataset": "owner_short_train_hardneg_unreviewed_remainder",
                "source_manifest_reference": portable_artifact_path(
                    Path(spec_by_id[block_id]["merged_scan"]) / "events.jsonl"
                ),
                "canary_cohort": None,
                "candidate_block": block_id,
                "retrieval_stratum_internal": str(row["retrieval_stratum_internal"]),
                "nearest_owner_yes_distance_internal": float(row["nearest_owner_yes_distance"]),
                "nearest_owner_no_distance_internal": float(row["nearest_owner_no_distance"]),
                "owner_yes_affinity_internal": float(row["owner_yes_affinity"]),
                "selection_future_used": False,
                "future_bars": 0,
                "owner_verdict": None,
                "reviewed_at": None,
                "training_eligible": False,
                "production_eligible": False,
                "holdout_read": False,
            }
        )
        rendered.append(review)
        if number % 25 == 0 or number == len(blind):
            print(f"early-frontier review render [{number}/{len(blind)}]", flush=True)
    return rendered


def _causality_audit(rows: list[dict[str, Any]], output: Path) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for row in rows:
        image = ROOT / str(row["image_path"])
        model_input = ROOT / str(row["model_input_path"])
        future = ROOT / str(row["future_review_path"])
        checks = {
            "visible_end_equals_decision": int(row["visible_end_bar"]) == int(row["decision_bar"]),
            "future_bars_zero": int(row["future_bars"]) == 0,
            "selection_future_unused": not bool(row["selection_future_used"]),
            "causal_image_sha_matches": image.is_file() and sha256_file(image) == row["image_sha256"],
            "model_input_sha_matches": model_input.is_file()
            and sha256_file(model_input) == row["model_input_sha256"],
            "future_image_sha_matches": future.is_file()
            and sha256_file(future) == row["future_review_sha256"],
            "future_review_is_separate": bool(row["future_review_only"]),
            "future_review_exactly_48": int(row["future_review_bars"]) == FUTURE_REVIEW_BARS,
            "future_review_before_holdout": utc(row["future_review_end_time"]) < HOLDOUT_START,
            "holdout_clean": not bool(row["holdout_read"]),
        }
        items.append({"review_id": row["review_id"], **checks, "pass": all(checks.values())})
    result = {
        "protocol": PROTOCOL,
        "rows": len(items),
        "all_pass": all(item["pass"] for item in items),
        "holdout_read": False,
        "items": items,
    }
    (output / "causality_audit.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def build(
    *,
    block_root: Path | None = None,
    output: Path = DEFAULT_OUT,
    frozen_main_commit: str,
) -> dict[str, Any]:
    """Build the frozen review package and all machine-readable audits."""
    if output.exists():
        raise FileExistsError(f"refusing to overwrite {output}")
    if sha256_file(R1_WEIGHTS) != EXPECTED_R1_SHA256:
        raise ValueError("frozen R1 weight SHA drift")
    subprocess.run(
        ["git", "cat-file", "-e", f"{frozen_main_commit}^{{commit}}"], cwd=ROOT, check=True
    )
    builder_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    yes_reference, no_reference, reference_counts = _reference_vectors()
    reviewed_ids = {
        str(row["event_id"])
        for path in PRIOR_REVIEW_MANIFESTS
        for row in read_jsonl(path)
    }
    if len(reviewed_ids) != 700:
        raise ValueError(f"prior reviewed event count drift: {len(reviewed_ids)}")
    specs = block_specs(block_root)
    pool, block_audit, skips = _candidate_pool(
        specs, yes_reference, no_reference, reviewed_ids
    )
    selected, block_quotas, stratum_quotas = select_review_rows(pool)

    output.mkdir(parents=True, exist_ok=False)
    pool_path = output / "candidate_pool.jsonl"
    selected_path = output / "selected_candidates.jsonl"
    write_jsonl(pool_path, pool)
    write_jsonl(selected_path, selected)
    rows = _write_review_artifacts(selected, specs, output)
    manifest = output / "review_manifest.jsonl"
    write_jsonl(manifest, rows)
    html_path = output / "index.html"
    html_path.write_text(render_review_html(rows, html_path), encoding="utf-8")
    readme = output / "README.md"
    readme.write_text(
        """# Local Signal V2 · 早期启动前沿 300 张审核

本包是发现集，不是独立验证集。内部用上一轮 Canary 的 11 YES / 89 NO 做因果相似度检索，
页面不显示检索分层、模型置信度或推荐答案。

启动：

```bash
cd /Users/zhangzc/fable-trading
PYTHONPATH=.:/Users/zhangzc/yoyo-trading .venv/bin/python scripts/serve_local_signal_v2_semantic_review.py --out analysis/output/local_signal_v2_early_frontier_review300_v1 --port 8766
```

浏览器打开 `http://127.0.0.1:8766/`。Y=YES，N=NO，S=SKIP，左右键切换。
每次判断追加保存到 `owner_verdicts.jsonl`，可中断继续和修改。左图止于 decision；
右图未来48根只供人工对照，不进入检索、模型输入或训练。
""",
        encoding="utf-8",
    )
    causality = _causality_audit(rows, output)
    selected_counts = Counter(str(row["candidate_block"]) for row in rows)
    retrieval_counts = Counter(str(row["retrieval_stratum_internal"]) for row in rows)
    sampling = {
        "protocol": PROTOCOL,
        "seed": SEED,
        "reference_counts": reference_counts,
        "reference_role": "discovery retrieval only; not an independent validation target",
        "prior_reviewed_unique_events_excluded": len(reviewed_ids),
        "candidate_population": len(pool),
        "candidate_skips": dict(skips),
        "selected": len(rows),
        "selected_by_block": dict(sorted(selected_counts.items())),
        "block_quotas": block_quotas,
        "retrieval_stratum_quotas": stratum_quotas,
        "selected_by_retrieval_stratum_internal": dict(sorted(retrieval_counts.items())),
        "selected_symbols": len({str(row["symbol"]) for row in rows}),
        "source_block_ids": [block_id for block_id, _end in BLOCKS],
        "selected_overlap_with_prior_reviews": len(
            {str(row["event_id"]) for row in rows} & reviewed_ids
        ),
        "selection_future_used": False,
        "future_loaded_after_selection_only": True,
        "owner_ui_blinded_fields": [
            "retrieval_stratum",
            "model_confidence",
            "owner_yes_affinity",
            "source_block",
        ],
        "owner_verdicts_preselected": 0,
    }
    (output / "sampling_audit.json").write_text(
        json.dumps(sampling, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    freeze = {
        "protocol": PROTOCOL,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "audit_start_main_commit": frozen_main_commit,
        "builder_commit": builder_commit,
        "weights": {"r1": {"path": portable_artifact_path(R1_WEIGHTS), "sha256": sha256_file(R1_WEIGHTS)}},
        "input_sha256": {
            portable_artifact_path(REFERENCE_JOINED): sha256_file(REFERENCE_JOINED),
            portable_artifact_path(BOUNDARY_FEATURES): sha256_file(BOUNDARY_FEATURES),
            portable_artifact_path(DEFAULT_R1_EVENTS): sha256_file(DEFAULT_R1_EVENTS),
            portable_artifact_path(DEFAULT_R2_EVENTS): sha256_file(DEFAULT_R2_EVENTS),
            **{
                portable_artifact_path(path): sha256_file(path)
                for path in SOURCE_POOLS + PRIOR_REVIEW_MANIFESTS
            },
            **{
                f"{block_id}:events": audit["events_sha256"]
                for block_id, audit in block_audit.items()
            },
        },
        "block_audit": block_audit,
        "canary_contract": {
            "confidence": 0.25,
            "nms_iou": 0.70,
            "window_lengths": list(range(12, 20)),
            "event_gap_bars": 5,
        },
        "holdout": {
            "start": HOLDOUT_START.isoformat(),
            "use_number": 0,
            "rows_materialized": 0,
            "latest_future_review_time": max(str(row["future_review_end_time"]) for row in rows),
        },
        "prohibitions_observed": {
            "new_model_training": False,
            "r3_or_r4_created": False,
            "weights_modified": False,
            "confidence_modified": False,
            "nms_modified": False,
            "positive_labels_modified": False,
            "active_modified": False,
            "deployed": False,
            "orders": False,
            "forward_log_cleared": False,
            "new_holdout_read": False,
        },
    }
    (output / "freeze_receipt.json").write_text(
        json.dumps(freeze, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    summary = {
        "protocol": PROTOCOL,
        "rows": len(rows),
        "candidate_population": len(pool),
        "reference_canary_yes": reference_counts["YES"],
        "reference_canary_no": reference_counts["NO"],
        "yes_like_retrieval": retrieval_counts["yes_like"],
        "similar_no_boundary_retrieval": retrieval_counts["similar_no_boundary"],
        "unique_review_ids": len({row["review_id"] for row in rows}),
        "unique_event_ids": len({row["event_id"] for row in rows}),
        "unique_causal_image_sha256": len({row["image_sha256"] for row in rows}),
        "future_review_images": len({row["future_review_path"] for row in rows}),
        "owner_verdicts_preselected": sum(row["owner_verdict"] is not None for row in rows),
        "training_eligible": sum(bool(row["training_eligible"]) for row in rows),
        "production_eligible": False,
        "holdout_read": False,
        "manifest": portable_artifact_path(manifest),
        "manifest_sha256": sha256_file(manifest),
        "candidate_pool_sha256": sha256_file(pool_path),
        "selected_candidates_sha256": sha256_file(selected_path),
        "causal_image_tree_sha256": json_sha256(sorted(row["image_sha256"] for row in rows)),
        "model_input_tree_sha256": json_sha256(sorted(row["model_input_sha256"] for row in rows)),
        "future_review_tree_sha256": json_sha256(sorted(row["future_review_sha256"] for row in rows)),
        "review_html": portable_artifact_path(html_path),
        "review_html_sha256": sha256_file(html_path),
        "sampling_audit_sha256": sha256_file(output / "sampling_audit.json"),
        "causality_audit_sha256": sha256_file(output / "causality_audit.json"),
        "freeze_receipt_sha256": sha256_file(output / "freeze_receipt.json"),
        "readme_sha256": sha256_file(readme),
        "quality_gates": {
            "exactly_300": len(rows) == REVIEW_TOTAL,
            "unique_300_events": len({row["event_id"] for row in rows}) == REVIEW_TOTAL,
            "unique_300_review_ids": len({row["review_id"] for row in rows}) == REVIEW_TOTAL,
            "retrieval_150_plus_150": retrieval_counts
            == Counter({"yes_like": YES_LIKE_TOTAL, "similar_no_boundary": SIMILAR_NO_TOTAL}),
            "all_events_unreviewed": not (
                {str(row["event_id"]) for row in rows} & reviewed_ids
            ),
            "all_blocks_preholdout": all(utc(spec["audit_end"]) < HOLDOUT_START for spec in specs),
            "independent_causal_images": len({row["image_path"] for row in rows}) == REVIEW_TOTAL,
            "unique_causal_image_hashes": len({row["image_sha256"] for row in rows}) == REVIEW_TOTAL,
            "separate_future_images": len({row["future_review_path"] for row in rows}) == REVIEW_TOTAL,
            "no_owner_default": all(row["owner_verdict"] is None for row in rows),
            "nothing_training_eligible": not any(bool(row["training_eligible"]) for row in rows),
            "causality_all_green": bool(causality["all_pass"]),
            "holdout_clean": not any(bool(row["holdout_read"]) for row in rows),
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
    parser.add_argument(
        "--block-root",
        type=Path,
        default=None,
        help="test/rebuild override; default uses the frozen B/C block roots",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--frozen-main-commit", required=True)
    args = parser.parse_args()
    summary = build(
        block_root=args.block_root,
        output=args.out,
        frozen_main_commit=args.frozen_main_commit,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
