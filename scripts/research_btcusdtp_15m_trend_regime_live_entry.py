#!/usr/bin/env python3
"""Audit a causal current-trend liveness gate for BTCUSDT.P 15m.

The only new feature check is evaluated on the completed K2 bar ``t``:
directional EMA30(HL2)-SMA60(HL2) spread must be positive and the directional
four-bar EMA30 slope must be non-negative.  K1/K2 morphology, regime state,
execution, and labels come from the frozen parent experiment.  Future bars are
read only by the unchanged trade resolver.  No row at or after the repository
holdout boundary is loaded.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pandas as pd

import scripts.research_btcusdtp_15m_trend_regime_episode as parent
from scripts.backtest_two_key_candle_pine_v8_btc_1h import signflip_p
from scripts.research_btcusdtp_15m_dual_ma_runner import (
    _assignment_metrics,
    matched_controls,
)
from scripts.research_btcusdtp_15m_ma_state_trend import (
    json_value,
    utc,
    write_csv,
    write_json,
)
from scripts.research_two_key_candle_ma_retest_1h import sha256_file

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-btcusdtp-15m-trend-regime-live-entry-preholdout-20260904-v1"
EXPERIMENT = ROOT / "experiments" / "active" / EXPERIMENT_ID
CONFIG_PATH = EXPERIMENT / "config.json"
PREREG_PATH = EXPERIMENT / "preregistration.json"
RESULTS = EXPERIMENT / "results"
SCRIPT_PATH = Path(__file__).resolve()


def _assert_head_frozen(path: Path) -> None:
    relative = path.relative_to(ROOT).as_posix()
    subprocess.run(
        ["git", "ls-files", "--error-unmatch", relative],
        cwd=ROOT,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    head_bytes = subprocess.check_output(["git", "show", f"HEAD:{relative}"], cwd=ROOT)
    disk_bytes = path.read_bytes()
    if hashlib.sha256(head_bytes).digest() != hashlib.sha256(disk_bytes).digest():
        raise RuntimeError(f"{relative} is not frozen in HEAD")


def _live_pairs(pairs: pd.DataFrame) -> pd.DataFrame:
    """Keep only K2 bars whose fast/slow direction and fast slope are live."""

    return pairs[
        pairs["signed_fast_slow_spread_atr"].gt(0.0)
        & pairs["signed_fast_slope4_atr_per_bar"].ge(0.0)
    ].copy()


def _folds(events: pd.DataFrame, names: list[str], baseline: pd.DataFrame) -> pd.DataFrame:
    return parent.fold_density_table(events, names, baseline=baseline)


def _matched_summary(
    events: pd.DataFrame,
    frame: pd.DataFrame,
    config: dict[str, Any],
) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]:
    pconfig = parent.load_config()
    start = utc(config["splits"]["audit_start_inclusive"])
    end = utc(config["splits"]["audit_end_exclusive"])
    controls, pair_rows = matched_controls(
        events,
        frame,
        parent._control_config(pconfig),
        policy="ma_trail1_after_2atr",
        start=start,
        end=end,
    )
    matched = pair_rows[pair_rows["match_status"].eq("matched_exact")].copy()
    p_value = signflip_p(matched["paired_excess_return"], resamples=100_000, seed=90416)
    result = {
        "matched_events": len(matched),
        "mean_candidate_net_bp": float(matched["candidate_net_return"].mean() * 1e4),
        "mean_control_net_bp": float(matched["control_mean_net_return"].mean() * 1e4),
        "mean_paired_excess_bp": float(matched["paired_excess_return"].mean() * 1e4),
        "paired_signflip_p": float(p_value),
        "assignment_metrics": _assignment_metrics(controls),
    }
    return result, controls, pair_rows


def run() -> dict[str, Any]:
    for path in (CONFIG_PATH, PREREG_PATH, SCRIPT_PATH):
        _assert_head_frozen(path)
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    pconfig = parent.load_config()
    frame, quality = parent.load_frame(pconfig)
    if int(quality["holdout_rows_read"]) != 0:
        raise RuntimeError("live-entry audit materialized repository holdout")
    raw_pairs = parent.build_v3_pairs(frame, pconfig)
    v3 = parent.simulate_v3(raw_pairs, frame, pconfig)
    params = config["fixed_parent_parameters"]
    v4 = parent.simulate_regime(raw_pairs, frame, pconfig, params)
    v5 = parent.simulate_regime(_live_pairs(raw_pairs), frame, pconfig, params)

    split = config["splits"]
    dev_start = utc(split["development_start_inclusive"])
    dev_end = utc(split["development_end_exclusive"])
    audit_start = utc(split["audit_start_inclusive"])
    audit_end = utc(split["audit_end_exclusive"])
    windows: dict[str, tuple[pd.Timestamp, pd.Timestamp]] = {
        "development": (dev_start, dev_end),
        "audit": (audit_start, audit_end),
    }
    sliced: dict[str, dict[str, pd.DataFrame]] = {}
    for label, (start, end) in windows.items():
        sliced[label] = {
            "v3": parent._window_events(v3, start, end),
            "v4": parent._window_events(v4, start, end),
            "v5": parent._window_events(v5, start, end),
        }

    matched, controls, matched_pairs = _matched_summary(sliced["audit"]["v5"], frame, config)
    RESULTS.mkdir(parents=True, exist_ok=True)
    for window, policies in sliced.items():
        for policy, rows in policies.items():
            write_csv(rows, RESULTS / f"{window}_{policy}_trades.csv.gz")
    write_csv(
        _folds(
            sliced["development"]["v5"],
            list(split["development_folds"]),
            sliced["development"]["v3"],
        ).assign(policy="V5 live regime"),
        RESULTS / "development_v5_fold_metrics.csv",
    )
    write_csv(
        _folds(
            sliced["audit"]["v5"],
            list(split["audit_slices"]),
            sliced["audit"]["v3"],
        ).assign(policy="V5 live regime"),
        RESULTS / "audit_v5_fold_metrics.csv",
    )
    write_csv(controls, RESULTS / "audit_matched_controls.csv.gz")
    write_csv(matched_pairs, RESULTS / "audit_matched_control_pairs.csv")

    metrics_by_window: dict[str, dict[str, Any]] = {}
    for label, (start, end) in windows.items():
        policies = sliced[label]
        metrics_by_window[label] = {
            name: parent.density_metrics(rows, start=start, end=end, baseline=policies["v3"])
            for name, rows in policies.items()
        }
    audit_v5 = metrics_by_window["audit"]["v5"]
    summary = {
        "experiment_id": EXPERIMENT_ID,
        "status": "research_display_only",
        "single_change": config["single_change"],
        "tuned_parameters": [],
        "source": quality,
        "repository_holdout_rows_read": 0,
        "development": metrics_by_window["development"],
        "audit": metrics_by_window["audit"],
        "matched_control": matched,
        "gates": {
            "audit_density_below_10_per_30d": bool(audit_v5["signals_per_30d"] < 10.0),
            "audit_burst_below_10pct": bool(audit_v5["within_24h_previous_share"] < 0.10),
            "audit_net_mean_positive": bool(audit_v5["mean_net_bp"] > 0.0),
            "matched_p_lt_0_01": bool(matched["paired_signflip_p"] < 0.01),
        },
        "hashes": {
            "config_sha256": sha256_file(CONFIG_PATH),
            "preregistration_sha256": sha256_file(PREREG_PATH),
            "script_sha256": sha256_file(SCRIPT_PATH),
        },
        "production_or_live_changed": False,
    }
    write_json(RESULTS / "summary.json", summary)
    print(json.dumps(json_value(summary), ensure_ascii=False, indent=2))
    return summary


if __name__ == "__main__":
    run()
