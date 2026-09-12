"""Focused causal and selection tests for account-growth post diagnostics."""
from __future__ import annotations

import hashlib

import pandas as pd

from yoyo.evaluation.spike_account_growth import simulate_shared_account
from yoyo.evaluation.spike_account_growth_post import (
    assert_seed_zero_reproduces_summary,
    build_btc_eth_regime,
    build_launch_breadth,
    replay_seed_sensitivity,
    select_on_development,
    select_seed_zero_configurations,
)


def test_pandas_mixed_account_timestamps_cover_same_bar_nanosecond_receipt():
    values = pd.Series([
        "2024-11-01 13:00:00+00:00",
        "2024-11-01 13:00:00.000000001+00:00",
    ])
    parsed = pd.to_datetime(values, utc=True, format="mixed", errors="raise")
    assert parsed.iloc[1] - parsed.iloc[0] == pd.Timedelta(nanoseconds=1)


def _market_csv(path, closes):
    times = pd.date_range("2024-01-01", periods=len(closes), freq="30min", tz="UTC")
    pd.DataFrame({"time": times, "close": closes}).to_csv(
        path, index=False, compression={"method": "gzip", "mtime": 0}
    )
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_market_feature_is_known_only_after_four_hour_bar_close(tmp_path):
    btc = tmp_path / "btc.csv.gz"
    eth = tmp_path / "eth.csv.gz"
    values = list(range(1, 8 * 250 + 1))
    btc_hash = _market_csv(btc, values)
    eth_hash = _market_csv(eth, [value * 2 for value in values])
    regime = build_btc_eth_regime(btc, eth, btc_sha256=btc_hash, eth_sha256=eth_hash)
    assert regime.known_time.iloc[0] == pd.Timestamp("2024-01-01T04:00:00Z")
    assert regime.btc_close.iloc[0] == 8
    first_ready = regime.loc[regime.market_regime.ne("unavailable")].iloc[0]
    assert first_ready.known_time > pd.Timestamp("2024-01-01T04:00:00Z")


def test_breadth_uses_trailing_events_and_includes_current_closed_signal():
    trades = pd.DataFrame([
        dict(variant="v7_bb_both", entry_time="2024-01-01T00:00Z", base_asset="A", side=1),
        dict(variant="v7_bb_both", entry_time="2024-01-01T00:00Z", base_asset="A", side=1),
        dict(variant="v7_bb_both", entry_time="2024-01-01T01:00Z", base_asset="B", side=-1),
        dict(variant="v7_bb_both", entry_time="2024-01-02T02:00Z", base_asset="C", side=1),
    ])
    trades["entry_time"] = pd.to_datetime(trades["entry_time"], utc=True)
    query = pd.to_datetime(["2024-01-01T00:00Z", "2024-01-01T01:00Z", "2024-01-02T02:00Z"], utc=True)
    breadth, meta = build_launch_breadth(
        trades, query, development_end=pd.Timestamp("2024-01-03T00:00Z"), quantile=.5
    )
    assert breadth.breadth_assets_24h.tolist() == [1, 2, 1]
    assert breadth.breadth_long_assets_24h.tolist() == [1, 1, 1]
    assert meta["development_observations"] == 3


def test_development_choice_is_joined_to_same_validation_parameters():
    rows = []
    for period, balances in (("development", [1200, 1500]), ("validation", [2000, 400])):
        for risk, balance in zip((.03, .10), balances):
            rows.append({
                "source_arm": "v7_bb_both", "venue_scope": "combined", "timeframe": "240",
                "period_scope": period, "sizing": "compound", "risk_fraction": risk,
                "final_balance": balance, "max_drawdown_fraction": .2, "floor_triggered": balance < 500,
                "bankrupt": False, "reached_100k": False, "run_id": f"{period}-{risk}",
                "accepted": 2, "candidates": 10,
            })
    rows.extend([
        {**rows[0], "period_scope": "full", "final_balance": 1800, "run_id": "full-.03"},
        {**rows[1], "period_scope": "full", "final_balance": 600, "run_id": "full-.10"},
    ])
    selected = select_on_development(pd.DataFrame(rows))
    assert selected.risk_fraction.iloc[0] == .10
    assert selected.development_final_balance.iloc[0] == 1500
    assert selected.validation_final_balance.iloc[0] == 400


def _sensitivity_source() -> pd.DataFrame:
    """Small validation-only source with one contested and one solo entry time."""
    frame = pd.DataFrame([
        dict(trade_id="a", variant="v7_bb_both", venue="okx", timeframe_min=30,
             entry_time="2025-10-01T00:00Z", exit_time="2025-10-01T01:00Z",
             account_exit_time="2025-10-01T01:00Z", base_asset="A", entry_price=100,
             initial_risk=10, side=1, net_return=0, censored=False),
        dict(trade_id="b", variant="v7_bb_both", venue="okx", timeframe_min=30,
             entry_time="2025-10-01T00:00Z", exit_time="2025-10-01T01:00Z",
             account_exit_time="2025-10-01T01:00Z", base_asset="B", entry_price=100,
             initial_risk=10, side=1, net_return=0, censored=False),
        dict(trade_id="solo", variant="v7_bb_both", venue="okx", timeframe_min=30,
             entry_time="2025-10-01T02:00Z", exit_time="2025-10-01T03:00Z",
             account_exit_time="2025-10-01T03:00Z", base_asset="C", entry_price=100,
             initial_risk=10, side=1, net_return=0, censored=False),
    ])
    for name in ("entry_time", "exit_time", "account_exit_time"):
        frame[name] = pd.to_datetime(frame[name], utc=True)
    return frame


