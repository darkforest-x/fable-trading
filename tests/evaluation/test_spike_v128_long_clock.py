"""Focused checks for long-horizon clock cohorts and empty-stream ledgers."""
import pandas as pd
import pytest

from yoyo.evaluation import spike_v128_entry_clock as prior_clock
from yoyo.evaluation import spike_v128_long_clock as study


TZ = "Asia/Shanghai"


def raw_trade(key, entry, exit_time, *, symbol="BTCUSDT", status="closed", net_return=0.01, net_r=1.0):
    return {
        "trade_key": key, "arm": "v9_both", "symbol": symbol, "timeframe_min": 15,
        "policy": "baseline", "status": status, "entry_time": entry, "exit_time": exit_time,
        "net_return": net_return if status == "closed" else None,
        "net_r": net_r if status == "closed" else None,
        "gross_return": net_return + 0.002 if status == "closed" else None,
    }


def clocked(rows):
    columns = ["trade_key", "arm", "symbol", "timeframe_min", "policy", "status",
               "entry_time", "exit_time", "net_return", "net_r", "gross_return"]
    return prior_clock.clock(pd.DataFrame(rows, columns=columns), TZ)


def test_analysis_window_uses_actual_entry_and_right_open_end():
    frame = pd.DataFrame({"entry_time": [
        "2022-12-31T23:59:00Z", "2023-01-01T00:00:00Z", "2026-09-23T04:00:00Z",
    ]})
    cfg = {"analysis_start": "2023-01-01T00:00:00Z", "end": "2026-09-23T04:00:00Z"}
    selected = study.analysis_window(frame, cfg)
    assert selected.index.tolist() == [1]


def test_calendar_controls_require_same_bjt_month_as_target():
    targets = clocked([
        raw_trade("same-month", "2023-02-02T16:00:00Z", "2023-02-02T17:00:00Z"),
        raw_trade("wrong-month", "2023-03-02T16:00:00Z", "2023-03-02T17:00:00Z"),
    ])
    controls = clocked([
        raw_trade("same-month", "2023-02-03T16:00:00Z", "2023-02-03T17:00:00Z"),
        raw_trade("wrong-month", "2023-02-02T16:00:00Z", "2023-02-02T17:00:00Z"),
    ]).rename(columns={"net_return": "net_return", "net_r": "net_r"})
    targets["policy"] = "retest"
    controls["policy"] = "retest"
    targets = study.add_calendar_columns(targets, TZ)
    controls = study.add_calendar_columns(controls, TZ)

    february = study._calendar_pairs(targets, controls, "4h", "2023-02")
    march = study._calendar_pairs(targets, controls, "4h", "2023-03")
    assert february.trade_key.tolist() == ["same-month"]
    assert march.empty


def test_calendar_counts_keep_cross_period_exit_and_left_cut_boundary():
    trades = clocked([
        raw_trade("known", "2023-01-31T15:00:00Z", "2023-01-31T15:30:00Z"),
        raw_trade("cross", "2023-01-31T14:00:00Z", "2023-02-01T16:00:00Z"),
        raw_trade("censored", "2023-01-30T14:00:00Z", None, status="censored_boundary"),
    ])
    trades = study.add_calendar_columns(trades, TZ)
    counts = study.calendar_period_counts(
        trades, "month", "2023-01", TZ, "2023-03-01T00:00:00Z", "2023-01-01T00:00:00Z",
    )
    assert counts["period_end_known_closed"] == 1
    assert counts["cross_period_exit"] == 1
    assert counts["period_end_known_closed_rate"] == pytest.approx(1 / 3)
    assert counts["calendar_period_end_observed"] is True
    # Analysis begins at 08:00 BJT on Jan 1, so Jan 1's first eight hours are outside.
    assert counts["calendar_period_start_observed"] is False
    assert counts["calendar_period_complete_in_analysis_window"] is False


