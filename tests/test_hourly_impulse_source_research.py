"""Synthetic V7 orchestration, denominator and source-occupancy contracts."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import hourly_impulse_source_research as research
from yoyo.evaluation.hourly_impulse_k2_research import single_pending_ledger


def config_pair():
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root/"experiments/active/exp-btcusdtp-1h-frozen-source-preholdout-20260906-v7/config.json").read_text())
    return config, json.loads((root/config["base_config"]).read_text())


def prepared(n=80):
    records = []
    for i in range(n):
        fold = i//20
        month = 1 + fold*6 + (i%20)//5
        time = pd.Timestamp(2023+((month-1)//12), ((month-1)%12)+1, (i%5)+1, tz="UTC")
        records.append({"event_id": "event%d" % i, "zone_id": "zone%d" % i,
                        "fold": "F%d" % fold, "signal_time": time-pd.Timedelta(hours=1),
                        "decision_time": time, "direction": 1, "initial_stop": 90.,
                        "signal_atr": 10., "range_atr": 1.2, "ltf_entry_state": "aligned"})
    entries = pd.DataFrame(records)
    zones = entries[["event_id", "zone_id", "fold"]].copy()
    zones["zone_arm_time"] = entries.decision_time-pd.Timedelta(hours=2)
    zones["zone_deadline"] = zones.zone_arm_time+pd.Timedelta(hours=8)
    zones["terminal_time"] = entries.decision_time
    zones["status"] = "request_emitted"
    assignments = entries[["event_id"]].assign(match_status="matched", assigned_controls=3)
    return entries, zones, assignments


def outcome(entries, net=.003):
    frame = entries.copy()
    frame["entry_time"] = frame.decision_time
    frame["exit_time"] = frame.decision_time+pd.Timedelta(hours=1)
    frame["entry_price"] = 100.
    frame["exit_price"] = 100.*(1+net+.002)
    frame["closed"] = True
    frame["outcome"] = "transition_colour_exit"
    frame["gross_return"] = net+.002
    frame["net_return"] = net
    frame["hold_minutes"] = 60.
    frame["max_favourable_r"] = .2
    frame["max_adverse_r"] = -.1
    frame["risk_pct"] = .1
    return frame


def test_support_uses_all_requests_not_zones_or_later_winners():
    entries, zones, assignment = prepared()
    extra = zones.iloc[[0]].assign(zone_id="no_release", event_id=None, status="expired_no_release")
    zones = pd.concat([zones, extra], ignore_index=True)
    result = research.support_info(entries, zones, assignment, ["F0", "F1", "F2", "F3"])
    assert result["passed"] and result["requests"] == 80 and result["zones"] == 81
    assert result["assignment_coverage"] == 1
    assert result["active_request_months"] == 16
    assert not result["pnl_computed"]
    noisy = entries.assign(net_return=999., closed=True)
    assert research.support_info(noisy, zones, assignment, ["F0", "F1", "F2", "F3"]) == result


@pytest.mark.parametrize("what", ["count", "fold", "matching", "months", "unknown"])
def test_each_preoutcome_gate_fails_closed(what):
    entries, zones, assignment = prepared()
    if what == "count":
        entries, zones, assignment = entries.iloc[:-1], zones.iloc[:-1], assignment.iloc[:-1]
    elif what == "fold":
        entries.loc[entries.fold.eq("F3"), "fold"] = "F2"
        zones.loc[zones.fold.eq("F3"), "fold"] = "F2"
    elif what == "matching":
        assignment.loc[:8, ["match_status", "assigned_controls"]] = ["insufficient_exact_controls", 0]
    elif what == "months":
        entries["decision_time"] = pd.Timestamp("2024-01-01", tz="UTC")
    else:
        zones.loc[0, ["event_id", "status"]] = [None, "censored_source_gap"]
        entries, assignment = entries.iloc[1:], assignment.iloc[1:]
    assert not research.support_info(entries, zones, assignment, ["F0", "F1", "F2", "F3"])["passed"]


@pytest.mark.parametrize("what", ["duplicate_request", "lost_assignment", "bad_zone_link", "partial_control", "unknown_status"])
def test_denominator_corruption_rejected(what):
    entries, zones, assignment = prepared()
    if what == "duplicate_request":
        entries.loc[0, "event_id"] = entries.loc[1, "event_id"]
    elif what == "lost_assignment":
        assignment = assignment.iloc[1:]
    elif what == "bad_zone_link":
        zones.loc[0, "event_id"] = "foreign"
    elif what == "partial_control":
        assignment.loc[0, "assigned_controls"] = 2
    else:
        zones.loc[0, "status"] = "future_selected"
    with pytest.raises(ValueError):
        research.support_info(entries, zones, assignment, ["F0", "F1", "F2", "F3"])


def test_rejected_support_never_touches_study_or_outcome_writer(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("No outcomes before support passes")
    monkeypatch.setattr(research, "simulate", forbidden)
    monkeypatch.setattr(research, "write_json", forbidden)
    monkeypatch.setattr(research, "read_frame", forbidden)
    result = research.evaluate_prepared(None, None, None, None, None, None, tmp_path, {"passed": False})
    assert result["status"] == "rejected_support_no_outcomes"
    assert not result["outcomes_computed"] and not list(tmp_path.iterdir())


def test_zone_zeros_unknowns_and_actual_occupancy_are_distinct():
    entries, zones, _ = prepared(5)
    zones.loc[1, ["event_id", "status"]] = [None, "expired_no_release"]
    zones.loc[2, ["event_id", "status"]] = [None, "censored_source_gap"]
    trades = outcome(entries.loc[[0, 3, 4]])
    trades.loc[trades.event_id.eq("event3"), ["closed", "outcome", "net_return"]] = [False, "entry_invalid_risk", np.nan]
    trades.loc[trades.event_id.eq("event4"), ["closed", "outcome", "net_return"]] = [False, "entry_missing_open", np.nan]
    before = zones.copy(deep=True)
    ledger = research.zone_outcome_ledger(zones, trades).set_index("zone_id")
    pd.testing.assert_frame_equal(zones, before)
    assert ledger.loc["zone0", "episode_net_return"] == .003
    assert ledger.loc["zone1", "episode_net_return"] == 0
    assert pd.isna(ledger.loc["zone2", "episode_net_return"])
    assert ledger.loc["zone3", "episode_net_return"] == 0
    assert pd.isna(ledger.loc["zone4", "episode_net_return"])
    assert ledger.loc["zone0", "occupied_until"] == entries.loc[0, "decision_time"]+pd.Timedelta(hours=1)
    for key in ("zone2", "zone4"):
        assert ledger.loc[key, "occupied_until"] == ledger.loc[key, "zone_arm_time"]+pd.Timedelta(hours=80)


@pytest.mark.parametrize("kind", ["missing", "foreign", "duplicate"])
def test_zone_ledger_requires_one_execution_for_each_request(kind):
    entries, zones, _ = prepared(2)
    trades = outcome(entries)
    if kind == "missing":
        trades = trades.iloc[:1]
    elif kind == "foreign":
        trades.loc[0, "event_id"] = "unknown"
    else:
        trades = pd.concat([trades, trades.iloc[:1]], ignore_index=True)
    with pytest.raises(ValueError):
        research.zone_outcome_ledger(zones, trades)


def test_serial_starts_at_arm_not_signal_and_future_exit_does_not_change_sources():
    entries, zones, _ = prepared(3)
    t = entries.loc[0, "decision_time"]
    entries["decision_time"] = [t, t+pd.Timedelta(hours=2), t+pd.Timedelta(hours=3)]
    zones["zone_arm_time"] = [t-pd.Timedelta(hours=2), t+pd.Timedelta(minutes=30), t+pd.Timedelta(hours=1)]
    zones["terminal_time"] = entries.decision_time
    trades = outcome(entries)
    ledger = research.zone_outcome_ledger(zones, trades)
    serial = single_pending_ledger(ledger)
    assert serial.portfolio_selected.tolist() == [True, False, True]
    assert ledger.terminal_time.tolist() == zones.terminal_time.tolist()


@pytest.mark.parametrize("key", ["zone", "policy", "selection", "matching", "production_eligible"])
def test_frozen_config_rejects_unregistered_changes(key):
    config, base = config_pair()
    research.verify_config(config, base)
    revised = deepcopy(config)
    if key == "production_eligible":
        revised[key] = True
    else:
        revised[key]["undocumented_change"] = True
    with pytest.raises(RuntimeError):
        research.verify_config(revised, base)


def test_economic_pipeline_keeps_request_vs_zone_units_and_three_control_links(monkeypatch, tmp_path):
    entries, zones, assignments = prepared()
    controls = pd.concat([entries.assign(event_id=entries.event_id+"::control%d" % n,
                         parent_event_id=entries.event_id,
                         decision_time=entries.decision_time+pd.Timedelta(hours=n+3)) for n in range(3)], ignore_index=True)
    case_trades, control_trades = outcome(entries), outcome(controls, -.003)
    monkeypatch.setattr(research, "simulate", lambda study, requested, policy: control_trades if "parent_event_id" in requested else case_trades)
    monkeypatch.setattr(research, "read_frame", lambda path: case_trades)
    study = SimpleNamespace(folds=[["F%d" % i, "unused", "unused"] for i in range(4)])
    config, _ = config_pair()
    for filename in ("support.json", "case_requests.csv.gz", "control_requests.csv.gz"):
        # Synthetic presentation-only receipts; no external price material.
        (tmp_path/filename).write_text("synthetic")
    result = research.evaluate_prepared(study, entries, zones, controls, assignments, config, tmp_path, {"passed": True})
    assert result["outcomes_computed"] and result["metrics"]["events"] == 80
    assert result["control_metrics"]["events"] == 240
    assert result["matching"]["mother_events"] == 80
    assert result["matching"]["paired_events"] == 80
    assert result["matching"]["mean_excess_bp"] == pytest.approx(60)
    assert (tmp_path/"classified_case_trades.csv.gz").exists()
    assert (tmp_path/"diagnosis_losing_trades.csv").exists()
    assert not result["audit_opened"] and not result["independent_confirmation"]
