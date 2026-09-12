"""Synthetic contracts for the frozen SPIKE account-report generator."""
from __future__ import annotations

import hashlib
import json

import pandas as pd
import pytest

from yoyo.evaluation.spike_account_growth_report import build_account_growth_report, main


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_csv(path, frame):
    path.parent.mkdir(parents=True, exist_ok=True)
    compression = {"method": "gzip", "mtime": 0} if path.suffix == ".gz" else None
    frame.to_csv(path, index=False, compression=compression)
    return _sha(path)


def _bundle(tmp_path):
    result, post = tmp_path / "full", tmp_path / "post"
    result.mkdir()
    full_rows = []
    for run_id, arm, contract, sizing, risk, balance in (
        ("v1-fair", "v1_common_execution_long", "common_execution_next_open_shared_exit", "fixed", .03, 1_500.),
        ("v7-fair", "v7_bb_long", "common_execution_next_open_shared_exit", "compound", .10, 120_000.),
        ("v1-native", "v1_native_long", "original_v1_native_exit_not_fair_common_execution", "fixed", .05, 2_000.),
    ):
        base = dict(run_id=run_id, source_arm=arm, venue_scope="combined", timeframe="30", source_contract=contract, sizing=sizing, risk_fraction=risk, seed=0, initial_balance=1000., portfolio_risk_cap=.10, max_drawdown_fraction=.25)
        full_rows.extend([
            {**base, "period_scope": "full", "final_balance": balance, "reached_100k": balance >= 100000},
            {**base, "run_id": f"{run_id}-dev", "period_scope": "development", "final_balance": 900., "reached_100k": False},
            {**base, "run_id": f"{run_id}-val", "period_scope": "validation", "final_balance": 800., "reached_100k": False},
        ])
    summary = pd.DataFrame(full_rows)
    curve = pd.DataFrame([
        dict(run_id="v1-fair", period_scope="full", time="2025-01-01T00:00:00Z", balance=1000.),
        dict(run_id="v1-fair", period_scope="full", time="2025-01-02T00:00:00Z", balance=1500.),
        dict(run_id="v7-fair", period_scope="full", time="2025-01-01T00:00:00Z", balance=1000.),
        dict(run_id="v7-fair", period_scope="full", time="2025-01-02T00:00:00Z", balance=2000.),
        dict(run_id="v7-fair", period_scope="full", time="2025-01-03T00:00:00Z", balance=120000.),
        dict(run_id="v1-native", period_scope="full", time="2025-01-01T00:00:00Z", balance=1000.),
        dict(run_id="v1-native", period_scope="full", time="2025-01-02T00:00:00Z", balance=2000.),
    ])
    accepted_ledger = pd.DataFrame([
        dict(run_id="v7-fair", period_scope="full", base_asset=asset, side=side, timeframe_min=30, entry_time="2025-01-01T00:00:00Z", exit_time="2025-01-03T00:00:00Z", exit_r_multiple=r, realized_pnl=pnl, entry_balance=1000., balance_after_exit=after)
        for asset, side, r, pnl, after in (("ACT", -1, 5.0, 20000., 21000.), ("PUMP", 1, 4.9, 19000., 40000.), ("SPK", 1, -1.0, -4000., 120000.))
    ])
    output_hashes = {
        "summary.csv": _write_csv(result / "summary.csv", summary),
        "equity_curve.csv.gz": _write_csv(result / "equity_curve.csv.gz", curve),
        "accepted_ledger.csv.gz": _write_csv(result / "accepted_ledger.csv.gz", accepted_ledger),
        "rejections.csv.gz": _write_csv(result / "rejections.csv.gz", pd.DataFrame({"x": []})),
        "daily_realized_pnl.csv.gz": _write_csv(result / "daily_realized_pnl.csv.gz", pd.DataFrame({"x": []})),
    }
    replay_manifest = {"outputs": output_hashes, "source_rows": 100, "loaded_relevant_rows": 9, "runs": len(summary), "output_row_counts": {"accepted_ledger": len(accepted_ledger)}, "source_time_range": {"entry_time_min": "2024-09-10T00:00:00+00:00", "entry_time_max": "2026-09-09T00:00:00+00:00"}}
    (result / "run_manifest.json").write_text(json.dumps(replay_manifest))

    selection = pd.DataFrame([
        dict(source_arm="v1_common_execution_long", venue_scope="combined", timeframe="30", sizing="fixed", risk_fraction=.03, development_final_balance=900., validation_final_balance=800., full_final_balance=1500., full_run_id="v1-fair", validation_run_id="v1-fair-val", validation_reached_100k=False, full_reached_100k=False),
        dict(source_arm="v7_bb_long", venue_scope="combined", timeframe="30", sizing="compound", risk_fraction=.10, development_final_balance=900., validation_final_balance=800., full_final_balance=120000., full_run_id="v7-fair", validation_run_id="v7-fair-val", validation_reached_100k=False, full_reached_100k=True),
    ])
    risk = pd.DataFrame([
        dict(period_scope="full", sizing=sizing, risk_fraction=risk, paths=2, median_final_balance=balance, best_final_balance=balance * 1.1, worst_final_balance=balance * .8, reached_100k_paths=int(balance >= 100000), median_closed_mdd=.1)
        for sizing in ("fixed", "compound") for risk, balance in ((.03, 1100.), (.05, 1250.), (.10, 1400.))
    ])
    seeds = pd.DataFrame([
        dict(source_arm="v7_bb_long", venue_scope="combined", timeframe="30", sizing="compound", risk_fraction=.10, period_scope="validation", seed=seed, final_balance=800. + seed, max_balance=1000., max_closed_drawdown_fraction=.2, reached_100k=False) for seed in range(3)
    ] + [dict(source_arm="v7_bb_long", venue_scope="combined", timeframe="30", sizing="compound", risk_fraction=.10, period_scope="full", seed=seed, final_balance=120000. if seed == 0 else 7000., max_balance=120000. if seed == 0 else 9000., max_closed_drawdown_fraction=.9, reached_100k=seed == 0) for seed in range(3)])
    regime = pd.DataFrame([dict(run_id="v7-fair-val", period_scope="validation", market_regime="bull", breadth_regime="high", closed=4, wins=3, realized_pnl=-200.)])
    daily = pd.DataFrame([dict(run_id="v7-fair", date_bjt="2025-01-03", realized_pnl=35000., start_balance=85000., end_balance=120000., exit_events=3)])
    milestones = pd.DataFrame([
        dict(run_id="v7-fair", threshold=2000., first_reached_time="2025-01-02T00:00:00Z", balance=2000.),
        dict(run_id="v7-fair", threshold=100000., first_reached_time="2025-01-03T00:00:00Z", balance=120000.),
    ])
    matched = pd.DataFrame([dict(variant="v7_bb_long", timeframe_min=30, matched_months=12, exploratory_month_block_sign_flip_p=.02, paired_mean_net_r_difference=.3)])
    accepted = pd.DataFrame([dict(run_id="v7-fair", exit_time="2025-01-03T00:00:00Z", base_asset="BTC")])
    entry_hour = pd.DataFrame([dict(run_id="v7-fair-val", period_scope="validation", entry_hour_bjt=8, closed=6, wins=3, realized_pnl=-100., mean_account_r=-.1, win_rate=.5)])
    entry_weekday = pd.DataFrame([dict(run_id="v7-fair-val", period_scope="validation", entry_weekday_bjt="Monday", closed=6, wins=3, realized_pnl=-100., mean_account_r=-.1, win_rate=.5)])
    entry_month = pd.DataFrame([dict(run_id="v7-fair-val", period_scope="validation", entry_month_bjt="2025-01", closed=6, wins=3, realized_pnl=-100., mean_account_r=-.1, win_rate=.5)])
    tables = {
        "development_selection.csv": selection, "risk_summary.csv": risk, "seed_sensitivity.csv": seeds,
        "regime_metrics.csv": regime, "daily_realized_bjt.csv.gz": daily, "best_days.csv": daily,
        "milestone_chain.csv": milestones, "matched_control_reference.csv": matched,
        "annotated_accepted.csv.gz": accepted, "entry_hour_metrics.csv": entry_hour,
        "entry_weekday_metrics.csv": entry_weekday, "entry_month_metrics.csv": entry_month,
    }
    post_hashes = {name: _write_csv(post / name, frame) for name, frame in tables.items()}
    (post / "post_manifest.json").write_text(json.dumps({"outputs": post_hashes, "account_replay_manifest_sha256": _sha(result / "run_manifest.json")}))
    return result, post


