#!/usr/bin/env python3
"""Validate the V1/V2/V3 two-key-candle research artifacts and boundaries."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd


PROJECT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def assert_receipt_hash(receipt: dict[str, Any], key: str) -> None:
    item = receipt[key]
    path = PROJECT / item["path"]
    assert path.is_file(), f"missing {key} path: {path}"
    assert sha256(path) == item["sha256"], f"stale {key} hash in receipt"


def validate_events(
    events: pd.DataFrame,
    *,
    holdout_start: pd.Timestamp,
    cost: float,
    required_controls: int = 3,
) -> dict[str, Any]:
    times = pd.to_datetime(events["k2_time"], utc=True)
    assert times.notna().all(), "unparseable K2 timestamps"
    assert times.lt(holdout_start).all(), "holdout K2 found in research events"
    assert events["n_controls"].eq(required_controls).all(), "control count mismatch"
    expected_side = np.where(events["direction"].eq(1), "long", "short")
    assert events["side"].eq(expected_side).all(), "side/direction mismatch"
    maximum_error = 0.0
    for horizon in (12, 24, 48):
        assert events[f"entry_i_{horizon}"].eq(events["k2_i"] + 1).all(), "non-causal entry index"
        expected_stop = np.where(events["direction"].eq(1), events["k2_low"], events["k2_high"])
        assert np.allclose(events[f"stop_price_{horizon}"], expected_stop, rtol=0, atol=1e-10), "stop differs from K2 extreme"
        cost_error = (
            events[f"net_return_{horizon}"]
            - events[f"gross_return_{horizon}"]
            + cost
        ).abs()
        excess_error = (
            events[f"paired_excess_{horizon}"]
            - events[f"net_return_{horizon}"]
            + events[f"control_net_return_{horizon}"]
        ).abs()
        maximum_error = max(maximum_error, float(cost_error.max()), float(excess_error.max()))
        assert float(cost_error.max()) < 1e-10, "cost arithmetic mismatch"
        assert float(excess_error.max()) < 1e-10, "paired excess arithmetic mismatch"
    return {
        "events": int(len(events)),
        "symbols": int(events["symbol"].nunique()),
        "min_k2_time": times.min().isoformat(),
        "max_k2_time": times.max().isoformat(),
        "holdout_rows": int(times.ge(holdout_start).sum()),
        "max_abs_arithmetic_error": maximum_error,
    }


def validate_control_coverage(events: pd.DataFrame, control_paths: Iterable[Path]) -> None:
    controls = pd.concat(
        [pd.read_csv(path, usecols=["event_id", "control_rank"]) for path in control_paths],
        ignore_index=True,
    )
    counts = controls.groupby("event_id").size()
    event_ids = events["event_id"].drop_duplicates()
    assert event_ids.isin(counts.index).all(), "selected event missing from saved control detail"
    assert counts.reindex(event_ids).eq(9).all(), "each event must have 3 controls x 3 horizons"


def validate_v1() -> dict[str, Any]:
    root = PROJECT / "experiments/active/exp-two-key-candle-ma-retest-1h-preholdout-v1"
    config = load_json(root / "config.json")
    summary = load_json(root / "results/summary.json")
    assert summary["holdout_consumed"] is False
    assert summary["training_eligible"] is False and summary["production_eligible"] is False
    assert_receipt_hash(summary, "script")
    assert_receipt_hash(summary, "config")
    events = pd.read_csv(root / "results/selected_events.csv.gz")
    receipt = validate_events(
        events,
        holdout_start=pd.Timestamp(config["holdout_start"]),
        cost=float(config["round_trip_cost"]),
    )
    expected_counts = {row["segment"]: int(row["n_events"]) for row in summary["summary"]}
    assert events["segment"].value_counts().to_dict() == expected_counts
    validate_control_coverage(events, [root / "results/matched_controls.csv.gz"])
    assert pd.Timestamp(summary["data"]["last_hour"]) < pd.Timestamp(config["safe_end_exclusive"])
    return {"experiment": "v1", "status": "passed", **receipt}


def validate_v2() -> dict[str, Any]:
    root = PROJECT / "experiments/active/exp-two-key-candle-ma-retest-sma40-state-v2"
    config = load_json(root / "config.json")
    summary = load_json(root / "results/summary.json")
    assert summary["holdout_consumed"] is False
    assert summary["training_eligible"] is False and summary["production_eligible"] is False
    assert_receipt_hash(summary, "script")
    assert_receipt_hash(summary, "config")
    events = pd.read_csv(root / "results/profile_events.csv.gz")
    receipt = validate_events(
        events,
        holdout_start=pd.Timestamp(config["holdout_start"]),
        cost=float(config["round_trip_cost"]),
    )
    expected = {
        (row["profile"], row["segment"]): int(row["n_events"])
        for row in summary["profile_summary"]
    }
    actual = events.groupby(["profile", "segment"]).size().to_dict()
    assert {
        key: value for key, value in expected.items() if value > 0
    } == actual, "profile/segment counts differ from summary"
    assert all(key not in actual for key, value in expected.items() if value == 0), (
        "zero-count summary rows must not exist in the event table"
    )
    validate_control_coverage(
        events,
        [
            root / "results/historical_matched_controls.csv.gz",
            root / "results/fresh_matched_controls.csv.gz",
        ],
    )
    assert summary["source"]["safe_end_exclusive"] == summary["source"]["holdout_start"]
    return {"experiment": "v2", "status": "passed", **receipt}


def validate_v3() -> dict[str, Any]:
    root = PROJECT / "experiments/active/exp-two-key-candle-feature-atlas-v3"
    config = load_json(root / "config.json")
    summary = load_json(root / "results/summary.json")
    assert summary["holdout_consumed"] is False
    assert summary["training_eligible"] is False and summary["production_eligible"] is False
    assert_receipt_hash(summary, "script")
    assert_receipt_hash(summary, "config")
    source = PROJECT / summary["source"]["path"]
    assert sha256(source) == summary["source"]["sha256"], "V3 source hash drift"
    events = pd.read_csv(source)
    events = events[events["profile"].eq(config["source_profile"])].copy()
    receipt = validate_events(
        events,
        holdout_start=pd.Timestamp(config["time_splits"]["holdout_start"]),
        cost=float(config["round_trip_cost"]),
    )
    assert receipt["events"] == int(summary["source"]["rows"])
    assert summary["feature_families"] == len(config["feature_families"]) == 55
    assert summary["original_preregistered_families"] == 42
    assert summary["diagnostic_amendment_families"] == 13

    selection = pd.read_csv(root / "results/discovery_selection.csv")
    replay = pd.read_csv(root / "results/walkforward_replay.csv")
    atlas = pd.read_csv(root / "results/dimension_atlas.csv")
    assert int(selection["selected"].sum()) == 55
    assert set(selection["family_status"]) == {"original_preregistered", "diagnostic_amendment"}
    assert set(atlas["family_status"]) == {"original_preregistered", "diagnostic_amendment"}
    validation = replay[replay["split"].eq("validation")]
    assert int(validation["passes_validation_gate"].sum()) == summary["validation_pass_count"] == 0
    assert int(validation["passes_diagnostic_screen"].sum()) == summary["diagnostic_screen_pass_count"] == 0
    assert validation["dimension"].nunique() == 55
    assert set(replay["split"]) == {"discovery", "validation", "bridge", "fresh_preholdout"}

    for filename in (
        "core_halfyear.png",
        "walkforward_generalization.png",
        "validation_rank.png",
        "fixed_target_sensitivity.png",
        "gap_walkforward.png",
        "owner_anchor_replay.png",
    ):
        path = root / "results" / filename
        assert path.is_file() and path.stat().st_size > 10_000, f"missing or empty chart {filename}"

    owner_receipt = load_json(root / "results/owner_anchor_source_receipt.json")
    assert "not holdout evaluation" in owner_receipt["purpose"]
    assert owner_receipt["confirmed_rows"] > 1000
    owner_csv = PROJECT / owner_receipt["csv_path"]
    owner_script = PROJECT / owner_receipt["script_path"]
    assert sha256(owner_csv) == owner_receipt["csv_sha256"], "owner anchor CSV hash drift"
    assert sha256(owner_script) == owner_receipt["script_sha256"], "owner replay script hash drift"
    owner_pairs = pd.read_csv(root / "results/owner_anchor_pairs.csv")
    assert set(owner_pairs["name"]) == {"long", "short"}
    assert dict(zip(owner_pairs["name"], owner_pairs["gap_bars"])) == {"short": 6, "long": 3}
    assert owner_pairs["anchor_score"].between(80, 85).all()
    return {"experiment": "v3", "status": "passed", **receipt}


VALIDATORS = {"v1": validate_v1, "v2": validate_v2, "v3": validate_v3}


def main(default: str | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("experiment", nargs="?", choices=sorted(VALIDATORS), default=default)
    args = parser.parse_args()
    if args.experiment is None:
        payload = {name: validator() for name, validator in VALIDATORS.items()}
    else:
        payload = VALIDATORS[args.experiment]()
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
