import hashlib
import json
import cv2
import numpy as np
import pandas as pd
import pytest

from yoyo.datasets import ma_profit_dataset
from yoyo.datasets.ma_profit_dataset import arms_for_asset, asset_stem, build, event_assets, label_line


def source(n=1300):
    base = [100 + i * .01 for i in range(n)]
    return pd.DataFrame({"open_time": pd.date_range("2025-01-01T00:00:00Z", periods=n, freq="15min"), "open": base, "high": [x + .5 for x in base], "low": [x - .5 for x in base], "close": [x + .1 for x in base], "volume": 1.0})


def row(frame, split="train", retained=True, c=1250, event_id="event"):
    return {"event_id": event_id, "cluster_id": event_id, "canonical_asset": "X", "symbol": "X", "direction": "LONG", "source_path": "unused.csv", "bar_minutes": 15, "core_start_time": frame.open_time.iloc[c - 3].isoformat(), "core_end_time": frame.open_time.iloc[c].isoformat(), "split": split, "profit": {"retained": retained, "outcome": "TP" if retained else "SL", "decision_close_time_utc": (frame.open_time.iloc[c + 5] + pd.Timedelta(minutes=15)).isoformat(), "label_window_end_utc": "2025-01-15T00:00:00+00:00"}}


def test_variants_are_distinct_but_share_causal_right_edge_and_future_is_irrelevant():
    frame0 = source(); assets = event_assets(frame0, row(frame0))
    assert [x["variant"] for x in assets] == ["A", "B1", "B2"]
    assert len({hashlib.sha256(x["png"]).hexdigest() for x in assets}) == 3
    assert len({x["visible"]["visible_end_open_time_utc"] for x in assets}) == 1
    changed = frame0.copy(); changed.loc[1256:, "close"] *= 9
    changed_assets = event_assets(changed, row(changed))
    assert [hashlib.sha256(x["png"]).hexdigest() for x in assets] == [hashlib.sha256(x["png"]).hexdigest() for x in changed_assets]
    assert [label_line(x) for x in assets] == [label_line(x) for x in changed_assets]


def test_visible_range_policy_is_causal_and_expands_low_amplitude_window():
    frame0 = source()
    # Keep all prices near 100: legacy's relative floor dominates this window.
    for column in ("open", "high", "low", "close"):
        frame0[column] = 100 + (frame0[column] - frame0[column].iloc[0]) * .001
    visible = event_assets(frame0, row(frame0), "visible_range_v1")
    legacy = event_assets(frame0, row(frame0), "legacy_min_rel_span_v1")
    assert legacy == event_assets(frame0, row(frame0))
    assert visible[0]["png"] != legacy[0]["png"]
    def content_height(asset):
        pixels = cv2.imdecode(np.frombuffer(asset["png"], np.uint8), cv2.IMREAD_COLOR)
        ys = np.where(np.any(pixels < 245, axis=2))[0]
        return int(ys.max() - ys.min() + 1)
    assert content_height(visible[0]) > 600
    assert content_height(legacy[0]) < 50
    changed = frame0.copy()
    changed.loc[1256:, ["open", "high", "low", "close"]] *= 9
    assert visible == event_assets(changed, row(changed), "visible_range_v1")


def test_visible_range_core_box_uses_the_new_price_to_pixel_mapping():
    frame = source()
    assets = event_assets(frame, row(frame), "visible_range_v1")
    core_start, core_end = 1247, 1250
    support = core_start - 11 - 1200
    causal = ma_profit_dataset.add_hl2_mas(frame.iloc[support:core_end + 6].reset_index(drop=True))
    core = causal.iloc[core_start - support:core_end - support + 1]
    values = np.concatenate([core.high, core.low, core.loc[:, list(ma_profit_dataset.SIX_MA_COLUMNS)].to_numpy().ravel()])
    low, high = float(values.min()), float(values.max())
    padding = (high - low) * .04
    for asset in assets:
        price_min, price_max = asset["visible"]["price_min"], asset["visible"]["price_max"]
        # Independent coordinate equation; reusing legacy boxes fails this check.
        y0 = max(0, int(12 + (price_max - high - padding) / (price_max - price_min) * 718))
        y1 = min(742, int(12 + (price_max - low + padding) / (price_max - price_min) * 718))
        assert asset["box"]["y0"] == y0
        assert asset["box"]["y1"] == y1
        assert float(label_line(asset).split()[4]) == pytest.approx((y1 - y0) / 742, abs=1e-8)


