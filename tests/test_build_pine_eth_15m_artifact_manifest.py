"""Tests for the complete ETH 15m research artifact manifest."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from scripts.build_pine_eth_15m_artifact_manifest import (
    build_entries,
    safe_relative_path,
    verify_entries,
)


def test_manifest_entries_hash_and_verify_exact_files(tmp_path: Path) -> None:
    artifact = tmp_path / "results" / "summary.json"
    artifact.parent.mkdir()
    artifact.write_text('{"ok": true}\n', encoding="utf-8")
    entries = build_entries([artifact], project=tmp_path)
    assert entries == [
        {
            "path": "results/summary.json",
            "bytes": artifact.stat().st_size,
            "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        }
    ]
    assert verify_entries(entries, project=tmp_path)["status"] == "pass"
    artifact.write_text('{"ok": false}\n', encoding="utf-8")
    failed = verify_entries(entries, project=tmp_path)
    assert failed["status"] == "fail"
    assert failed["hash_mismatch"]


def test_manifest_refuses_raw_data_and_symlinks(tmp_path: Path) -> None:
    raw = tmp_path / "data" / "bars.csv"
    raw.parent.mkdir()
    raw.write_text("x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="forbidden"):
        safe_relative_path(raw, project=tmp_path)

    target = tmp_path / "safe.txt"
    target.write_text("safe\n", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="symlinks"):
        safe_relative_path(link, project=tmp_path)
