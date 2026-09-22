"""Independent-event and train-only capacity gate regressions."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PureWindowsPath

import pytest
import pandas as pd

import yoyo.datasets.ma_profit_cohort as cohort
from yoyo.contracts.ma_profit_filter import resolve_ma_profit_event
from yoyo.datasets.ma_profit_training_contract import OWNER_V2_REQUEST
from yoyo.datasets.ma_profit_training_contract import _repo_relative


def _plan() -> dict:
    return {
        "experiment_id": cohort.EXPERIMENT_ID,
        "discovery": {"minimum_independent_winners": 3000, "maximum_dataset_winners": 5000},
        "splits": {"train_end_exclusive": "2025-01-01T00:00:00Z", "validation_end_exclusive": "2026-01-01T00:00:00Z", "test_end_exclusive": "2027-01-01T00:00:00Z"},
    }


def _row(event_id: str, stamp: str, *, symbol: str = "PEPEUSDT", score: float = 1.0, profit: float = 0.0) -> dict:
    return {"event_id": event_id, "sample_id": event_id, "symbol": symbol, "direction": "LONG", "core_end_time": stamp, "quality_score": score, "quality_tier": "PERFECT_CANDIDATE", "reference_gate_pass": True, "profit_value_ignored": profit}


def test_collect_uses_quality_sorted_nms_and_label_compatible_sources(tmp_path, monkeypatch) -> None:
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(_plan()), encoding="utf-8")
    strict = tmp_path / "strictGradeA.jsonl"
    strict_rows = [
        _row("a", "2024-01-01T00:00:00Z", symbol="1000PEPEUSDT", score=.9),
        _row("b", "2024-01-01T04:00:00Z", symbol="PEPE-USDT-SWAP", score=.1),
        _row("c", "2024-01-01T08:00:00Z", symbol="PEPE_USDT_SWAP", score=.8),
    ]
    strict.write_text("".join(json.dumps(row) + "\n" for row in strict_rows), encoding="utf-8")
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({"status": "completed", "binding": {"source_path": "input.csv", "source_sha256": "source", "miner_sha256": hashlib.sha256((cohort.ROOT / "yoyo/datasets/ma_profit_miner.py").read_bytes()).hexdigest(), "plan_sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(), "rule_dependency_sha256": {str(path.relative_to(cohort.ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in cohort.RULE_DEPENDENCY_PATHS}}, "artifacts": {"strictGradeA.jsonl": hashlib.sha256(strict.read_bytes()).hexdigest()}}), encoding="utf-8")
    manifest = tmp_path / "sources.json"
    manifest.write_text(json.dumps({"sources": [{"source_path": "input.csv", "sha256": "source", "receipt_path": str(receipt)}]}), encoding="utf-8")
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setattr(cohort, "DEFAULT_BINANCE_SCORED", empty)
    monkeypatch.setattr(cohort, "DEFAULT_RANKED_MANIFEST", empty)
    cohort.collect(plan_path, [manifest], tmp_path / "out")
    frozen = [json.loads(line) for line in (tmp_path / "out/frozen_events.jsonl").read_text().splitlines()]
    assert [row["origin_event_id"] for row in frozen] == ["a", "c"]  # 0h and 8h remain distinct; 4h joins a once.
    assert frozen[0]["cluster_member_count"] == 2
    sources = json.loads((tmp_path / "out/frozen_sources.json").read_text())
    assert sources["sources"][0]["source_path"] == "input.csv"
    assert sources["sources"][0]["sha256"] == "source"
    compact_receipt = cohort.collect(plan_path, [manifest], tmp_path / "compact", compact_events=True)
    compact_rows = [json.loads(line) for line in (tmp_path / "compact/frozen_events.jsonl").read_text().splitlines()]
    assert [row["event_id"] for row in compact_rows] == [row["event_id"] for row in frozen]
    for original, projected in zip(frozen, compact_rows):
        assert projected == cohort.compact_event(original)
        assert projected["full_collection_row_sha256"] == cohort._json_sha(original)
        assert projected["cluster_members"] == original["cluster_members"]
        assert projected["quality_score"] == original["quality_score"]
    assert compact_receipt["event_projection"]["selection_changed"] is False


def test_collection_is_independent_of_profit_fields() -> None:
    base = [cohort._origin_row(_row("a", "2024-01-01T00:00:00Z", score=.1, profit=-999), "x"), cohort._origin_row(_row("b", "2024-01-01T02:00:00Z", score=.9, profit=999), "y")]
    changed = [{**row, "profit_value_ignored": -row["profit_value_ignored"] * 100} for row in base]
    first, _ = cohort._freeze(base)
    second, _ = cohort._freeze(changed)
    assert [(row["cluster_id"], row["origin_event_id"]) for row in first] == [(row["cluster_id"], row["origin_event_id"]) for row in second]


def _label(index: int, split: str, *, retained: bool = True, symbol: str = "PEPEUSDT", profit: float = 0.0) -> dict:
    return {"cluster_id": f"cluster_{index:06d}", "event_id": f"event_{index:06d}", "symbol": symbol, "direction": "LONG", "core_end_time": "2024-06-01T00:00:00Z" if split == "train" else "2025-06-01T00:00:00Z", "split": split, "purge_reason": "" if split != "purged" else "support_crosses_time_split", "profit": {"retained": retained, "outcome": "TP" if retained else "TIMEOUT", "gross_r": 3.0 if retained else profit, "net_r": 2.9 if retained else profit}}


def _write_pinned_plan(tmp_path: Path) -> tuple[Path, Path]:
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(_plan(), sort_keys=True), encoding="utf-8")
    contract_path = tmp_path / "training_contract.json"
    contract_path.write_text(json.dumps({"experiment_id": cohort.EXPERIMENT_ID, "quota_scope": "train_independent_retained_events", "minimum_train_winners": 3000, "maximum_train_winners": 5000, "original_plan_sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest()}), encoding="utf-8")
    return plan_path, contract_path


def test_owner_v2_amendment_path_is_portable_on_windows() -> None:
    root = PureWindowsPath("C:/fable")
    amendment = root / "experiments/active/exp-ma-profit3r-20260922-v1/owner_amendment_1500_v2.json"
    assert _repo_relative(amendment, root) == "experiments/active/exp-ma-profit3r-20260922-v1/owner_amendment_1500_v2.json"


def _write_owner_v2_controls(root: Path) -> tuple[Path, Path]:
    exp = root / "experiment"; exp.mkdir(parents=True)
    plan_path = exp / "plan.json"
    plan_path.write_text(json.dumps(_plan(), sort_keys=True), encoding="utf-8")
    original = exp / "training_contract.json"
    original.write_text(json.dumps({"experiment_id": cohort.EXPERIMENT_ID, "quota_scope": "train_independent_retained_events", "minimum_train_winners": 3000, "maximum_train_winners": 5000, "original_plan_sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest()}), encoding="utf-8")
    amendment = exp / "owner_amendment_1500_v2.json"
    amendment.write_text(json.dumps({"schema_version": 1, "experiment_id": cohort.EXPERIMENT_ID, "owner_request": OWNER_V2_REQUEST, "authorized_minimum_train_winners": 1500, "maximum_train_winners": 5000, "quota_scope": "train_independent_retained_events", "original_plan_sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(), "original_training_contract_sha256": hashlib.sha256(original.read_bytes()).hexdigest(), "only_capacity_changed": True, "training_authorized": True, "production_eligible": False}, ensure_ascii=False), encoding="utf-8")
    owner_v2 = exp / "training_contract_owner1500_v2.json"
    owner_v2.write_text(json.dumps({"schema_version": 2, "experiment_id": cohort.EXPERIMENT_ID, "quota_scope": "train_independent_retained_events", "minimum_train_winners": 1500, "maximum_train_winners": 5000, "original_plan_sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(), "original_training_contract_sha256": hashlib.sha256(original.read_bytes()).hexdigest(), "owner_amendment_path": str(amendment.relative_to(root)), "owner_amendment_sha256": hashlib.sha256(amendment.read_bytes()).hexdigest()}), encoding="utf-8")
    return plan_path, owner_v2


def test_owner_v2_opens_at_1500_and_rejects_unapproved_lower_contract(tmp_path, monkeypatch) -> None:
    root = tmp_path / "repo"
    monkeypatch.setattr(cohort, "ROOT", root)
    plan_path, owner_v2 = _write_owner_v2_controls(root)
    labels = [_label(index, "train", symbol=f"COIN{index}USDT") for index in range(1499)]
    below = cohort.select_training_cohort(plan_path, labels, [], root / "detached/below", training_contract=owner_v2)
    assert below["capacity_gate"] is False
    labels.append(_label(1499, "train", symbol="COIN1499USDT"))
    accepted_out = root / "detached/at"
    accepted = cohort.select_training_cohort(plan_path, labels, [], accepted_out, training_contract=owner_v2)
    assert accepted["capacity_gate"] is True
    assert accepted["minimum_train_winners"] == 1500
    assert cohort.verify_selected_cohort(accepted_out / "dataset_ledger.jsonl", accepted_out / "selection_receipt.json", owner_v2)["selected_train_winners"] == 1500
    unapproved = plan_path.parent / "training_contract_unapproved.json"
    unapproved.write_bytes(owner_v2.read_bytes())
    with pytest.raises(cohort.ProfitCohortError, match="unapproved training contract path"):
        cohort.select_training_cohort(plan_path, labels, [], root / "detached/rejected", training_contract=unapproved)


def test_in_memory_or_v2_with_mutated_original_contract_cannot_lower_capacity(tmp_path, monkeypatch) -> None:
    root = tmp_path / "repo"
    monkeypatch.setattr(cohort, "ROOT", root)
    plan_path, owner_v2 = _write_owner_v2_controls(root)
    lowered_plan = _plan(); lowered_plan["discovery"]["minimum_independent_winners"] = 1499
    with pytest.raises(cohort.ProfitCohortError, match="pinned 3000/5000"):
        cohort.select_training_cohort(lowered_plan, [], [], root / "in_memory")
    original = plan_path.parent / "training_contract.json"
    payload = json.loads(original.read_text()); payload["minimum_train_winners"] = 2999
    original.write_text(json.dumps(payload), encoding="utf-8")
    amendment = plan_path.parent / "owner_amendment_1500_v2.json"
    amendment_payload = json.loads(amendment.read_text()); amendment_payload["original_training_contract_sha256"] = hashlib.sha256(original.read_bytes()).hexdigest()
    amendment.write_text(json.dumps(amendment_payload, ensure_ascii=False), encoding="utf-8")
    owner_payload = json.loads(owner_v2.read_text()); owner_payload["original_training_contract_sha256"] = hashlib.sha256(original.read_bytes()).hexdigest(); owner_payload["owner_amendment_sha256"] = hashlib.sha256(amendment.read_bytes()).hexdigest()
    owner_v2.write_text(json.dumps(owner_payload), encoding="utf-8")
    with pytest.raises(cohort.ProfitCohortError, match="original training contract binding drift"):
        cohort.select_training_cohort(plan_path, [], [], root / "v2_rejected", training_contract=owner_v2)


def test_capacity_gate_rejects_2999_and_keeps_selection_ledger(tmp_path) -> None:
    labels = [_label(index, "train") for index in range(2999)] + [_label(4000, "val")]
    receipt = cohort.select_training_cohort(_plan(), labels, [], tmp_path / "out")
    assert receipt["capacity_gate"] is False
    assert not (tmp_path / "out/dataset_ledger.jsonl").read_text()
    selection = [json.loads(line) for line in (tmp_path / "out/selection_ledger.jsonl").read_text().splitlines()]
    assert next(row for row in selection if row["split"] == "val")["dataset_reason"] == "capacity_gate_closed"


def test_nested_profit_frozen_val_split_reference_and_receipt_validator(tmp_path) -> None:
    plan_path, contract_path = _write_pinned_plan(tmp_path)
    labels = [_label(index, "train", symbol=f"COIN{index}USDT", profit=float(index)) for index in range(5001)]
    labels.extend([_label(6000, "val", symbol="1000PEPEUSDT"), _label(6001, "purged"), _label(6002, "test", symbol="OTHERUSDT")])
    refs = [{"symbol": "PEPE_USDT_SWAP", "anchor_time": "2025-06-01T02:00:00Z"}]
    receipt = cohort.select_training_cohort(plan_path, labels, refs, tmp_path / "out")
    assert receipt["capacity_gate"] and receipt["train_retained_winners_selected"] == 5000
    ledger = [json.loads(line) for line in (tmp_path / "out/dataset_ledger.jsonl").read_text().splitlines()]
    assert all(row["split"] != "val" for row in ledger)  # ±4h reference exclusion only applies to evaluation.
    assert all(row["split"] != "purged" for row in ledger)
    assert any(row["split"] == "test" for row in ledger)  # train cap never deletes resolved evaluation events.
    assert cohort.verify_selected_cohort(tmp_path / "out/dataset_ledger.jsonl", tmp_path / "out/selection_receipt.json", contract_path)["verified"]
    receipt_data = json.loads((tmp_path / "out/selection_receipt.json").read_text())
    assert receipt_data["events"] == [{key: row[key] for key in ("event_id", "cluster_id", "split", "canonical_asset", "direction", "core_end_time")} for row in ledger]


def _resolved_profit(kind: str) -> dict:
    """Use the production resolver, not a hand-written outcome schema."""

    future = {
        "TP": (10.0, 13.0, 10.0, 12.0),
        "SL": (10.0, 10.0, 9.0, 9.5),
        "TIMEOUT": (10.0, 11.0, 10.0, 10.5),
    }[kind]
    frame = pd.DataFrame({
        "open_time": pd.date_range("2024-01-01T00:00:00Z", periods=3, freq="h"),
        "open": [10.0, 10.0, future[0]], "high": [10.0, 10.0, future[1]],
        "low": [9.0, 10.0, future[2]], "close": [10.0, 10.0, future[3]],
    })
    result = resolve_ma_profit_event(frame, 0, 0, "LONG", bar_minutes=60, confirmation_bars=1, horizon_hours=1)
    assert result["outcome"] == kind
    return result


def test_real_resolver_tp_sl_timeout_are_all_resolved_validation_events(tmp_path) -> None:
    labels = [_label(index, "train", symbol=f"TRAIN{index}USDT") for index in range(3000)]
    for offset, outcome in enumerate(("TP", "SL", "TIMEOUT")):
        row = _label(7000 + offset, "val", symbol=f"EVAL{offset}USDT")
        row["profit"] = _resolved_profit(outcome)
        labels.append(row)
    cohort.select_training_cohort(_plan(), labels, [], tmp_path / "out")
    ledger = [json.loads(line) for line in (tmp_path / "out/dataset_ledger.jsonl").read_text().splitlines()]
    assert {row["profit"]["outcome"] for row in ledger if row["split"] == "val"} == {"TP", "SL", "TIMEOUT"}


def test_validator_rejects_sha_drift(tmp_path) -> None:
    plan_path, contract_path = _write_pinned_plan(tmp_path)
    labels = [_label(index, "train") for index in range(3000)]
    cohort.select_training_cohort(plan_path, labels, [], tmp_path / "out")
    ledger = tmp_path / "out/dataset_ledger.jsonl"
    ledger.write_text(ledger.read_text() + "{}\n", encoding="utf-8")
    with pytest.raises(cohort.ProfitCohortError, match="SHA drift"):
        cohort.verify_selected_cohort(ledger, tmp_path / "out/selection_receipt.json", contract_path)
