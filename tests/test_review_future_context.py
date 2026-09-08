"""Synthetic controls for longer human-only futures and immutable lookup assets."""
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
import pandas as pd
import pytest

from yoyo.datasets import review_future_context as p


def frame(size=390):
    i = np.arange(size)
    center = 100 + np.sin(i / 9) * 2
    return pd.DataFrame({"open_time": pd.date_range("2025-01-01", periods=size, freq="15min", tz="UTC"),
        "open": center - .2, "high": center + 1, "low": center - .6,
        "close": center + .7, "volume": np.ones(size)})


def row():
    return {"dataset_sample_id": "sample0", "sample_kind": "positive", "event_id": "event1",
        "source_path": "data/synthetic.csv", "split": "train", "window_start_i": 200,
        "window_end_i": 217, "window_bars": 18, "window_start_time": "2025-01-03T02:00:00+00:00",
        "window_end_time": "2025-01-03T06:15:00+00:00", "post_bars": 9, "variant_index": 0,
        "image_path": "images/train/sample0.png", "label_path": "labels/train/sample0.txt",
        "image_sha256": "a" * 64, "label_sha256": "b" * 64,
        "moving_average_price_source": "hl2", "annotation_drawn_into_image": False}


def test_complete_150_keeps_all_input_and_historical_constant():
    original = frame()
    window, info = p.bounded_future_window(original, row())
    assert len(window) == 168
    assert info["actual_future_bars"] == info["requested_future_bars"] == 150
    assert info["missing_future_reason"] is None
    pd.testing.assert_frame_equal(window.iloc[:18], original.iloc[200:218])
    assert p.manual.FUTURE_BARS == 40
    assert p.source_read_end([row()]) == pd.Timestamp("2025-01-04T20:00:00Z")


def test_holdout_stops_at_13_and_read_bound_is_exclusive():
    r, raw = row(), frame()
    times = pd.date_range(end=pd.Timestamp(p.HOLDOUT_START) - 14 * p.manual.BAR,
                         periods=218, freq="15min", tz="UTC")
    raw["open_time"] = pd.date_range(times[0], periods=len(raw), freq="15min", tz="UTC")
    r.update(window_start_time=times[200].isoformat(), window_end_time=times[217].isoformat())
    window, info = p.bounded_future_window(raw, r)
    assert len(window) == 31
    assert info["actual_future_bars"] == 13 and info["missing_future_reason"] == "holdout_boundary"
    assert pd.Timestamp(info["review_available_at"]) == p.HOLDOUT_START
    assert p.source_read_end([r]) == p.HOLDOUT_START


@pytest.mark.parametrize("size,expected", [(218, 0), (223, 5), (367, 149)])
def test_source_end_is_honest(size, expected):
    window, info = p.bounded_future_window(frame(size), row())
    assert info["actual_future_bars"] == expected
    assert info["missing_future_reason"] == "source_end"
    assert len(window) == 18 + expected


def test_gap_stops_before_missing_future_and_input_gap_is_rejected():
    raw = frame().drop(index=223).reset_index(drop=True)
    _, info = p.bounded_future_window(raw, row())
    assert (info["actual_future_bars"], info["missing_future_reason"]) == (5, "source_gap")
    with pytest.raises(ValueError, match="original input"):
        p.bounded_future_window(frame().drop(index=204).reset_index(drop=True), row())


def test_render_has_fixed_canvas_full_chart_count_banner_and_separator():
    raw = frame()
    features = p.with_hl2_mas(raw)
    before = features.copy(deep=True)
    content, info = p.render_future(features, row())
    canvas = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_COLOR)
    assert canvas.shape == (742, 1280, 3)
    assert info["chart_transform"]["height"] == 710
    assert info["chart_transform"]["n_bars"] == 168
    assert info["banner_text"] == "INPUT 18  |  FUTURE 150 / 150  |  15m"
    assert 0 < info["input_end_separator_x_px"] < 200
    assert np.any(canvas[:32] != 255) and np.any(canvas[32:] != 255)
    pd.testing.assert_frame_equal(features, before)


