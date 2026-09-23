"""Synthetic coverage for frozen BB-slope cohorts and clustered inference."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import spike_v128_bb_slope_stats as stats


CFG = {
    "start": "2025-01-01T00:00:00Z",
    "split": "2025-01-05T00:00:00Z",
    "end": "2025-01-10T00:00:00Z",
    "symbols": ["ETH_USDT_SWAP"],
    "timeframes": [15],
    "round_trip_cost": 0.002,
    "bootstrap_draws": 300,
    "permutations": 300,
    "statistics_seed": 42,
}


def _synthetic_rows() -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict[str, object]] = []
    control_rows: list[dict[str, object]] = []

    def add(key: str, signal: pd.Timestamp, exit_: pd.Timestamp | None, feature: float,
            side: int, *, censored: bool = False) -> None:
        entry = signal + pd.Timedelta(minutes=5)
        net_return = 0.001 if not censored else np.nan
        gross_return = net_return + CFG["round_trip_cost"] if not censored else np.nan
        rows.append({
            "arm": "v9_both", "symbol": "ETH_USDT_SWAP", "timeframe_min": 15,
            "side": side, "trade_key": key, "signal_close": signal.isoformat(),
            "entry_time": entry.isoformat(), "exit_time": exit_.isoformat() if exit_ is not None else "",
            "censored": censored, "gross_return": gross_return, "net_return": net_return,
            "net_r": net_return / 0.02 if not censored else np.nan,
            "initial_risk_frac": 0.02, "mfe_known_r": 0.5 if not censored else np.nan,
            "pre12_opp": feature, "pre12_trend": feature * 0.5,
            "pre12_inward": feature * 0.25, "now12_opp": feature + 1,
            "pre3_opp": feature + 2, "pre24_opp": feature + 3,
            "episode12_opp": feature + 4,
        })
        control_rows.append({
            "trade_key": key, "matched": True,
            "control_net_return": 0.0005 if not censored else np.nan,
            "control_signal_close": signal.isoformat(),
            "control_exit_time": exit_.isoformat() if exit_ is not None else "",
        })

    start = pd.Timestamp(CFG["start"])
    split = pd.Timestamp(CFG["split"])
    for i, value in enumerate(range(6)):
        signal = start + pd.Timedelta(hours=i + 1)
        add(f"early-{i}", signal, signal + pd.Timedelta(minutes=20), float(value), 1 if i % 2 == 0 else -1)
    for i, value in enumerate((-100.0, -50.0, 100.0, 150.0)):
        signal = split + pd.Timedelta(hours=i + 1)
        add(f"late-{i}", signal, signal + pd.Timedelta(minutes=20), value, 1 if i % 2 == 0 else -1)
    # Signal and entry are pre-split, but target/control exit crosses the cut.
    cross_signal = split - pd.Timedelta(hours=2)
    add("crosscut", cross_signal, split + pd.Timedelta(minutes=10), 2.5, 1)
    censored_signal = split + pd.Timedelta(days=2)
    add("censored", censored_signal, None, 3.5, -1, censored=True)
    return pd.DataFrame(rows), pd.DataFrame(control_rows)


def _write_parent_run(root: Path, config: dict[str, object]) -> Path:
    run_dir = root / "run"
    folder = run_dir / "streams" / "ETH_USDT_SWAP_15m"
    folder.mkdir(parents=True)
    trades, controls = _synthetic_rows()
    trade_path, control_path = folder / "trades.csv.gz", folder / "controls.csv.gz"
    trades.to_csv(trade_path, index=False, compression="gzip")
    controls.to_csv(control_path, index=False, compression="gzip")
    files = {name: hashlib.sha256((folder / name).read_bytes()).hexdigest()
             for name in ("trades.csv.gz", "controls.csv.gz")}
    identity = {"config": config, "selected": [15]}
    run_identity = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    stream_receipt = {"symbol": "ETH_USDT_SWAP", "minutes": 15,
                      "run_identity": run_identity, "files": files, "trades": len(trades)}
    (folder / "receipt.json").write_text(json.dumps(stream_receipt))
    manifest = {"complete": True, "errors": [], "run_identity": run_identity,
                "receipts": [{"symbol": "ETH_USDT_SWAP", "minutes": 15,
                              "run_identity": run_identity, "files": files}]}
    (run_dir / "identity.json").write_text(json.dumps(identity))
    (run_dir / "manifest.json").write_text(json.dumps(manifest))
    return run_dir


def test_thresholds_freeze_on_closed_early_rows_and_crosscut_is_purged(tmp_path: Path) -> None:
    run_dir = _write_parent_run(tmp_path, CFG)
    output = tmp_path / "stats"
    receipt = stats.run(run_dir, CFG, output)

    thresholds = pd.read_csv(output / "thresholds.csv")
    primary_cut = thresholds.loc[thresholds.feature.eq("pre12_opp")].iloc[0]
    assert primary_cut.feature_role == "primary"
    assert primary_cut.early_closed_n == 6
    assert primary_cut.q1_tercile == pytest.approx(5 / 3)
    assert primary_cut.q2_tercile == pytest.approx(10 / 3)

    cohorts = pd.read_csv(output / "cohort_metrics.csv")
    primary = cohorts.loc[(cohorts.feature == "pre12_opp") & (cohorts.bucket == "baseline")]
    all_row = primary.loc[primary.cohort.eq("all")].iloc[0]
    early_row = primary.loc[primary.cohort.eq("earlier")].iloc[0]
    late_row = primary.loc[primary.cohort.eq("later")].iloc[0]
    assert all_row.n_closed == 11  # includes the closed cross-cut target
    assert early_row.n_closed == 6  # cross-cut is excluded from the early fold
    assert late_row.n_closed == 4
    assert all_row.matched_control_n == 11
    assert early_row.matched_control_n == 6
    assert late_row.matched_control_n == 4
    assert all_row.mean_net_bp == pytest.approx(10.0)

    primary_inference = pd.read_csv(output / "primary_inference.csv").iloc[0]
    assert primary_inference.early_closed_n == 6
    assert primary_inference.later_low_n == 2
    assert primary_inference.later_high_n == 2
    assert primary_inference.primary_status == "ready"

    side = pd.read_csv(output / "side_metrics.csv")
    side_cut = side.loc[(side.cohort == "earlier") & (side.bucket == "baseline")]
    assert set(side_cut.side) == {-1, 1}
    assert np.allclose(side_cut.q1_tercile.to_numpy(), 5 / 3)
    assert receipt["generated_at"].endswith("+00:00")
    assert receipt["statistics_source_sha256"]
    assert receipt["statistics_test_source_sha256"]
    assert set(receipt["outputs"]) == {
        "cohort_metrics.csv", "thresholds.csv", "feature_diagnostics.csv",
        "primary_inference.csv", "side_metrics.csv",
    }
    with pytest.raises(FileExistsError):
        stats.run(run_dir, CFG, output)


def test_input_identity_binds_config_and_ledger_costs_are_checked(tmp_path: Path) -> None:
    run_dir = _write_parent_run(tmp_path, CFG)
    changed = {**CFG, "round_trip_cost": 0.003}
    with pytest.raises(ValueError, match="config differs"):
        stats._verify_run_manifest(run_dir, changed, CFG["symbols"], CFG["timeframes"])

    trades, _ = _synthetic_rows()
    trades.loc[0, "gross_return"] += 0.001
    with pytest.raises(ValueError, match="round-trip cost"):
        stats._prepare_trades(trades, CFG)


def test_stratified_permutation_keeps_labels_in_fixed_strata_and_counts() -> None:
    frame = pd.DataFrame({
        "_bucket": ["high", "low", "high", "low"],
        "side": [1, -1, 1, 1],
        "_signal_week": ["2025-W01", "2025-W02", "2025-W03", "2025-W03"],
        "outcome": [1.0, 0.0, 1.0, 0.0],
    })
    result = stats._stratified_permutation(frame, "outcome", draws=200, seed=4)
    assert result["high_n_observed"] == 2
    assert result["high_n_draw_min"] == result["high_n_draw_max"] == 2
    assert result["shufflable_n"] == 2
    assert result["coverage"] == pytest.approx(0.5)
    assert np.isfinite(result["p_value"])


def test_holm_keeps_seven_prespecified_slots_when_p_values_are_missing() -> None:
    values = pd.Series([0.01, np.nan, 0.04, np.nan, np.nan, np.nan, np.nan])
    adjusted = stats._holm(values, family_n=7)
    assert adjusted.iloc[0] == pytest.approx(0.07)
    assert adjusted.iloc[2] == pytest.approx(0.24)
    assert adjusted.iloc[[1, 3, 4, 5, 6]].isna().all()


def test_week_bootstrap_resamples_whole_signal_weeks() -> None:
    frame = pd.DataFrame({
        "_signal_week": np.repeat(["2025-W01", "2025-W02", "2025-W03", "2025-W04"], 2),
        "_bucket": ["high", "low"] * 4,
        "outcome": [1.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 1.0],
    })
    draws, seed = 200, 19
    result = stats._cluster_bootstrap(frame, "outcome", draws=draws, seed=seed)

    # Independent slow reference: each sampled unit is a whole week, keeping
    # the within-week high/low outcome pair together.
    weeks = list(sorted(frame._signal_week.unique()))
    by_week = {week: frame.loc[frame._signal_week.eq(week)] for week in weeks}
    rng = np.random.default_rng(seed)
    samples = []
    for selected in rng.choice(weeks, size=(draws, len(weeks)), replace=True):
        high = np.concatenate([by_week[week].loc[by_week[week]._bucket.eq("high"), "outcome"].to_numpy()
                               for week in selected])
        low = np.concatenate([by_week[week].loc[by_week[week]._bucket.eq("low"), "outcome"].to_numpy()
                              for week in selected])
        samples.append(float(high.mean() - low.mean()))
    expected = np.quantile(samples, [0.025, 0.975])
    assert result["valid_draws"] == draws
    assert result["ci_low"] == pytest.approx(expected[0])
    assert result["ci_high"] == pytest.approx(expected[1])
