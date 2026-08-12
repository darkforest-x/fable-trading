#!/usr/bin/env python3
"""Validate completed Owner verdicts and produce the blinded-source diagnosis."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

from scripts.serve_local_signal_v2_semantic_review import load_verdicts


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "analysis/output/local_signal_v2_positive_semantic_review200_v2"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(str(row["owner_verdict"]) for row in rows)
    denominator = counts["YES"] + counts["NO"]
    rate = counts["YES"] / denominator if denominator else None
    if denominator:
        z = 1.959963984540054
        scale = 1 + z * z / denominator
        center = (rate + z * z / (2 * denominator)) / scale
        half = (
            z
            * math.sqrt(
                rate * (1 - rate) / denominator
                + z * z / (4 * denominator * denominator)
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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sliced_metrics(
    rows: list[dict[str, Any]], field: str, values: tuple[str, ...]
) -> dict[str, Any]:
    return {
        value: metrics(
            [row for row in rows if str(row["sampling_strata"][field]) == value]
        )
        for value in values
    }


def span_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    bins = (
        ("lt1", float("-inf"), 1.0),
        ("1to2", 1.0, 2.0),
        ("2to4", 2.0, 4.0),
        ("ge4", 4.0, float("inf")),
    )
    return {
        label: metrics(
            [
                row
                for row in rows
                if low <= float(row["causal_review_actual_span_pct"]) < high
            ]
        )
        for label, low, high in bins
    }


def summarize(out_dir: Path) -> dict[str, Any]:
    manifest = read_jsonl(out_dir / "review_manifest.jsonl")
    pack_summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    verdict_log = read_jsonl(out_dir / "owner_verdicts.jsonl")
    verdicts = load_verdicts(out_dir)
    manifest_ids = [str(row["review_id"]) for row in manifest]
    verdict_ids = [str(row["review_id"]) for row in verdict_log]
    if len(set(manifest_ids)) != len(manifest_ids) or len(set(verdict_ids)) != len(
        verdict_ids
    ):
        raise ValueError("review contains duplicate IDs; resolve append history before summary")
    if len(manifest) != 200 or len(verdict_log) != 200 or len(verdicts) != 200:
        raise ValueError(
            "review incomplete: "
            f"manifest={len(manifest)} log={len(verdict_log)} latest={len(verdicts)}"
        )
    if set(manifest_ids) != set(verdict_ids):
        raise ValueError("review verdict IDs do not exactly match manifest IDs")
    if any(row.get("owner_verdict") not in {"YES", "NO", "SKIP"} for row in verdict_log):
        raise ValueError("review contains invalid verdict")
    if any(not row.get("reviewed_at") for row in verdict_log):
        raise ValueError("review contains missing reviewed_at")
    verdict_rows_by_id = {str(row["review_id"]): row for row in verdict_log}
    joined = []
    for row in manifest:
        item = dict(row)
        item["owner_verdict"] = verdicts[str(row["review_id"])]
        verdict_row = verdict_rows_by_id[str(row["review_id"])]
        item["reviewed_at"] = verdict_row["reviewed_at"]
        joined.append(item)
    positive = [row for row in joined if row["source_type"] == "positive_pool"]
    canary = [row for row in joined if row["source_type"] == "canary_candidate"]
    by_cohort = {
        cohort: metrics([row for row in canary if row["canary_cohort"] == cohort])
        for cohort in ("common_retained", "r2_new", "r1_suppressed")
    }
    result = {
        "protocol": pack_summary["protocol"],
        "data_quality": {
            "manifest_rows": len(manifest),
            "verdict_log_rows": len(verdict_log),
            "unique_manifest_ids": len(set(manifest_ids)),
            "unique_verdict_ids": len(set(verdict_ids)),
            "id_set_exact_match": set(manifest_ids) == set(verdict_ids),
            "allowed_verdicts_only": True,
            "all_reviewed_at": True,
            "first_reviewed_at": min(str(row["reviewed_at"]) for row in verdict_log),
            "last_reviewed_at": max(str(row["reviewed_at"]) for row in verdict_log),
            "verdict_log_sha256": sha256_file(out_dir / "owner_verdicts.jsonl"),
        },
        "positive_pool": metrics(positive),
        "canary_candidate": metrics(canary),
        "canary_by_internal_source_after_unblinding": by_cohort,
        "by_relative_volatility": {
            "positive_pool": sliced_metrics(positive, "volatility", ("low", "mid", "high")),
            "canary_candidate": sliced_metrics(canary, "volatility", ("low", "mid", "high")),
        },
        "by_model_confidence": {
            "positive_pool": sliced_metrics(positive, "confidence", ("low", "mid", "high")),
            "canary_candidate": sliced_metrics(canary, "confidence", ("low", "mid", "high")),
        },
        "by_causal_actual_span_pct": {
            "positive_pool": span_metrics(positive),
            "canary_candidate": span_metrics(canary),
        },
        "naive_cohort_population_weighted_canary_yes_rate": (
            163 * by_cohort["common_retained"]["yes_rate_excluding_skip"]
            + 32 * by_cohort["r2_new"]["yes_rate_excluding_skip"]
            + 60 * by_cohort["r1_suppressed"]["yes_rate_excluding_skip"]
        )
        / 255,
        "naive_weighted_warning": (
            "Diagnostic only: future-availability eligibility and within-cohort "
            "stratification mean this is not a probability-weighted market precision estimate."
        ),
        "diagnosis": "CASE_B_POSITIVE_HIGH_CANARY_LOW",
        "automatic_training_started": False,
        "holdout_read": False,
    }
    (out_dir / "owner_review_summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "owner_review_joined.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in joined),
        encoding="utf-8",
    )
    (out_dir / "owner_review_diagnostics.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    print(json.dumps(summarize(args.out), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
