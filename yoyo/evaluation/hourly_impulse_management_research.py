"""V8 fixed-request native5m versus native15m management specification.

Entry context reads completed native OHLC/SMA40 and raw bars no later than the
real entry open. All subsequent exits, MFE, returns and contrasts are outcomes.
V7 requests, control identities and zones are byte pinned; no rematching or
entry filtering. Aggregation, colour clock and MA memory change together, so
this is NOT a pure check-frequency experiment. Development2023--2024 only.

Month draws preserve case plus three controls and both arms as one request:
https://numpy.org/doc/2.0/reference/random/generated/numpy.random.Generator.choice.html
Timestamp normalization follows pandas2.3; exact UTC comparisons precede float
tolerance. https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.to_datetime.html
"""
from __future__ import annotations

import json
import subprocess

import numpy as np
import pandas as pd

from yoyo.data.hourly_impulse_management_context import attach_management_context
from yoyo.evaluation.hourly_impulse_context_research import committed_sources, development_gates, month_support
from yoyo.evaluation.hourly_impulse_diagnostics import diagnose_frame
from yoyo.evaluation.hourly_impulse_k2_research import describe, direct_requests, episode_ledger, matched_episodes, single_pending_ledger
from yoyo.evaluation.hourly_impulse_research import ROOT, Study, digest, metrics, utc, write_csv, write_json
from yoyo.evaluation.hourly_impulse_source_research import SOURCE_PATHS as PARENT_SOURCES, SELECTION, support_info, zone_outcome_ledger
from yoyo.evaluation.hourly_impulse_transition_research import read_frame, state_diagnostics
from yoyo.layers.l3_backtest.hourly_impulse import simulate_events


EXPERIMENT = ROOT / "experiments/active/exp-btcusdtp-1h-management-spec-preholdout-20260906-v8"
POLICIES = [{"id": f"{minutes}m_native40", "management_minutes": minutes, "ma_kind": "SMA",
             "ma_length": 40, "exit_mode": "transition_colour", "confirmations": 1} for minutes in (5, 15)]
SOURCES = list(dict.fromkeys(PARENT_SOURCES + [
    "yoyo/data/hourly_impulse_management_context.py",
    "yoyo/evaluation/hourly_impulse_management_research.py",
    "tests/test_hourly_impulse_management_research.py",
    "tests/test_hourly_impulse_management_context.py",
    "tests/test_hourly_impulse_transition_15m.py",
    "tests/test_hourly_impulse_transition.py",
]))


def _indexed(frame):
    if frame.columns.duplicated().any() or frame.event_id.isna().any() or frame.event_id.duplicated().any():
        raise ValueError("Unique finite identities and columns required")
    return frame.set_index("event_id").sort_index()


def assert_saved_parity(before: pd.DataFrame, after: pd.DataFrame) -> None:
    """Every saved column, not just economics; only CSV nulls normalize.

    New diagnostic columns are allowed. Time changes of even one nanosecond
    fail; float serialization tolerance is 1e-12. Caller data stays unchanged.
    """
    left, right = _indexed(before), _indexed(after)
    if not left.index.equals(right.index) or not set(left).issubset(right):
        raise ValueError("Saved identities or columns changed")
    for column in left:
        a, b = left[column].copy(), right[column].copy()
        if column.endswith(("_time", "_at", "_deadline", "_until", "_bar_open")):
            a, b = (pd.to_datetime(x, utc=True, format="mixed") for x in (a, b))
            pd.testing.assert_series_equal(a, b, check_dtype=False, check_exact=True)
        else:
            # Preserve nonempty strings, including 'nan', whitespace and 'None'.
            if a.dtype == object or b.dtype == object:
                a = a.map(lambda x: np.nan if isinstance(x, str) and x == "" else x)
                b = b.map(lambda x: np.nan if isinstance(x, str) and x == "" else x)
            pd.testing.assert_series_equal(a, b, check_dtype=False, check_exact=False, rtol=1e-12, atol=1e-12)


