"""Metadata-only counterexamples for cross-pool temporal overlap semantics."""
from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest

from yoyo.datasets import owner_hl2_overlap as mod


def fixtures(base=None, shift=0, venue="okx", direction="LONG"):
    base = base or datetime(2025, 1, 1, tzinfo=timezone.utc)
    time = lambda i: (base + i * mod.BAR).isoformat()
    path = "data/kline_fetched/okx_BTC_USDT_SWAP_15m_1000.csv"
    old = {"review_id": "old", "box_id": "old-box", "source_path": path, "symbol": "BTC_USDT_SWAP",
        "owner_side": "long", "original_split": "train", "main_start_i": 3, "main_end_i": 18,
        "main_start_time": time(3), "main_end_time": time(18), "cut_global": 15, "cut_time": time(15),
        "original_geometry": {"source_start_i": 9, "source_end_i": 15, "window_start_i": 0},
        "baseline": {"core_start_i": 10, "core_end_i": 13},
        "original_reference_allowed": base + 200 * mod.BAR <= mod.HOLDOUT_START,
        "original_reference_check": {"window_start_bar_open": time(0), "window_end_bar_open": time(199),
            "window_available_at": time(200), "allowed": base + 200 * mod.BAR <= mod.HOLDOUT_START},
        "future": {"start_bar_open": time(3), "review_end_bar_open": time(58), "main_available_at": time(19),
            "review_available_at": time(59), "actual_future_bars": 40},
        "asset_roles": {"image": "old.png"}, "assets": {"old.png": "old-sha"}}
    new_path = path if venue == "okx" else "data/binance/binance_um_BTCUSDT_15m_1000.csv"
    v = {"review_id": "new", "dataset_sample_id": "aa", "direction": direction, "split": "train",
        "core_start_time": time(10 + shift), "core_end_time": time(13 + shift),
        "window_start_time": time(5 + shift), "window_end_time": time(22 + shift),
        "window_start_i": 5 + shift, "window_end_i": 22 + shift,
        "source_core_start_i": 10 + shift, "source_core_end_i": 13 + shift,
        "image_path": f"images/train/A00001_BTC_USDT_SWAP_{direction}_aa.png", "is_representative": True}
    new = {"review_id": "new", "source_event_id": "new-event", "source_path": new_path,
        "direction": direction, "split": "train", "main_start_time": time(5 + shift), "main_end_time": time(22 + shift),
        "representative_sample_id": "aa", "variant_sample_ids": ["aa"], "lineage": [v],
        "future": {"input_start_bar_open": time(5 + shift), "input_available_at": time(23 + shift),
            "review_end_bar_open": time(62 + shift), "review_available_at": time(63 + shift), "actual_future_bars": 40},
        "asset_roles": {"image": "new.png"}, "assets": {"new.png": "new-sha"}}
    return old, new


def test_same_source_exact_core_keeps_two_identities_and_never_transfers_labels():
    old, new = fixtures()
    result = mod.audit_overlap([old], [new])
    p = result["pairs"][0]
    assert "same_source_core_identity" in p["relations"]
    assert p["old_review_id"] == "old" and p["new_review_id"] == "new"
    assert p["duplicate_review_candidate"] and not p["automatic_label_transfer"]
    assert result["safety"]["identities_merged"] == 0
    assert result["counts"]["exact_main_image_sha_pairs"] == 0


def test_adjacent_closed_open_cores_are_not_intersections_or_duplicates():
    old, new = fixtures(shift=4)
    result = mod.audit_overlap([old], [new])
    p = result["pairs"][0]
    assert "core_overlap" not in p["relations"]
    assert "owner_box_core_overlap" in p["relations"]
    assert not p["duplicate_review_candidate"]
    assert result["relations"]["core_overlap"]["pair_count"] == 0


