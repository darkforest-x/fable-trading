"""Unit tests for P2-12 data audit helpers (no network, synthetic frames)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from scripts import data_audit


def _frame(times: list[str], *, volume: float = 1.0, spike: bool = False) -> pd.DataFrame:
    n = len(times)
    close = [100.0] * n
    if spike and n >= 2:
        close[-1] = 200.0  # +100% bar
    return pd.DataFrame(
        {
            "open_time": pd.to_datetime(times, utc=True),
            "open": close,
            "high": [c * 1.01 for c in close],
            "low": [c * 0.99 for c in close],
            "close": close,
            "volume": [volume] * n,
        }
    )


def test_audit_frame_detects_gap_spike_and_zero_vol() -> None:
    frame = _frame(
        [
            "2026-07-01 00:00:00+00:00",
            "2026-07-01 00:15:00+00:00",
            "2026-07-01 02:00:00+00:00",  # 1h45 gap on 15m
            "2026-07-01 02:15:00+00:00",
        ],
        volume=0.0,
        spike=True,
    )
    metrics = data_audit.audit_frame(frame, bar="15m")
    assert metrics["n_gaps"] >= 1
    assert metrics["spike_bars"] >= 1
    assert metrics["zero_vol_share"] == 1.0
    assert metrics["ohlc_bad"] == 0


def test_audit_frame_detects_ohlc_inversion() -> None:
    frame = _frame(
        ["2026-07-01 00:00:00+00:00", "2026-07-01 00:15:00+00:00"]
    )
    frame.loc[0, "high"] = 50.0  # below open/close/low
    frame.loc[0, "low"] = 150.0
    metrics = data_audit.audit_frame(frame, bar="15m")
    assert metrics["ohlc_bad"] >= 1


def test_flag_reasons_structural_vs_stale() -> None:
    row = {
        "n_gaps": 0,
        "zero_vol_share": 0.0,
        "spike_bars": 0,
        "ohlc_bad": 0,
        "age_hours": 100.0,
    }
    assert data_audit.flag_reasons(row) == ["stale>48.0h"]
    row["n_gaps"] = 10
    assert "gaps>5" in data_audit.flag_reasons(row)
    row["spike_bars"] = 2
    assert not any(r.startswith("spikes") for r in data_audit.flag_reasons(row))
    row["spike_bars"] = 3
    assert any(r.startswith("spikes") for r in data_audit.flag_reasons(row))


def test_blacklist_candidate_stricter_than_flag() -> None:
    thin_stock = {
        "symbol": "AAPL_USDT_SWAP",
        "n_gaps": 0,
        "zero_vol_share": 0.03,
        "spike_bars": 0,
        "ohlc_bad": 0,
    }
    assert data_audit.is_blacklist_candidate(thin_stock)
    mild_crypto = {
        "symbol": "BTC_USDT_SWAP",
        "n_gaps": 0,
        "zero_vol_share": 0.0,
        "spike_bars": 2,
        "ohlc_bad": 0,
    }
    assert not data_audit.is_blacklist_candidate(mild_crypto)
    chronic = {**mild_crypto, "spike_bars": 10}
    assert data_audit.is_blacklist_candidate(chronic)


def test_scan_part_files(tmp_path: Path) -> None:
    part = tmp_path / "FOO_USDT_SWAP_15m.part.csv"
    part.write_text("ts,open\n1,1\n2,2\n", encoding="utf-8")
    (tmp_path / "okx_FOO_USDT_SWAP_15m_2.csv").write_text("x\n", encoding="utf-8")
    found = data_audit.scan_part_files(tmp_path)
    assert len(found) == 1
    assert found[0]["path"] == part.name
    assert found[0]["approx_rows"] == 2