def _same_identity(left, right):
    a, b = _indexed(left), _indexed(right)
    if not a.index.equals(b.index):
        raise ValueError("Fixed intention IDs changed")
    columns = ["event_id", "mother_decision_time"]
    if "fold" in left and "fold" in right:
        columns.append("fold")
    assert_saved_parity(left[columns], right[columns])
    return a, b


def _difference(before, after, column):
    a, b = _same_identity(before, after)
    result = a[["mother_decision_time"]].copy()
    result["before"] = a[column]
    result["after"] = b[column]
    result["difference"] = result["after"]-result["before"]
    return result.reset_index()


def paired_effects(case5, case15, matched5, matched15, serial5, serial15):
    """D: all requests; I: fixed complete triplets; serial: all source zones.

    An unmatched request stays in D but has unknown I, never a zero control.
    A serial skipped intention is known zero; a selected unknown stays unknown.
    Outcomes cannot change identities, timestamps or control assignments.
    """
    _same_identity(case5, case15)
    m5, m15 = _same_identity(matched5, matched15)
    if not m5.assigned_controls.equals(m15.assigned_controls):
        raise ValueError("Frozen control assignments changed")
    for cases, matching in ((case5, matched5), (case15, matched15)):
        c, m = _same_identity(cases, matching)
        if not m.assigned_controls.isin([0, 3]).all():
            raise ValueError("Controls must be all three or unassigned")
        np.testing.assert_allclose(c.episode_net_return, m.event_net_return, rtol=1e-12, atol=1e-12, equal_nan=True)
        np.testing.assert_allclose(m.excess, m.event_net_return-m.control_mean_return, rtol=1e-12, atol=1e-12, equal_nan=True)
        if m.loc[m.assigned_controls.eq(0), ["control_mean_return", "excess"]].notna().any().any():
            raise ValueError("Unassigned controls cannot acquire a finite outcome")
    serial_values = []
    for serial in (serial5, serial15):
        if not serial.portfolio_selected.isin([True, False]).all():
            raise ValueError("Every intention needs explicit serial selection")
        serial_values.append(serial.assign(episode_net_return=serial.episode_net_return.where(serial.portfolio_selected, 0.)))
    frames = {"case_delta": _difference(case5, case15, "episode_net_return"),
              "excess_delta": _difference(matched5, matched15, "excess"),
              "serial_delta": _difference(*serial_values, "episode_net_return")}
    np.testing.assert_allclose(frames["excess_delta"].difference,
        frames["case_delta"].difference.to_numpy()-(m15.control_mean_return-m5.control_mean_return).to_numpy(),
        rtol=1e-12, atol=1e-12, equal_nan=True)
    effects = {}
    for name, frame in frames.items():
        delta = frame.difference
        effects[name] = {**describe(delta, frame.mother_decision_time), "total_pairs": len(frame),
            "unknown_pairs": int((~np.isfinite(delta)).sum()), "improved": int(delta.gt(1e-12).sum()),
            "worsened": int(delta.lt(-1e-12).sum()), "unchanged": int(delta.abs().le(1e-12).sum())}
    return frames, effects


def simulate_native(study, entries, policy):
    """Native management features, raw5m risk clock, unchanged fold boundary."""
    management = study.featured(policy["management_minutes"], policy["ma_kind"], policy["ma_length"])
    pieces = []
    for fold, _, end in study.folds:
        part = entries.loc[entries.fold.eq(fold)]
        if len(part):
            pieces.append(simulate_events(study.raw, management, part,
                {**study.config["execution"], **policy}, end_exclusive=utc(end)))
    return pd.concat(pieces, ignore_index=True)


def positive_inference(effect):
    p, lower = effect["month_cluster_p"], effect["ci95_bp"][0]
    return bool(np.isfinite(p) and p < .01 and np.isfinite(lower) and lower > 0 and effect["mean_bp"] > 0)


