import hashlib
import json
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


def test_nonwinner_train_is_excluded_but_val_is_single_empty_label_candidate():
    frame0 = source()
    assert event_assets(frame0, row(frame0, "train", False)) == []
    assets = event_assets(frame0, row(frame0, "val", False))
    assert len(assets) == 1 and assets[0]["variant"] == "A" and not assets[0]["positive"]


def test_arms_keep_variants_and_shared_evaluation_views_separate():
    assert arms_for_asset("train", "A") == ("A",)
    assert arms_for_asset("train", "B1") == arms_for_asset("train", "B2") == ("B",)
    assert arms_for_asset("val", "A") == arms_for_asset("test", "A") == ("A", "B")


def test_build_writes_disjoint_train_lists_and_shared_evaluation_lists(tmp_path, monkeypatch):
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
    plan.write_text(json.dumps({"events_sha256": hashlib.sha256(events.read_bytes()).hexdigest(), "render": {"classes": {"0": "profitlong", "1": "profitshort"}}, "owner_authorization": {"training_authorized": True}, "training_contract_sha256": "fixture-contract", "selection_receipt_sha256": "fixture-receipt"}))
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
