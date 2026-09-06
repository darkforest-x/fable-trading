"""V9 fixed native5m/SMA40 state, changing only transition decision cadence.

V7 source requests, assigned controls, stops, fees and 72h limits are immutable.
Both arms use exactly the same entry-known native5m management observations;
the treatment samples that unchanged state only on UTC quarter-hour boundaries.
Hard stops and source-gap checks remain on raw5m. Initial arming is unchanged.
All subsequent exits, MFE, paired returns and mechanism tables are outcomes,
never input features or new selection thresholds. Only reused 2023--2024 is run.

The V8 evaluator owns execution/parity, fixed-control inference and serial
accounting; this module is an orchestration contract, not another backtester.
Exact in-memory context equality includes order, values and dtypes, following:
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.testing.assert_frame_equal.html
"""
from __future__ import annotations

import json
import subprocess

import numpy as np
import pandas as pd

from yoyo.data.hourly_impulse_management_context import CONTEXT_COLUMNS, attach_management_context
from yoyo.evaluation.hourly_impulse_context_research import committed_sources
from yoyo.evaluation.hourly_impulse_management_diagnostics import mechanism_tables
from yoyo.evaluation.hourly_impulse_management_research import (
    SOURCES as V8_SOURCES, assert_saved_parity, evaluate_arm, paired_effects,
    positive_inference,
)
from yoyo.evaluation.hourly_impulse_research import ROOT, Study, digest, utc, write_csv, write_json
from yoyo.evaluation.hourly_impulse_source_research import SELECTION, support_info
from yoyo.evaluation.hourly_impulse_transition_research import read_frame


EXPERIMENT_ID = "exp-btcusdtp-1h-decision-cadence-preholdout-20260906-v9"
EXPERIMENT = ROOT / "experiments/active" / EXPERIMENT_ID
BASE_CONFIG = "experiments/active/exp-btcusdtp-1h-impulse-ltf-exit-preholdout-20260906-v1/config.json"
BASE_CONFIG_SHA256 = "95e82bd2c57d1c2aa5c8c972a07635d1d9960de4a47aa6197bd6d3cf8473733a"
PARENT_RESULTS = "experiments/active/exp-btcusdtp-1h-frozen-source-preholdout-20260906-v7/results"
FROZEN_INPUTS = {
    "case_requests.csv.gz": "86eca6fdd773b61203abbe2a6d3d1c575021d5d9303bf1b00b0445eb9296c041",
    "control_requests.csv.gz": "71f35b4841199a72d67f05ba17cae265bd38dfa2b4ea06d30700dd6bcd1e1f39",
    "source_zones.csv.gz": "d2a2fcf124b74dfdb0a75f3cf22870a02f75c268229cc7012bd8bcc4c8cfd170",
    "assignments.csv.gz": "c7839e588071179a90b28d44e5f0b1d616c3feb9524c9e9bb78cb4c57821c77f",
    "case_trades.csv.gz": "8f1e867f52baf9ee56af86a95e879d6248f97920bb0d6980265bba5c4114023e",
    "control_trades.csv.gz": "9818809642ac8fbc70bf50557938582b2d19667e9d6a1f324bad44d6b7e8a3fd",
    "case_request_outcomes.csv.gz": "34af7c88f323602b3acb345491876502d97f1a00349b933ef91c8f50bdf6cc65",
    "control_request_outcomes.csv.gz": "8832309d44dc6bf11c4032f5080f48d185801b777e79b7e6dd237e72c376d5f7",
    "matched_request_outcomes.csv": "2363ba35644fe6ee9f92797d935fca7c0141fc91524f5e29ee9a659575caa031",
    "single_pending_zone_ledger.csv.gz": "d0bbbb055ae32ccbb25fad3652ded44c26ad91aa65dcc94d918507a9592f4e5c",
    "summary.json": "8303b6a3fc18f5d6e12e91b95a331717c7306ce90b3be3ddcfca54db309b4984",
    "support.json": "9f950a76c527aa6a212ad1f94b5f4915e07497adfe2ffe61468741fc111c0074",
}
POLICIES = [
    {"id": "5m_native40", "management_minutes": 5, "ma_kind": "SMA",
     "ma_length": 40, "exit_mode": "transition_colour", "confirmations": 1},
    {"id": "5m_native40_check15m", "management_minutes": 5, "ma_kind": "SMA",
     "ma_length": 40, "exit_mode": "transition_colour", "confirmations": 1,
     "decision_minutes": 15},
]
INFERENCE = {"draws": 9999, "seed": 20260906, "p_limit": .01,
             "joint_required": ["case_delta", "excess_delta"]}
