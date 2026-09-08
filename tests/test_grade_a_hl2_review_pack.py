"""Synthetic-only contracts for copied HL2 pixels, actual labels and safe future."""
from copy import deepcopy
from dataclasses import asdict
import hashlib
from pathlib import Path

import cv2
import numpy as np
import pandas as pd
import pytest

from yoyo.datasets import grade_a_hl2_review_pack as p
from yoyo.layers.l1_detection.render import make_chart_transform, render_chart


def frame(size=270):
    i = np.arange(size)
    center = 100+np.sin(i/9)*2
    return pd.DataFrame({"open_time": pd.date_range("2025-01-01", periods=size, freq="15min", tz="UTC"),
        "open": center-.2, "high": center+1, "low": center-.6, "close": center+.7, "volume": np.ones(size)})


def row(index=0, kind="positive"):
    r = {"dataset_sample_id": f"sample{index}", "sample_kind": kind, "event_id": "event1",
        "negative_event_id": "negative1", "source_path": "data/synthetic.csv", "split": "train",
        "direction": "LONG", "window_start_i": 200, "window_end_i": 217, "window_bars": 18,
        "window_start_time": "2025-01-03T02:00:00+00:00", "window_end_time": "2025-01-03T06:15:00+00:00",
        "variant_id": f"v{index}", "variant_index": index, "pre_bars": 5, "post_bars": 9, "core_bars": 4,
        "source_core_start_i": 205, "source_core_end_i": 208, "source_comparison_anchor_i": 210,
        "core_start_time": "2025-01-03T03:15:00+00:00", "core_end_time": "2025-01-03T04:00:00+00:00",
        "moving_average_price_source": "hl2", "canvas_transform_source": "baseline_close_transform",
        "annotation_drawn_into_image": False, "boxes_per_image": 1 if kind == "positive" else 0,
        "box": {"cx_norm": .55, "cy_norm": .35, "w_norm": .2, "h_norm": .18},
        "image_path": f"images/train/sample{index}.png", "label_path": f"labels/train/sample{index}.txt",
        "image_sha256": "a"*64, "label_sha256": "b"*64,
        "baseline_image_sha256": "c"*64, "baseline_label_sha256": "d"*64}
    return r


def old_lineage(r):
    prior = {k: r[k] for k in p.manual.VARIANT_FIELDS}
    prior["image_sha256"] = r["baseline_image_sha256"]
    prior["label_sha256"] = r["baseline_label_sha256"]
    prior["chart_transform"] = asdict(make_chart_transform(p.manual.add_six_mas(frame()).iloc[200:218]))
    return prior


def png(image):
    ok, data = cv2.imencode(".png", image)
    assert ok
    return data.tobytes()


def test_metadata_all_rows_gated_and_negative_inventory_only(monkeypatch):
    a, b = row(), row(1, "negative")
    monkeypatch.setattr(Path, "open", lambda *a, **k: pytest.fail("metadata opened assets"))
    groups, transforms, stats = p.metadata([a, b], [old_lineage(a), old_lineage(b)], expected=False)
    assert list(groups) == [("positive", "event1")]
    assert stats["negative_events"] == 1 and stats["negative_inventory_metadata_only"]
    assert len(transforms) == 2


@pytest.mark.parametrize("key,value", [("window_end_time", "2026-05-04T00:00:00Z"),
    ("moving_average_price_source", "close"), ("split", "test"), ("source_core_start_i", 206)])
def test_bad_metadata_rejected(key, value):
    r = row(); old = old_lineage(r); r[key] = value
    with pytest.raises(ValueError):
        p.metadata([r], [old], expected=False)


def test_close_transform_lineage_rejects_wrong_baseline_and_split():
    r = row(); old = old_lineage(r); old["image_sha256"] = "e"*64
    with pytest.raises(ValueError, match="lineage"):
        p.metadata([r], [old], expected=False)


def test_actual_yolo_label_maps_to_ls_without_reboxing():
    label = p.parse_label(b"0 0.55 0.35 0.2 0.18\n", row())
    value = label["ls_value"]
    assert value["x"] == pytest.approx(45) and value["y"] == pytest.approx(26)
    assert value["width"] == 20 and value["height"] == 18
    assert label["xyxy_px"] == pytest.approx([576, 192.92, 832, 326.48])
    assert value["rectanglelabels"] == ["多头"]


@pytest.mark.parametrize("data", [b"", b"2 .5 .5 .2 .2\n", b"1 .55 .35 .2 .18\n",
    b"0 nan .35 .2 .18\n", b"0 .99 .35 .2 .18\n", b"0 .55 .35 .2 .18\n0 .55 .35 .2 .18\n"])
