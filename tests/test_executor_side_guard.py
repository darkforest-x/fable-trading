"""Direction compatibility guard for the current long-only executor."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.execution import ledger
from src.execution.config import ExecutorConfig
from src.execution.executor import MISSING_SIDE, run_once, signal_trade_side


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("LONG", "long"),
        ("short", "short"),
        ("buy", "buy"),
    ],
)
def test_signal_trade_side_preserves_explicit_direction(raw, expected) -> None:
    assert signal_trade_side(pd.Series({"side": raw})) == expected


@pytest.mark.parametrize("raw", [None, float("nan"), "", "   "])
def test_absent_side_is_not_long(raw) -> None:
    """The one default that can turn an unlabelled row into a real buy.

    Until 2026-08-03 these resolved to "long". The mainline is short, so the row
    most likely to arrive without a side is a short one, and defaulting it to the
    only direction this executor can actually trade is the worst available guess.
    Takeover plan P0-02 / acceptance A-03.
    """
    assert signal_trade_side(pd.Series({"side": raw})) == MISSING_SIDE


def _write_signal(path: Path, *, side: str) -> None:
    now = pd.Timestamp.now(tz="UTC")
    pd.DataFrame(
        [
            {
                "source": "okx",
                "symbol": "BTC_USDT_SWAP",
                "signal_time": str(now - pd.Timedelta(minutes=5)),
                "detected_at": str(now),
                "status": "open",
                "score": 0.9,
                "threshold": 0.5,
                "entry_price": 100.0,
                "atr_pct": 0.01,
                "side": side,
            }
        ]
    ).to_csv(path, index=False)


@pytest.mark.parametrize("side", ["short", "unknown", ""])
def test_run_once_rejects_non_long_without_retry_spam(tmp_path: Path, side: str) -> None:
    forward_log = tmp_path / "forward_log.csv"
    ledger_path = tmp_path / "ledger.jsonl"
    _write_signal(forward_log, side=side)
    cfg = ExecutorConfig(
        forward_log=str(forward_log),
        ledger=str(ledger_path),
        kill_switch_file=str(tmp_path / "KILL"),
        sizing_mode="fixed",
        notional_usdt=10.0,
    )

    first = run_once(cfg, dry_run=True)

    assert first["opened"] == 0
    assert first["skipped"] == 1
    events = ledger.load_all(ledger_path)
    assert len(events) == 1
    assert events[0]["event"] == "skipped_unsupported_side"
    # normalized, not raw: an empty CSV field reads back as NaN and must land on
    # MISSING_SIDE so the ledger says why it was refused rather than showing blank
    assert events[0]["signal_side"] == (side or MISSING_SIDE)
    assert events[0]["side"] is None

    second = run_once(cfg, dry_run=True)
    assert second["opened"] == 0
    assert second["skipped"] == 0
    assert len(ledger.load_all(ledger_path)) == 1
