"""Review catalog safety: primary-only pixels, identity retention and no false GT."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from yoyo.datasets import owner_box_curation as c


def row(number=0):
    rid = f"review{number:03d}"
    rel = f"annotation_images/{rid}.png"
    return {"review_id": rid, "box_id": f"box{number}", "asset_roles": {"image": rel},
        "assets": {rel: str(number)}, "main_start_time": "2025-07-01T00:00:00+00:00",
        "main_end_time": "2025-07-01T04:45:00+00:00", "owner_side": "short",
        "main_canvas_role": "review_only_not_a_training_input", "chart_transform": {"width": 1280, "height": 742},
        "new_gold": False, "sample_owner_geometry_confirmed": False, "training_eligible": False, "production_eligible": False,
        "proposal": {"x0": 10, "y0": 20, "x1": 110, "y1": 140, "core_bars": 5},
        "wick_reference_box": {"x0": 12, "y0": 30, "x1": 108, "y1": 130},
        "exact_star": number == 0, "symbol": "BTC_USDT_SWAP", "original_split": "train",
        "original_window_dependency_id": f"dep{number}", "alias_candidate_group": f"alias{number}"}


def quality(rows):
    return {"rows": [{"review_id": r["review_id"], "box_id": r["box_id"], "issues": [], "retained": True} for r in rows],
        "groups": {"exact_duplicates": [], "near_duplicates": []}}


def queue(rows, q=None, pairs=None):
    return c.make_queue(rows, quality(rows) if q is None else q,
        {"possible_visible_negative_conflict": {"pairs": pairs or []}},
        {r["review_id"]: i + 10 for i, r in enumerate(rows)})


def test_primary_role_passes_but_reference_or_future_role_is_refused():
    r = row()
    assert c.validate_row(r).parent == c.PACK / "annotation_images"
    for rel in ("future_only/images/review000.png", "original_reference_only/review000.jpg",
                "comparison_images/review000.png", "annotation_images/../future_only/review000.png"):
        r["asset_roles"]["image"] = rel
        with pytest.raises(ValueError, match="primary"):
            c.validate_row(r)


@pytest.mark.parametrize("value", ["2026-05-04T00:00:00+00:00", "2025-07-01T04:45:00"])
def test_late_or_naive_window_fails(value):
    r = row(); r["main_end_time"] = value
    with pytest.raises(ValueError, match="window"):
        c.validate_row(r)


@pytest.mark.parametrize("key", ["new_gold", "sample_owner_geometry_confirmed", "training_eligible", "production_eligible"])
def test_cannot_promote_geometry_proposal_through_catalog(key):
    r = row(); r[key] = True
    with pytest.raises(ValueError, match="eligibility"):
        c.validate_row(r)


@pytest.mark.parametrize("change", [{"x0": -1}, {"x1": 1281}, {"y1": float("nan")}, {"y0": True}])
def test_bad_rectangles_fail_before_import(change):
    r = row(); r["proposal"].update(change)
    with pytest.raises(ValueError):
        c.validate_row(r)


def test_ranking_retains_every_identity_and_source_split_without_changing_labels():
    rows = [row(i) for i in range(60)]; original = deepcopy(rows)
    q = quality(rows); q["groups"]["exact_duplicates"] = [["review002", "review003"]]
    result = queue(rows, q, [{"owner_box_id": "box59", "current_event_key": "negative:one"}])
    assert [r["review_id"] for r in result["rows"][:3]] == ["review059", "review002", "review003"]
    assert len(result["rows"]) == 60 and len(result["views"][0]["review_ids"]) == 50
    assert rows == original
    assert all(r["source_split"] == "train" and r["owner_side"] == "short" for r in result["rows"])
    assert result["ranking_is_label_quality_probability"] is False
    assert result["all_records_retained"] is True


def test_quality_missing_identity_or_deleted_row_cannot_build_queue():
    rows = [row(0), row(1)]
    q = quality(rows); q["rows"].pop()
    with pytest.raises(ValueError, match="cover every"):
        queue(rows, q)
    q = quality(rows); q["rows"][0]["retained"] = False
    with pytest.raises(ValueError, match="remove"):
        queue(rows, q)
    q = quality(rows); q["rows"][0]["box_id"] = "another Owner box"
    with pytest.raises(ValueError, match="different Owner box"):
        queue(rows, q)


def test_primary_symlink_cannot_redirect_to_another_role(tmp_path, monkeypatch):
    monkeypatch.setattr(c, "PACK", tmp_path)
    r = row()
    path = tmp_path / r["asset_roles"]["image"]
    path.parent.mkdir()
    path.symlink_to(tmp_path / "future_only" / "same-image.png")
    with pytest.raises(ValueError, match="redirects"):
        c.validate_row(r)


def test_duplicate_foreign_group_is_not_silently_dropped():
    rows = [row(0), row(1)]; q = quality(rows)
    q["groups"]["near_duplicates"] = [["review000", "foreign"]]
    with pytest.raises(ValueError, match="duplicate"):
        queue(rows, q)


def test_envelope_ratio_is_descriptive_and_future_fields_do_not_affect_it():
    rows = [row(0), row(1)]
    first = queue(rows)
    rows[0]["future"] = {"return": 999, "prices": [99999]}
    assert queue(rows) == first
    assert first["rows"][0]["envelope_expansion_ratio"] == 1.2


def test_catalog_reentry_checks_real_box_not_only_stored_identity():
    record = queue([row()])["rows"][0]
    class Sample(dict):
        pass
    sample = Sample({k: v for k, v in record.items() if k != "proposal_bbox"})
    sample.filepath = str(c.PACK / record["image_relative"])
    sample.proposal_boxes = SimpleNamespace(detections=[SimpleNamespace(label="SHORT", bounding_box=record["proposal_bbox"], confidence=None)])
    sample.tags = sorted(set(record["audit_flags"] + ["review_only", "unconfirmed_proposal"]))
    c.verify_sample(sample, record)
    sample.proposal_boxes.detections[0].bounding_box = [.1, .1, .3, .3]
    with pytest.raises(ValueError, match="proposal changed"):
        c.verify_sample(sample, record)


def test_frozen_outputs_allow_equal_reentry_and_reject_changes(tmp_path: Path):
    path = tmp_path / "out.json"
    c.frozen_json(path, {"count": 4}); before = path.read_bytes()
    c.frozen_json(path, {"count": 4})
    with pytest.raises(ValueError, match="Frozen"):
        c.frozen_json(path, {"count": 5})
    assert path.read_bytes() == before
