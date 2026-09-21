"""Independently verify frozen event identity, follow-up timing, prices and assets.

Source columns: open_time/ts/OHLC; endpoint MAs use only the full history through
the tested bar. EMA endpoints use an explicit weighted sum, not the builder's
pandas ewm implementation. This is visual artifact QA, not an economic test.
"""
from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / "experiments/active/exp-ma-launch-followup50-20260922-v1"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    plan = json.loads((EXP / "plan.json").read_text())
    selection = [json.loads(line) for line in (EXP / "selection.jsonl").read_text().splitlines()]
    original = [json.loads(line) for line in (ROOT / plan["source_manifest"]).read_text().splitlines()]
    groups = {}
    for row in original:
        if row.get("sample_kind") == "positive":
            groups.setdefault(row["event_id"], []).append(row)
    expected_ids = random.Random(20260922).sample(sorted(groups), 50)
    assert expected_ids == [r["event_id"] for r in selection]
    review = ROOT / plan["output_dir"]
    rendered = json.loads((review / "manifest.json").read_text())
    assert [r["event_id"] for r in rendered] == expected_ids
    expected_prices = {r["event_id"]: r for r in json.loads((EXP / "independent_expected_values.json").read_text())}
    before = json.loads((EXP / "training_inputs_before.json").read_text())
    for record in before["selected_training_files"]:
        assert sha(ROOT / record["path"]) == record["sha256"], record["path"]
    assert sha(ROOT / plan["source_manifest"]) == before["manifest_sha256"]
    assert sha(ROOT / plan["dataset_root"] / "data.yaml") == before["data_yaml_sha256"]
    max_ma_error = 0.0
    source_hashes = {}
    wrong_time_rejections = 0
    for n, (selected, output) in enumerate(zip(selection, rendered)):
        source_path = ROOT / selected["source_path"]
        if str(source_path) not in source_hashes:
            source_hashes[str(source_path)] = sha(source_path)
        assert source_hashes[str(source_path)] == output["source_sha256"]
        source = pd.read_csv(source_path)
        c = selected["source_core_end_i"]
        t0, end = c + 5, c + 53
        times = pd.to_datetime(source.open_time, utc=True)
        assert times.iloc[c].isoformat() == selected["core_end_time"]
        wrong_time_rejections += times.iloc[c].isoformat() != selection[(n + 1) % 50]["core_end_time"]
        assert pd.Timestamp(output["t0_close_time_utc"]) == times.iloc[t0] + pd.Timedelta(minutes=15)
        assert pd.Timestamp(output["future_end_close_time_utc"]) == times.iloc[end] + pd.Timedelta(minutes=15)
        assert times.iloc[end] - times.iloc[t0] == pd.Timedelta(hours=12)
        assert output["future_start_i"] == t0 + 1 and output["future_end_i"] == end
        assert output["future_bar_count"] == 48
        expected = expected_prices[selected["event_id"]]
        for actual_key, expected_key in (("confirmation_close", "t0_close"), ("future_end_close", "end_close"), ("raw_close_change_percent", "raw_close_change_pct"), ("direction_aligned_close_change_percent", "direction_aligned_close_change_pct")):
            assert np.isclose(output[actual_key], expected[expected_key], rtol=1e-12, atol=1e-12)
        csv_path = review / output["visible_ohlc_ma_csv"]
        assert sha(csv_path) == output["visible_ohlc_ma_csv_sha256"]
        visible = pd.read_csv(csv_path)
        indexes = visible.source_index.astype(int).to_numpy()
        assert len(indexes) == 112 and np.array_equal(indexes, np.arange(t0 - 63, end + 1))
        assert np.all(np.diff(visible.ts) == 900000)
        for column in ("open", "high", "low", "close"):
            assert np.allclose(visible[column], source[column].iloc[indexes], rtol=1e-13, atol=1e-13)
        hl2 = (source.high.to_numpy() + source.low.to_numpy()) / 2
        for k in (t0, end):
            v = visible.loc[visible.source_index.eq(k)].iloc[0]
            for period in (20, 60, 120):
                alpha = 2 / (period + 1)
                sma = np.mean(hl2[k - period + 1 : k + 1])
                powers = np.power(1 - alpha, np.arange(k - 1, -1, -1, dtype=float))
                ema = hl2[0] * (1 - alpha) ** k + alpha * np.sum(hl2[1 : k + 1] * powers)
                for name, expected_value in ((f"sma{period}", sma), (f"ema{period}", ema)):
                    error = abs(float(v[name]) - expected_value)
                    max_ma_error = max(max_ma_error, error)
                    assert np.isclose(v[name], expected_value, rtol=1e-10, atol=1e-11), (n, name, error)
        assert sha(review / output["chart"]) == output["chart_sha256"]
        with Image.open(review / output["chart"]) as image:
            assert image.size == (1920, 1000)
            image.verify()
        assert sha(review / output["original_image_copy"]) == selected["image_sha256"]
        assert not output["training_eligible"] and not output["production_eligible"]
    assert len(list((review / "charts").glob("*.png"))) == 50
    assert len({x["chart_sha256"] for x in rendered}) == 50
    assert not list(review.rglob("*.txt")) and not list(review.rglob("data.yaml"))
    assert wrong_time_rejections == 50
    result = {"status": "passed", "events": 50, "unique_events": 50, "same_frozen_random_order": True, "exact_48_bar_12h_windows": 50, "raw_csv_price_checks": 50, "source_hash_checks": len(source_hashes), "original_training_files_unchanged": 100, "original_manifest_and_yaml_unchanged": True, "pngs_1920x1000_and_unique": 50, "original_copies_byte_identical": 50, "ma_endpoint_checks": 600, "max_ma_absolute_error": max_ma_error, "null_control_wrong_event_timestamp_links_rejected": wrong_time_rejections, "review_contains_training_labels_or_yaml": False}
    (EXP / "independent_qa.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
