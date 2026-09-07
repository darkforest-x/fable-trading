"""Synthetic identity, geometric locality, and pre-holdout review contracts."""
from copy import deepcopy

import numpy as np
import pandas as pd
import pytest

from yoyo.datasets import owner_box_refinement as pack


def source_row(name="event", side="short", time="2025-01-03T02:00:00Z", cut=200, b0=93, b1=100):
    return {"box_id": name+"__b0", "stem": name, "owner_side": side, "cut_time": time,
        "cut_global": str(cut), "width_bars": str(b1-b0+1), "bar_b0": str(b0), "bar_b1": str(b1),
        "symbol": "TEST_USDT_SWAP", "split": "train", "yolo_xc": ".45", "yolo_yc": ".5",
        "yolo_w": ".1", "yolo_h": ".1", "win_mode": "start", "box_index": "0",
        "preview_path": "previews/never-open.jpg", "image_path": "old/missing.png"}


def mapping(row):
    return {"sample_id": row["box_id"], "symbol": row["symbol"], "owner_side": row["owner_side"],
        "decision_bar": row["cut_global"], "decision_time": row["cut_time"],
        "resolved_source_csv": "data/kline_fetched/synthetic.csv"}


def plan(row=None):
    row = row or source_row()
    return pack.plan_rows([row], [mapping(row)], {})[0][0]


def frame_for(p, extra=40):
    size = p["main_end_i"]+1+extra
    times = pd.date_range(pack.stamp(p["cut_time"])-p["cut_global"]*pack.BAR, periods=size, freq="15min")
    close = 100 + np.sin(np.arange(size)/13)*.3
    return pack.add_six_mas(pd.DataFrame({"open_time": times, "open": close-.1,
        "high": close+.3, "low": close-.3, "close": close, "volume": 1.0}))


def test_plans_preserve_sides_stars_and_all_alias_identities():
    a, b, c, skip = source_row("a"), source_row("b"), source_row("c", "long"), source_row("s", "skip")
    stars = {"c": {"boxes": [{"cx": .45, "cy": .5, "w": .1, "h": .1}], "source_export": "old/export.json"}}
    rows = [a, b, c, skip]
    plans, exclusions = pack.plan_rows(rows, [mapping(r) for r in rows], stars)
    assert len(plans) == 3 and len(exclusions) == 1
    assert plans[0]["box_id"] == "c__b0" and plans[0]["exact_star"]
    assert {p["owner_side"] for p in plans} == {"long", "short"}
    short = [p for p in plans if p["owner_side"] == "short"]
    assert short[0]["alias_candidate_group"] == short[1]["alias_candidate_group"]
    assert all(p["alias_candidate_count"] == 2 for p in short)
    assert len({p["review_id"] for p in plans}) == 3
    assert all(not p["sample_owner_geometry_confirmed"] for p in plans)
    assert all(p["native_ls_reference"] is None for p in plans)


@pytest.mark.parametrize("change", [{"decision_bar": 199}, {"decision_time": "2025-01-02T02:00:00Z"}, {"owner_side": "long"}])
def test_mapping_disagreements_fail_before_source_reads(change):
    row = source_row()
    mapped = {**mapping(row), **change}
    with pytest.raises(ValueError, match="mapping"):
        pack.plan_rows([row], [mapped], {})


def test_duplicate_sheet_or_mapping_identity_fails():
    row = source_row()
    with pytest.raises(ValueError, match="one-to-one"):
        pack.plan_rows([row, row], [mapping(row), mapping(row)], {})


