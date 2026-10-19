import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from scripts.summarize_owner_short_recent_core10 import (
    BASELINE_SHA256,
    CORE_SYMBOLS,
    HARDNEG_SHA256,
    build_summary,
    render_report,
)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _event(symbol: str, index: int, conf: float = 0.4) -> dict:
    return {
        "symbol": symbol,
        "event_id": f"{symbol}-{index}",
        "decision_i": index + 5,
        "decision_time": (
            pd.Timestamp("2026-08-06T00:00:00Z")
            + pd.Timedelta(minutes=15 * (index + 5))
        ).isoformat(),
        "core_mid_i": float(index),
        "core_mid_time": (
            pd.Timestamp("2026-08-06T00:00:00Z")
            + pd.Timedelta(minutes=15 * index)
        ).isoformat(),
        "conf": conf,
        "event_conf_max": conf + 0.1,
        "predicted_core_bars": 5,
        "decision_delay_bars": 4,
    }


def test_build_summary_validates_core10_and_keeps_outcomes_absent(tmp_path: Path) -> None:
    snapshot = tmp_path / "kline_snapshot"
    snapshot.mkdir(parents=True)
    start = pd.Timestamp("2026-08-06T00:00:00Z")
    times = pd.date_range(start - pd.Timedelta(minutes=15 * 150), periods=155, freq="15min")
    for symbol in CORE_SYMBOLS:
        pd.DataFrame({"open_time": times.astype(str)}).to_csv(snapshot / f"{symbol}.csv", index=False)
    end = times[-1]
    replay_start = times[-5]
    endpoints = 4 * len(CORE_SYMBOLS)
    exposures = endpoints * 8
    common = {
        "protocol": "test",
        "symbols": 10,
        "scanned_symbols": sorted(CORE_SYMBOLS),
        "stale_symbols": [],
        "latest_bar": end.isoformat(),
        "replay_start_exclusive": replay_start.isoformat(),
        "hours": 1.0,
        "window_lengths": list(range(12, 20)),
        "confidence": 0.25,
        "nms_iou": 0.7,
        "nms_device": "cpu",
        "nms_implementation": "torchvision",
        "nms_max_time_img": 60.0,
        "event_gap_bars": 5,
        "bar_endpoints": endpoints,
        "window_exposures": exposures,
        "evaluation_scope": "holdout",
        "owner_authorized_in_conversation": True,
        "promoted": False,
        "orders_placed": False,
        "snapshot_dir": str(snapshot),
    }
    baseline_event = _event(CORE_SYMBOLS[0], 151)
    hardneg_event = _event(CORE_SYMBOLS[0], 151, 0.5)
    for name, weight, use, event in (
        ("baseline", BASELINE_SHA256, 2, baseline_event),
        ("hardneg", HARDNEG_SHA256, 1, hardneg_event),
    ):
        directory = tmp_path / name
        summary = dict(common)
        summary.update(
            {
                "weights_sha256": weight,
                "holdout_use_number": use,
                "raw_detections": 1,
                "deduplicated_events": 1,
            }
        )
        _write_json(directory / "scan_summary.json", summary)
        _write_jsonl(directory / "raw_detections.jsonl", [event])
        _write_jsonl(directory / "events.jsonl", [event])
        _write_json(directory / "_SUCCESS.json", {"ok": True, "events": 1})
    snapshot_summary = {
        "generated_at": "2026-08-21T00:00:00Z",
        "requested_symbols": 10,
        "usable_symbols": 10,
        "canonical_data_written": False,
        "symbols": [
            {
                "symbol": symbol,
                "rows": 155,
                "sha256": hashlib.sha256(
                    (snapshot / f"{symbol}.csv").read_bytes()
                ).hexdigest(),
                "error": None,
            }
            for symbol in CORE_SYMBOLS
        ],
    }
    _write_json(tmp_path / "fetch_summary.json", snapshot_summary)

    native_parity = tmp_path / "parity_fp32_eth_20260811"
    cpu_parity = tmp_path / "parity_cpu_torchvision_nms60_v2_b32_eth_20260811"
    parity_summary = dict(common)
    parity_summary.update(
        {
            "weights_sha256": BASELINE_SHA256,
            "raw_detections": 1,
            "deduplicated_events": 1,
        }
    )
    _write_json(native_parity / "scan_summary.json", parity_summary)
    cpu_parity_summary = dict(parity_summary)
    cpu_parity_summary.update(
        {
            "nms_device": "cpu",
            "nms_implementation": "torchvision",
            "nms_max_time_img": 60.0,
        }
    )
    _write_json(cpu_parity / "scan_summary.json", cpu_parity_summary)
    for directory in (native_parity, cpu_parity):
        _write_jsonl(directory / "raw_detections.jsonl", [baseline_event])
        _write_jsonl(directory / "events.jsonl", [baseline_event])

    result = build_summary(
        tmp_path / "baseline",
        tmp_path / "hardneg",
        tmp_path / "fetch_summary.json",
    )

    assert result["future_outcomes_read"] is False
    assert result["baseline"]["events"] == 1
    assert result["hardneg"]["events"] == 1
    assert result["baseline"]["events_per_symbol"]["median"] == 0.0
    assert result["model_lineage"]["hardneg_r1"]["train_hard_negative"] == 2286
    assert result["runtime_integrity"]["cpu_nms_parity"]["status"] == "byte_identical"
    assert "events.jsonl" in result["scan_artifacts"]["baseline"]["sha256"]
    assert result["cross_model_overlap"]["matched_events"] == 1
    assert result["self_comparison_null_control"]["r1_only_events"] == 0

    report = tmp_path / "report.md"
    render_report(result, report)
    report_text = report.read_text(encoding="utf-8")
    assert "这两个模型到底是什么" in report_text
    assert "没有读取未来收益" in report_text

    with (snapshot / f"{CORE_SYMBOLS[0]}.csv").open("a", encoding="utf-8") as handle:
        handle.write("\n")
    with pytest.raises(ValueError, match="snapshot CSV SHA drift"):
        build_summary(
            tmp_path / "baseline",
            tmp_path / "hardneg",
            tmp_path / "fetch_summary.json",
        )