def evaluate_arm(study, policy, entries, controls, zones, results, config, parent=None):
    """Replay, source-intention accounting and diagnostics, preserving all rows."""
    results.mkdir()
    folds = [f[0] for f in study.folds]
    trades, episodes = {}, {}
    parity = {}
    for label, requests in (("case", entries), ("control", controls)):
        trade = simulate_native(study, requests, policy)
        if parent is not None:
            saved = read_frame(parent/f"{label}_trades.csv.gz")
            assert_saved_parity(saved, trade)
            parity[label+"_all_saved_trade_columns"] = {"rows": len(saved), "columns": len(saved.columns)}
        valid = trade.risk_pct.gt(0) & trade.risk_atr.gt(0)
        if not trade.loc[valid, "mg_entry_state"].eq(trade.loc[valid, "transition_initial_state"]).all():
            raise ValueError("Independent entry-context and L3 state disagree")
        episode = episode_ledger(requests, direct_requests(requests)[1], trade)
        if parent is not None:
            assert_saved_parity(read_frame(parent/f"{label}_request_outcomes.csv.gz"), episode)
        trades[label], episodes[label] = trade, episode
        write_csv(results/f"{label}_trades.csv.gz", trade)
        write_csv(results/f"{label}_request_outcomes.csv.gz", episode)
        # Only this diagnostic copy aliases the new state; old ltf fields remain
        # frozen in every request, trade and episode ledger.
        states, _ = state_diagnostics(trade.assign(ltf_entry_state=trade.mg_entry_state))
        write_csv(results/f"{label}_management_states.csv", states)
    pairs, matching = matched_episodes(episodes["case"], episodes["control"])
    serial = single_pending_ledger(zone_outcome_ledger(zones, trades["case"]))
    if parent is not None:
        assert_saved_parity(read_frame(parent/"matched_request_outcomes.csv"), pairs)
        assert_saved_parity(read_frame(parent/"single_pending_zone_ledger.csv.gz"), serial)
    write_csv(results/"matched_request_outcomes.csv", pairs)
    write_csv(results/"single_pending_zone_ledger.csv.gz", serial)
    accepted = set(serial.loc[serial.portfolio_selected, "entry_event_id"].dropna())
    single = trades["case"].loc[trades["case"].event_id.isin(accepted)]
    write_csv(results/"single_position_trades.csv.gz", single)
    info, random_info, single_info = (metrics(t, folds) for t in (trades["case"], trades["control"], single))
    months = month_support(trades["case"], folds)
    gates = development_gates(info, matching, single_info, months, config)
    net = describe(episodes["case"].episode_net_return, episodes["case"].mother_decision_time)
    gates.update(complete_evidence=bool(episodes["case"].observed.all() and episodes["control"].observed.all() and serial.observed.all()),
                 net_inference=positive_inference(net), excess_inference=positive_inference(matching["effect"]))
    classified, diagnosis, diagnostic_tables = diagnose_frame(trades["case"])
    write_csv(results/"classified_case_trades.csv.gz", classified)
    for name, table in diagnostic_tables.items():
        write_csv(results/f"diagnosis_{name}.csv", table)
    info = {"policy": policy, "metrics": info, "control_metrics": random_info,
        "matching": matching, "single_position": single_info, "net_effect": net, "months": months,
        "gates": gates, "diagnosis": diagnosis, "old_replay_parity": parity,
        "serial_selected_zones": int(serial.portfolio_selected.sum()), "source_zones": len(serial)}
    write_json(results/"summary.json", info)
    return info, trades, episodes, pairs, serial


def verify_config(config, base):
    if config["policies"] != POLICIES or config["selection"] != SELECTION:
        raise ValueError("Frozen policies/gates changed")
    if base["execution"]["max_hours"] != 72 or base["execution"]["cost_fraction"] != .002:
        raise ValueError("Frozen execution economics changed")
    if config["inference"] != {"draws": 9999, "seed": 20260906, "p_limit": .01, "joint_required": ["case_delta", "excess_delta"]}:
        raise ValueError("Frozen joint improvement hypotheses changed")
    if not config["no_audit_entry_point"] or any(config[k] is not False for k in ("holdout_consumed", "training_eligible", "production_eligible")):
        raise ValueError("Development research only")