def test_fixed_incumbent_universe_is_frozen_before_analysis_start():
    start_ms = pd.Timestamp("2023-01-01T00:00:00Z").value // 10**6
    rows = {
        "old": {"first_ms": start_ms - 1},
        "starts_at_cut": {"first_ms": start_ms},
        "new": {"first_ms": start_ms + 1},
    }
    assert study.fixed_incumbent_symbols(rows, "2023-01-01T00:00:00Z") == {"old"}


def test_fixed_incumbent_summary_excludes_first_analysis_month():
    trades = clocked([
        raw_trade("jan", "2023-01-05T00:00:00Z", "2023-01-05T01:00:00Z", net_return=0.01),
        raw_trade("feb", "2023-02-05T00:00:00Z", "2023-02-05T01:00:00Z", net_return=0.02),
        raw_trade("new", "2023-02-06T00:00:00Z", "2023-02-06T01:00:00Z", symbol="NEWUSDT", net_return=0.03),
    ])
    controls = clocked([])
    cfg = {
        "analysis_start": "2023-01-01T00:00:00Z", "analysis_split": "2023-02-01T00:00:00Z",
        "end": "2023-03-01T00:00:00Z", "timezone": TZ, "timeframes": [15],
        "arms": ["v9_both"], "policies": ["baseline"], "bootstrap": 10,
        "statistics_seed": 7,
    }
    input_rows = {
        "BTCUSDT": {"first_ms": pd.Timestamp("2022-11-01T00:00:00Z").value // 10**6},
        "NEWUSDT": {"first_ms": pd.Timestamp("2023-02-01T00:00:00Z").value // 10**6},
    }
    summary = study.build_fixed_incumbent_summary(trades, controls, cfg, input_rows)
    all_months = summary[(summary.scope == "all_fixed_incumbents") &
                         summary.kind.eq("overall") & summary.period.eq("all")].iloc[0]
    without_first = summary[(summary.scope == "excluding_first_analysis_month") &
                            summary.kind.eq("overall") & summary.period.eq("all")].iloc[0]
    assert all_months.closed == 2
    assert without_first.closed == 1
    assert all_months.symbols == 1
    assert all_months.fixed_incumbent_symbols == 1


def test_empty_worker_stream_may_omit_execution_columns(tmp_path):
    path = tmp_path / "empty.csv"
    path.write_text("trade_key,policy\n")
    frame = study._read_required_csv(path, ("trade_key", "policy", "entry_time"))
    assert frame.empty
    assert frame.columns.tolist() == ["trade_key", "policy", "entry_time"]

    empty_trades = tmp_path / "empty-trades.csv"
    empty_trades.write_text("trade_key,arm,symbol,timeframe_min,policy,status\n")
    trades = study._read_required_csv(empty_trades, study.TRADE_COLUMNS)
    assert trades.empty
    assert trades.columns.tolist() == list(study.TRADE_COLUMNS)

    empty_controls = tmp_path / "empty-controls.csv"
    empty_controls.write_text("trade_key,control_pool\n")
    controls = study._read_required_csv(empty_controls, study.CONTROL_COLUMNS)
    assert controls.empty
    assert controls.columns.tolist() == list(study.CONTROL_COLUMNS)


def test_nonempty_worker_stream_may_not_omit_execution_columns(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("trade_key,policy\na,baseline\n")
    with pytest.raises(ValueError, match="lacks required columns"):
        study._read_required_csv(path, ("trade_key", "policy", "entry_time"))


def test_frozen_historical_clock_candidates_are_not_reselected():
    ids = {row["candidate_id"] for row in study.FIXED_CLOCK_CANDIDATES}
    assert ids == {
        "baseline_15_v9_both_4h20", "baseline_15_joint_4h20",
        "baseline_60_v9_both_4h00", "baseline_60_joint_4h00",
        "retest_15_v9_both_4h20", "retest_15_v9_both_1h23",
    }