@pytest.mark.parametrize("end", [7, 8, 12, 30])
def test_historical_center_interval_is_preserved(end):
    a, b = pack.central_core(0, end)
    assert b-a+1 == max(4, min(7, (end+2)//2))
    assert abs(a-(end-b)) <= 1


@pytest.mark.parametrize("remaining", [61, 71, 88])
def test_three_long_original_boundary_shapes_never_open_original(monkeypatch, remaining):
    row = source_row(side="long", time="2026-05-03T23:45:00Z", b0=199-remaining-7, b1=199-remaining)
    p = plan(row)
    assert not p["original_reference_allowed"]
    assert pack.stamp(p["main_end_time"])+pack.BAR == pack.HOLDOUT_START
    def forbidden(*args, **kwargs):
        raise AssertionError("disallowed original was opened")
    monkeypatch.setattr(pack.Path, "read_bytes", forbidden)
    assert pack.original_reference(p, b"safe canvas") == (b"safe canvas", "png")


def test_anchor_at_holdout_fails_before_per_sample_reads():
    with pytest.raises(ValueError, match="pre-holdout"):
        plan(source_row(time="2026-05-04T00:00:00Z"))


def test_checked_window_rejects_shifted_source_index_and_missing_main_bar():
    p = plan()
    frame = frame_for(p)
    shifted = frame.copy()
    shifted.loc[p["cut_global"], "open_time"] += pack.BAR
    with pytest.raises(ValueError, match="index/time"):
        pack.checked_window(shifted, p)
    missing = frame.copy()
    missing.loc[p["main_start_i"]+1, "open_time"] += pack.BAR
    with pytest.raises(ValueError, match="continuous"):
        pack.checked_window(missing, p)


def test_proposal_uses_same_core_and_no_outside_values_with_fixed_transform():
    p = plan()
    frame = frame_for(p)
    window = pack.checked_window(frame, p).copy()
    _, tf = pack.render_chart(window)
    before = pack.geometry(window, tf, p)
    a, b = (p["baseline"][k]-p["main_start_i"] for k in ("core_start_i", "core_end_i"))
    columns = ["open", "high", "low", "close", "sma20", "ema20", "sma60", "ema60", "sma120", "ema120"]
    changed = window.copy()
    outside = [i for i in range(len(window)) if not a <= i <= b]
    changed.iloc[outside, changed.columns.get_indexer(columns)] *= 100
    assert pack.geometry(changed, tf, p) == before
    assert before["proposal"]["core_bars"] == b-a+1
    assert before["proposal"]["confirmation_bars_inside_box"] == 0
    assert before["proposal"]["pad_fraction"] == .04


def test_future_mutation_cannot_change_proposal_and_extra_frame_is_refused():
    p = plan()
    frame = frame_for(p)
    window = pack.checked_window(frame, p)
    _, tf = pack.render_chart(window)
    baseline = pack.geometry(window, tf, p)
    frame.loc[p["main_end_i"]+1:, ["high", "low", "close"]] = 999999
    assert pack.geometry(pack.checked_window(frame, p), tf, p) == baseline
    with pytest.raises(ValueError, match="exactly"):
        pack.geometry(frame.iloc[p["main_start_i"]:], tf, p)


def test_main_window_hash_detects_ohlcv_and_ma_changes_but_not_index_labels():
    p = plan()
    window = pack.checked_window(frame_for(p), p).copy()
    digest = pack.window_sha256(window)
    assert pack.window_sha256(window.reset_index(drop=True)) == digest
    for column in ("open", "volume", "ema120"):
        changed = window.copy()
        changed.iloc[0, changed.columns.get_loc(column)] += .01
        assert pack.window_sha256(changed) != digest


@pytest.mark.parametrize("mode,reason", [("complete", None), ("gap", "source_gap"),
                                        ("cross_holdout", "holdout_boundary"), ("short_source", "source_end")])
def test_actual_full_original_clock_gate_ignores_ohlcv_columns(tmp_path, monkeypatch, mode, reason):
    row = source_row(time="2026-05-03T11:30:00Z", b0=143, b1=150)
    p = plan(row)
    assert p["original_reference_allowed"]  # arithmetic says close == holdout
    frame = frame_for(p, extra=100)
    clocks = frame[["open_time"]].copy()
    if mode == "gap":
        # A gap with a clock still before holdout must also block the image.
        clocks.loc[p["main_end_i"]+1:, "open_time"] += pack.BAR
        clocks.loc[:, "open_time"] -= 10*pack.BAR
        p["cut_time"] = (pack.stamp(p["cut_time"])-10*pack.BAR).isoformat()
    elif mode == "cross_holdout":
        clocks.loc[p["main_end_i"]+1:, "open_time"] += 12*pack.BAR
    elif mode == "short_source":
        clocks = clocks.iloc[:p["main_end_i"]+1]
    clocks["ts"] = clocks["open_time"].astype("int64")//1_000_000
    clocks["open"] = "OHLCV MUST NOT BE PARSED"
    source = tmp_path/"source.csv"
    clocks.to_csv(source, index=False)
    real_read = pack.pd.read_csv
    def guarded_read(*args, **kwargs):
        assert kwargs["usecols"] == ["ts", "open_time"]
        return real_read(*args, **kwargs)
    monkeypatch.setattr(pack.pd, "read_csv", guarded_read)
    audit = pack.verify_original_timestamps(source, [p])
    check = audit["checks"][p["review_id"]]
    assert check["allowed"] == (reason is None) and check["reason"] == reason
    assert not audit["ohlcv_materialized"]


def test_original_clock_alias_schema_is_not_silently_accepted(tmp_path):
    p = plan()
    source = tmp_path/"changed-schema.csv"
    source.write_text("timestamp,open\n2025-01-01T00:00:00Z,1\n")
    with pytest.raises(ValueError, match="Usecols"):
        pack.verify_original_timestamps(source, [p])


def test_future_reports_complete_gap_end_and_holdout_truncation():
    p = plan()
    frame = frame_for(p)
    window, m = pack.future_window(frame, p)
    assert m["actual_future_bars"] == 40 and m["missing_future_reason"] is None
    assert len(window) == m["main_bars"]+40
    _, m = pack.future_window(frame.iloc[:p["main_end_i"]+6], p)
    assert (m["actual_future_bars"], m["missing_future_reason"]) == (5, "source_end")
    frame.loc[p["main_end_i"]+3:, "open_time"] += pack.BAR
    _, m = pack.future_window(frame, p)
    assert (m["actual_future_bars"], m["missing_future_reason"]) == (2, "source_gap")
    near = plan(source_row(time="2026-05-03T23:30:00Z"))
    _, m = pack.future_window(frame_for(near), near)
    assert (m["actual_future_bars"], m["missing_future_reason"]) == (0, "holdout_boundary")
    assert pack.stamp(m["review_available_at"]) == pack.HOLDOUT_START


def test_frozen_output_is_idempotent_and_cannot_replace_prior_evidence(tmp_path):
    p = tmp_path/"a.json"
    pack.frozen_write(p, b"a")
    pack.frozen_write(p, b"a")
    with pytest.raises(ValueError, match="differs"):
        pack.frozen_write(p, b"b")
    assert p.read_bytes() == b"a"


@pytest.mark.parametrize("side,label", [("long", "多头"), ("short", "空头")])
def test_ls_predictions_are_only_unconfirmed_proposals(side, label, tmp_path):
    p = plan(source_row(side=side))
    window = pack.checked_window(frame_for(p), p)
    _, tf = pack.render_chart(window)
    record = {**p, **pack.geometry(window, tf, p), "future": {"actual_future_bars": 40, "missing_future_reason": None},
        "asset_roles": {"image": "annotation_images/x.png", "original_image": "original_reference_only/x.jpg",
            "comparison_image": "comparison_images/x.png", "future_image": "future_only/images/x.png"}}
    record["assets"] = {r: "a"*64 for r in record["asset_roles"].values()}
    task = pack.task_for(record, tmp_path)
    assert "annotations" not in task
    result = task["predictions"][0]["result"][0]
    assert result["value"]["rectanglelabels"] == [label]
    assert result["from_name"] == "pattern" and result["to_name"] == "image"
    assert result["original_width"] == 1280 and result["original_height"] == 742
    v = result["value"]
    assert 0 <= v["x"] < v["x"]+v["width"] <= 100
    assert 0 <= v["y"] < v["y"]+v["height"] <= 100
    assert set(task["data"]) == {"review_id", "protocol_id", "caption", "image", "image_sha256",
        "original_image", "original_image_sha256", "comparison_image", "comparison_image_sha256", "future_image", "future_image_sha256"}
