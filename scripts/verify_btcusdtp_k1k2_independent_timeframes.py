#!/usr/bin/env python3
"""Independently verify frozen BTCUSDT.P 15m/5m research artifacts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / (
    "experiments/active/"
    "exp-btcusdtp-k1k2-15m-5m-independent-preholdout-20260904-v1"
)
RESULTS = EXPERIMENT / "results"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def close(left: Any, right: Any, tolerance: float = 1e-12) -> bool:
    return bool(np.isclose(float(left), float(right), atol=tolerance, rtol=tolerance))


def main() -> None:
    config_path = EXPERIMENT / "config.json"
    script_path = PROJECT / "scripts/optimize_btcusdtp_k1k2_independent_timeframes.py"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    selection = json.loads((RESULTS / "selection_receipt.json").read_text(encoding="utf-8"))
    audit = json.loads((RESULTS / "audit_summary.json").read_text(encoding="utf-8"))
    source = PROJECT / config["source"]["path"]
    checks: list[dict[str, Any]] = []

    def check(name: str, passed: bool, detail: Any) -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    check("source_sha", sha256(source) == config["source"]["sha256"], sha256(source))
    check(
        "config_sha",
        sha256(config_path) == selection["config_sha256"],
        sha256(config_path),
    )
    check(
        "script_sha",
        sha256(script_path) == selection["script_sha256"],
        sha256(script_path),
    )
    source_times = pd.to_datetime(
        pd.read_csv(source, usecols=["open_time"])["open_time"], utc=True
    )
    holdout = pd.Timestamp(config["window"]["holdout_start"])
    check(
        "physical_source_before_holdout",
        bool(source_times.max() < holdout),
        {"max": source_times.max().isoformat(), "holdout": holdout.isoformat()},
    )
    check("audit_declared_non_pristine", audit["audit_window_pristine"] is False, False)
    check("zero_holdout_rows", int(audit["holdout_rows_read"]) == 0, audit["holdout_rows_read"])

    for bar in ("15m", "5m"):
        events = pd.read_csv(RESULTS / f"audit_{bar}_selected_trades.csv.gz")
        pairs = pd.read_csv(RESULTS / f"audit_{bar}_matched_pairs.csv")
        metrics = audit["timeframes"][bar]["metrics"]
        check(f"{bar}_unique_setup_ids", events["setup_id"].is_unique, len(events))
        check(
            f"{bar}_score_bounds",
            bool(events["secondary_score"].between(0.0, 1.0).all()),
            [float(events["secondary_score"].min()), float(events["secondary_score"].max())],
        )
        check(f"{bar}_event_count", len(events) == int(metrics["events"]), len(events))
        check(
            f"{bar}_mean_gross",
            close(events["gross_return"].mean() * 1e4, metrics["mean_gross_bp"]),
            float(events["gross_return"].mean() * 1e4),
        )
        check(
            f"{bar}_mean_net",
            close(events["net_return"].mean() * 1e4, metrics["mean_net_bp"]),
            float(events["net_return"].mean() * 1e4),
        )
        check(
            f"{bar}_win_rate",
            close(events["net_return"].gt(0.0).mean(), metrics["win_rate"]),
            float(events["net_return"].gt(0.0).mean()),
        )
        outcome_total = (
            int(events["outcome"].eq("tp").sum())
            + int(events["outcome"].astype(str).str.startswith("sl").sum())
            + int(events["outcome"].astype(str).str.startswith("protected_stop").sum())
            + int(events["outcome"].eq("timeout").sum())
        )
        check(f"{bar}_outcomes_reconcile", outcome_total == len(events), outcome_total)
        matched = pairs[pairs["match_status"].eq("matched_exact")]
        excess = float(matched["paired_excess_return"].mean() * 1e4)
        check(
            f"{bar}_matched_excess",
            close(excess, metrics["matched_control_excess_bp"]),
            excess,
        )

    output = {
        "status": "passed" if all(row["passed"] for row in checks) else "failed",
        "checks": checks,
        "check_count": len(checks),
        "holdout_rows_read": 0,
    }
    target = RESULTS / "verification.json"
    target.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(output, indent=2))
    if output["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