DEVELOPMENT_FOLDS = [
    ["2023H1", "2023-01-01", "2023-07-01"],
    ["2023H2", "2023-07-01", "2024-01-01"],
    ["2024H1", "2024-01-01", "2024-07-01"],
    ["2024H2", "2024-07-01", "2025-01-01"],
]
SOURCES = list(dict.fromkeys(V8_SOURCES + [
    "yoyo/evaluation/hourly_impulse_management_diagnostics.py",
    "yoyo/evaluation/hourly_impulse_cadence_research.py",
    "tests/test_hourly_impulse_cadence_research.py",
    "tests/test_hourly_impulse_transition_cadence.py",
]))


def verify_config(config: dict, base: dict) -> None:
    """Reject configuration drift before a Study can read price rows."""
    expected = {"experiment_id": EXPERIMENT_ID, "base_config": BASE_CONFIG,
                "base_config_sha256": BASE_CONFIG_SHA256, "parent_results": PARENT_RESULTS,
                "inputs": FROZEN_INPUTS, "policies": POLICIES, "selection": SELECTION,
                "inference": INFERENCE, "no_audit_entry_point": True,
                "holdout_consumed": False, "training_eligible": False,
                "production_eligible": False}
    if json.dumps(config, sort_keys=True) != json.dumps(expected, sort_keys=True):
        raise ValueError("Frozen V9 configuration, identities or permissions changed")
    for key in ("holdout_consumed", "training_eligible", "production_eligible"):
        if config[key] is not False:
            raise ValueError("Research eligibility must be explicit false")
    if config["no_audit_entry_point"] is not True:
        raise ValueError("No audit entry point is allowed")
    execution = base["execution"]
    if execution["max_hours"] != 72 or execution["cost_fraction"] != .002 or execution["stop_first"] is not True:
        raise ValueError("Frozen 72h, 20bp or hard-stop-first economics changed")
    if base["development_folds"] != DEVELOPMENT_FOLDS:
        raise ValueError("Only frozen development 2023-2024 is allowed")


def validate_fixed_inputs(requests: dict, zones: pd.DataFrame) -> None:
    """No outcomes: fixed full identity counts and causal development times."""
    if (len(requests["case"]), len(requests["control"]), len(zones)) != (286, 849, 959):
        raise ValueError("Frozen V7 support changed")
    for frame, identity, timestamp in [(requests["case"], "event_id", "decision_time"),
                                        (requests["control"], "event_id", "decision_time"),
                                        (zones, "zone_id", "zone_arm_time")]:
        if frame[identity].isna().any() or frame[identity].duplicated().any():
            raise ValueError("Frozen input identities must be unique and finite")
        times = pd.to_datetime(frame[timestamp], utc=True, format="mixed")
        if times.isna().any() or not (times.ge(utc("2023-01-01")) & times.lt(utc("2025-01-01"))).all():
            raise ValueError("Frozen input cannot contain non-development decisions")


def prepare_contexts(study, requests: dict) -> dict:
    """Attach both arms independently, then require EXACT full context equality.

    All original request fields/order remain; native five-minute OHLC/SMA40
    availability is validated by the pure helper at entry. No exits are read.
    """
    prepared = {}
    for policy in POLICIES:
        prepared[policy["id"]] = {}
        for label, entries in requests.items():
            contextual = attach_management_context(study.raw, study.featured(5, "SMA", 40), entries, 5)
            assert_saved_parity(entries, contextual)
            if not set(CONTEXT_COLUMNS).issubset(contextual):
                raise ValueError("Independent native5m context is incomplete")
            prepared[policy["id"]][label] = contextual
    for label in requests:
        pd.testing.assert_frame_equal(prepared[POLICIES[0]["id"]][label],
                                      prepared[POLICIES[1]["id"]][label], check_exact=True)
    return prepared