def test_truncation_banner_explains_actual_count_in_english():
    _, info = p.render_future(p.with_hl2_mas(frame(223)), row())
    assert "FUTURE 5 / 150" in info["banner_text"] and "SOURCE END" in info["banner_text"]
    assert info["review_only"] and not info["training_eligible"]


@pytest.mark.parametrize("changed_index", [199, 210])
def test_main_replay_rejects_changed_past_prefix_or_input_ohlcv(changed_index):
    raw = frame()
    original_features = p.with_hl2_mas(raw)
    original, tf = p.render_chart(p.manual.checked_window(original_features, row()))
    ok, encoded = cv2.imencode(".png", original)
    assert ok
    content = encoded.tobytes()
    digest = p.replay_main(original_features, row(), asdict(tf), content)
    assert digest == hashlib.sha256(original.tobytes()).hexdigest()
    # A prefix MA change outside the image and a changed candle inside it both fail.
    changed = raw.copy(deep=True)
    changed.loc[changed_index, "high"] += 50
    with pytest.raises(ValueError, match="does not replay"):
        p.replay_main(p.with_hl2_mas(changed), row(), asdict(tf), content)
    assert content == encoded.tobytes()


def metadata_case():
    r = row()
    rid = p.hl2.review_id([r])
    future = {"requested_future_bars": 40, "actual_future_bars": 40,
        "review_end_bar_open": "2025-01-03T16:15:00+00:00",
        "review_available_at": "2025-01-03T16:30:00+00:00", "missing_future_reason": None}
    def record(identity):
        roles = {"image": f"input_images/{identity}.png", "original_image": f"input_images/{identity}.png",
                 "future_image": f"future_only/images/{identity}.png"}
        return {"review_id": identity, "main_start_time": r["window_start_time"],
            "main_end_time": r["window_end_time"], "future": deepcopy(future), "asset_roles": roles,
            "assets": {roles["image"]: "a" * 64, roles["future_image"]: "c" * 64}}
    old, new = record("b" * 24), record(rid)
    old.update(original_reference_allowed=False, original_reference_check={"allowed": False})
    new.update(representative_sample_id=r["dataset_sample_id"], source_path=r["source_path"])
    expected = {}
    for pack, protocol, rec in [(p.export.OLD_PACK, p.export.OLD_PROTOCOL, old),
                                (p.export.NEW_PACK, p.export.NEW_PROTOCOL, new)]:
        data = {"protocol_id": protocol}
        for role, relative in rec["asset_roles"].items():
            data[role] = f"/data/local-files/?d=label_studio/{pack.name}/{relative}"
            data[role + "_sha256"] = rec["assets"][relative]
        expected[rec["review_id"]] = {"data": data}
    return expected, [old], [new], [r]


def test_metadata_whole_population_needs_no_media_open(monkeypatch):
    case = metadata_case()
    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: pytest.fail("media opened in metadata gate"))
    assert len(p.checked_metadata(*case, exact=False)) == 1


@pytest.mark.parametrize("area,field,value", [
    ("old", "main_end_time", "2026-05-04T00:00:00Z"),
    ("new", "main_start_time", "2025-01-03T02:00:00"),
    ("row", "window_end_time", "2026-05-04T00:00:00Z"),
    ("row", "moving_average_price_source", "close"),
    ("new", "source_path", "../outside.csv")])
def test_invalid_metadata_fails_before_media(monkeypatch, area, field, value):
    case = metadata_case()
    target = {"old": case[1][0], "new": case[2][0], "row": case[3][0]}[area]
    target[field] = value
    monkeypatch.setattr(Path, "open", lambda *a, **kw: pytest.fail("invalid metadata opened media"))
    with pytest.raises(ValueError):
        p.checked_metadata(*case, exact=False)


