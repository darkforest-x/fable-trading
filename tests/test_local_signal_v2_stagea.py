"""Contract tests for the owner-authorized Stage-A real-candle random crops."""
from __future__ import annotations

import json
import sys

import numpy as np
import scripts.build_local_signal_v2_stagea as stagea

from scripts.audit_local_signal_v2_stagea import audit_real_candle_position
from scripts.build_local_signal_v2_stagea import (
    POSITION_BUCKETS,
    offsets_for_bucket,
    sample_geometry,
)


def test_every_stage_a_bucket_has_valid_real_candle_offsets() -> None:
    for win_len in range(20, 31):
        for delay in (1, 2):
            for bucket_index, (_name, lo, hi, _share) in enumerate(POSITION_BUCKETS):
                offsets = offsets_for_bucket(win_len, delay, bucket_index)
                assert offsets
                for offset in offsets:
                    ratio = offset / (win_len - 1)
                    assert lo <= ratio <= hi
                    assert offset >= 2
                    assert offset + delay < win_len - 1


def test_stage_a_geometry_is_seeded_and_never_flush_with_real_content_edge() -> None:
    first = np.random.default_rng(20260807)
    second = np.random.default_rng(20260807)
    a = [sample_geometry(first) for _ in range(200)]
    b = [sample_geometry(second) for _ in range(200)]
    assert a == b
    assert {row[3] for row in a} == {bucket[0] for bucket in POSITION_BUCKETS}
    for win_len, delay, offset, _bucket in a:
        assert win_len - 1 - (offset + delay) >= 1


def _summary() -> dict:
    return {
        "position_buckets": [
            {"name": name, "lo": lo, "hi": hi, "target_share": share}
            for name, lo, hi, share in POSITION_BUCKETS
        ],
        "position_share_tolerance": 0.05,
    }


def test_position_audit_accepts_real_sequence_diversity() -> None:
    rows = []
    counts = {"left_mid": 20, "mid": 35, "mid_right": 30, "right": 15}
    ratios = {"left_mid": 0.25, "mid": 0.45, "mid_right": 0.65, "right": 0.80}
    for name, count in counts.items():
        rows.extend(
            {
                "position_bucket": name,
                "anchor_x_ratio": ratios[name],
                "future_bars": 3,
            }
            for _ in range(count)
        )
    result = audit_real_candle_position(rows, _summary())
    assert result["pass"]
    assert result["all_boxes_have_real_bars_to_right"]
    assert result["right_blank_slots_all_zero"]


def test_position_audit_rejects_blank_only_or_content_edge_layout() -> None:
    rows = [
        {
            "position_bucket": "right",
            "anchor_x_ratio": 0.8,
            "future_bars": 0,
            "right_blank_slots": 12,
        }
        for _ in range(100)
    ]
    result = audit_real_candle_position(rows, _summary())
    assert not result["pass"]
    assert not result["all_boxes_have_real_bars_to_right"]
    assert not result["right_blank_slots_all_zero"]


def test_preview_cli_serializes_path_as_json_string(
    tmp_path, monkeypatch, capsys
) -> None:
    source_manifest = tmp_path / "source.json"
    source_manifest.write_text("[]")
    preview_dir = tmp_path / "preview"
    monkeypatch.setattr(stagea, "run_preview", lambda *_args, **_kwargs: {"n": 24})
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build_local_signal_v2_stagea.py",
            "--src-manifest",
            str(source_manifest),
            "--preview",
            "--preview-dir",
            str(preview_dir),
        ],
    )

    assert stagea.main() == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload == {"preview": str(preview_dir), "n": 24}
