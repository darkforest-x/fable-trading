"""The migration must not invent causal anchors.

Seeding causal_onset_i from box_end_i would fill thousands of rows instantly and
silently reintroduce the exact semantics Phase 3 exists to replace: the box edge
marks where owner drew after seeing what followed. If migration seeded it, the
pilot could never detect the difference -- it would be measuring agreement with
a number the code wrote.
"""
import json

from yoyo.layers.l1_detection.onset.events.migration import box_bar_span, migrate_library
from yoyo.layers.l1_detection.onset.events.validator import validate_many

ANCHORS = ("formation_start_i", "causal_onset_i", "formation_confirm_i", "launch_i")


def _library(tmp_path, n=3):
    pats = []
    for i in range(n):
        pats.append({
            "pattern_id": f"dense_{i:05d}",
            "source": "golden_pool",
            "symbol": "ETH_USDT_SWAP",
            "timeframe": "15m",
            "signal_i": 8850 + i,
            "window": {"bars": 200, "start_i": 8700 + i, "end_i": 8899 + i},
            "bbox_xywhn": [0.75, 0.5, 0.12, 0.2],
            "human_label": "A" if i == 0 else None,
            "human_reviewed_at": "2026-08-06T00:00:00Z" if i == 0 else None,
        })
    p = tmp_path / "lib.json"
    p.write_text(json.dumps({"patterns": pats}))
    return p


def test_every_causal_anchor_is_null_after_migration(tmp_path):
    events = migrate_library(_library(tmp_path, 5))
    assert len(events) == 5
    for ev in events:
        for k in ANCHORS:
            assert ev["anchors"][k] is None, f"{k} must not be auto-filled"


def test_onset_is_not_seeded_from_box_edge(tmp_path):
    events = migrate_library(_library(tmp_path, 3))
    for ev in events:
        assert ev["anchors"]["causal_onset_i"] != ev["original_box"]["box_end_i"]
        assert ev["anchors"]["causal_onset_i"] is None


def test_box_span_is_pure_geometry():
    lo, hi = box_bar_span([0.75, 0.5, 0.12, 0.2], window_start_i=8700, window_bars=200)
    assert lo == 8700 + round((0.75 - 0.06) * 200)
    assert hi == 8700 + round((0.75 + 0.06) * 200)
    assert lo < hi


def test_box_span_none_when_no_box():
    assert box_bar_span(None, 8700, 200) == (None, None)


def test_migrated_records_pass_validation(tmp_path):
    events = migrate_library(_library(tmp_path, 4))
    report = validate_many(events)
    assert report["n_errors"] == 0
    assert report["schema_valid_rate"] == 1.0


def test_quality_label_carries_over_but_validity_stays_unreviewed(tmp_path):
    events = migrate_library(_library(tmp_path, 2))
    assert events[0]["quality_label"] == "A"
    # a quality grade says nothing about whether causal anchors were reviewed
    assert events[0]["event_validity"] == "unreviewed"


def test_provenance_records_library_hash(tmp_path):
    events = migrate_library(_library(tmp_path, 1))
    sha = events[0]["provenance"]["pattern_library_sha256"]
    assert isinstance(sha, str) and len(sha) == 64
