"""Contracts for the five-day Owner 10k-positive/30k-negative YOLO probe."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from scripts import scan_15m_ma_launch_t3_daily_movers as common
from scripts.run_15m_ma_launch_owner_yolo_recent5d_remote import commit_sha
from scripts.scan_15m_ma_launch_owner_yolo_recent5d import (
    DEFAULT_PREREG,
    EXPECTED_DAYS,
    load_preregistration,
    verify_training_geometry,
)
from scripts.verify_15m_ma_launch_owner_yolo_recent5d import (
    OwnerRecent5dVerificationError,
    resolve_receipt_path,
    verify_rankings,
    verify_signals,
)


ROOT = Path(__file__).resolve().parents[1]


def _rankings() -> pd.DataFrame:
    rows = []
    for day in EXPECTED_DAYS:
        for rank in range(1, 21):
            value = (21 - rank) / 100
            rows.append(
                {
                    "day": day,
                    "rank": rank,
                    "symbol": f"S{rank:02d}_USDT_SWAP",
                    "daily_return": value,
                    "abs_return": value,
                    "eligible_daily_universe": 275,
                }
            )
    return pd.DataFrame(rows)


def test_official_contract_uses_exact_five_days_and_training_geometry() -> None:
    payload = load_preregistration(DEFAULT_PREREG)
    assert payload["calendar"]["complete_days"] == [
        "2026-08-23T00:00:00Z",
        "2026-08-24T00:00:00Z",
        "2026-08-25T00:00:00Z",
        "2026-08-26T00:00:00Z",
        "2026-08-27T00:00:00Z",
    ]
    assert payload["detector"]["window_lengths"] == list(range(18, 26))
    assert payload["detector"]["mapped_core_length_bars_allowed"] == [4, 5]
    assert payload["detector"]["mapped_confirmation_bars_allowed"] == [4, 5, 6]
    assert payload["detector"]["confidence"] == 0.25
    assert payload["safety"]["production_eligible"] is False


def test_training_manifest_support_matches_frozen_inference_support() -> None:
    summary = verify_training_geometry(
        ROOT / "datasets" / "ma_launch_owner_autofill10000_yolo_neg30000_v2" / "manifest.jsonl"
    )
    assert summary["positive_rows"] == 10_000
    assert list(summary["window_counts"]) == list(range(18, 26))
    assert list(summary["core_counts"]) == [4, 5]
    assert list(summary["confirmation_counts"]) == [4, 5, 6]


def test_five_day_daily_fetch_requests_room_for_partial_bar(monkeypatch) -> None:
    captured = []

    def fake_request(url: str):
        captured.append(url)
        return {"code": "0", "data": []}

    monkeypatch.setattr(common, "_request", fake_request)
    common.fetch_daily_rows("BTC-USDT-SWAP", EXPECTED_DAYS)
    assert len(captured) == 1
    assert "bar=1Dutc" in captured[0]
    assert "limit=7" in captured[0]


def test_remote_runner_requires_an_exact_committed_source_identity() -> None:
    value = "a" * 40
    assert commit_sha(value) == value
    for bad in ("a" * 39, "A" * 40, "z" * 40):
        try:
            commit_sha(bad)
        except Exception:
            continue
        raise AssertionError(f"invalid source commit accepted: {bad}")


def test_receipt_paths_are_cross_platform_and_cannot_escape_repository() -> None:
    expected = ROOT / "analysis" / "output" / "signals.csv"
    assert resolve_receipt_path("analysis/output/signals.csv") == expected
    assert resolve_receipt_path(r"analysis\output\signals.csv") == expected
    for bad in ("../outside.csv", r"C:\outside.csv", "/outside.csv"):
        try:
            resolve_receipt_path(bad)
        except OwnerRecent5dVerificationError:
            continue
        raise AssertionError(f"unsafe receipt path accepted: {bad}")


def test_dynamic_rank_verifier_accepts_exact_five_top20_boards() -> None:
    summary = verify_rankings(_rankings())
    assert summary["symbol_days"] == 100
    assert summary["unique_symbols"] == 20


def test_signal_verifier_accepts_owner_training_geometry() -> None:
    rankings = _rankings()
    day = EXPECTED_DAYS[0]
    signals = pd.DataFrame(
        [
            {
                "day": day,
                "rank": 1,
                "symbol": "S01_USDT_SWAP",
                "daily_return": 0.20,
                "class_id": 0,
                "class_name": "dense_long",
                "confidence": 0.50,
                "core_start_time": day + pd.Timedelta(hours=1),
                "core_end_time": day + pd.Timedelta(hours=1, minutes=45),
                "window_end_time": day + pd.Timedelta(hours=2, minutes=45),
                "core_start_i": 100,
                "core_end_i": 103,
                "core_length_bars": 4,
                "confirmation_bars": 4,
                "window_start_i": 86,
                "window_end_i": 107,
                "window_len": 22,
            }
        ]
    )
    summary = verify_signals(signals, rankings)
    assert summary["signals"] == 1
    assert summary["class_counts"] == {"dense_long": 1}
    assert summary["direction_aligned"] == 1
