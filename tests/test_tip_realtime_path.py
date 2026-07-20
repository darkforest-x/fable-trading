"""End-to-end guard for the 2026-07-20 real-time tip path.

A signal on the NEWEST closed bar must produce a forward-log row on the same
pulse (status=open, proxy entry, maker_filled empty), and the next pulse must
backfill the true entry fields without touching detected_at. Before this path
existed the scan dropped tip signals entirely, costing 15-22 min of edge on
every live trade (and 20-min freshness gates made trading structurally
impossible).
"""
from __future__ import annotations

import types

import numpy as np
import pandas as pd
import pytest

import src.judgment.forward_scan as fs
from src.judgment.forward_records import merge_forward_log, read_forward_log
from src.judgment.forward_types import ForwardScanInput


def _synthetic_frame(n_bars: int) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    open_time = pd.date_range("2026-07-01", periods=n_bars, freq="15min", tz="UTC")
    base = 100 + np.cumsum(rng.normal(0, 0.35, n_bars))
    spread = np.abs(rng.normal(0.4, 0.1, n_bars)) + 0.2
    opens = base
    closes = base + rng.normal(0, 0.25, n_bars)
    highs = np.maximum(opens, closes) + spread
    lows = np.minimum(opens, closes) - spread
    return pd.DataFrame(
        {
            "ts": (open_time.view("int64") // 10**6),
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": np.abs(rng.normal(1000, 100, n_bars)),
            "open_time": open_time.astype(str),
        }
    )


class _StubBooster:
    def predict(self, rows, num_iteration=None):  # noqa: ANN001, ARG002
        return np.full(len(rows), 0.9)


def _stub_artifact() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        threshold=0.5,
        relative_model_path="models/stub.txt",
        dataset_sha256="stub",
        model_path="models/stub.txt",
        best_iteration=1,
    )


def _run_pulse(frame: pd.DataFrame, existing: pd.DataFrame, monkeypatch: pytest.MonkeyPatch, detected_at: str):
    tip_i = len(frame) - 1
    monkeypatch.setattr(fs, "CANDIDATE_SOURCE", "rules")
    monkeypatch.setattr(
        fs, "iter_series", lambda **kw: iter([("okx", "TESTCOIN_USDT_SWAP", frame)])
    )
    monkeypatch.setattr(fs, "forward_candidate_indices", lambda enriched, **kw: [tip_i])
    scan = fs.scan_forward_records(
        ForwardScanInput(
            artifact=_stub_artifact(),
            booster=_StubBooster(),
            detected_at=detected_at,
            start_time=pd.Timestamp("2026-07-01", tz="UTC"),
            existing_log=existing,
        )
    )
    return scan


def test_tip_signal_recorded_same_pulse_and_backfilled_next(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    frame_t0 = _synthetic_frame(650)
    empty = read_forward_log(tmp_path / "missing.csv")

    # Pulse A: signal bar IS the tip -- must be recorded, not dropped.
    scan_a = _run_pulse(frame_t0, empty, monkeypatch, "pulse-A")
    assert len(scan_a.records) == 1
    rec = scan_a.records[0]
    assert rec["status"] == "open"
    assert rec["maker_filled"] is None  # entry pending sentinel
    sig_time = pd.Timestamp(rec["signal_time"])
    assert pd.Timestamp(rec["entry_time"]) == sig_time + pd.Timedelta(minutes=15)
    # proxy entry = signal bar close
    enriched_tip = float(frame_t0["close"].iloc[-1])
    assert rec["entry_price"] == pytest.approx(enriched_tip)

    merged_a = merge_forward_log(empty, scan_a.records)
    assert merged_a.new_signals == 1

    # CSV round-trip must preserve the pending sentinel (NaN, not False).
    log_path = tmp_path / "forward_log.csv"
    merged_a.frame.to_csv(log_path, index=False)
    persisted = read_forward_log(log_path)
    assert pd.isna(persisted.iloc[0]["maker_filled"])

    # Pulse B: one more bar printed; tracked key re-resolves with real entry.
    frame_t1 = _synthetic_frame(651)  # same seed -> same first 650 bars + 1
    scan_b = _run_pulse_tracked(frame_t1, persisted, monkeypatch, "pulse-B")
    assert len(scan_b.records) == 1
    rec_b = scan_b.records[0]
    assert rec_b["maker_filled"] is not None

    merged_b = merge_forward_log(persisted, scan_b.records)
    assert merged_b.new_signals == 0
    row = merged_b.frame.iloc[0]
    assert row["detected_at"] == "pulse-A"  # first-seen wins (lag accounting)
    assert row["entry_price"] == pytest.approx(float(frame_t1["open"].iloc[650]))
    assert not pd.isna(row["maker_filled"])


def _run_pulse_tracked(frame, existing, monkeypatch, detected_at):
    """Pulse where the candidate comes from the tracked-open key injection."""
    signal_i = len(frame) - 2  # yesterday's tip, now one bar back
    monkeypatch.setattr(fs, "CANDIDATE_SOURCE", "rules")
    monkeypatch.setattr(
        fs, "iter_series", lambda **kw: iter([("okx", "TESTCOIN_USDT_SWAP", frame)])
    )
    monkeypatch.setattr(fs, "forward_candidate_indices", lambda enriched, **kw: [signal_i])
    return fs.scan_forward_records(
        ForwardScanInput(
            artifact=_stub_artifact(),
            booster=_StubBooster(),
            detected_at=detected_at,
            start_time=pd.Timestamp("2026-07-01", tz="UTC"),
            existing_log=existing,
        )
    )