def _frozen_seed_zero_selection() -> pd.DataFrame:
    return pd.DataFrame([{
        "source_arm": "v7_bb_both", "venue_scope": "okx", "timeframe": "30",
        "sizing": "fixed", "risk_fraction": .03, "development_final_balance": 1100.0,
        "development_closed_mdd": .10, "development_run_id": "development-seed0",
    }])


def test_seed_sensitivity_keeps_parameters_fixed_and_seed_only_changes_contested_admission():
    source = _sensitivity_source()
    engine_input = source.assign(exit_time=source.account_exit_time).loc[:, [
        "trade_id", "entry_time", "exit_time", "base_asset", "entry_price", "initial_risk", "side", "net_return", "censored",
    ]]
    selected = []
    for seed in range(32):
        ledger = simulate_shared_account(
            engine_input, sizing="fixed", risk_fraction=.03, portfolio_risk_cap=.03, seed=seed,
        )["ledger"].set_index("trade_id")
        selected.append(ledger.index[ledger.selected].tolist())
        assert ledger.loc["solo", "selected"]  # Its timestamp has no competing candidate.
    assert {tuple(ids) for ids in selected} >= {("a", "solo"), ("b", "solo")}

    replayed = replay_seed_sensitivity(
        source, _frozen_seed_zero_selection(), seeds=(0, 1, 2), portfolio_risk_cap=.03,
    )
    assert len(replayed) == 6  # validation and full, never the original parameter grid.
    assert replayed[["timeframe", "sizing", "risk_fraction", "selection_seed", "selection_period"]].drop_duplicates().to_dict("records") == [{
        "timeframe": "30", "sizing": "fixed", "risk_fraction": .03,
        "selection_seed": 0, "selection_period": "development",
    }]
    assert replayed["max_balance"].eq(1000.0).all()
    assert replayed["max_closed_drawdown_fraction"].eq(0.0).all()
    assert not replayed["reached_100k"].any()


def test_seed_zero_replay_reproduces_frozen_summary_without_validation_selection():
    source = _sensitivity_source()
    replayed = replay_seed_sensitivity(source, _frozen_seed_zero_selection(), seeds=(0,), portfolio_risk_cap=.03)
    expected_rows = []
    for period_scope in ("validation", "full"):
        engine_input = source.assign(exit_time=source.account_exit_time).loc[:, [
            "trade_id", "entry_time", "exit_time", "base_asset", "entry_price", "initial_risk", "side", "net_return", "censored",
        ]]
        summary = simulate_shared_account(
            engine_input, sizing="fixed", risk_fraction=.03, portfolio_risk_cap=.03, seed=0,
        )["summary"]
        expected_rows.append({
            "source_arm": "v7_bb_both", "venue_scope": "okx", "timeframe": "30", "sizing": "fixed",
            "risk_fraction": .03, "period_scope": period_scope, "seed": "0",
            "final_balance": summary["final_balance"], "net_pnl": summary["net_pnl"], "net_return": summary["net_return"],
            "candidates": summary["candidates"], "accepted": summary["selected"], "rejected": summary["rejected"],
            "closed": summary["closed"], "censored_boundary": summary["censored_boundary"],
            "floor_triggered": summary["floor_triggered"], "bankrupt": summary["bankrupt"],
        })
    assert_seed_zero_reproduces_summary(replayed, pd.DataFrame(expected_rows))

    rows = []
    for period, balances in (("development", [1200, 1500]), ("validation", [1_000_000, 1])):
        for risk, balance in zip((.03, .10), balances):
            rows.append({
                "source_arm": "v7_bb_both", "venue_scope": "combined", "timeframe": "240",
                "period_scope": period, "sizing": "compound", "risk_fraction": risk,
                "final_balance": balance, "max_drawdown_fraction": .2, "floor_triggered": False,
                "bankrupt": False, "reached_100k": False, "run_id": f"{period}-{risk}",
                "accepted": 2, "candidates": 10, "seed": "0",
            })
    rows.extend([
        {**rows[0], "period_scope": "full", "final_balance": 1800, "run_id": "full-.03"},
        {**rows[1], "period_scope": "full", "final_balance": 600, "run_id": "full-.10"},
    ])
    selected = select_seed_zero_configurations(pd.DataFrame(rows))
    assert selected.risk_fraction.iloc[0] == .10
