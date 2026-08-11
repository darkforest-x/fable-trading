import json
from pathlib import Path

import pytest

from scripts.compare_owner_short_canary import build_comparison, validate_contract


def summary(weight: str, *, raw: int, events: int) -> dict:
    return {
        "protocol": "owner_short_gold_center_recent2d_v1_20260811",
        "symbols": 2,
        "scanned_symbols": ["A", "B"],
        "stale_symbols": [],
        "latest_bar": "2026-05-03T23:45:00+00:00",
        "replay_start_exclusive": "2026-05-03T12:00:00+00:00",
        "hours": 11.75,
        "window_lengths": list(range(12, 20)),
        "confidence": 0.25,
        "nms_iou": 0.7,
        "event_gap_bars": 5,
        "bar_endpoints": 94,
        "window_exposures": 752,
        "evaluation_scope": "preholdout_postval_canary",
        "holdout_use_number": 0,
        "weights_sha256": weight,
        "raw_detections": raw,
        "deduplicated_events": events,
    }


def event(symbol: str, conf: float, *, core_mid: float = 10.0) -> dict:
    return {
        "event_id": f"{symbol}-{core_mid}",
        "symbol": symbol,
        "conf": conf,
        "event_conf_max": conf + 0.1,
        "predicted_core_bars": 6,
        "decision_delay_bars": 4,
        "core_mid_i": core_mid,
        "decision_i": int(core_mid) + 4,
    }


def write_scan(path: Path, payload: dict, events: list[dict]) -> None:
    path.mkdir(parents=True)
    (path / "scan_summary.json").write_text(json.dumps(payload), encoding="utf-8")
    (path / "events.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in events), encoding="utf-8"
    )


def test_build_comparison_requires_and_reports_one_contract(tmp_path: Path) -> None:
    r1_events = [
        event("A", 0.3, core_mid=10),
        event("A", 0.4, core_mid=30),
        event("B", 0.5, core_mid=10),
    ]
    r2_events = [event("A", 0.6, core_mid=11)]
    write_scan(tmp_path / "r1", summary("r1", raw=20, events=3), r1_events)
    write_scan(tmp_path / "r2", summary("r2", raw=5, events=1), r2_events)
    snapshot = {
        "evaluation_scope": "preholdout_postval_canary",
        "holdout_rows_materialized": 0,
        "max_materialized_time": "2026-05-03T23:45:00+00:00",
        "canonical_data_written": False,
    }
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")

    result = build_comparison(tmp_path / "r1", tmp_path / "r2", snapshot_path)

    assert result["holdout_read"] is False
    assert result["r1"]["deduplicated_events"] == 3
    assert result["r2"]["deduplicated_events"] == 1
    assert result["r2_minus_r1"]["deduplicated_events"]["relative"] == pytest.approx(-2 / 3)
    assert result["r2"]["predicted_core_4_to_7_share"] == 1.0
    assert result["cross_model_event_overlap"]["matched_events"] == 1
    assert result["cross_model_event_overlap"]["r1_only_events"] == 2
    assert result["cross_model_event_overlap"]["r2_only_events"] == 0


def test_contract_rejects_threshold_drift() -> None:
    left = summary("r1", raw=1, events=1)
    right = summary("r2", raw=1, events=1)
    right["confidence"] = 0.45

    with pytest.raises(ValueError, match="contract mismatch"):
        validate_contract(left, right)
