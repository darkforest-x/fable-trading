"""Unit tests for the new train-block hard-negative review builder."""

from __future__ import annotations

import pytest

from scripts import build_owner_short_train_hardneg_newblocks_review as module


def _event(block: str, index: int, *, symbol: str | None = None) -> dict:
    return {
        "candidate_block": block,
        "symbol": symbol or f"S{index}",
        "hard_negative_affinity_v3": 10.0 - index,
        "event_conf_max": 0.5,
        "decision_time": f"2025-01-01T00:{index:02d}:00Z",
        "event_id": f"{block}-{index}",
    }


def test_block_specs_keep_audit_context_inside_declared_block(tmp_path) -> None:
    specs = module.block_specs(tmp_path)

    assert len(specs) == 5
    assert all(spec["audit_end"] - spec["scan_end"] == module.pd.Timedelta(hours=12) for spec in specs)
    assert specs[0]["block_id"] == "C01_20250615"
    assert specs[-1]["block_id"] == "C05_20260215"


def test_diverse_selection_respects_each_block(monkeypatch) -> None:
    monkeypatch.setattr(module, "BLOCKS", (("A", "2025-01-01"), ("B", "2025-02-01")))
    rows = [_event(block, index) for block in ("A", "B") for index in range(4)]

    selected = module.select_hard_negative_diverse(rows, total=6, preferred_per_block=3)

    assert [row["event_id"] for row in selected] == ["A-0", "A-1", "A-2", "B-0", "B-1", "B-2"]


def test_diverse_selection_relaxes_symbol_cap(monkeypatch) -> None:
    monkeypatch.setattr(module, "BLOCKS", (("A", "2025-01-01"),))
    rows = [_event("A", index, symbol="ONLY") for index in range(5)]

    assert len(module.select_hard_negative_diverse(rows, total=5, preferred_per_block=5)) == 5


def test_diverse_selection_rejects_underfilled_block(monkeypatch) -> None:
    monkeypatch.setattr(module, "BLOCKS", (("A", "2025-01-01"),))

    with pytest.raises(ValueError, match="need 2"):
        module.select_hard_negative_diverse([_event("A", 0)], total=2, preferred_per_block=2)


def test_quota_allocator_redistributes_a_sparse_block(monkeypatch) -> None:
    monkeypatch.setattr(
        module,
        "BLOCKS",
        (("A", "2025-01-01"), ("B", "2025-02-01"), ("C", "2025-03-01")),
    )
    rows = (
        [_event("A", 0)]
        + [_event("B", index) for index in range(5)]
        + [_event("C", index) for index in range(5)]
    )

    assert module.allocate_block_quotas(rows, total=6, preferred_per_block=2) == {
        "A": 1,
        "B": 3,
        "C": 2,
    }