def test_old_future_and_blocked_original_cannot_cross_holdout(monkeypatch):
    case = metadata_case()
    case[1][0]["future"]["review_available_at"] = "2026-05-04T00:15:00Z"
    with pytest.raises(ValueError, match="review input/future"):
        p.checked_metadata(*case, exact=False)
    case = metadata_case()
    case[1][0]["original_reference_allowed"] = True
    case[1][0]["original_reference_check"] = {"allowed": True, "window_available_at": "2026-05-05T00:00:00Z"}
    with pytest.raises(ValueError, match="original reference"):
        p.checked_metadata(*case, exact=False)


@pytest.mark.parametrize("relative", ["../outside.png", "/tmp/outside.png", "images/../../outside.png"])
def test_output_escape_rejected(tmp_path, relative):
    with pytest.raises(ValueError, match="path escapes"):
        p.inside(tmp_path, relative)


def test_output_ancestor_symlink_and_unknown_files_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(p, "PACK", tmp_path / "pack")
    p.PACK.mkdir()
    outside = tmp_path / "outside"; outside.mkdir()
    (p.PACK / "images").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        p.write("images/image.png", b"pixels")
    (p.PACK / "images").unlink()
    (p.PACK / "unknown.json").write_text("{}")
    with pytest.raises(ValueError, match="unknown output"):
        p.audit_output(set(), set())


def test_legacy_symlink_preserves_exact_bytes_and_rejects_changed_target(tmp_path, monkeypatch):
    monkeypatch.setattr(p, "ROOT", tmp_path)
    monkeypatch.setattr(p, "PACK", tmp_path / "new")
    source = tmp_path / "old.png"; source.write_bytes(b"frozen forty-bar pixels")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    result = p.link_legacy("a" * 24, source, digest)
    link = p.PACK / result["image_path"]
    assert link.is_symlink() and link.read_bytes() == source.read_bytes()
    assert p.link_legacy("a" * 24, source, digest) == result
    source.write_bytes(b"changed")
    with pytest.raises(ValueError, match="target changed"):
        p.link_legacy("a" * 24, source, digest)


def test_legacy_regular_unknown_file_and_wrong_link_are_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(p, "ROOT", tmp_path)
    monkeypatch.setattr(p, "PACK", tmp_path / "new")
    source = tmp_path / "old.png"; source.write_bytes(b"frozen")
    digest = hashlib.sha256(b"frozen").hexdigest()
    link = p.PACK / p.image_relative("a" * 24)
    link.parent.mkdir(parents=True)
    link.write_bytes(b"frozen")
    with pytest.raises(ValueError, match="registered symlink"):
        p.link_legacy("a" * 24, source, digest)
    link.unlink()
    other = tmp_path / "other.png"; other.write_bytes(b"frozen")
    link.symlink_to(other)
    with pytest.raises(ValueError, match="lookup target changed"):
        p.link_legacy("a" * 24, source, digest)


def test_future_only_directory_rejects_labels_and_changed_generated_pixels(tmp_path, monkeypatch):
    monkeypatch.setattr(p, "PACK", tmp_path / "pack")
    relative = p.image_relative("a" * 24)
    p.write(relative, b"future")
    p.write(relative, b"future")
    with pytest.raises(ValueError, match="existing output differs"):
        p.write(relative, b"replacement")
    (p.PACK / "labels").mkdir()
    with pytest.raises(ValueError, match="contains labels"):
        p.audit_output({relative}, set())


def test_prefix_reader_never_converts_holdout_values(tmp_path):
    source = tmp_path / "source.csv"
    source.write_text("open_time,open,high,low,close,volume\n"
        "2026-05-03T23:45:00Z,100,101,99,100,1\n"
        "2026-05-04T00:00:00Z,FORBIDDEN,FORBIDDEN,FORBIDDEN,FORBIDDEN,FORBIDDEN\n")
    raw, audit = p.manual.read_preholdout_prefix(source, end_exclusive=pd.Timestamp(p.HOLDOUT_START))
    assert len(raw) == 1 and audit["holdout_ohlcv_rows_materialized"] == 0
    assert audit["boundary_timestamp_rows_inspected"] == 1