def test_builds_a_manifest_checked_chinese_report_and_five_relative_pngs(tmp_path):
    result, post = _bundle(tmp_path)
    report, figures = tmp_path / "out" / "report.md", tmp_path / "out" / "figures"
    outcome = build_account_growth_report(result, post, report, figures)
    text = report.read_text()
    assert outcome["raw_best_run_id"] == "v7-fair"
    assert outcome["transient_run_id"] == "v7-fair"
    assert "development-selected" in text
    assert "瞬时 100k" in text
    assert "第 1 次" in text
    assert "非公平：原 V1 原生退出" in text
    assert "sign-flip p" in text and "北京日链路" in text
    assert "ACT" in text and "PUMP" in text and "SPK" in text
    assert "post-hoc leads" in text
    assert "156" in text and "3,300R" in text
    assert "0/2 高于起始余额" in text
    assert "市场状态" in text and "validation 从 1,000U 降到 800.00" in text
    assert "日初 85,000.00" in text and "3 笔合计 35,000.00" in text
    assert "AUC" in text and "不适用" in text
    assert "不是 32 次独立市场试验" in text
    assert "小时" in text and "Monday" in text and "2025-01" in text
    assert "fixed 5%初始余额" in text
    assert "reproduce_full_v1" in text and "禁止把重现输出写回" in text
    assert "figures/focus_historical_path.png" in text
    assert len(list(figures.glob("*.png"))) == 5
    assert all(path.stat().st_size > 1000 for path in figures.glob("*.png"))