def test_bad_actual_labels_rejected(data):
    with pytest.raises(ValueError):
        p.parse_label(data, row())


def test_hl2_prefix_replay_future_mutation_and_no_main_mutation():
    r, raw = row(), frame()
    tf = old_lineage(r)["chart_transform"]
    main, _ = render_chart(p.with_hl2_mas(raw).iloc[200:218], fixed_transform=p.ChartTransform(**tf))
    original = png(main)
    before = hashlib.sha256(original).hexdigest()
    future, meta = p.render_future(raw, r, tf, original)
    assert meta["actual_future_bars"] == 40 and meta["review_bars"] == 58
    assert meta["moving_average_price_source"] == "hl2" and not meta["used_for_proposal"]
    changed = raw.copy(); changed.loc[218:, ["high", "low", "close", "open"]] += 25
    altered, other = p.render_future(changed, r, tf, original)
    assert future != altered
    assert meta["main_replay_pixel_sha256"] == other["main_replay_pixel_sha256"]
    assert hashlib.sha256(original).hexdigest() == before
    wrong, _ = render_chart(p.manual.add_six_mas(raw).iloc[200:218], fixed_transform=p.ChartTransform(**tf))
    with pytest.raises(ValueError, match="replay"):
        p.render_future(raw, r, tf, png(wrong))


def test_future_gap_truncation_and_holdout_close_gate():
    r, raw = row(), frame()
    raw.loc[221:, "open_time"] += pd.Timedelta(minutes=15)
    tf = old_lineage(r)["chart_transform"]
    main, _ = render_chart(p.with_hl2_mas(raw).iloc[200:218], fixed_transform=p.ChartTransform(**tf))
    _, info = p.render_future(raw, r, tf, png(main))
    assert info["actual_future_bars"] == 3 and info["missing_future_reason"] == "source_gap"
    raw = frame(); shift = pd.Timestamp(p.manual.HOLDOUT_START)-raw.iloc[217]["open_time"]-p.manual.BAR
    raw["open_time"] += shift
    r["window_start_time"] = raw.iloc[200]["open_time"].isoformat()
    r["window_end_time"] = raw.iloc[217]["open_time"].isoformat()
    window, info = p.manual.bounded_future_window(raw, r)
    assert len(window) == 18 and info["actual_future_bars"] == 0
    assert info["missing_future_reason"] == "holdout_boundary"


def test_freeze_uses_current_hl2_bytes_and_task_uses_actual_label(tmp_path, monkeypatch):
    monkeypatch.setattr(p, "ROOT", tmp_path)
    monkeypatch.setattr(p, "DATASET", tmp_path/"dataset")
    monkeypatch.setattr(p, "PACK", tmp_path/"pack")
    r = row(); raw = frame(); tf = old_lineage(r)["chart_transform"]
    im, _ = render_chart(p.with_hl2_mas(raw).iloc[200:218], fixed_transform=p.ChartTransform(**tf))
    content = png(im); label = b"0 0.55 0.35 0.2 0.18\n"
    for kind, value in (("image", content), ("label", label)):
        path = p.DATASET/r[kind+"_path"]; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(value)
        r[kind+"_sha256"] = hashlib.sha256(value).hexdigest()
    labels = p.checked_assets([r])
    record = p.freeze_main([r], {r["dataset_sample_id"]:tf}, labels)
    assert (p.PACK/record["asset_roles"]["image"]).read_bytes() == content
    assert (p.PACK/record["asset_roles"]["comparison_image"]).read_bytes() == content
    assert not (p.PACK/"future_only").exists()
    record["future"] = {"input_bars": 18, "actual_future_bars": 40, "missing_future_reason": None}
    record["assets"][record["asset_roles"]["future_image"]] = "f"*64
    task = p.task_for(record)
    assert task["predictions"][0]["model_version"] == p.PROTOCOL
    assert task["predictions"][0]["result"][0]["value"] == labels[r["dataset_sample_id"]]["ls_value"]
    assert task["data"]["original_image"] == task["data"]["image"]
    assert "非金标" in task["data"]["caption"] and "annotations" not in task
    p.freeze_main([r], {r["dataset_sample_id"]:tf}, labels)
    (p.PACK/record["asset_roles"]["image"]).write_bytes(b"drift")
    with pytest.raises(ValueError, match="existing output differs"):
        p.freeze_main([r], {r["dataset_sample_id"]:tf}, labels)


def test_build_source_gate_precedes_every_data_read(monkeypatch):
    def fail(): raise ValueError("not committed")
    monkeypatch.setattr(p, "source_identity", fail)
    monkeypatch.setattr(Path, "read_text", lambda *a, **k: pytest.fail("read before source gate"))
    with pytest.raises(ValueError, match="not committed"):
        p.build()