def cadence_mechanisms(before, after):
    """Retrospective case diagnostics; unknown net outcomes are not losers."""
    joined, _, _ = mechanism_tables(before, after)
    known = np.isfinite(joined.net_return_5m) & np.isfinite(joined.net_return_15m)
    known_ids = set(joined.loc[known, "event_id"])
    _, transitions, exits = mechanism_tables(before.loc[before.event_id.isin(known_ids)],
                                            after.loc[after.event_id.isin(known_ids)])
    for column in ("old_win", "new_win"):
        joined[column] = joined[column].astype("boolean").where(known, pd.NA)
    def rename(frame):
        return frame.rename(columns={column: column[:-3]+"_check5m" if column.endswith("_5m")
            else column[:-4]+"_check15m" for column in frame.columns if column.endswith(("_5m", "_15m"))})
    tables = {"paired_case_mechanics": rename(joined), "win_loss_transitions": transitions,
              "exit_transitions": rename(exits)}
    info = {"total_pairs": len(joined), "known_pairs": int(known.sum()), "unknown_pairs": int((~known).sum()),
            "native_management_minutes_both": 5, "check_minutes_before": 5, "check_minutes_after": 15,
            "win_loss_transitions": transitions.to_dict("records"), "exit_transitions": rename(exits).to_dict("records"),
            "limitation": "Retrospective outcomes only; unknown pairs excluded from win/loss classification, never admission features."}
    info["distribution_checks"] = distribution_checks(joined)
    return tables, info


def distribution_checks(joined):
    """Untrimmed descriptive outcomes, never a normality-based method switch.

    Optional Shapiro follows https://docs.scipy.org/doc/scipy-1.13.1/reference/generated/scipy.stats.shapiro.html
    Fewer than three or constant observations are explicitly untestable. Missing
    and invalid infinite values remain counted, never converted to zero returns.
    """
    result = {}
    for name, column in (("net_check5m", "net_return_5m"), ("net_check15m", "net_return_15m"),
                         ("case_delta", "difference")):
        values = joined[column]
        finite = np.isfinite(values)
        bp = values.loc[finite]*1e4
        info = {"total": len(values), "finite_count": len(bp), "missing_count": int(values.isna().sum()),
                "nonfinite_count": int((~finite).sum()), "outliers_removed": 0,
                "quantiles_bp": {str(k): float(v) for k, v in bp.quantile([0,.05,.25,.5,.75,.95,1]).items()},
                "sd_bp": bp.std(ddof=1), "shapiro_used_for_selection": False}
        if len(bp) < 3 or bp.nunique() < 2:
            info["shapiro_unavailable"] = "fewer than three or constant finite observations"
        else:
            try:
                from scipy.stats import shapiro
                w, p = shapiro(bp)
                info.update(shapiro_w=float(w), shapiro_p=float(p))
            except ImportError:
                info["shapiro_unavailable"] = "scipy is not installed; no dependency added"
        result[name] = info
    return result


def serial_intentions(before, after):
    """All original source zones per arm, including skipped and unknown ones."""
    assert_saved_parity(before[["event_id", "mother_decision_time"]],
                        after[["event_id", "mother_decision_time"]])
    rows = []
    for policy, serial in zip(POLICIES, (before, after)):
        if not serial.portfolio_selected.isin([True, False]).all():
            raise ValueError("Every serial zone needs an explicit participation decision")
        selected = serial.loc[serial.portfolio_selected]
        skipped = serial.loc[~serial.portfolio_selected]
        missed = skipped.loc[skipped.entry_event_id.notna()]
        net = serial.episode_net_return.where(serial.portfolio_selected, 0.)
        rows.append({"arm": policy["id"], "native_management_minutes": 5,
            "decision_minutes": policy.get("decision_minutes", 5), "zones": len(serial),
            "selected_zones": len(selected), "skipped_zones": len(skipped),
            "skipped_emitted_requests": len(missed),
            "skipped_winners": int(missed.episode_net_return.gt(0).sum()),
            "skipped_losers": int(missed.episode_net_return.lt(0).sum()),
            "skipped_unknown_requests": int((~np.isfinite(missed.episode_net_return)).sum()),
            "unknown_selected_zones": int((~np.isfinite(selected.episode_net_return)).sum()),
            "finite_intentions": int(np.isfinite(net).sum()),
            "mean_net_bp_per_original_zone": net.mean()*1e4 if np.isfinite(net).all() else np.nan,
            "net_event_sum_bp": net.sum()*1e4 if np.isfinite(net).all() else np.nan})
    return pd.DataFrame(rows)


