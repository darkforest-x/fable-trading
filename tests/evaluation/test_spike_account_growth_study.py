"""Focused contracts for the frozen-ledger account-growth orchestration."""
from __future__ import annotations

import hashlib
import json

import pandas as pd
import pytest
import yoyo.evaluation.spike_account_growth_study as study

from yoyo.evaluation.spike_account_growth_study import (
    base_asset_from_symbol,
    load_common_execution_trades,
    load_native_v1_trades,
    main,
    run_account_growth_study,
    select_period_scope,
    select_scope,
)


def _write_source(path):
    frame = pd.DataFrame([
        dict(signal_bar_open="2024-01-01T00:00Z", entry_time="2024-01-01T01:00Z", exit_time="2024-01-01T02:00Z", side=1, entry_price=100.0, initial_risk=10.0, exit_price=120.0, exit_reason="trail", net_return=.20, net_r=2.0, mfe_r=3.0, censored=False, variant="v1_common_execution_long", venue="okx", symbol="AAA-USDT-SWAP", timeframe_min=30, segment="development", year_block="2024"),
        dict(signal_bar_open="2024-01-01T00:00Z", entry_time="2024-01-01T01:00Z", exit_time="2024-01-01T03:00Z", side=1, entry_price=100.0, initial_risk=10.0, exit_price=110.0, exit_reason="trail", net_return=.10, net_r=1.0, mfe_r=2.0, censored=False, variant="v1_common_execution_long", venue="binance", symbol="AAAUSDT.P", timeframe_min=30, segment="development", year_block="2024"),
        dict(signal_bar_open="2024-01-01T02:00Z", entry_time="2024-01-01T03:00Z", exit_time="2024-01-01T04:00Z", side=1, entry_price=100.0, initial_risk=10.0, exit_price=90.0, exit_reason="stop", net_return=-.10, net_r=-1.0, mfe_r=.5, censored=False, variant="v7_bb_long", venue="okx", symbol="BBB-USDC-SWAP", timeframe_min=60, segment="development", year_block="2024"),
        dict(signal_bar_open="2024-01-01T02:00Z", entry_time="2024-01-01T03:00Z", exit_time="2024-01-01T04:00Z", side=1, entry_price=100.0, initial_risk=10.0, exit_price=90.0, exit_reason="stop", net_return=-.10, net_r=-1.0, mfe_r=.5, censored=False, variant="v6_unfiltered_long", venue="okx", symbol="NOISEUSDT", timeframe_min=60, segment="development", year_block="2024"),
    ])
    frame.to_csv(path, index=False, compression={"method": "gzip", "mtime": 0})
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_native_source(path):
    frame = pd.DataFrame([
        dict(event_id="native-30", venue="okx", symbol="AAA-USDT-SWAP", asset="AAA", timeframe_min=30,
             direction="long", signal_bar_open="2024-10-01T00:00Z", entry_time="2024-10-01T01:00Z",
             entry_price=100.0, exit_time="2024-10-01T02:00Z", exit_price=120.0, exit_reason="trail",
             net_return=.20, reference_signal_risk=10.0, net_r=2.0, mfe_return=.30, censored=False),
        dict(event_id="native-day", venue="okx", symbol="AAA-USDT-SWAP", asset="AAA", timeframe_min=1440,
             direction="long", signal_bar_open="2024-10-01T00:00Z", entry_time="2024-10-01T01:00Z",
             entry_price=100.0, exit_time="2024-10-01T02:00Z", exit_price=120.0, exit_reason="trail",
             net_return=.20, reference_signal_risk=10.0, net_r=2.0, mfe_return=.30, censored=False),
        dict(event_id="native-multiplier", venue="binance", symbol="1000PEPEUSDT", asset="1000PEPE", timeframe_min=60,
             direction="long", signal_bar_open="2024-10-02T00:00Z", entry_time="2024-10-02T01:00Z",
             entry_price=.01, exit_time="2024-10-02T02:00Z", exit_price=.012, exit_reason="trail",
             net_return=.20, reference_signal_risk=.001, net_r=2.0, mfe_return=.30, censored=False),
    ])
    frame.to_csv(path, index=False, compression={"method": "gzip", "mtime": 0})
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_symbol_normalization_reserves_cross_venue_perpetuals_as_one_asset():
    assert base_asset_from_symbol("BTCUSDT") == "BTC"
    assert base_asset_from_symbol("BTC-USDT-SWAP") == "BTC"
    assert base_asset_from_symbol("BTCUSDT.P") == "BTC"
    assert base_asset_from_symbol("SOL/USDC:USDC") == "SOL"
    assert base_asset_from_symbol("1000PEPEUSDT") == "PEPE"
    assert base_asset_from_symbol("1000000BOBUSDT") == "BOB"
    assert base_asset_from_symbol("1INCHUSDT") == "1INCH"


