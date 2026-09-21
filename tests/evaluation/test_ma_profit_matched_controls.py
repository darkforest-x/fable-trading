"""Focused causal matching regressions for frozen Profit3R random controls."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from yoyo.contracts.ma_profit_filter import resolve_ma_profit_event
from yoyo.evaluation import ma_profit_matched_controls as controls


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _plan() -> dict:
    return {
        "experiment_id": controls.EXPERIMENT_ID,
        "label_contract": {"confirmation_bars": 5, "horizon_hours": 12, "target_r": 3.0, "round_trip_cost": 0.002},
        "splits": {
            "train_end_exclusive": "2024-01-10T00:00:00Z",
            "validation_end_exclusive": "2024-01-20T00:00:00Z",
            "test_end_exclusive": "2024-02-10T00:00:00Z",
        },
    }


def _contract(desired: int = 5) -> dict:
    return {
        "experiment_id": controls.EXPERIMENT_ID,
        "matched_random_control": {"desired_per_event": desired, "bucket_edges": [.001, .002, .004, .008, .016, .032, .064]},
    }


def _source(tmp_path: Path, *, periods: int = 3000) -> tuple[Path, pd.DataFrame]:
    times = pd.date_range("2024-01-01T00:00:00Z", periods=periods, freq="15min")
    close = 100.0 + np.arange(periods, dtype=float) * .1
    frame = pd.DataFrame({"open_time": times, "open": close - .03, "high": close + .1, "low": close - .12, "close": close, "volume": 1.0})
    path = tmp_path / "source.csv"
    frame.to_csv(path, index=False)
    return path, frame


def _event(event_id: str, frame: pd.DataFrame, end_i: int, *, split: str, source: Path, source_sha: str) -> dict:
    bars = 4
    return {
        "event_id": event_id,
        "symbol": "SYN_USDT_SWAP",
        "canonical_asset": "SYN",
        "direction": "LONG",
        "source_path": str(source),
        "source_sha256": source_sha,
        "venue": "fixture",
        "bar_minutes": 15,
        "core_bars": bars,
        "source_core_start_i": end_i - bars + 1,
        "source_core_end_i": end_i,
        "core_start_time": frame.open_time.iloc[end_i - bars + 1].isoformat(),
        "core_end_time": frame.open_time.iloc[end_i].isoformat(),
        "split": split,
        # This field must be ignored by causal matching.
        "profit": {"outcome": "TP", "retained": True, "net_r": 999.0},
    }


def _inputs(tmp_path: Path) -> tuple[dict, dict, list[dict], dict, dict, pd.DataFrame]:
    path, frame = _source(tmp_path)
    sha = _sha(path)
    # 2024-01-14 23:45 UTC is ISO-week 2, while c+5 is Monday ISO-week 3.
    # Matching must use the original core-end week, not confirmation's week.
    events = [_event("val_target", frame, 1343, split="val", source=path, source_sha=sha), _event("test_target", frame, 2200, split="test", source=path, source_sha=sha)]
    sources = {"sources": [{"source_path": str(path), "sha256": sha, "symbol": "SYN_USDT_SWAP", "venue": "fixture", "bar_minutes": 15}]}
    refs = {"radius_hours": 4, "events": [{"symbol": "SYN_USDT_SWAP", "anchor_time": frame.open_time.iloc[1364].isoformat()}]}
    return _plan(), _contract(), events, sources, refs, frame


def _rows(out: Path) -> list[dict]:
    return [json.loads(line) for line in (out / "frozen_events.jsonl").read_text().splitlines() if line]


def test_controls_match_source_week_bucket_split_and_exclusions(tmp_path: Path) -> None:
    plan, contract, events, sources, refs, frame = _inputs(tmp_path)
    receipt = controls.build_matched_controls(plan, contract, events, sources, refs, tmp_path / "out")
    rows = _rows(tmp_path / "out")
    assert receipt["controls"] == len(rows)
    assert receipt["outcomes_used_for_selection"] is False
    assert all(row["reason"] != "target_volatility_unknown" for row in receipt["shortages"])
    targets = {row["event_id"]: row for row in events}
    for row in rows:
        target = targets[row["matched_event_id"]]
        assert row["source_path"] == target["source_path"]
        assert row["source_sha256"] == target["source_sha256"]
        assert row["canonical_asset"] == target["canonical_asset"]
        assert row["direction"] == target["direction"]
        assert row["core_bars"] == target["core_bars"]
        assert row["split"] == target["split"]
        assert row["matched_utc_week"] == {"year": int(pd.Timestamp(target["core_end_time"]).isocalendar().year), "week": int(pd.Timestamp(target["core_end_time"]).isocalendar().week)}
        assert row["volatility_bucket"] == row["matched_volatility_bucket"]
        candidate_time = pd.Timestamp(row["core_end_time"])
        assert all(abs(candidate_time - pd.Timestamp(event["core_end_time"])) > pd.Timedelta(hours=4) for event in events)
        assert abs(candidate_time - pd.Timestamp(refs["events"][0]["anchor_time"])) > pd.Timedelta(hours=4)
    assert all(row["shortage"] >= 0 for row in receipt["shortages"])
    assert controls.volatility_bucket(.001, contract["matched_random_control"]["bucket_edges"]) == 0


def test_future_price_mutation_after_candidate_weeks_does_not_change_selection(tmp_path: Path) -> None:
    plan, contract, events, sources, refs, frame = _inputs(tmp_path)
    first = tmp_path / "first"
    controls.build_matched_controls(plan, contract, events[:1], sources, refs, first)
    before = (first / "frozen_events.jsonl").read_bytes()
    source = Path(sources["sources"][0]["source_path"])
    # This lies after the target's UTC week and is never parsed as an OHLC value
    # for that matching stratum; it remains available only as a timestamp.
    later = frame.index[-1]
    frame.loc[later, ["open", "high", "low", "close"]] = [999999.0, 1000000.0, 999998.0, 999999.0]
    frame.to_csv(source, index=False)
    sources["sources"][0]["sha256"] = _sha(source)
    events[0]["source_sha256"] = sources["sources"][0]["sha256"]
    events[0]["profit"] = {"outcome": "SL", "retained": False, "net_r": -999.0}
    second = tmp_path / "second"
    controls.build_matched_controls(plan, contract, events[:1], sources, refs, second)
    def causal_selection(blob: bytes) -> list[tuple]:
        rows = [json.loads(line) for line in blob.decode().splitlines() if line]
        return [(row["matched_event_id"], row["source_core_start_i"], row["source_core_end_i"], row["volatility_bucket"], row["match_order"]) for row in rows]
    # File SHA must change honestly after a source mutation; matching identity
    # and causal bucket/order must not depend on that later price.
    assert causal_selection(before) == causal_selection((second / "frozen_events.jsonl").read_bytes())


def test_shortage_is_recorded_and_output_rows_are_resolver_compatible(tmp_path: Path) -> None:
    plan, _, events, sources, refs, frame = _inputs(tmp_path)
    receipt = controls.build_matched_controls(plan, _contract(desired=10_000), events[:1], sources, refs, tmp_path / "out")
    assert receipt["events_with_shortage"] == 1
    rows = _rows(tmp_path / "out")
    source = Path(sources["sources"][0]["source_path"])
    for row in rows[:3]:
        outcome = resolve_ma_profit_event(frame, row["source_core_start_i"], row["source_core_end_i"], row["direction"], bar_minutes=row["bar_minutes"])
        assert outcome["outcome"] in {"TP", "SL", "TIMEOUT"}
        assert "profit" not in row
        assert row["label_window_end_utc"] == outcome["label_window_end_utc"]
    assert json.loads((tmp_path / "out" / "frozen_sources.json").read_text())["sources"][0]["sha256"] == _sha(source)


def test_invalid_cohort_source_hash_is_rejected(tmp_path: Path) -> None:
    plan, contract, events, sources, refs, _ = _inputs(tmp_path)
    events[0]["source_sha256"] = "wrong"
    with pytest.raises(controls.MatchedControlError, match="source SHA"):
        controls.build_matched_controls(plan, contract, events[:1], sources, refs, tmp_path / "out")


def test_resolved_target_ledger_can_select_from_full_cohort_with_unknown_rows(tmp_path: Path) -> None:
    plan, contract, events, sources, refs, _ = _inputs(tmp_path)
    target = dict(events[0])
    events[1] = {**events[1], "profit": {"outcome": "UNKNOWN", "retained": False}}
    receipt = controls.build_matched_controls(plan, contract, events, sources, refs, tmp_path / "out", target_events=[target])
    assert receipt["matched_events"] == 1
    assert receipt["shortages"][0]["reason"] != "target_volatility_unknown"


def test_dataset_training_rows_are_not_control_targets_and_outcome_drift_is_rejected(tmp_path: Path) -> None:
    plan, contract, events, sources, refs, frame = _inputs(tmp_path)
    spec = sources["sources"][0]
    train = _event("train_example", frame, 800, split="train", source=Path(spec["source_path"]), source_sha=spec["sha256"])
    all_rows = [train, *events]
    receipt = controls.build_matched_controls(plan, contract, all_rows, sources, refs, tmp_path / "out", target_events=all_rows)
    assert receipt["matched_events"] == 2
    changed = dict(events[0], profit={"outcome": "SL", "retained": False})
    with pytest.raises(controls.MatchedControlError, match="outcome drift"):
        controls.build_matched_controls(plan, contract, all_rows, sources, refs, tmp_path / "bad", target_events=[changed])


def test_target_bucket_does_not_require_target_to_be_a_label_complete_random_candidate(tmp_path: Path) -> None:
    path, frame = _source(tmp_path, periods=1360)
    sha = _sha(path)
    # The target has a known c+5 confirmation but no source tail through c+53.
    # It can still have an already-closed TP in the full ledger; matching must
    # compute its causal TR bucket rather than requiring it in the random pool.
    event = _event("tail_tp", frame, 1343, split="val", source=path, source_sha=sha)
    sources = {"sources": [{"source_path": str(path), "sha256": sha, "symbol": "SYN_USDT_SWAP", "venue": "fixture", "bar_minutes": 15}]}
    receipt = controls.build_matched_controls(_plan(), _contract(), [event], sources, {"radius_hours": 4, "events": []}, tmp_path / "out")
    assert receipt["shortages"][0]["reason"] != "target_volatility_unknown"