def test_unknown_price_scale_fails_closed():
    with pytest.raises(ma_profit_dataset.ProfitDatasetError, match="unknown render price_scale"):
        event_assets(source(), row(source()), "unknown")


def test_compact_discovery_metadata_preserves_all_three_images_and_labels():
    from yoyo.datasets.ma_profit_cohort import compact_event
    frame = source()
    full = row(frame) | {"features": {"unused": [1, 2, 3]}, "strict_metrics": {"unused": 99}, "quality_score": .8}
    # Projection occurs before label/split computation; the same resolver
    # result is appended to both representations afterwards.
    compact = compact_event(full) | {"split": full["split"], "profit": full["profit"]}
    original_assets, compact_assets = event_assets(frame, full), event_assets(frame, compact)
    assert [asset["png"] for asset in original_assets] == [asset["png"] for asset in compact_assets]
    assert [label_line(asset) for asset in original_assets] == [label_line(asset) for asset in compact_assets]


def test_nonwinner_train_is_excluded_but_val_is_single_empty_label_candidate():
    frame0 = source()
    assert event_assets(frame0, row(frame0, "train", False)) == []
    assets = event_assets(frame0, row(frame0, "val", False))
    assert len(assets) == 1 and assets[0]["variant"] == "A" and not assets[0]["positive"]


@pytest.mark.parametrize("price_scale", ["legacy_min_rel_span_v1", "visible_range_v1"])
@pytest.mark.parametrize("outcome", ["SL", "TIMEOUT"])
def test_future_profit_label_changes_supervision_but_not_input_pixels(outcome, price_scale):
    frame = source()
    winner = row(frame, "val", True)
    nonwinner = row(frame, "val", False)
    nonwinner["profit"].update({"outcome": outcome, "gross_r": -1.0, "net_r": -1.02})
    positive = event_assets(frame, winner, price_scale=price_scale)[0]
    negative = event_assets(frame, nonwinner, price_scale=price_scale)[0]
    assert positive["png"] == negative["png"]
    assert positive["box"] == negative["box"]
    assert label_line(positive).strip()
    assert label_line(negative) == ""


def test_arms_keep_variants_and_shared_evaluation_views_separate():
    assert arms_for_asset("train", "A") == ("A",)
    assert arms_for_asset("train", "B1") == arms_for_asset("train", "B2") == ("B",)
    assert arms_for_asset("val", "A") == arms_for_asset("test", "A") == ("A", "B")