def run():
    config_path = EXPERIMENT/"config.json"
    config = json.loads(config_path.read_text())
    base_path = ROOT/config["base_config"]
    if digest(base_path) != BASE_CONFIG_SHA256:
        raise RuntimeError("Parent config changed")
    base = json.loads(base_path.read_text())
    verify_config(config, base)
    sources = committed_sources([ROOT/p for p in SOURCES] + [config_path, base_path, EXPERIMENT/"PROJECT_PLAN.md"])
    parent = ROOT/config["parent_results"]
    for name, expected in FROZEN_INPUTS.items():
        if digest(parent/name) != expected:
            raise RuntimeError("Frozen V7 input changed: "+name)
    requests = {label: read_frame(parent/f"{label}_requests.csv.gz") for label in ("case", "control")}
    zones, assignments = read_frame(parent/"source_zones.csv.gz"), read_frame(parent/"assignments.csv.gz")
    validate_fixed_inputs(requests, zones)
    support = support_info(requests["case"], zones, assignments, [f[0] for f in DEVELOPMENT_FOLDS])
    if not support["passed"]:
        raise ValueError("Frozen support no longer passes")
    results = EXPERIMENT/"results"
    if results.exists():
        raise RuntimeError("Preserve previous attempts; output already exists")
    results.mkdir()
    write_json(results/"started.json", {"at": pd.Timestamp.now(tz="UTC"), "sources": sources,
        "inputs": config["inputs"], "builder_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, cwd=ROOT).strip()})
    study = Study(base, "development")
    prepared = prepare_contexts(study, requests)
    for arm, populations in prepared.items():
        for label, frame in populations.items():
            write_csv(results/f"{arm}_{label}_context.csv.gz", frame)
    write_json(results/"contexts_frozen.json", {"at": pd.Timestamp.now(tz="UTC"),
        "both_native_minutes": 5, "exact_context_parity": True,
        "sha256": {p.name: digest(p) for p in results.glob("*_context.csv.gz")}})
    arms = []
    for policy in POLICIES:
        arm = policy["id"]
        arms.append(evaluate_arm(study, policy, prepared[arm]["case"], prepared[arm]["control"],
            zones, results/arm, config, parent=parent if arm == "5m_native40" else None))
    invariant = ["event_id", "entry_time", "entry_price", "initial_stop", "signal_atr", "risk_pct", "risk_atr"]
    for label in requests:
        assert_saved_parity(arms[0][1][label][invariant], arms[1][1][label][invariant])
    a, b = arms
    frames, effects = paired_effects(a[2]["case"], b[2]["case"], a[3], b[3], a[4], b[4])
    for name, frame in frames.items():
        write_csv(results/(name+".csv"), frame)
    mechanisms, mechanism = cadence_mechanisms(a[1]["case"], b[1]["case"])
    mechanisms["serial_intentions"] = serial_intentions(a[4], b[4])
    mechanism["serial_intentions"] = mechanisms["serial_intentions"].to_dict("records")
    for name, frame in mechanisms.items():
        write_csv(results/(name+(".csv.gz" if name == "paired_case_mechanics" else ".csv")), frame)
    gates = {**b[0]["gates"], **{key+"_improves": positive_inference(effects[key]) for key in INFERENCE["joint_required"]}}
    gates["complete_paired_support"] = effects["case_delta"]["n"] == 286 and effects["excess_delta"]["n"] == 283 and effects["serial_delta"]["n"] == 959
    final = {"status": "development_pass_requires_prospective_validation" if all(gates.values()) else "rejected_development_no_audit",
        "arms": [a[0], b[0]], "effects": effects, "gates": gates, "support": support, "mechanism": mechanism,
        "exact_entry_context_parity": True, "source_receipt": study.source_receipt,
        "config_sha256": digest(config_path), "source_hashes": sources,
        "holdout_price_rows": 0, "audit_opened": False, "independent_confirmation": False,
        "training_eligible": False, "production_eligible": False}
    write_json(results/"summary.json", final)
    print(json.dumps({"status": final["status"], "summary": str(results/"summary.json")}, ensure_ascii=False))


if __name__ == "__main__":
    run()