def test_reports_actual_validation_counts_and_handles_no_100k_seed(tmp_path):
    result, post = _bundle(tmp_path)
    summary = pd.read_csv(result / "summary.csv")
    summary.loc[summary["run_id"].eq("v1-fair-val"), "final_balance"] = 1100.
    summary.loc[summary["run_id"].eq("v7-fair"), ["final_balance", "reached_100k"]] = [9000., False]
    curve = pd.read_csv(result / "equity_curve.csv.gz")
    curve.loc[curve["run_id"].eq("v7-fair") & curve["balance"].gt(9000.), "balance"] = 9000.
    manifest = json.loads((result / "run_manifest.json").read_text())
    manifest["outputs"]["summary.csv"] = _write_csv(result / "summary.csv", summary)
    manifest["outputs"]["equity_curve.csv.gz"] = _write_csv(result / "equity_curve.csv.gz", curve)
    (result / "run_manifest.json").write_text(json.dumps(manifest))
    selection = pd.read_csv(post / "development_selection.csv")
    selection.loc[selection["source_arm"].eq("v1_common_execution_long"), "validation_final_balance"] = 1100.
    selection.loc[selection["source_arm"].eq("v7_bb_long"), ["full_final_balance", "full_reached_100k"]] = [9000., False]
    seed = pd.read_csv(post / "seed_sensitivity.csv")
    seed["reached_100k"] = False
    seed["max_balance"] = seed["max_balance"].clip(upper=9000.)
    milestones = pd.read_csv(post / "milestone_chain.csv")
    milestones = milestones.loc[milestones["threshold"].lt(100000.)]
    post_manifest = json.loads((post / "post_manifest.json").read_text())
    post_manifest["outputs"]["development_selection.csv"] = _write_csv(post / "development_selection.csv", selection)
    post_manifest["outputs"]["seed_sensitivity.csv"] = _write_csv(post / "seed_sensitivity.csv", seed)
    post_manifest["outputs"]["milestone_chain.csv"] = _write_csv(post / "milestone_chain.csv", milestones)
    post_manifest["account_replay_manifest_sha256"] = _sha(result / "run_manifest.json")
    (post / "post_manifest.json").write_text(json.dumps(post_manifest))
    report, figures = tmp_path / "report.md", tmp_path / "figures"
    build_account_growth_report(result, post, report, figures)
    text = report.read_text()
    assert "1/2 development-selected 配置的 validation 终值低于" in text
    assert "1/2 高于起始余额" in text
    assert "历史最高峰值" in text
    assert "该配置的 3 个 full seed 收据" in text


def test_refuses_a_post_bundle_that_is_not_linked_to_the_replay_manifest(tmp_path):
    result, post = _bundle(tmp_path)
    manifest = json.loads((post / "post_manifest.json").read_text())
    manifest["account_replay_manifest_sha256"] = "0" * 64
    (post / "post_manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="does not reference"):
        build_account_growth_report(result, post, tmp_path / "report.md", tmp_path / "figures")


def test_refuses_a_manifest_pinned_csv_with_changed_bytes(tmp_path):
    result, post = _bundle(tmp_path)
    (result / "summary.csv").write_text("tampered\n")
    with pytest.raises(ValueError, match="hash mismatch"):
        build_account_growth_report(result, post, tmp_path / "report.md", tmp_path / "figures")


def test_cli_accepts_all_four_required_output_locations(tmp_path):
    result, post = _bundle(tmp_path)
    report, figures = tmp_path / "report.md", tmp_path / "figures"
    assert main(["--result", str(result), "--post", str(post), "--report", str(report), "--figures", str(figures)]) == 0
    assert report.is_file()
