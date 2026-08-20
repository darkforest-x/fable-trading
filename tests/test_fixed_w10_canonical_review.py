"""Uniform causal-OHLC triage pack contracts."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest
from PIL import Image

from yoyo.datasets.fixed_w10_blind_audit import AuditBuildError
from yoyo.datasets.fixed_w10_canonical_review import (
    IMG_HEIGHT,
    IMG_WIDTH,
    PACK_ID,
    RENDER_SPEC_ID,
    WINDOW_BARS,
    _page_html,
    _write_png,
    load_preholdout_symbol_prefix,
    render_canonical_primary,
    resolve_current_source,
)
from yoyo.datasets.window_render import enrich


PROJECT = Path(__file__).resolve().parents[1]


def _write_ohlc(path: Path, times: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["ts", "open", "high", "low", "close", "volume", "open_time"],
        )
        writer.writeheader()
        for index, value in enumerate(times):
            price = 100 + index / 10
            writer.writerow(
                {
                    "ts": index,
                    "open": price,
                    "high": price + 1,
                    "low": price - 1,
                    "close": price + 0.2,
                    "volume": 10 + index,
                    "open_time": value,
                }
            )


def test_loader_reanchors_by_time_and_stops_before_later_rows(tmp_path: Path) -> None:
    path = tmp_path / "okx_TEST_USDT_SWAP_15m_99.csv"
    times = [
        "2026-05-03T22:30:00+00:00",
        "2026-05-03T22:45:00+00:00",
        "2026-05-03T23:00:00+00:00",
        "2026-05-03T23:15:00+00:00",
        "2026-05-04T00:00:00+00:00",
    ]
    _write_ohlc(path, times)
    frame, anchors = load_preholdout_symbol_prefix(path, [times[1], times[3]])
    assert len(frame) == 4
    assert anchors[pd.Timestamp(times[1]).isoformat()] == 1
    assert anchors[pd.Timestamp(times[3]).isoformat()] == 3
    assert pd.Timestamp(frame.iloc[-1]["open_time"]) < pd.Timestamp("2026-05-04", tz="UTC")


def test_missing_decision_time_fails_closed_as_negative_control(tmp_path: Path) -> None:
    path = tmp_path / "okx_TEST_USDT_SWAP_15m_4.csv"
    _write_ohlc(
        path,
        [
            "2026-01-01T00:00:00+00:00",
            "2026-01-01T00:15:00+00:00",
            "2026-01-01T00:30:00+00:00",
        ],
    )
    with pytest.raises(AuditBuildError, match="missing"):
        load_preholdout_symbol_prefix(path, ["2026-01-01T00:20:00+00:00"])


def test_current_source_resolution_ignores_historical_row_count_suffix(tmp_path: Path) -> None:
    current = tmp_path / "okx_TEST_USDT_SWAP_15m_31273.csv"
    current.write_text("open_time,open,high,low,close,volume\n")
    assert resolve_current_source(tmp_path, "TEST_USDT_SWAP", "15m") == current.resolve()


def test_canonical_render_is_fixed_causal_png_with_decision_marker(tmp_path: Path) -> None:
    n = WINDOW_BARS + 60
    frame = pd.DataFrame(
        {
            "open_time": pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC"),
            "open": [100 + i * 0.01 for i in range(n)],
            "high": [101 + i * 0.01 for i in range(n)],
            "low": [99 + i * 0.01 for i in range(n)],
            "close": [100.2 + i * 0.01 for i in range(n)],
            "volume": [1000 + i for i in range(n)],
        }
    )
    result = render_canonical_primary(enrich(frame), n - 1)
    assert result["window_bars"] == WINDOW_BARS
    assert result["visible_end_index"] == n - 1
    assert result["future_bars"] == 0
    assert result["image"].shape == (IMG_HEIGHT, IMG_WIDTH, 3)
    target = tmp_path / "canonical.png"
    _write_png(target, result["image"])
    with Image.open(target) as image:
        assert image.size == (IMG_WIDTH, IMG_HEIGHT)
        assert image.mode == "RGB"
        assert image.format == "PNG"


def test_page_has_shortcuts_contract_warning_and_no_private_truth() -> None:
    page = _page_html(
        [
            {
                "review_id": "cv_public",
                "image": "images/cv_public.png",
                "historical_image": "historical_original/cv_public.jpg",
            }
        ],
        gold_sha256="a" * 64,
    )
    for required in (
        "K / 1 · 保留",
        "X / 2 · 去掉",
        "? / 3 · 待定",
        "原始 OHLC 因果 W200",
        "可能含未来",
        "localStorage",
        "导入进度",
        "导出 JSON",
        PACK_ID,
        RENDER_SPEC_ID,
    ):
        assert required in page
    for secret in ("shape_label", "source_kind", "gold_id", "decision_time"):
        assert secret not in page


def test_cli_can_be_invoked_outside_repository(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(PROJECT / "tools/datasets/fixed_w10_canonical_review.py"), "--help"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "build" in result.stdout and "summarize" in result.stdout