def test_loader_rejects_unpinned_source_and_scope_filters(tmp_path):
    path = tmp_path / "frozen.csv.gz"
    digest = _write_source(path)
    trades = load_common_execution_trades(path, expected_sha256=digest)
    assert trades.base_asset.tolist() == ["AAA", "AAA", "BBB"]
    assert trades.attrs["file_total_rows"] == 4
    assert trades.attrs["loaded_relevant_rows"] == 3
    assert len(select_scope(trades, arm="v1_common_execution_long", venue_scope="okx", timeframe=30)) == 1
    assert len(select_scope(trades, arm="v1_common_execution_long", venue_scope="combined", timeframe="all")) == 2
    with pytest.raises(ValueError, match="hash mismatch"):
        load_common_execution_trades(path, expected_sha256="0" * 64)


def test_loader_accounting_timestamp_keeps_same_bar_stop_after_entry(tmp_path):
    path = tmp_path / "same-bar.csv.gz"
    _write_source(path)
    raw = pd.read_csv(path)
    raw.loc[0, "exit_time"] = raw.loc[0, "entry_time"]
    raw.to_csv(path, index=False, compression={"method": "gzip", "mtime": 0})
    trades = load_common_execution_trades(path, expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    assert trades.loc[0, "account_exit_time"] > trades.loc[0, "exit_time"]
    assert trades.loc[1, "account_exit_time"] == trades.loc[1, "exit_time"]


def test_native_v1_mapping_is_long_only_and_excludes_daily_rows(tmp_path):
    path = tmp_path / "native.csv.gz"
    native = load_native_v1_trades(path, expected_sha256=_write_native_source(path))
    assert len(native) == 2
    row = native.iloc[0]
    assert row.trade_id == "native-30"
    assert row.variant == "v1_native_long" and row.side == 1 and row.base_asset == "AAA"
    assert row.initial_risk == pytest.approx(10)
    assert row.source_contract == "original_v1_native_exit_not_fair_common_execution"
    assert native.set_index("trade_id").loc["native-multiplier", "base_asset"] == "PEPE"


def test_fixed_grid_writes_auditable_outputs_and_preserves_rejections(tmp_path):
    source = tmp_path / "frozen.csv.gz"
    digest = _write_source(source)
    output = tmp_path / "result"
    summary = run_account_growth_study(
        output,
        source_path=source,
        expected_sha256=digest,
        arms=("v1_common_execution_long",),
        venue_scopes=("combined",),
        timeframes=(30,),
        period_scopes=("full",),
        sizings=("fixed", "compound"),
        risk_fractions=(.03,),
    )
    assert len(summary) == 2
    assert set(summary["sizing"]) == {"fixed", "compound"}
    assert summary["candidates"].eq(2).all()
    assert summary["accepted"].eq(1).all()
    assert summary["rejected"].eq(1).all()
    assert summary["rejected_base_asset_open"].eq(1).all()
    for name in ("summary.csv", "accepted_ledger.csv.gz", "rejections.csv.gz", "equity_curve.csv.gz", "daily_realized_pnl.csv.gz", "run_manifest.json"):
        assert (output / name).is_file()
    rejected = pd.read_csv(output / "rejections.csv.gz")
    assert rejected.rejection_reason.tolist() == ["base_asset_open", "base_asset_open"]
    accepted = pd.read_csv(output / "accepted_ledger.csv.gz")
    assert {"exit_price", "exit_reason", "net_return", "net_r", "mfe_r", "segment", "year_block"} <= set(accepted)
    manifest = json.loads((output / "run_manifest.json").read_text())
    assert manifest["source_rows"] == 4
    assert manifest["loaded_relevant_rows"] == 3
    assert manifest["sources"]["common_execution"]["file_total_rows"] == 4
    assert manifest["sources"]["common_execution"]["loaded_relevant_rows"] == 3


def test_period_scopes_use_entry_time_and_each_subperiod_restarts_at_1000(tmp_path):
    source = tmp_path / "periods.csv.gz"
    _write_source(source)
    raw = pd.read_csv(source)
    raw.loc[0, ["signal_bar_open", "entry_time", "exit_time"]] = [
        "2024-10-01T00:00Z", "2024-10-01T01:00Z", "2024-10-01T02:00Z"
    ]
    raw.loc[1, ["signal_bar_open", "entry_time", "exit_time"]] = [
        "2025-10-01T00:00Z", "2025-10-01T01:00Z", "2025-10-01T02:00Z"
    ]
    raw.to_csv(source, index=False, compression={"method": "gzip", "mtime": 0})
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    trades = load_common_execution_trades(source, expected_sha256=digest)
    assert len(select_period_scope(trades, "development")) == 1
    assert len(select_period_scope(trades, "validation")) == 1
    summary = run_account_growth_study(
        tmp_path / "period-output", source_path=source, expected_sha256=digest,
        arms=("v1_common_execution_long",), venue_scopes=("combined",), timeframes=(30,),
        period_scopes=("development", "validation"), sizings=("fixed",), risk_fractions=(.03,),
    )
    assert set(summary.period_scope) == {"development", "validation"}
    assert summary.initial_balance.eq(1000).all()


def test_subperiod_rejections_are_counted_but_not_written_as_detail(tmp_path):
    source = tmp_path / "subperiod-rejections.csv.gz"
    _write_source(source)
    raw = pd.read_csv(source)
    # Two same-asset candidates share one development entry timestamp.  The
    # account accepts one and records the other as asset-open, but subperiod
    # rejection detail is intentionally omitted from the output bundle.
    raw.loc[[0, 1], ["signal_bar_open", "entry_time", "exit_time"]] = [
        ["2024-10-01T00:00Z", "2024-10-01T01:00Z", "2024-10-01T02:00Z"],
        ["2024-10-01T00:00Z", "2024-10-01T01:00Z", "2024-10-01T03:00Z"],
    ]
    raw.to_csv(source, index=False, compression={"method": "gzip", "mtime": 0})
    digest = hashlib.sha256(source.read_bytes()).hexdigest()

    summary = run_account_growth_study(
        tmp_path / "subperiod-output", source_path=source, expected_sha256=digest,
        arms=("v1_common_execution_long",), venue_scopes=("combined",), timeframes=(30,),
        period_scopes=("development",), sizings=("fixed",), risk_fractions=(.03,),
    )

    row = summary.iloc[0]
    assert row["accepted"] == 1
    assert row["rejected"] == 1
    assert row["rejected_base_asset_open"] == 1
    rejected = pd.read_csv(tmp_path / "subperiod-output" / "rejections.csv.gz")
    assert rejected.empty


def test_development_cross_boundary_is_zero_pnl_censor_not_future_exit_read(tmp_path):
    source = tmp_path / "cross-boundary.csv.gz"
    _write_source(source)
    raw = pd.read_csv(source)
    raw.loc[0, ["signal_bar_open", "entry_time", "exit_time", "net_return", "exit_price"]] = [
        "2024-10-01T00:00Z", "2024-10-01T01:00Z", "2025-10-01T02:00Z", .20, 120.0
    ]
    raw.to_csv(source, index=False, compression={"method": "gzip", "mtime": 0})
    first_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    initial = load_common_execution_trades(source, expected_sha256=first_hash)
    development = select_period_scope(initial, "development")
    assert development.iloc[0].period_boundary_censored
    assert development.iloc[0].censored
    assert development.iloc[0].account_exit_time == pd.Timestamp("2025-09-10T00:00:00Z")
    first = run_account_growth_study(
        tmp_path / "first", source_path=source, expected_sha256=first_hash,
        arms=("v1_common_execution_long",), venue_scopes=("okx",), timeframes=(30,),
        period_scopes=("development",), sizings=("fixed",), risk_fractions=(.03,),
    )
    raw.loc[0, ["net_return", "exit_price"]] = [999.0, 99999.0]
    raw.to_csv(source, index=False, compression={"method": "gzip", "mtime": 0})
    second_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    second = run_account_growth_study(
        tmp_path / "second", source_path=source, expected_sha256=second_hash,
        arms=("v1_common_execution_long",), venue_scopes=("okx",), timeframes=(30,),
        period_scopes=("development",), sizings=("fixed",), risk_fractions=(.03,),
    )
    assert first.final_balance.iloc[0] == pytest.approx(1000)
    assert second.final_balance.iloc[0] == pytest.approx(first.final_balance.iloc[0])
    assert second.period_boundary_censored.iloc[0] == 1


def test_cli_requires_only_output_and_delegates_without_real_replay(tmp_path, monkeypatch):
    seen = {}

    def fake_run(output_dir, **kwargs):
        seen["output"] = output_dir
        seen.update(kwargs)
        return pd.DataFrame()

    monkeypatch.setattr(study, "run_account_growth_study", fake_run)
    assert main(["--output", str(tmp_path / "cli-output")]) == 0
    assert seen["output"] == tmp_path / "cli-output"
    assert seen["expected_sha256"] == study.COMMON_EXECUTION_SHA256
