"""Explain frozen V2 missed events without thresholds, new labels or scoring.

Inputs: authenticated completed recall-study artifacts and their frozen original
feature frames. Recompute the SAME causal V2 fields/state to decompose its gates,
then require exact agreement with the previously frozen signal schedule. Labels
are read, never regenerated. No trade simulation or statistical fitting occurs.

Each positive event missed by its +1 close is examined at t and t+1. Failure
counts overlap: they describe observed obstructions, not independently causal
effects of relaxing a gate. Existing tracking is never credited as a new arrow.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_burst_progressive as v2
from yoyo.evaluation import spike_burst_recall_study as study
from yoyo.evaluation.spike_burst_dataset import load_feature

CRITERIA = ("ready", "recent_density", "break_prior12", "above_six_ma", "green_candle",
    "zlema_rising", "md_ge_signal", "advance_3bar", "volume_3bar", "efficiency_3bar", "close_position")
STATE_GATES = ("no_existing_hold", "not_exit_bar", "no_v1_priority")
NUMERIC_FIELDS = ("open", "high", "low", "close", "atr", "middle", "md", "sb", "ropeHigh",
    "pastWidth", "pastCrosses", "prog_dense_hits", "prog_prior_high", "prog_advance",
    "prog_volume_base", "prog_volume_ratio", "prog_efficiency", "prog_close_position")
THRESHOLDS = dict(density_width=v2.DENSE_WIDTH, density_crosses=v2.DENSE_CROSSES,
    density_lookback=v2.DENSE_LOOKBACK, progress_bars=v2.PROGRESS_BARS,
    advance_3bar=v2.MIN_PROGRESS, volume_3bar=v2.MIN_PROGRESS_VOLUME,
    efficiency_3bar=v2.MIN_EFFICIENCY, close_position=v2.MIN_PROGRESS_END)


def gate_table(frame, state):
    """Decompose existing progressive_fields exactly, including holding order.

