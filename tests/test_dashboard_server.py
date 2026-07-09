from __future__ import annotations

import math
import warnings
from pathlib import Path

import pandas as pd
import pytest

from src.webapp import forward_payloads
from src.webapp.forward_payloads import forward_metrics


def test_forward_metrics_empty_log_has_no_fake_pf() -> None:
    metrics = forward_metrics(pd.DataFrame())

    assert metrics["n_trades"] == 0
    assert metrics["profit_factor"] is None
    assert metrics["win_rate"] is None


def test_forward_metrics_uses_net_returns() -> None:
    frame = pd.DataFrame({"net_ret": [0.0094, -0.0056, 0.0044]})

    metrics = forward_metrics(frame)

    assert metrics["n_trades"] == 3
    assert math.isclose(metrics["profit_factor"], 2.464, abs_tol=0.001)
    assert metrics["win_rate"] == 0.6667
    assert metrics["mean_net_per_trade"] == 0.00273


def test_forward_payload_rounds_numeric_rows_without_datetime_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = {column: "" for column in forward_payloads.FORWARD_COLUMNS}
    record.update(
        {
            "source": "okx",
            "symbol": "BTC_USDT_SWAP",
            "signal_time": "2026-07-09T00:00:00+00:00",
            "detected_at": "2026-07-09T00:15:00+00:00",
            "status": "closed",
            "score": 0.1234567,
            "threshold": 0.1111111,
            "entry_time": "2026-07-09T00:15:00+00:00",
            "entry_price": 123.456789,
            "maker_filled": True,
            "outcome": "tp",
            "exit_time": "2026-07-09T01:15:00+00:00",
            "realized_ret": 0.007389,
            "atr_pct": 0.00123456,
        }
    )
    log_path = tmp_path / "forward_log.csv"
    pd.DataFrame([record]).to_csv(log_path, index=False)
    monkeypatch.setattr(forward_payloads, "FORWARD_LOG_PATH", log_path)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        payload = forward_payloads.forward_payload()

    messages = [str(item.message) for item in caught]
    assert not any("round has no effect" in message for message in messages)
    assert payload["rows"][0]["score"] == 0.12346
    assert payload["rows"][0]["entry_price"] == 123.45679