def test_cross_venue_same_clock_is_separate_not_same_source():
    old, new = fixtures(venue="binance_um")
    result = mod.audit_overlap([old], [new])
    p = result["pairs"][0]
    assert "core_exact_time" in p["relations"] and "same_source_core_identity" not in p["relations"]
    assert not p["same_venue"] and not p["duplicate_review_candidate"]
    assert result["relations"]["core_overlap"]["cross_venue"] == 1


def test_opposite_direction_is_not_identical_source_core_identity():
    old, new = fixtures(direction="SHORT")
    result = mod.audit_overlap([old], [new])
    assert not result["pairs"][0]["same_direction"]
    assert result["relations"]["same_source_core_identity"]["pair_count"] == 0
    assert result["relations"]["core_overlap"]["different_direction"] == 1
    assert result["safety"]["automatic_label_transfer"] is False


def test_different_core_with_shared_wide_context_is_not_duplicate_review():
    old, new = fixtures(shift=100)
    result = mod.audit_overlap([old], [new])
    assert result["pairs"][0]["relations"] == ["original_w200_variant_overlap"]
    assert not result["pairs"][0]["duplicate_review_candidate"]


def test_w200_adjacent_to_variant_union_does_not_overlap():
    old, new = fixtures(shift=195)
    result = mod.audit_overlap([old], [new])
    assert result["pairs"] == []
    assert result["unmatched_old_review_ids"] == ["old"]


def test_all_variants_expand_visible_union_without_changing_event_count():
    old, new = fixtures(shift=195)
    v = deepcopy(new["lineage"][0]); v.update(dataset_sample_id="bb", is_representative=False)
    v["window_start_i"] -= 2
    v["window_start_time"] = (mod.stamp(v["window_start_time"]) - 2 * mod.BAR).isoformat()
    new["lineage"].append(v); new["variant_sample_ids"].append("bb")
    result = mod.audit_overlap([old], [new])
    assert result["counts"]["new_rows"] == 1 and result["counts"]["new_variants"] == 2
    assert result["pairs"][0]["relations"] == ["original_w200_variant_overlap"]


@pytest.mark.parametrize("which", ["main", "variant", "future", "naive", "index", "source", "symbol", "membership"])
def test_all_metadata_is_gated_before_pairing(which):
    old, new = fixtures()
    if which == "main": old["main_end_time"] = mod.HOLDOUT_START.isoformat()
    elif which == "variant": new["lineage"][0]["window_end_time"] = mod.HOLDOUT_START.isoformat()
    elif which == "future": new["future"]["review_end_bar_open"] = mod.HOLDOUT_START.isoformat()
    elif which == "naive": old["cut_time"] = "2025-01-01T03:45:00"
    elif which == "index": new["lineage"][0]["source_core_start_i"] += 1
    elif which == "source": new["source_path"] = "data/../outside.csv"
    elif which == "symbol": old["symbol"] = "ETH_USDT_SWAP"
    else: new["variant_sample_ids"] = ["different"]
    with pytest.raises(ValueError): mod.audit_overlap([old], [new])


def test_old_blocked_w200_only_contributes_preholdout_timestamp_portion():
    old, new = fixtures(base=mod.HOLDOUT_START - 100 * mod.BAR)
    result = mod.audit_overlap([old], [new])
    assert result["counts"]["old_w200_clipped_at_holdout"] == 1
    assert result["pairs"][0]["old_intervals"]["original_w200"][1] == mod.HOLDOUT_START.isoformat()
    assert result["safety"]["holdout_content_read"] is False


def test_source_gate_precedes_formal_output(tmp_path, monkeypatch):
    def blocked(): raise ValueError("uncommitted source")
    monkeypatch.setattr(mod, "committed_source", blocked)
    with pytest.raises(ValueError): mod.run(tmp_path / "out.json")
    assert list(tmp_path.iterdir()) == []


def test_duplicate_identity_cannot_be_silently_counted_twice():
    old, new = fixtures()
    with pytest.raises(ValueError): mod.audit_overlap([old, deepcopy(old)], [new])
