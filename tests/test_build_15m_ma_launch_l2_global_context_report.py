"""Artifact smoke tests for the L2 global-context report builder."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pandas as pd
import pytest

import scripts.build_15m_ma_launch_l2_global_context_report as report


def _manifest(root: Path) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for index in range(12):
        group = "kept" if index < 6 else "rejected_high_l1"
        chart = root / "charts" / f"chart_{index:02d}.png"
        chart.parent.mkdir(parents=True, exist_ok=True)
        pixels = np.full((40, 64, 3), 20 + index * 10, dtype=np.uint8)
        assert cv2.imwrite(str(chart), pixels)
        rows.append(
            {
                "group": group,
                "episode_id": f"episode_{index:02d}",
                "symbol": "BTC_USDT_SWAP",
                "side": "long" if index % 2 == 0 else "short",
                "available_at": "2026-04-01T00:00:00+00:00",
                "l1_confidence": 0.5 + index / 100,
                "l2_score": index / 1000,
                "l2_threshold": 0.005,
                "l2_keep": index < 6,
                "chart_path": chart.relative_to(root).as_posix(),
                "chart_png_sha256": report.sha256_file(chart),
            }
        )
    return pd.DataFrame(rows)


def test_overview_and_gallery_preserve_all_source_hashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(report, "ROOT", tmp_path)
    manifest = _manifest(tmp_path)
    overview_path = tmp_path / "analysis" / "output" / "overview.png"
    overview = report.make_overview(manifest, overview_path)
    assert overview["width"] == 1920
    assert overview["height"] == 1726
    assert len(overview["sources"]) == 12
    assert overview["sha256"] == report.sha256_file(overview_path)

    gallery_path = tmp_path / "analysis" / "html" / "gallery.html"
    gallery = report.make_gallery(manifest, gallery_path, title="L2 test gallery")
    assert gallery["charts"] == 12
    document = gallery_path.read_text(encoding="utf-8")
    assert document.count("<article class='card'>") == 12
    assert "episode_00" in document and "episode_11" in document
    assert gallery["sha256"] == report.sha256_file(gallery_path)


def test_phase_commit_lineage_discloses_every_phase_and_rejects_invalid_identity() -> None:
    commits = report.phase_commit_lineage(
        {"source_commit": "1" * 40},
        {"source_commit": "2" * 40},
        {"source_commit": "a" * 40},
        {"source_commit": "f" * 40},
    )
    assert commits == {
        "snapshot": "1" * 40,
        "scan": "2" * 40,
        "dataset": "a" * 40,
        "training": "f" * 40,
    }
    with pytest.raises(report.ReportError, match="invalid phase source commit"):
        report.phase_commit_lineage(
            {"source_commit": "1" * 40},
            {"source_commit": "not-a-commit"},
            {"source_commit": "a" * 40},
            {"source_commit": "f" * 40},
        )


def test_overview_delivers_sparse_threshold_outcome_without_inventing_charts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(report, "ROOT", tmp_path)
    manifest = _manifest(tmp_path)
    manifest = manifest[
        manifest["episode_id"].isin(["episode_00", "episode_06", "episode_07"])
    ]
    overview_path = tmp_path / "overview.png"
    overview = report.make_overview(manifest, overview_path)
    assert overview["width"] == 1920
    assert overview["height"] == 475
    assert [row["episode_id"] for row in overview["sources"]] == [
        "episode_00",
        "episode_06",
        "episode_07",
    ]
    assert overview["sha256"] == report.sha256_file(overview_path)


def test_overview_fails_closed_when_manifest_has_no_delivered_chart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(report, "ROOT", tmp_path)
    with pytest.raises(report.ReportError, match="at least one delivered chart"):
        report.make_overview(pd.DataFrame(columns=["group"]), tmp_path / "overview.png")
