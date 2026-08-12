import json

from scripts.build_local_signal_v2_semantic_review import (
    CANARY_QUOTAS,
    draw_decision_boundary,
    pair_canary_events,
    render_review_html,
    round_robin_stratified,
    tercile_bucket,
)
import numpy as np


def test_pair_canary_events_reproduces_retained_and_unique_sets() -> None:
    r1 = [
        {"event_id": "a", "symbol": "ETH", "core_mid_i": 10, "decision_i": 14},
        {"event_id": "b", "symbol": "BTC", "core_mid_i": 20, "decision_i": 24},
    ]
    r2 = [
        {"event_id": "c", "symbol": "ETH", "core_mid_i": 12, "decision_i": 15},
        {"event_id": "d", "symbol": "SOL", "core_mid_i": 30, "decision_i": 34},
    ]
    pairs, r1_only, r2_only = pair_canary_events(r1, r2, gap_bars=5)
    assert [(left["event_id"], right["event_id"]) for left, right in pairs] == [("a", "c")]
    assert [row["event_id"] for row in r1_only] == ["b"]
    assert [row["event_id"] for row in r2_only] == ["d"]


def test_stratified_selector_is_deterministic_and_exact() -> None:
    rows = [
        {"event_id": f"e{i}", "symbol": f"s{i % 7}", "bucket": str(i % 3)}
        for i in range(50)
    ]
    first = round_robin_stratified(rows, 20, stratum_fields=("bucket",), salt="x")
    second = round_robin_stratified(rows, 20, stratum_fields=("bucket",), salt="x")
    assert [row["event_id"] for row in first] == [row["event_id"] for row in second]
    assert len({row["event_id"] for row in first}) == 20


def test_owner_html_blinds_internal_source_and_has_only_three_choices(tmp_path) -> None:
    rows = [{"review_id": "S001", "symbol": "ETH", "image_path": "analysis/output/x/S001.png"}]
    page = render_review_html(rows, tmp_path / "index.html")
    assert "YES" in page and "NO" in page and "SKIP" in page
    assert "Y=YES" in page and "N=NO" in page and "S=SKIP" in page
    assert "model_confidence" not in page
    assert "source_model" not in page
    assert "common_retained" not in page
    assert "future" not in json.loads(page.split("const ITEMS=", 1)[1].split(";let index", 1)[0])[0]
    assert CANARY_QUOTAS == {"common_retained": 50, "r2_new": 25, "r1_suppressed": 25}


def test_decision_boundary_changes_only_a_narrow_right_edge() -> None:
    image = np.full((742, 1280, 3), 255, dtype=np.uint8)
    draw_decision_boundary(image)
    changed = np.any(image != 255, axis=2)
    assert changed.any()
    assert changed[:, :1100].sum() < 500


def test_tercile_bucket_uses_population_relative_boundaries() -> None:
    values = list(range(9))
    assert tercile_bucket(0, values) == "low"
    assert tercile_bucket(4, values) == "mid"
    assert tercile_bucket(8, values) == "high"
