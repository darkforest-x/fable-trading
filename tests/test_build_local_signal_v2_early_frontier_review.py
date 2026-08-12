from collections import Counter

from scripts.build_local_signal_v2_early_frontier_review import (
    BLOCKS,
    HOLDOUT_START,
    allocate_block_quotas,
    allocate_stratum_quotas,
    block_specs,
    select_review_rows,
)


def test_block_contract_is_new_preholdout_and_has_48_future_bars(tmp_path) -> None:
    specs = block_specs(tmp_path)
    assert len(specs) == 8
    assert [spec["block_id"] for spec in specs] == [block_id for block_id, _end in BLOCKS]
    assert all(spec["audit_end"] < HOLDOUT_START for spec in specs)
    assert all((spec["audit_end"] - spec["scan_end"]).total_seconds() == 48 * 15 * 60 for spec in specs)


def test_quota_allocator_is_exactly_300_and_150_plus_150() -> None:
    available = {block_id: 60 for block_id, _end in BLOCKS}
    block_quotas = allocate_block_quotas(available)
    strata = allocate_stratum_quotas(block_quotas)
    assert sum(block_quotas.values()) == 300
    assert max(block_quotas.values()) - min(block_quotas.values()) <= 1
    assert sum(value["yes_like"] for value in strata.values()) == 150
    assert sum(value["similar_no_boundary"] for value in strata.values()) == 150


def test_selector_returns_unique_balanced_blind_discovery_rows() -> None:
    rows = []
    for block_number, (block_id, _end) in enumerate(BLOCKS):
        for number in range(60):
            affinity = 3.0 - number / 10 if number < 30 else -(number - 29) / 10
            rows.append(
                {
                    "candidate_block": block_id,
                    "event_id": f"{block_id}:{number}",
                    "symbol": f"S{block_number:02d}_{number % 20:02d}",
                    "owner_yes_affinity": affinity,
                    "nearest_owner_yes_distance": 1.0 + number / 100,
                }
            )
    selected, block_quotas, _stratum_quotas = select_review_rows(rows)
    assert len(selected) == 300
    assert len({row["event_id"] for row in selected}) == 300
    assert Counter(row["retrieval_stratum_internal"] for row in selected) == Counter(
        {"yes_like": 150, "similar_no_boundary": 150}
    )
    assert Counter(row["candidate_block"] for row in selected) == Counter(block_quotas)
    assert all("owner_verdict" not in row for row in selected)
