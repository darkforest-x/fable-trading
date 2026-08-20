"""Tests for the complete ETH 15m research artifact manifest."""
from __future__ import annotations

import hashlib
import json
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


def test_delivery_report_tracks_signal_time_equity_results() -> None:
    project = Path(__file__).resolve().parents[1]
    results = project / "experiments/active/exp-pine-eth-15m-v1/results"
    summary = json.loads((results / "summary.json").read_text(encoding="utf-8"))
    path_risk = json.loads(
        (results / "path_risk_bootstrap.json").read_text(encoding="utf-8")
    )
    report = (project / "analysis/p0_pine_eth_15m_v1_20260821.md").read_text(
        encoding="utf-8"
    )

    v9 = summary["v9_final_preholdout"]
    v10 = summary["v10_post_selection_final_preholdout"]
    assert f"15m 收盘最大回撤 {v9['max_drawdown_15m_percent']:.2f}%" in report
    assert f"回撤 {v10['max_drawdown_15m_percent']:.2f}%" in report
    assert (
        f"{path_risk['arms'][2]['actual_drawdown_15m_percent']:.2f}% 压到\n"
        f"{path_risk['arms'][0]['actual_drawdown_15m_percent']:.2f}%"
    ) in report
    assert "反手单数量冻结在 signal close 的 marked equity" in report
    assert "TradingView 官方 Pine v6 编译已经通过" in report
    assert "交易导出**逐笔 parity" in report
