#!/usr/bin/env python3
"""Join Owner verdicts to the early-frontier 300 pack and unblind the internal strata.

Scope is fixed by the 2026-08-12 handoff: ID join, overall YES rate, and the
150 `yes_like` / 150 `similar_no_boundary` unblinding, plus read-only slices over
fields that were already materialised inside the frozen pack (retrieval affinity,
model confidence, causal span, box geometry, candidate block).

This module never trains, never rewrites labels, never touches holdout: every input
is the frozen `review_manifest.jsonl` plus the append-only `owner_verdicts.jsonl`
written by `scripts/serve_local_signal_v2_semantic_review.py`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from scripts.serve_local_signal_v2_semantic_review import load_verdicts

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "analysis/output/local_signal_v2_early_frontier_review300_v1"
EXPECTED_ROWS = 300
HOLDOUT_START = "2026-05-04T00:00:00+00:00"
PERMUTATIONS = 10000
SEED = 20260812

CONFIDENCE_BINS: tuple[tuple[str, float, float], ...] = (
    ("lt0.35", float("-inf"), 0.35),
    ("0.35to0.5", 0.35, 0.5),
    ("0.5to0.7", 0.5, 0.7),
    ("ge0.7", 0.7, float("inf")),
)
SPAN_BINS: tuple[tuple[str, float, float], ...] = (
    ("lt1", float("-inf"), 1.0),
    ("1to2", 1.0, 2.0),
    ("2to4", 2.0, 4.0),
    ("ge4", 4.0, float("inf")),
)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def metrics(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(str(row["owner_verdict"]) for row in rows)
    denominator = counts["YES"] + counts["NO"]
    rate = counts["YES"] / denominator if denominator else None
    if denominator and rate is not None:
        z = 1.959963984540054
        scale = 1 + z * z / denominator
        center = (rate + z * z / (2 * denominator)) / scale
        half = (
            z
            * math.sqrt(
                rate * (1 - rate) / denominator + z * z / (4 * denominator * denominator)
            )
            / scale
        )
        interval: list[float | None] = [center - half, center + half]
    else:
        interval = [None, None]
    return {
        "reviewed": len(rows),
        "YES": counts["YES"],
        "NO": counts["NO"],
        "SKIP": counts["SKIP"],
        "yes_rate_excluding_skip": rate,
        "wilson95": interval,
    }


def grouped_metrics(
    rows: Sequence[dict[str, Any]], key: Callable[[dict[str, Any]], Any]
) -> dict[str, Any]:
    buckets: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[key(row)].append(row)
    return {str(name): metrics(bucket) for name, bucket in sorted(buckets.items(), key=lambda kv: str(kv[0]))}


def binned_metrics(
    rows: Sequence[dict[str, Any]],
    field: str,
    bins: Iterable[tuple[str, float, float]],
) -> dict[str, Any]:
    return {
        label: metrics([row for row in rows if low <= float(row[field]) < high])
        for label, low, high in bins
    }


def auc(scores: Sequence[float], positives: Sequence[bool]) -> float | None:
    """Mann-Whitney AUC with tie handling; None when one class is empty."""
    pos = [score for score, flag in zip(scores, positives) if flag]
    neg = [score for score, flag in zip(scores, positives) if not flag]
    if not pos or not neg:
        return None
    order = sorted(range(len(scores)), key=lambda index: scores[index])
    ranks = [0.0] * len(scores)
    position = 0
    while position < len(order):
        end = position
        while end + 1 < len(order) and scores[order[end + 1]] == scores[order[position]]:
            end += 1
        shared = (position + end) / 2 + 1
        for index in range(position, end + 1):
            ranks[order[index]] = shared
        position = end + 1
    rank_sum = sum(rank for rank, flag in zip(ranks, positives) if flag)
    return (rank_sum - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))


def permutation_p(
    labels: Sequence[bool],
    statistic: Callable[[Sequence[bool]], float],
    permutations: int = PERMUTATIONS,
    seed: int = SEED,
) -> dict[str, Any]:
    observed = statistic(labels)
    rng = random.Random(seed)
    shuffled = list(labels)
    hits = 0
    for _ in range(permutations):
        rng.shuffle(shuffled)
        if abs(statistic(shuffled)) >= abs(observed) - 1e-12:
            hits += 1
    return {
        "observed": observed,
        "permutations": permutations,
        "two_sided_p": (hits + 1) / (permutations + 1),
        "seed": seed,
    }


def ranking_power(
    rows: Sequence[dict[str, Any]],
    field: str,
    higher_means_yes: bool,
    permutations: int = PERMUTATIONS,
) -> dict[str, Any]:
    scored = [row for row in rows if row["owner_verdict"] in {"YES", "NO"}]
    sign = 1.0 if higher_means_yes else -1.0
    scores = [sign * float(row[field]) for row in scored]
    labels = [row["owner_verdict"] == "YES" for row in scored]
    test = permutation_p(labels, lambda flags: (auc(scores, flags) or 0.5) - 0.5, permutations)
    return {
        "field": field,
        "oriented_higher_means_yes": higher_means_yes,
        "n": len(scored),
        "auc": auc(scores, labels),
        "permutation_two_sided_p": test["two_sided_p"],
        "permutations": test["permutations"],
        "seed": test["seed"],
    }


def group_spread(
    rows: Sequence[dict[str, Any]], field: str, permutations: int = PERMUTATIONS
) -> dict[str, Any]:
    """Max-min YES-rate spread across a categorical field, with a permutation p."""
    scored = [row for row in rows if row["owner_verdict"] in {"YES", "NO"}]
    keys = [str(row[field]) for row in scored]
    labels = [row["owner_verdict"] == "YES" for row in scored]

    def spread(flags: Sequence[bool]) -> float:
        totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for key, flag in zip(keys, flags):
            totals[key][0] += int(flag)
            totals[key][1] += 1
        rates = [hit / total for hit, total in totals.values() if total]
        return max(rates) - min(rates) if rates else 0.0

    test = permutation_p(labels, spread, permutations)
    return {
        "field": field,
        "groups": len(set(keys)),
        "observed_yes_rate_spread": test["observed"],
        "permutation_two_sided_p": test["two_sided_p"],
        "permutations": test["permutations"],
        "seed": test["seed"],
    }


def review_pace(verdict_log: Sequence[dict[str, Any]]) -> dict[str, Any]:
    stamps = sorted(datetime.fromisoformat(str(row["reviewed_at"])) for row in verdict_log)
    gaps = [
        (stamps[index] - stamps[index - 1]).total_seconds() for index in range(1, len(stamps))
    ]
    ordered = sorted(gaps)
    return {
        "first_reviewed_at": stamps[0].isoformat(),
        "last_reviewed_at": stamps[-1].isoformat(),
        "elapsed_minutes": (stamps[-1] - stamps[0]).total_seconds() / 60,
        "gap_seconds_min": ordered[0] if ordered else None,
        "gap_seconds_median": ordered[len(ordered) // 2] if ordered else None,
        "gap_seconds_p90": ordered[int(0.9 * len(ordered))] if ordered else None,
        "gap_seconds_max": ordered[-1] if ordered else None,
        "gaps_under_0_5s": sum(1 for gap in gaps if gap < 0.5),
        "gaps_under_1s": sum(1 for gap in gaps if gap < 1.0),
    }


def validate(
    manifest: Sequence[dict[str, Any]],
    verdict_log: Sequence[dict[str, Any]],
    verdicts: dict[str, str],
) -> None:
    manifest_ids = [str(row["review_id"]) for row in manifest]
    verdict_ids = [str(row["review_id"]) for row in verdict_log]
    if len(set(manifest_ids)) != len(manifest_ids):
        raise ValueError("manifest contains duplicate review_id")
    if len(manifest) != EXPECTED_ROWS or len(verdicts) != EXPECTED_ROWS:
        raise ValueError(
            f"review incomplete: manifest={len(manifest)} latest_verdicts={len(verdicts)}"
        )
    if set(manifest_ids) != set(verdict_ids):
        raise ValueError("verdict IDs do not exactly match manifest IDs")
    if any(str(row.get("owner_verdict", "")).upper() not in {"YES", "NO", "SKIP"} for row in verdict_log):
        raise ValueError("verdict log contains an invalid owner_verdict")
    if any(not row.get("reviewed_at") for row in verdict_log):
        raise ValueError("verdict log contains a missing reviewed_at")
    if any(row.get("training_eligible") for row in manifest):
        raise ValueError("pack must stay training_eligible=false until Owner approves")
    if any(row.get("production_eligible") for row in manifest):
        raise ValueError("pack must stay production_eligible=false")
    if any(row.get("holdout_read") for row in manifest):
        raise ValueError("pack must not read holdout")
    if any(row["visible_end_bar"] != row["decision_bar"] for row in manifest):
        raise ValueError("causal review image must stop at the decision bar")
    if max(str(row["future_review_end_time"]) for row in manifest) >= HOLDOUT_START:
        raise ValueError("future review context must stay strictly pre-holdout")


def summarize(out_dir: Path, permutations: int = PERMUTATIONS) -> dict[str, Any]:
    manifest = read_jsonl(out_dir / "review_manifest.jsonl")
    pack_summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    verdict_log = read_jsonl(out_dir / "owner_verdicts.jsonl")
    verdicts = load_verdicts(out_dir)
    validate(manifest, verdict_log, verdicts)

    log_by_id = {str(row["review_id"]): row for row in verdict_log}
    joined: list[dict[str, Any]] = []
    for row in manifest:
        item = dict(row)
        review_id = str(row["review_id"])
        item["owner_verdict"] = verdicts[review_id]
        item["reviewed_at"] = log_by_id[review_id]["reviewed_at"]
        item["core_box_bars"] = int(row["box_end_bar"]) - int(row["box_start_bar"]) + 1
        item["decision_delay_bars"] = int(row["decision_bar"]) - int(row["box_end_bar"])
        joined.append(item)

    yes_like = [row for row in joined if row["retrieval_stratum_internal"] == "yes_like"]
    boundary = [
        row for row in joined if row["retrieval_stratum_internal"] == "similar_no_boundary"
    ]
    stratum_labels = [
        row["owner_verdict"] == "YES" for row in joined if row["owner_verdict"] in {"YES", "NO"}
    ]
    stratum_flags = [
        row["retrieval_stratum_internal"] == "yes_like"
        for row in joined
        if row["owner_verdict"] in {"YES", "NO"}
    ]

    def stratum_gap(flags: Sequence[bool]) -> float:
        yes_like_hits = sum(1 for flag, is_yes_like in zip(flags, stratum_flags) if is_yes_like and flag)
        yes_like_total = sum(1 for is_yes_like in stratum_flags if is_yes_like)
        other_hits = sum(1 for flag, is_yes_like in zip(flags, stratum_flags) if not is_yes_like and flag)
        other_total = sum(1 for is_yes_like in stratum_flags if not is_yes_like)
        if not yes_like_total or not other_total:
            return 0.0
        return yes_like_hits / yes_like_total - other_hits / other_total

    result: dict[str, Any] = {
        "protocol": pack_summary["protocol"],
        "generated_by": "scripts/summarize_local_signal_v2_early_frontier_review.py",
        "data_quality": {
            "manifest_rows": len(manifest),
            "verdict_log_rows": len(verdict_log),
            "unique_manifest_ids": len({str(row["review_id"]) for row in manifest}),
            "unique_verdict_ids": len({str(row["review_id"]) for row in verdict_log}),
            "id_set_exact_match": True,
            "allowed_verdicts_only": True,
            "all_reviewed_at": True,
            "revised_verdicts": len(verdict_log) - len(verdicts),
            "verdict_log_sha256": sha256_file(out_dir / "owner_verdicts.jsonl"),
            "manifest_sha256": pack_summary["manifest_sha256"],
            "unique_event_ids": len({str(row["event_id"]) for row in manifest}),
            "symbols": len({str(row["symbol"]) for row in manifest}),
        },
        "causality_and_scope": {
            "visible_end_equals_decision": sum(
                1 for row in manifest if row["visible_end_bar"] == row["decision_bar"]
            ),
            "manifest_future_bars_nonzero": sum(1 for row in manifest if row.get("future_bars")),
            "selection_future_used": sum(1 for row in manifest if row.get("selection_future_used")),
            "latest_decision_time": max(str(row["decision_time"]) for row in manifest),
            "latest_future_review_end_time": max(
                str(row["future_review_end_time"]) for row in manifest
            ),
            "holdout_start": HOLDOUT_START,
            "holdout_read": False,
            "training_eligible": 0,
            "production_eligible": False,
            "verdict_source": "future_assisted_semantic_judgement",
        },
        "overall": metrics(joined),
        "review_pace": review_pace(verdict_log),
        "internal_strata_after_unblinding": {
            "yes_like": metrics(yes_like),
            "similar_no_boundary": metrics(boundary),
            "yes_rate_gap": permutation_p(stratum_labels, stratum_gap, permutations),
        },
        "retrieval_ranking_power": {
            "owner_yes_affinity_internal": ranking_power(
                joined, "owner_yes_affinity_internal", True, permutations
            ),
            "nearest_owner_yes_distance_internal": ranking_power(
                joined, "nearest_owner_yes_distance_internal", False, permutations
            ),
            "model_confidence": ranking_power(joined, "model_confidence", True, permutations),
        },
        "by_candidate_block": grouped_metrics(joined, lambda row: row["candidate_block"]),
        "block_effect": group_spread(joined, "candidate_block", permutations),
        "by_core_box_bars": grouped_metrics(joined, lambda row: row["core_box_bars"]),
        "by_decision_delay_bars": grouped_metrics(joined, lambda row: row["decision_delay_bars"]),
        "by_window_length": grouped_metrics(joined, lambda row: row["window_length"]),
        "by_model_confidence": binned_metrics(joined, "model_confidence", CONFIDENCE_BINS),
        "by_causal_actual_span_pct": binned_metrics(
            joined, "causal_review_actual_span_pct", SPAN_BINS
        ),
        "block_stratum_balance": {
            block: dict(Counter(row["retrieval_stratum_internal"] for row in rows))
            for block, rows in sorted(
                {
                    block: [row for row in joined if row["candidate_block"] == block]
                    for block in {str(row["candidate_block"]) for row in joined}
                }.items()
            )
        },
        "automatic_training_started": False,
        "labels_converted": False,
        "holdout_read": False,
    }

    (out_dir / "owner_review_summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "owner_review_joined.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in joined),
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--permutations", type=int, default=PERMUTATIONS)
    args = parser.parse_args()
    print(json.dumps(summarize(args.out, args.permutations), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
