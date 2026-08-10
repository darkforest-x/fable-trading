"""Unit tests for P2 hard-negative mining selection."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from scripts import build_local_signal_v2_p2_hardneg as p2
from scripts.build_local_signal_v2_p2_hardneg import (
    hard_negative_event_id,
    select_hard_negatives,
    split_hard_negative_banks,
)


def _candidate(stem: str, split: str, start: int) -> dict:
    return {
        "stem": stem,
        "symbol": "BTC_USDT_SWAP",
        "split": split,
        "win_start": start,
        "win_len": 30,
    }


def test_selects_candidate_if_any_box_reaches_frozen_threshold():
    rows = [_candidate("a", "train", 100), _candidate("b", "val", 200)]
    predictions = {
        "a": [{"confidence": 0.34}, {"confidence": 0.35}],
        "b": [{"confidence": 0.349999}],
    }
    selected = select_hard_negatives(rows, predictions, 0.35)
    assert [row["stem"] for row in selected] == ["a"]
    assert selected[0]["mining_box_count"] == 1
    assert selected[0]["mining_max_confidence"] == pytest.approx(0.35)
    assert selected[0]["mining_boxes"] == [{"confidence": 0.35}]
    assert selected[0]["hard_negative_type"] == "b2_false_positive_conf035"


def test_event_id_is_stable_and_split_sensitive():
    train = _candidate("a", "train", 100)
    val = _candidate("a", "val", 100)
    assert hard_negative_event_id(train) == hard_negative_event_id(dict(train))
    assert hard_negative_event_id(train) != hard_negative_event_id(val)


def test_duplicate_candidate_stem_is_rejected():
    rows = [_candidate("a", "train", 100), _candidate("a", "train", 101)]
    with pytest.raises(ValueError, match="duplicate candidate stem"):
        select_hard_negatives(rows, {"a": [{"confidence": 0.9}]}, 0.35)


def test_split_hard_negative_banks_keeps_val_evaluation_only():
    train, heldout = split_hard_negative_banks(
        [_candidate("a", "train", 100), _candidate("b", "val", 200)]
    )
    assert [row["stem"] for row in train] == ["a"]
    assert [row["stem"] for row in heldout] == ["b"]


def test_split_hard_negative_banks_rejects_unknown_split():
    with pytest.raises(ValueError, match="unknown hard-negative splits"):
        split_hard_negative_banks(
            [_candidate("a", "train", 100), _candidate("b", "test", 200)]
        )


def test_split_hard_negative_banks_requires_both_cohorts():
    with pytest.raises(ValueError, match="both train and held-out val"):
        split_hard_negative_banks([_candidate("a", "train", 100)])


def test_assemble_excludes_heldout_bank_from_yolo_data(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(p2, "PROJECT", tmp_path)
    base = tmp_path / "base"
    candidates = tmp_path / "candidates"
    out = tmp_path / "out"
    for root in (base, candidates):
        for split in ("train", "val"):
            (root / "images" / split).mkdir(parents=True)
            (root / "labels" / split).mkdir(parents=True)

    positive_rows = []
    negative_rows = []
    manifest_rows = []
    for split, start in (("train", 100), ("val", 200)):
        pos_stem = f"pos_{split}"
        neg_stem = f"neg_{split}"
        pos_image = base / "images" / split / f"{pos_stem}.png"
        pos_label = base / "labels" / split / f"{pos_stem}.txt"
        neg_image = base / "images" / split / f"{neg_stem}.png"
        neg_label = base / "labels" / split / f"{neg_stem}.txt"
        pos_image.write_bytes(b"pos")
        pos_label.write_text("0 0.5 0.5 0.1 0.1\n")
        neg_image.write_bytes(b"neg")
        neg_label.write_text("")
        positive_rows.append(
            {
                "split": split,
                "out_img": str(pos_image),
                "out_lbl": str(pos_label),
                "symbol": "BTC_USDT_SWAP",
                "decision_bar": start + 29,
                "win_start": start,
                "win_len": 30,
                "future_bars": 0,
                "end_time": "2026-03-01 00:00:00+00:00" if split == "train" else "2026-04-01 00:00:00+00:00",
                "event_id": f"event_{split}",
            }
        )
        negative_rows.append(
            {
                "split": split,
                "out_img": str(neg_image),
                "out_lbl": str(neg_label),
                "symbol": "BTC_USDT_SWAP",
                "win_start": start + 40,
                "win_len": 30,
                "kind": "empty_bg",
                "end_time": "2026-03-02 00:00:00+00:00" if split == "train" else "2026-04-02 00:00:00+00:00",
            }
        )
        manifest_rows.append(
            {
                "split": split,
                "image_path": str(pos_image.relative_to(tmp_path)),
                "label_path": str(pos_label.relative_to(tmp_path)),
                "sample_type": "positive",
            }
        )

    (base / "w20_manifest.json").write_text(json.dumps(positive_rows))
    (base / "w20_neg_manifest.json").write_text(json.dumps(negative_rows))
    (base / "manifest.jsonl").write_text("".join(json.dumps(row) + "\n" for row in manifest_rows))
    (base / "stageb_summary.json").write_text(json.dumps({"split_rule": "strict_time"}))
    (base / "data.yaml").write_text(f"path: {base}\ntrain: images/train\nval: images/val\n")

    candidate_rows = []
    predictions = {}
    for split, start, end_time in (
        ("train", 300, "2026-03-03 00:00:00+00:00"),
        ("val", 400, "2026-04-03 00:00:00+00:00"),
    ):
        stem = f"candidate_{split}"
        image = candidates / "images" / split / f"{stem}.png"
        label = candidates / "labels" / split / f"{stem}.txt"
        image.write_bytes(split.encode())
        label.write_text("")
        candidate_rows.append(
            {
                "stem": stem,
                "symbol": "BTC_USDT_SWAP",
                "split": split,
                "win_start": start,
                "win_len": 30,
                "start_time": str(pd.Timestamp(end_time) - pd.Timedelta(minutes=29 * 15)),
                "end_time": end_time,
                "out_img": str(image),
                "out_lbl": str(label),
                "renderer_version": "test",
            }
        )
        predictions[stem] = [{"confidence": 0.9, "xywhn": [0.5, 0.5, 0.1, 0.1]}]
    p2.write_jsonl(candidates / "candidate_neg_manifest.jsonl", candidate_rows)
    prediction_path = candidates / "mining_predictions.json"
    prediction_path.write_text(json.dumps({"predictions": predictions}))

    summary = p2.assemble_dataset(base, candidates, out, prediction_path)

    assert summary["counts_p2"]["train_hard_negative"] == 1
    assert summary["counts_p2"]["val_hard_negative_in_training_dataset"] == 0
    assert summary["counts_p2"]["heldout_val_hard_negative"] == 1
    assert len(p2.read_jsonl(out / "hard_negative_bank.jsonl")) == 1
    assert len(p2.read_jsonl(out / "heldout_hard_negative_bank.jsonl")) == 1
    assert "evaluation" not in (out / "data.yaml").read_text()
    assert not any("heldout_hard_negative" in line for line in (out / "manifest.jsonl").read_text().splitlines())