def run():
    config_path = EXPERIMENT/"config.json"
    config = json.loads(config_path.read_text())
    base_path = ROOT/config["base_config"]
    if digest(base_path) != config["base_config_sha256"]:
        raise RuntimeError("Parent config changed")
    base = json.loads(base_path.read_text())
    verify_config(config, base)
    sources = committed_sources([ROOT/p for p in SOURCES] + [config_path, base_path, EXPERIMENT/"PROJECT_PLAN.md"])
    parent = ROOT/config["parent_results"]
    for name, expected in config["inputs"].items():
        if digest(parent/name) != expected:
            raise RuntimeError("Frozen V7 input changed: "+name)
    requests = {label: read_frame(parent/f"{label}_requests.csv.gz") for label in ("case", "control")}
    zones, assignments = read_frame(parent/"source_zones.csv.gz"), read_frame(parent/"assignments.csv.gz")
    if (len(requests["case"]), len(requests["control"]), len(zones)) != (286, 849, 959):
        raise ValueError("Frozen V7 support changed")
    results = EXPERIMENT/"results"
    if results.exists():
        raise RuntimeError("Preserve previous attempts; output already exists")
    results.mkdir()
    write_json(results/"started.json", {"at": pd.Timestamp.now(tz="UTC"), "sources": sources,
        "inputs": config["inputs"], "builder_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, cwd=ROOT).strip()})
    study = Study(base, "development")
    support = support_info(requests["case"], zones, assignments, [f[0] for f in study.folds])
    if not support["passed"]:
        raise ValueError("Frozen support no longer passes")
    prepared = {}
    for policy in POLICIES:
        arm, minutes = policy["id"], policy["management_minutes"]
        prepared[arm] = {}
        for label, entries in requests.items():
            contextual = attach_management_context(study.raw, study.featured(minutes, "SMA", 40), entries, minutes)
            assert_saved_parity(entries, contextual)
            prepared[arm][label] = contextual
            write_csv(results/f"{arm}_{label}_context.csv.gz", contextual)
    # All new native entry states are frozen before either new outcome replay.
    write_json(results/"contexts_frozen.json", {"at": pd.Timestamp.now(tz="UTC"), "sha256": {
        p.name: digest(p) for p in results.glob("*_context.csv.gz")}})
    arms = []
    for policy in POLICIES:
        arm = policy["id"]
        arms.append(evaluate_arm(study, policy, prepared[arm]["case"], prepared[arm]["control"],
            zones, results/arm, config, parent=parent if arm == "5m_native40" else None))
    invariant_columns = ["event_id", "entry_time", "entry_price", "initial_stop", "signal_atr", "risk_pct", "risk_atr"]
    for label in requests:
        assert_saved_parity(arms[0][1][label][invariant_columns], arms[1][1][label][invariant_columns])
        old, new = (_indexed(prepared[policy["id"]][label]) for policy in POLICIES)
        cross = pd.crosstab(old.mg_entry_state, new.mg_entry_state).reindex(index=["aligned", "opposite", "unknown"], columns=["aligned", "opposite", "unknown"], fill_value=0)
        write_csv(results/f"{label}_state_crosstab.csv", cross.rename_axis("state_5m").reset_index())
    a, b = arms
    frames, effects = paired_effects(a[2]["case"], b[2]["case"], a[3], b[3], a[4], b[4])
    for name, frame in frames.items():
        write_csv(results/(name+".csv"), frame)
    gates = {**b[0]["gates"], **{key+"_improves": positive_inference(effects[key]) for key in config["inference"]["joint_required"]}}
    gates["complete_paired_support"] = effects["case_delta"]["n"] == 286 and effects["excess_delta"]["n"] == 283 and effects["serial_delta"]["n"] == 959
    final = {"status": "development_pass_requires_prospective_validation" if all(gates.values()) else "rejected_development_no_audit",
        "arms": [a[0], b[0]], "effects": effects, "gates": gates, "support": support,
        "source_receipt": study.source_receipt, "config_sha256": digest(config_path), "source_hashes": sources,
        "holdout_price_rows": 0, "audit_opened": False, "independent_confirmation": False,
        "training_eligible": False, "production_eligible": False}
    write_json(results/"summary.json", final)
    print(json.dumps({"status": final["status"], "summary": str(results/"summary.json")}, ensure_ascii=False))


if __name__ == "__main__":
    run()