State before the progressive branch is reconstructed from the prior holding
side, current exit flag and the current hard-route arrow. An exit bar stays
blocked even when its previous holding has just ended; hard routes win priority.
"""
    if not frame.index.equals(state.index):
        raise ValueError("State/frame clocks differ")
    out = frame.loc[:, list(NUMERIC_FIELDS)].copy()
    out["previous_middle"] = frame.middle.shift(1)
    gates = dict(ready=frame.ready.eq(True), recent_density=frame.prog_recent_dense.eq(True),
        break_prior12=frame.close.gt(frame.prog_prior_high), above_six_ma=frame.close.gt(frame.ropeHigh),
        green_candle=frame.close.gt(frame.open), zlema_rising=frame.middle.gt(frame.middle.shift(1)),
        md_ge_signal=frame.md.ge(frame.sb), advance_3bar=frame.prog_advance.ge(v2.MIN_PROGRESS),
        volume_3bar=frame.prog_volume_ratio.ge(v2.MIN_PROGRESS_VOLUME),
        efficiency_3bar=frame.prog_efficiency.ge(v2.MIN_EFFICIENCY),
        close_position=frame.prog_close_position.ge(v2.MIN_PROGRESS_END))
    out["prior_trend_side"] = state.trend_side.shift(1, fill_value=0)
    out["holding_through_close"] = out.prior_trend_side.ne(0) & ~state.exit.eq(True)
    out["exit_this_close"] = state.exit.eq(True)
    out["v1_same_bar_priority"] = state.burst.eq(True) & state.route.isin(["price_first", "release_confirm"])
    gates.update(no_existing_hold=~out.holding_through_close,
        not_exit_bar=~out.exit_this_close, no_v1_priority=~out.v1_same_bar_priority)
    for key, value in gates.items():
        out["pass_" + key] = value.fillna(False).astype(bool)
    out["all_criteria"] = out[["pass_" + key for key in CRITERIA]].all(axis=1)
    if not np.array_equal(out.all_criteria.to_numpy(), frame.prog_up.to_numpy()):
        raise ValueError("Gate decomposition differs from frozen progressive fields")
    out["progressive_eligible"] = out.all_criteria & out[["pass_" + key for key in STATE_GATES]].all(axis=1)
    for key in ("burst", "route", "trend_side", "entry_bar", "entry_ref", "initial_stop", "risk_valid", "protection", "state"):
        out["v2_" + key] = state[key]
    for keys, column in ((CRITERIA, "failed_criteria"), (STATE_GATES, "failed_state_gates")):
        values = out[["pass_" + key for key in keys]].itertuples(index=False, name=None)
        out[column] = [";".join(key for key, passed in zip(keys, row) if not passed) for row in values]
    return out


def miss_records(labels, detections, gates, instrument, asset):
    """Read fixed positive/+1 decisions; each event keeps the same denominator."""
    d = detections.loc[detections.instrument.eq(instrument) & detections.arm.eq("v2")]
    label = labels.loc[labels.instrument.eq(instrument) & labels.in_study.eq(True) & labels.label.eq("positive")]
    merged = label.merge(d, on=["instrument", "event_i"], validate="one_to_one")
    if len(merged) != len(label):
        raise ValueError("Positive labels lack frozen detection rows")
    rows = []
    for item in merged.itertuples():
        if bool(item.hit_1):
            continue
        i = int(item.event_i)
        if i + 1 >= len(gates):
            raise ValueError("Complete positive event lacks its +1 observation")
        first, second = gates.iloc[i], gates.iloc[i+1]
        record = dict(instrument=instrument, asset=asset, label_id=item.label_id,
            event_i=i, decision_time=item.decision_time, already_tracking=bool(item.already_tracking),
            first_signal_lag=item.first_signal_lag,
            full=True, case_night=bool(study.NIGHT[0] <= item.decision_time < study.NIGHT[1]),
            criteria_pass_once=bool(first.all_criteria or second.all_criteria),
            criteria_pass_both=bool(first.all_criteria and second.all_criteria),
            holding_both=bool(first.holding_through_close and second.holding_through_close))
        for gate in CRITERIA + STATE_GATES:
            record["fail_t_" + gate] = not bool(first["pass_" + gate])
            record["fail_t1_" + gate] = not bool(second["pass_" + gate])
            record["fail_both_" + gate] = record["fail_t_" + gate] and record["fail_t1_" + gate]
        rows.append(record)
    return rows, dict(positive_events=len(merged), hits_1=int(merged.hit_1.sum()), misses_1=len(rows),
        already_tracking=int(merged.already_tracking.sum()))


def summarize_misses(misses):
    """Nonexclusive counts; gate rows must never be summed as unique events."""
    rows = []
    for period in ("full", "case_night"):
        p = misses.loc[misses[period].eq(True)]
        groups = (("all", p), ("already_tracking", p.loc[p.already_tracking.eq(True)]),
                  ("not_already_tracking", p.loc[~p.already_tracking.eq(True)]))
        for cohort, g in groups:
            for gate in CRITERIA + STATE_GATES:
                rows.append(dict(period=period, cohort=cohort, gate=gate, missed_events=len(g),
                    fail_at_anchor=int(g["fail_t_" + gate].sum()), fail_at_plus1=int(g["fail_t1_" + gate].sum()),
                    fail_both=int(g["fail_both_" + gate].sum()),
                    fail_both_fraction=g["fail_both_" + gate].mean(), mutually_exclusive=False))
    return pd.DataFrame(rows)


def case_rows(gates, state, asset, instrument):
    """Target open bars plus latest preceding and first in-window V2 entries."""
    decisions = gates.index + study.HOUR
    arrows = np.flatnonzero(state.burst.eq(True).to_numpy())
    selected = {}
    for op in study.SNAPSHOT_OPENS:
        if op in gates.index:
            i = int(gates.index.get_loc(op))
            selected.setdefault(i, []).append("target_open_" + op.tz_convert("Asia/Shanghai").strftime("%m%d_%H%M"))
            before = arrows[arrows <= i]
            if len(before):
                selected.setdefault(int(before[-1]), []).append("latest_entry_at_or_before_" + op.tz_convert("Asia/Shanghai").strftime("%H%M"))
    preceding = arrows[decisions[arrows] < study.NIGHT[0]]
    inside = arrows[(decisions[arrows] >= study.NIGHT[0]) & (decisions[arrows] < study.NIGHT[1])]
    if len(preceding):
        selected.setdefault(int(preceding[-1]), []).append("latest_entry_before_night")
    if len(inside):
        selected.setdefault(int(inside[0]), []).append("first_entry_inside_night")
    rows = []
    for i, roles in sorted(selected.items()):
        row = dict(asset=asset, instrument=instrument, observation_roles=";".join(roles),
            bar_open=gates.index[i], confirmed_at=decisions[i], **gates.iloc[i].to_dict())
        entry_i = state.entry_bar.iloc[i]
        row["active_entry_confirmed_at"] = decisions[int(entry_i)] if state.trend_side.iloc[i] == 1 and pd.notna(entry_i) else None
        rows.append(row)
    context = dict(asset=asset, instrument=instrument,
        latest_entry_before_night=decisions[int(preceding[-1])] if len(preceding) else None,
        first_entry_inside_night=decisions[int(inside[0])] if len(inside) else None,
        signals_inside_night=len(inside))
    return rows, context


def run(results=study.EXPERIMENT / "results", output=study.EXPERIMENT / "qa/gates"):
    """Replay existing decisions for explanation only; authenticate every input."""
    results, output = Path(results).resolve(), Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("Refusing diagnostic overwrite")
    pins = study.source_pins()
    extra_paths = [Path(__file__).resolve(), study.ROOT / "tests/test_spike_burst_recall_gates.py"]
    for path in extra_paths:
        rel = str(path.relative_to(study.ROOT))
        if subprocess.check_output(["git", "show", "HEAD:" + rel], cwd=study.ROOT) != path.read_bytes():
            raise ValueError("Commit exact diagnostic builder/test first")
        pins[rel] = study.sha(path)
    prepared_path, validation_path = results / "prepared_manifest.json", results / "validation_manifest.json"
    prepared, validation = json.loads(prepared_path.read_text()), json.loads(validation_path.read_text())
    if prepared["status"] != "complete" or validation["status"] != "complete":
        raise ValueError("Requires completed frozen study")
    if validation["prepared_manifest_sha256"] != study.sha(prepared_path):
        raise ValueError("Prepared/validation receipts differ")
    if prepared["source_pins"] != study.source_pins() or validation["source_pins"] != prepared["source_pins"]:
        raise ValueError("Study sources differ from completed run")
    inputs = {str(prepared_path): study.sha(prepared_path), str(validation_path): study.sha(validation_path)}
    for item in prepared["artifacts"] + validation["artifacts"]:
        study.checked(item["path"], item["sha256"])
        inputs[item["path"]] = item["sha256"]
    labels = pd.read_csv(results / "labels.csv.gz")
    labels["decision_time"] = pd.to_datetime(labels.decision_time, utc=True)
    detections = pd.read_csv(results / "detections.csv.gz")
    signals = pd.read_csv(results / "signals.csv.gz")
    jobs = json.loads((results / "matching.json").read_text())["jobs"]
    all_misses, all_cases, case_context, counts = [], [], [], []
    for job in jobs:
        frame = load_feature(job["features_path"], job["features_sha256"], 60, study.END)
        inputs[job["features_path"]] = job["features_sha256"]
        full = pd.concat([frame, v2.progressive_fields(frame)], axis=1)
        state = v2.replay(full, float(job["tick"]))
        closes = frame.index + study.HOUR
        indices = np.flatnonzero(state.burst.eq(True).to_numpy() & (closes >= study.START) & (closes < study.END))
        prior = signals.loc[signals.instrument.eq(job["instrument"]) & signals.arm.eq("v2"), "decision_i"]
        if set(indices) != set(prior.astype(int)):
            raise ValueError("Replayed arrows differ from frozen V2 schedule")
        gates = gate_table(full, state)
        if not np.array_equal(gates.progressive_eligible.to_numpy(), state.route.eq("progressive").to_numpy()):
            raise ValueError("State-gate decomposition differs from actual progressive route")
        missed, count = miss_records(labels, detections, gates, job["instrument"], job["asset"])
        all_misses.extend(missed)
        counts.append(dict(instrument=job["instrument"], asset=job["asset"], **count))
        if job["asset"] in ("HYPE", "NEAR", "PEPE"):
            rows, context = case_rows(gates, state, job["asset"], job["instrument"])
            all_cases.extend(rows)
            case_context.append(context)
        print("explained frozen", job["instrument"], flush=True)
    misses = pd.DataFrame(all_misses)
    output.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, table in (("miss_events.csv.gz", misses), ("miss_gate_counts.csv", summarize_misses(misses)),
                        ("case_gates.csv", pd.DataFrame(all_cases)), ("event_counts.csv", pd.DataFrame(counts))):
        path = output / name
        study.write_csv(path, table)
        paths.append(path)
    study._verify_references(inputs)
    for rel, digest in pins.items():
        study.checked(study.ROOT / rel, digest)
    receipt = dict(status="complete", kind="fixed_configuration_explanation_no_rescoring", source_pins=pins,
        inputs=inputs, thresholds=THRESHOLDS, case_context=case_context,
        observed_positive_events=sum(row["positive_events"] for row in counts),
        observed_hits_1=sum(row["hits_1"] for row in counts), missed_events=len(misses),
        artifacts=[study.artifact(path) for path in paths],
        limitations=["Gate counts overlap and are not causal effect estimates of removing gates",
            "Existing holding and the exit-bar embargo are reported separately from criterion failures",
            "Three shown assets and same-night moves are not an independent statistical test",
            "Only frozen proxy-label positives are diagnosed; this does not redefine all desirable launches"])
    study.write_json(output / "diagnostic_manifest.json", receipt)
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=study.EXPERIMENT / "results")
    parser.add_argument("--output", type=Path, default=study.EXPERIMENT / "qa/gates")
    args = parser.parse_args()
    run(args.results, args.output)
