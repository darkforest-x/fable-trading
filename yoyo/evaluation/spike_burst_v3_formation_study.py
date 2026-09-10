"""Preregistered fourth structural hypothesis: ordered six-MA formation.

D only gates V3 parents by median absolute ribbon width in the prior recent6
versus older6 bars. No reference occupancy, extra future wait or new threshold
search is added. Globally prepare V3/D parents, upgrades and common causal
controls before reading outcomes. Accepted parents alone receive unchanged
next-open fills, original risk/trailing exits and20bp costs. A/B/C p values stay
frozen and join D in four-hypothesis Holm. Previously seen history and thirteen
prior single-gate explorations preclude any claim of blind OOS validation.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path
import shutil
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_burst_early_warning as v3
from yoyo.evaluation import spike_burst_recall_study as old
from yoyo.evaluation import spike_burst_v3_formation_gate as formation
from yoyo.evaluation.spike_burst_dataset import load_feature, match_controls
from yoyo.evaluation.spike_burst_execution import simulate_trade


ROOT = old.ROOT
EXPERIMENT = ROOT / "experiments/active/exp-spike-v3-formation-gate-20260910-v1"
OUTPUT = EXPERIMENT / "results"
CACHE_SOURCE = ROOT / "experiments/active/exp-spike-v3-confirmation-gate-20260910-v1/results"
AB_SOURCE = ROOT / "experiments/active/exp-spike-v3-focus-20260910-v1/results"
AB_VALIDATION_SHA = "b94367b06517fd4639473dbf6a4806cdf9d12dddbb7cfa59c3a1f98f5f8d121b"
V3_RESULTS = v3.EXPERIMENT / "results"
PREPARED_SHA = "70a2f898a5593d4c87e5d272d1921ea0b9b492388b5e752e67ab1cf232da16d6"
VALIDATION_SHA = "ae9c40bf2e264763dfc517f3ff8afa6d64903800cd145bbdc50b0a09961e9209"
ARMS = ("v3", "formation_gate")
CONFIG = dict(
    schema="spike-v3-formation-gate-v1", arms=ARMS,
    new_hypothesis="D: prior12 six-MA absolute width recent6 median <= older6 median",
    interventions="one formation-process gate on V3 parent; no reference suppression; A/B/C not rerun or stacked",
    primary_economics="parent early only; confirmations are upgrades",
    structural_hypothesis_number=4, holm_family="frozen A, B, C and new D full-period p values",
    holdout_per_new_config=1, retrospective=True, exploratory=True,
    acceptance=dict(label_drop=.5, max_distinct_labels=6021, large_recall=.8, baseline_positive_retention=.9),
    execution="unchanged actual next-open; unchanged risk and exits; no detection occupancy",
    cost_bp=20, production_eligible=False,
)
STATE_COLUMNS = (
    "candidate_edge", "cooldown_blocked", "formation_width", "formation_old_median",
    "formation_recent_median", "formation_valid", "formed", "formation_blocked",
)


def source_pins():
    paths = [Path(__file__), EXPERIMENT / "PROJECT_PLAN.md",
             ROOT / "tests/test_spike_burst_v3_formation_study.py",
             ROOT / "tests/test_spike_burst_v3_formation_gate.py"]
    paths += [ROOT / "yoyo/evaluation" / name for name in (
        "spike_burst_v3_formation_gate.py", "spike_burst_early_warning.py",
        "spike_burst_replay.py", "spike_burst_progressive.py", "spike_burst_execution.py", "spike_burst_dataset.py",
        "spike_burst_recall_study.py", "altseason_research.py")]
    pins = {}
    for path in paths:
        relative = str(path.resolve().relative_to(ROOT))
        if subprocess.check_output(["git", "show", "HEAD:" + relative], cwd=ROOT) != path.read_bytes():
            raise ValueError("Commit exact source before running: " + relative)
        pins[relative] = old.sha(path)
    return pins


def frozen_inputs():
    """Authenticate historical C and its A/B/V3 sources without using returns in detection."""
    refs, receipts = {}, []
    for name, digest in (("prepared_manifest.json", PREPARED_SHA),
                         ("validation_manifest.json", VALIDATION_SHA)):
        path = old.checked(CACHE_SOURCE / name, digest)
        receipt = json.loads(path.read_text())
        if receipt["status"] != "complete":
            raise ValueError("Incomplete frozen source")
        refs[str(path)] = digest
        for item in receipt["artifacts"]:
            path = old.checked(item["path"], item["sha256"])
            refs[str(path)] = item["sha256"]
        receipts.append(receipt)
    prepared, validation = receipts
    if validation["prepared_sha"] != PREPARED_SHA:
        raise ValueError("Historical C outcomes belong to a different schedule")
    # Includes the original V3 event/label files and source feature hashes.
    for path, digest in prepared["sources"].items():
        old.checked(path, digest)
        refs[path] = digest
    for relative, digest in prepared["source_pins"].items():
        path = ROOT / relative
        old.checked(path, digest)
        refs[str(path)] = digest
    if refs.get(str(AB_SOURCE / "validation_manifest.json")) != AB_VALIDATION_SHA:
        raise ValueError("Historical A/B receipt must remain in the fixed four-hypothesis family")
    matching = json.loads((CACHE_SOURCE / "matching.json").read_text())
    labels = pd.read_csv(CACHE_SOURCE / "labels.csv.gz", float_precision="round_trip")
    for key in ("decision_time", "bar_open"):
        labels[key] = pd.to_datetime(labels[key], utc=True)
    selected = labels[labels.decision_time.ge(old.START) & labels.decision_time.lt(old.END)]
    if len(labels) != 14904 or len(selected) != 8046 or selected.label.eq("positive").sum() != 1660:
        raise ValueError("Frozen label population changed")
    return matching["jobs"], labels, refs


def economic_parents(signals):
    """Children are upgrades, not additional economic entries."""
    if not signals.stage.isin(["early", "confirmed"]).all():
        raise ValueError("Unknown signal stage")
    selected = signals[signals.stage.eq("early")].copy()
    if selected.event_id.duplicated().any():
        raise ValueError("Duplicate economic parent identity")
    return selected


def prepare():
    pins = source_pins()
    if OUTPUT.exists() and any(OUTPUT.iterdir()):
        raise ValueError("Refusing overwrite or rescoring")
    jobs, labels, refs = frozen_inputs()
    prior_signals = pd.read_csv(V3_RESULTS / "signals.csv.gz", float_precision="round_trip")
    OUTPUT.mkdir(parents=True)
    (OUTPUT / "states").mkdir()
    old.write_json(OUTPUT / "started.json", dict(config=CONFIG, source_pins=pins))
    signals, parents, controls, detections, exposures, states = [], [], [], [], [], []
    for job_number, job in enumerate(jobs):
        frame = load_feature(job["features_path"], job["features_sha256"], 60, old.END)
        refs[job["features_path"]] = job["features_sha256"]
        machines = {"v3": v3.detect(frame),
                    "formation_gate": formation.detect(frame)}
        required = set(STATE_COLUMNS)
        if not required.issubset(machines["formation_gate"].columns):
            raise ValueError("D detector must expose the causal ordered formation gate")
        context = {key: job[key] for key in ("instrument", "asset", "symbol", "venue", "minutes")}
        clocks = frame.index + old.HOUR
        window = (clocks >= old.START) & (clocks < old.END)
        local_labels = labels[labels.instrument.eq(job["instrument"])]
        union = sorted({int(i) for state in machines.values() for stage in ("early", "confirmed")
                        for i in np.flatnonzero(state[stage].to_numpy())})
        parent_union = sorted({int(i) for state in machines.values()
                               for i in np.flatnonzero(state.early.to_numpy()) if window[i]})
        matcher = frame.copy()
        matcher["history_count"] = np.where(frame.ready, frame.history_count, 0)
        random = match_controls(matcher, union, parent_union, job["instrument"], 60, old.START, old.END)
        saved = pd.DataFrame(dict(decision_i=np.arange(len(frame)), decision_time=clocks))
        for arm, state in machines.items():
            for key in ("early", "confirmed", "parent_i", "frozen_parent_high", "confirm_age"):
                saved[arm + "_" + key] = state[key].to_numpy()
            for key in STATE_COLUMNS:
                if key in state:
                    saved[arm + "_" + key] = state[key].to_numpy()
            for stage in ("early", "confirmed"):
                indices = np.flatnonzero(state[stage].to_numpy())
                if arm == "v3":
                    prior = prior_signals[prior_signals.instrument.eq(job["instrument"]) & prior_signals.arm.eq(stage)]
                    if not np.array_equal(indices[window[indices]], prior.decision_i.to_numpy(int)):
                        raise ValueError("Frozen V3 baseline sequence drift")
                dummy = pd.DataFrame(dict(trend_side=np.zeros(len(frame), dtype=int), burst=state[stage]), index=frame.index)
                matched, detected = old.match_signals(local_labels, indices, frame, dummy)
                detected["arm"], detected["stage"], detected["instrument"] = arm, stage, job["instrument"]
                detections.append(detected)
                matchmap = matched.set_index("decision_i").to_dict("index")
                for i in indices:
                    i = int(i)
                    eid = old.identity(job["instrument"], arm + "_" + stage, i)
                    parent_i = int(state.parent_i.iloc[i])
                    if not bool(state.early.iloc[parent_i]) or not 0 <= i - parent_i <= 3:
                        raise ValueError("Child has no accepted parent in its original age window")
                    if stage == "early":
                        parents.append(dict(context, arm=arm, event_id=eid, decision_i=i,
                                            decision_time=clocks[i], in_study=bool(window[i])))
                    if not window[i]:
                        continue
                    match = matchmap[i]
                    signals.append(dict(
                        context, arm=arm, stage=stage, event_id=eid,
                        decision_i=i, decision_time=clocks[i], bar_open=frame.index[i],
                        parent_i=parent_i, parent_decision_time=clocks[parent_i],
                        parent_event_id=old.identity(job["instrument"], arm + "_early", parent_i),
                        signal_close=float(frame.close.iloc[i]), signal_atr=float(frame.atr.iloc[i]),
                        relative_volume=float(frame.rv.iloc[i]), tr_expansion=float(frame.expansion.iloc[i]),
                        frozen_parent_high=float(state.frozen_parent_high.iloc[i]), confirm_age=state.confirm_age.iloc[i],
                        formation_at_parent=bool(machines["formation_gate"].formed.iloc[parent_i]),
                        formation_old_median=float(machines["formation_gate"].formation_old_median.iloc[parent_i]),
                        formation_recent_median=float(machines["formation_gate"].formation_recent_median.iloc[parent_i]),
                        match_status=match["match_status"], label_i=match["label_i"], lag=match["lag"]))
                    if stage == "early":
                        for number, random_i in enumerate(random[i]):
                            controls.append(dict(context, arm=arm, stage=stage,
                                event_id=old.identity(eid, "control", number), matched_event_id=eid,
                                control_number=number, decision_i=int(random_i), decision_time=clocks[int(random_i)]))
        state_path = OUTPUT / "states" / ("%03d_%s.csv.gz" % (job_number, job["asset"]))
        old.write_csv(state_path, saved)
        states.append(old.artifact(state_path))
        exposures.append(pd.DataFrame(dict(decision_time=clocks[window],
                                            eligible=frame.ready.to_numpy()[window].astype(int))))
        print("prepared", job_number + 1, len(jobs), job["asset"], flush=True)
    products = {"signals.csv.gz": pd.DataFrame(signals), "parent_registry.csv.gz": pd.DataFrame(parents),
                "controls.csv.gz": pd.DataFrame(controls), "detections.csv.gz": pd.concat(detections, ignore_index=True),
                "exposure.csv.gz": pd.concat(exposures).groupby("decision_time", as_index=False).eligible.sum()}
    for name, table in products.items():
        old.write_csv(OUTPUT / name, table)
    shutil.copyfile(CACHE_SOURCE / "labels.csv.gz", OUTPUT / "labels.csv.gz")
    old.write_json(OUTPUT / "matching.json", dict(jobs=jobs, config=CONFIG, state_artifacts=states))
    old._verify_references(refs)
    if source_pins() != pins:
        raise ValueError("Builder changed during preparation")
    old.write_json(OUTPUT / "prepared_manifest.json", dict(
        status="complete", config=CONFIG, source_pins=pins, sources=refs,
        artifacts=[old.artifact(OUTPUT / name) for name in list(products) + ["labels.csv.gz", "matching.json"]] + states))


def structural_holm(ab_summary, c_summary, current_summary):
    """Keep all four fixed structural hypotheses; history is not rescored."""
    rows = []
    for arm, table, origin in (("reference", ab_summary, "frozen_A"),
                               ("near_box", ab_summary, "frozen_B"),
                               ("confirmation_gate", c_summary, "frozen_C"),
                               ("formation_gate", current_summary, "fourth_exploratory_hypothesis_D")):
        selected = table[table.arm.eq(arm) & table.period.eq("full")]
        if len(selected) != 1:
            raise ValueError("Exactly one frozen full-period p value per structural hypothesis is required")
        p = selected.permutation_p.iloc[0]
        if pd.notna(p) and not 0 <= float(p) <= 1:
            raise ValueError("Permutation p must be within [0,1]")
        rows.append(dict(arm=arm, origin=origin, raw_p=p, correction_input_p=float(p) if pd.notna(p) else 1.0))
    family = pd.DataFrame(rows)
    family["holm_p_four"] = v3.holm(family.correction_input_p.to_numpy())
    family["interpretation"] = "four sequential exploratory hypotheses plus prior 13 gate explorations; not blind OOS"
    return family


def retention_summary(labels, detections, signals, exposure):
    rows = []
    for stage in ("early", "confirmed"):
        base = detections[detections.arm.eq("v3") & detections.stage.eq(stage)]
        base = base[["instrument", "event_i", "hit_1"]].rename(columns={"hit_1": "baseline_hit_1"})
        for arm in ARMS:
            local = detections[detections.arm.eq(arm) & detections.stage.eq(stage)]
            merged = labels.merge(local, on=["instrument", "event_i"], how="left", validate="one_to_one")
            merged = merged.merge(base, on=["instrument", "event_i"], how="left", validate="one_to_one")
            if merged[["hit_1", "hit_6", "baseline_hit_1"]].isna().any().any():
                raise ValueError("Missing detection rows must not shrink the fixed label denominator")
            for period, (start, end) in old.period_windows().items():
                p = merged[merged.label.eq("positive") & merged.decision_time.ge(start) & merged.decision_time.lt(end)]
                s = signals[signals.arm.eq(arm) & signals.stage.eq(stage) & signals.decision_time.ge(start) & signals.decision_time.lt(end)]
                b = signals[signals.arm.eq("v3") & signals.stage.eq(stage) & signals.decision_time.ge(start) & signals.decision_time.lt(end)]
                current = signals[signals.arm.eq(arm) & signals.decision_time.ge(start) & signals.decision_time.lt(end)]
                baseline = signals[signals.arm.eq("v3") & signals.decision_time.ge(start) & signals.decision_time.lt(end)]
                nlabels = len(current.drop_duplicates(["instrument", "decision_i"]))
                baseline_labels = len(baseline.drop_duplicates(["instrument", "decision_i"]))
                actualset, baseset = set(zip(s.instrument, s.decision_i)), set(zip(b.instrument, b.decision_i))
                oldhit, big = p[p.baseline_hit_1.eq(True)], p[p.large_peak.eq(True)]
                hours = float(exposure.loc[exposure.decision_time.ge(start) & exposure.decision_time.lt(end), "eligible"].sum())
                rows.append(dict(arm=arm, stage=stage, period=period, signals=len(s),
                    alerts_per_day=len(s) / ((end - start) / pd.Timedelta(days=1)),
                    distinct_labels=nlabels, label_drop=1 - nlabels / baseline_labels if baseline_labels else np.nan,
                    labels_per_100_asset_days=2400 * nlabels / hours if hours else np.nan,
                    baseline_signals=len(b), same_time_kept=len(actualset & baseset),
                    same_time_removed=len(baseset - actualset), new_times=len(actualset - baseset),
                    positive_events=len(p), hits_1=int(p.hit_1.sum()), recall_1=p.hit_1.mean(), recall_6=p.hit_6.mean(),
                    baseline_hit_events=len(oldhit), retained_hit_events=int(oldhit.hit_1.sum()), retention=oldhit.hit_1.mean(),
                    newly_caught=int((~p.baseline_hit_1 & p.hit_1).sum()),
                    large_positive_events=len(big), large_hits_1=int(big.hit_1.sum()), large_recall_1=big.hit_1.mean(),
                    large_baseline_hits=int(big.baseline_hit_1.sum()), large_retained_hits=int((big.baseline_hit_1 & big.hit_1).sum()),
                    matched=int(s.match_status.eq("matched").sum()), unmatched=int(s.match_status.eq("unmatched").sum()),
                    duplicates=int(s.match_status.eq("duplicate").sum()), unknown=int(s.match_status.eq("unknown").sum())))
    return pd.DataFrame(rows)


def assessment(retention, trades):
    r = retention[retention.arm.eq("formation_gate") & retention.stage.eq("early") & retention.period.eq("full")].iloc[0]
    t = trades[trades.arm.eq("formation_gate") & trades.period.eq("full")].iloc[0]
    return dict(label_drop_pass=bool(r.label_drop >= .5 and r.distinct_labels <= 6021), large_recall_pass=bool(r.large_recall_1 >= .8),
                baseline_retention_pass=bool(r.retention >= .9),
                economic_pass=bool(t.mean_excess_bp > 0 and t.holm_p_four < .01),
                thresholds=CONFIG["acceptance"], account_nav_not_computed=True,
                live_deployed=False, accepted_for_deployment=False,
                interpretation="Exploratory fourth hypothesis; fixed fresh-signal denominator, no holding coverage")


def version_comparison(retention, trades, original_recall, original_trades):
    """Original V1 is a preserved benchmark, not a newly matched trial arm."""
    rows = []
    for period in ("full", "first31", "last30", "case_night"):
        for arm in ("v1",) + ARMS:
            if arm == "v1":
                r = original_recall[original_recall.arm.eq("v1") & original_recall.period.eq(period)].iloc[0]
                t = original_trades[original_trades.arm.eq("v1") & original_trades.period.eq(period)].iloc[0]
                labels, fresh_retention = r.signals, np.nan
                source = "preserved original V1; original matching group; no recalculation"
            else:
                r = retention[retention.arm.eq(arm) & retention.stage.eq("early") & retention.period.eq(period)].iloc[0]
                t = trades[trades.arm.eq(arm) & trades.period.eq(period)].iloc[0]
                labels, fresh_retention = r.distinct_labels, r.retention
                source = "current V3/D common controls; parent-only economics"
            rows.append(dict(arm=arm, period=period, parent_signals=r.signals, distinct_labels=labels,
                positive_events=r.positive_events, hits_1=r.hits_1, recall_1=r.recall_1,
                large_recall_1=r.large_recall_1, v3_fresh_hit_retention=fresh_retention,
                natural_win_rate=t.natural_win_rate, mean_net_bp=t.mean_net_bp,
                mean_excess_bp=t.mean_excess_bp, control_comparable_to_new_D=arm != "v1", source=source,
                target_label_drop=.5, target_large_recall=.8, target_v3_fresh_retention=.9))
    return pd.DataFrame(rows)


_CACHE = {}
_CACHE_SIGNATURES = {}
_SIGNALS = _CONTROLS = None
OUTCOME_KEYS = ("valid invalid_reason reason exit_reason signal_i decision_i entry_i exit_i decision_time entry_time exit_time "
    "exit_time_lower exit_time_upper exit_timing exit_time_exact entry_price exit_price initial_stop initial_risk initial_risk_frac "
    "net_return net_r peak_r natural_exit censored hold_bars fee_return fee_bp signal_close signal_atr exit_at_open mfe_r mfe_return "
    "held_hours_lower held_hours_upper hold_seconds gross_return gross_bp net_bp gross_r final_protection trail_armed peak_return").split()


def job_signature(job):
    return (job["features_sha256"], float(job["tick"]), int(job["minutes"]), old.END.isoformat())


def assert_cache_signature(job, signatures):
    if signatures.get(job["instrument"]) != job_signature(job):
        raise ValueError("Cached outcome source/tick/timeframe/cutoff mismatch")


def initialize_evaluation():
    global _SIGNALS, _CONTROLS, _CACHE, _CACHE_SIGNATURES
    _SIGNALS = economic_parents(pd.read_csv(OUTPUT / "signals.csv.gz", float_precision="round_trip"))
    _CONTROLS = pd.read_csv(OUTPUT / "controls.csv.gz", float_precision="round_trip")
    if not _CONTROLS.stage.eq("early").all():
        raise ValueError("A confirmation upgrade cannot have economic control entries")
    cached_jobs = json.loads((CACHE_SOURCE / "matching.json").read_text())["jobs"]
    _CACHE_SIGNATURES = {job["instrument"]: job_signature(job) for job in cached_jobs}
    _CACHE = {}
    for name in ("trade_events.csv.gz", "trade_controls.csv.gz"):
        for row in pd.read_csv(CACHE_SOURCE / name, float_precision="round_trip").to_dict("records"):
            key = (row["instrument"], int(row["decision_i"]))
            _CACHE[key] = {name: row[name] for name in OUTCOME_KEYS if name in row}


def score_job(job):
    assert_cache_signature(job, _CACHE_SIGNATURES)
    frame = load_feature(job["features_path"], job["features_sha256"], 60, old.END)
    local, outputs, new = {}, [], 0
    for table in (_SIGNALS, _CONTROLS):
        rows = []
        for row in table[table.instrument.eq(job["instrument"])].to_dict("records"):
            i = int(row["decision_i"])
            key = (job["instrument"], i)
            if i not in local:
                if key in _CACHE:
                    outcome = dict(_CACHE[key])
                else:
                    outcome = simulate_trade(frame, i, float(job["tick"]), old.END)
                    new += 1
                local[i] = dict(outcome, **v3.forward_outcome(frame, i))
            rows.append(dict(row, **local[i], relative_volume=float(frame.rv.iloc[i]),
                             tr_expansion=float(frame.expansion.iloc[i])))
        outputs.append(rows)
    return outputs, new, job["asset"]


def evaluate(workers=3):
    pins = source_pins()
    prepared = json.loads((OUTPUT / "prepared_manifest.json").read_text())
    if prepared["status"] != "complete" or prepared["source_pins"] != pins:
        raise ValueError("Globally prepared exact source required before reading outcomes")
    if (OUTPUT / "evaluation_started.json").exists():
        raise ValueError("Refusing rescoring")
    for item in prepared["artifacts"]:
        old.checked(item["path"], item["sha256"])
    old._verify_references(prepared["sources"])
    old.write_json(OUTPUT / "evaluation_started.json", dict(source_pins=pins, prepared_sha=old.sha(OUTPUT / "prepared_manifest.json")))
    jobs = json.loads((OUTPUT / "matching.json").read_text())["jobs"]
    actual, controls, new_paths = [], [], 0
    with ProcessPoolExecutor(max_workers=workers, initializer=initialize_evaluation) as pool:
        for number, (output, new, asset) in enumerate(pool.map(score_job, jobs)):
            actual.extend(output[0])
            controls.extend(output[1])
            new_paths += new
            print("evaluated", number + 1, len(jobs), asset, "new paths", new, flush=True)
    actual, controls = pd.DataFrame(actual), pd.DataFrame(controls)
    for frame in (actual, controls):
        frame["decision_time"] = pd.to_datetime(frame.decision_time, utc=True)
    summaries, scores = [], []
    for arm in ARMS:
        for period, (start, end) in old.period_windows().items():
            a = actual[actual.arm.eq(arm) & actual.decision_time.ge(start) & actual.decision_time.lt(end)]
            c = controls[controls.matched_event_id.isin(a.event_id)]
            stats, features = old.trade_summary(a, c)
            known = a[a.forward_known.eq(True)]
            summaries.append(dict(arm=arm, period=period, **stats,
                                  forward_success=pd.to_numeric(known.forward_success).mean()))
            if period == "full":
                scores.extend(dict(arm=arm, **row) for row in features)
    summary = pd.DataFrame(summaries)
    ab_summary = pd.read_csv(AB_SOURCE / "trade_summary.csv", float_precision="round_trip")
    c_summary = pd.read_csv(CACHE_SOURCE / "trade_summary.csv", float_precision="round_trip")
    family = structural_holm(ab_summary, c_summary, summary)
    mask = summary.arm.eq("formation_gate") & summary.period.eq("full")
    summary.loc[mask, "holm_p_four"] = family[family.arm.eq("formation_gate")].holm_p_four.iloc[0]
    tables = {name: pd.read_csv(OUTPUT / (name + ".csv.gz"), float_precision="round_trip")
              for name in ("signals", "labels", "detections", "exposure")}
    for name in ("signals", "labels", "exposure"):
        tables[name]["decision_time"] = pd.to_datetime(tables[name].decision_time, utc=True)
    retention = retention_summary(tables["labels"], tables["detections"], tables["signals"], tables["exposure"])
    base = retention[retention.arm.eq("v3") & retention.period.eq("full")].set_index("stage")
    if (base.loc["early", "signals"], base.loc["confirmed", "signals"],
            base.loc["early", "hits_1"], base.loc["confirmed", "hits_1"]) != (10386, 3809, 1463, 488):
        raise ValueError("V3 baseline or fixed label denominator drift")
    original_recall = pd.read_csv(V3_RESULTS / "recall_comparison.csv", float_precision="round_trip")
    original_trades = pd.read_csv(V3_RESULTS / "trade_comparison.csv", float_precision="round_trip")
    products = {"trade_events.csv.gz": actual, "trade_controls.csv.gz": controls,
                "trade_summary.csv": summary, "score_summary.csv": pd.DataFrame(scores),
                "retention_summary.csv": retention, "structural_holm_family.csv": family,
                "version_comparison.csv": version_comparison(retention, summary, original_recall, original_trades)}
    for name, table in products.items():
        old.write_csv(OUTPUT / name, table)
    old.write_json(OUTPUT / "assessment.json", assessment(retention, summary))
    old._verify_references(prepared["sources"])
    if source_pins() != pins:
        raise ValueError("Source changed during evaluation")
    old.write_json(OUTPUT / "validation_manifest.json", dict(
        status="complete", config=CONFIG, source_pins=pins,
        prepared_sha=old.sha(OUTPUT / "prepared_manifest.json"), new_execution_paths=new_paths,
        prior_AB_validation_sha=AB_VALIDATION_SHA, prior_C_validation_sha=VALIDATION_SHA,
        artifacts=[old.artifact(OUTPUT / name) for name in list(products) + ["assessment.json"]],
        limitations=["Fourth sequential exploratory hypothesis on previously studied history, not blind OOS",
                     "A/B/C p values remain frozen; all four structural hypotheses receive Holm correction",
                     "No reference occupancy or coverage is used to substitute for timely new parents",
                     "Economics use independent parent events, not account NAV or actual reference occupancy",
                     "Original V1 is a preserved historical benchmark with its original random controls",
                     "No source, risk, cost, monitor, alert or execution configuration promoted"]))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "evaluate"))
    parser.add_argument("--workers", type=int, default=3)
    arguments = parser.parse_args()
    prepare() if arguments.phase == "prepare" else evaluate(arguments.workers)