@pytest.mark.parametrize("price_scale", ["legacy_min_rel_span_v1", "visible_range_v1"])
def test_build_writes_disjoint_train_lists_and_shared_evaluation_lists(tmp_path, monkeypatch, price_scale):
    frame = source(1400)
    source_path = tmp_path / "source.csv"
    frame.to_csv(source_path, index=False)
    source_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
    rows = [
        row(frame, "train", True, 1250, "train").copy(),
        row(frame, "val", False, 1300, "val").copy(),
        row(frame, "test", True, 1350, "test").copy(),
    ]
    for item in rows:
        item["source_path"] = str(source_path)
        item["source_sha256"] = source_sha
    events = tmp_path / "events.jsonl"
    events.write_text("".join(json.dumps(item) + "\n" for item in rows))
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"events_sha256": hashlib.sha256(events.read_bytes()).hexdigest(), "render": {"price_scale": price_scale, "classes": {"0": "profitlong", "1": "profitshort"}}, "owner_authorization": {"training_authorized": True}, "training_contract_sha256": "fixture-contract", "selection_receipt_sha256": "fixture-receipt"}))
    monkeypatch.setattr(ma_profit_dataset, "_committed", lambda _: "fixture-commit")
    monkeypatch.setattr(ma_profit_dataset, "_cohort_controls", lambda *_: ({}, []))

    summary = build(plan, events, tmp_path / "out")

    out = tmp_path / "out"
    train_a, train_b = (out / "train_A.txt").read_text().splitlines(), (out / "train_B.txt").read_text().splitlines()
    assert len(train_a) == 1 and train_a[0].endswith(asset_stem("train", "A") + ".png")
    assert len(train_b) == 2 and {item.rsplit("_", 1)[-1] for item in train_b} == {"B1.png", "B2.png"}
    assert not set(train_a) & set(train_b)
    assert "val: val.txt" in (out / "data_A.yaml").read_text()
    assert "val: val.txt" in (out / "data_B.yaml").read_text()
    assert (out / "val.txt").read_text().splitlines() == ["./images/val/" + asset_stem("val", "A") + ".png"]
    assert (out / "labels" / "val" / (asset_stem("val", "A") + ".txt")).read_text() == ""
    assert summary["arms"]["A"]["train"] == {"images": 1, "events": 1}
    assert summary["arms"]["B"]["train"] == {"images": 2, "events": 1}
    assert summary.get("price_scale", "legacy_min_rel_span_v1") == price_scale
    manifest = [json.loads(line) for line in (out / "manifest.jsonl").read_text().splitlines()]
    assert all(item.get("price_scale", "legacy_min_rel_span_v1") == price_scale for item in manifest)
    if price_scale == "visible_range_v1":
        assert all(item["price_min"] < item["price_max"] for item in manifest)


def test_build_rejects_ledger_source_sha_drift(tmp_path, monkeypatch):
    frame = source()
    source_path = tmp_path / "source.csv"
    frame.to_csv(source_path, index=False)
    item = row(frame)
    item.update({"source_path": str(source_path), "source_sha256": "0" * 64})
    events = tmp_path / "events.jsonl"
    events.write_text(json.dumps(item) + "\n")
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"events_sha256": hashlib.sha256(events.read_bytes()).hexdigest()}))
    monkeypatch.setattr(ma_profit_dataset, "_committed", lambda _: "fixture-commit")
    monkeypatch.setattr(ma_profit_dataset, "_cohort_controls", lambda *_: ({}, []))

    with pytest.raises(ma_profit_dataset.ProfitDatasetError, match="source SHA drift"):
        build(plan, events, tmp_path / "out")


def test_source_id_cannot_create_windows_reserved_or_nested_paths():
    for identity in ("scan::BTC/USDT:2026-01-01T12:00Z", "CON", "../../escape"):
        name = asset_stem(identity, "A")
        assert not any(char in name for char in '<>:"/\\|?*')
        assert name.startswith("event_") and name.endswith("_A")


def test_timestamp_lookup_uses_last_duplicate_like_the_previous_dict(monkeypatch):
    frame = source(1320)
    original_start, original_end = 1240, 1243
    duplicate_start, duplicate_end = 1300, 1303
    frame.loc[duplicate_start:duplicate_end, "open_time"] = list(
        frame.open_time.iloc[original_start:original_end + 1]
    )
    item = row(frame, c=original_end)
    item["profit"]["decision_close_time_utc"] = (
        frame.open_time.iloc[duplicate_end + 5] + pd.Timedelta(minutes=15)
    ).isoformat()

    monkeypatch.setattr(ma_profit_dataset, "_known_input_continuous", lambda *_: True)
    monkeypatch.setattr(
        ma_profit_dataset,
        "_window_asset",
        lambda _frame, **kwargs: (b"fixture", {}, {
            "visible_end_open_time_utc": _frame.open_time.iloc[-1].isoformat(),
            "feature_support_start_i": kwargs["support_start_i"],
            "feature_support_start_utc": "fixture-support",
        }),
    )

    assets = event_assets(frame, item)
    assert [(asset["core_start_i"], asset["core_end_i"]) for asset in assets] == [
        (duplicate_start, duplicate_end),
    ] * 3
