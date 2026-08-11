"""Contracts for the owner-confirmed Stage-B curriculum materializer."""
from __future__ import annotations

from scripts.materialize_local_signal_v2_stageb_from_stagea import (
    PROTOCOL,
    rewrite_and_copy_row,
    sha256_file,
)


def test_rewrite_preserves_pixels_and_marks_stage_b_role(tmp_path, monkeypatch) -> None:
    project = tmp_path / "project"
    source = project / "datasets/source"
    out = project / "datasets/out"
    image = source / "images/train/sample.png"
    label = source / "labels/train/sample.txt"
    image.parent.mkdir(parents=True)
    label.parent.mkdir(parents=True)
    image.write_bytes(b"pixel-bytes")
    label.write_text("0 0.5 0.5 0.2 0.3\n")
    monkeypatch.setattr(
        "scripts.materialize_local_signal_v2_stageb_from_stagea.PROJECT", project
    )
    row = {
        "out_img": "datasets/source/images/train/sample.png",
        "out_lbl": "datasets/source/labels/train/sample.txt",
        "image_sha256": sha256_file(image),
        "split": "train",
        "stage": "B",
        "future_bars": 0,
    }

    copied = rewrite_and_copy_row(row, source, out, sample_type="positive")

    assert (project / copied["out_img"]).read_bytes() == image.read_bytes()
    assert copied["label_sha256"] == sha256_file(label)
    assert copied["dataset_protocol"] == PROTOCOL
    assert copied["curriculum_role"] == "stage_b_causal_finetune_from_stage_a"
    assert copied["production_eligible"] is False
    assert copied["owner_confirmed_stage_b_layout"] is True
