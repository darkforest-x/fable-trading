import json

import pytest

from yoyo.copier.chartprime_history import chartprime_compound_summary


def test_chartprime_compound_summary_uses_each_trade_return(monkeypatch, tmp_path):
    path = tmp_path / "trades.json"
    path.write_text(
        json.dumps(
            {
                "trades": [
                    {"time": "2026-01-01", "result": "win_tp", "net_pnl_100u_5x": 10},
                    {"time": "2026-01-02", "result": "loss_sl", "net_pnl_100u_5x": -20},
                    {"time": "2026-01-03", "result": "unknown", "net_pnl_100u_5x": 99},
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("yoyo.copier.chartprime_history.TRADES_PATH", path)

    result = chartprime_compound_summary(initial_equity=500)

    assert result["settled_trades"] == 2
    assert result["final_equity"] == pytest.approx(440)
    assert result["net_pnl"] == pytest.approx(-60)
    assert result["max_drawdown_pct"] == pytest.approx(20)
    assert result["wiped_out"] is False
