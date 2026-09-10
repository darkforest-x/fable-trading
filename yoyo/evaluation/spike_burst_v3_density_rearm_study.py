"""G: one density-episode quota over unchanged causal V3 conditions.

After an accepted parent, the gate requires a known density departure and
known density with all prior12 raw width/flip observations after that owner.
Bootstrap is explicit. Rejected edges do not advance accepted-only cooldown,
so both machines are actually replayed; G is not a subset filter of V3. No
A-F mechanism, delay, occupancy, execution/risk change or parameter search.
All features use current/past source bars; only frozen labels and evaluation
outcomes can use future bars. Source/plan/tests/preflight must be committed
before global preparation. Parent economics use unchanged next-open/20bp;
children are upgrades. Frozen A-F raw p values plus G receive Holm7. This
previously inspected historical pool and thirteen prior gates are not OOS.
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
from yoyo.evaluation import spike_burst_v3_density_rearm_gate as gate
from yoyo.evaluation.spike_burst_dataset import load_feature, match_controls
from yoyo.evaluation.spike_burst_execution import simulate_trade

ROOT = old.ROOT
EXPERIMENT = ROOT / "experiments/active/exp-spike-v3-density-rearm-20260910-v1"
OUTPUT = EXPERIMENT / "results"
CACHE_SOURCE = ROOT / "experiments/active/exp-spike-v3-price-acceptance-20260910-v1/results"
V3_RESULTS = v3.EXPERIMENT / "results"
PREPARED_SHA = "32abdf5316732247e4912cd17e3c7c94353ba6756a33df38c007fd7933d83a42"
VALIDATION_SHA = "a0cb43b3b7d548148938b9404d1744d85b5d1a60ccb8b35ab04edff012b5a570"
ARMS = ("v3", "density_rearm")
HISTORICAL_ARMS = ("reference", "near_box", "confirmation_gate", "formation_gate", "htf_gate", "price_acceptance")
FIXED = dict(markets=278, source_bars=837239, source_labels=14904, anchors=8046,
    positive=1660, negative=6240, unknown=146, large_positive=947, parents=10386,
    children=3809, distinct_labels=12042, timely_positive=1463, timely_children=488, large_hits=798)
CONFIG = dict(schema="spike-v3-density-rearm-v1", arms=list(ARMS),
    new_hypothesis="G: bootstrap quota, then known false and known dense with prior12 support wholly after accepted owner",
    interventions="one density quota; actual accepted-only cooldown rebuild; no A-F stacking",
    primary_economics="early accepted parents only; original age0..3 quality upgrades",
    structural_hypothesis_number=7, prior_single_gate_explorations=13,
    holm_family="all frozen A-F raw full-period p values plus new G",
    holdout_per_new_config=1, retrospective=True, exploratory=True,
    acceptance=dict(max_distinct_labels=6021, min_retained=1317, baseline_timely=1463,
                    min_large_hits=758, large_denominator=947, min_label_drop=.5, holm_p=.01),
    fixed_population=FIXED, cost_bp=20,
    execution="unchanged next-open, prior5/2ATR risk, 2R close activation and4ATR next-bar ratchet",
    production_eligible=False, training_eligible=False)
G_FIELDS = ("density_valid", "density_is_dense", "density_known_false", "density_support_complete", "density_support_finite",
    "density_summaries_finite", "density_window_start_i", "density_window_end_i",
    "quota_armed_before", "quota_armed_for_edge", "quota_armed", "bootstrap_quota", "accept_origin",
    "structure_id", "structure_owner_i", "structure_owner_before_i", "structure_bootstrap", "bootstrap_accepted",
    "structure_high", "structure_low", "structure_age_bars", "density_false_seen", "support_disjoint",
    "first_false_i", "rearmed", "rearm_i", "rearm_window_start_i", "rearm_window_end_i",
    "rearm_window_high", "rearm_window_low", "rearm_owner_i", "rearm_false_i", "rearm_wait_bars", "structure_blocked", "reject_reason", "last_accepted_i", "gap_reset")
CONTEXT = ("instrument", "asset", "symbol", "venue", "minutes")
BASE_FIELDS = ("early", "confirmed", "parent_i", "frozen_parent_high", "confirm_age", "candidate_edge", "cooldown_blocked")


def source_pins():
    """Actual passing review and exact reviewed bytes must precede any phase."""
    core = [Path(__file__), ROOT / "yoyo/evaluation/spike_burst_v3_density_rearm_gate.py",
            ROOT / "tests/test_spike_burst_v3_density_rearm_study.py",
            ROOT / "tests/test_spike_burst_v3_density_rearm_gate.py", EXPERIMENT / "PROJECT_PLAN.md"]
    review = EXPERIMENT / "qa/preflight_review.json"
    paths = core + [review] + [ROOT / "yoyo/evaluation" / name for name in (
        "spike_burst_early_warning.py", "spike_burst_replay.py", "spike_burst_progressive.py",
        "spike_burst_execution.py", "spike_burst_dataset.py", "spike_burst_recall_study.py", "altseason_research.py")]
    pins = {}
    for path in paths:
        relative = str(path.resolve().relative_to(ROOT))
        if subprocess.check_output(["git", "show", "HEAD:" + relative], cwd=ROOT) != path.read_bytes():
            raise ValueError("Commit exact source/preflight before running: " + relative)
        pins[relative] = old.sha(path)
    qa = json.loads(review.read_text())
    if qa.get("status") != "passed":
        raise ValueError("Actual passing preflight required")
    for path in core:
        relative = str(path.resolve().relative_to(ROOT))
        if qa.get("source_sha256", {}).get(relative) != pins[relative]:
            raise ValueError("Preflight does not cover exact source: " + relative)
    return pins


def validate_labels(labels):
    selected = labels[labels.decision_time.ge(old.START) & labels.decision_time.lt(old.END)]
    counts = dict(source_labels=len(labels), anchors=len(selected),
        positive=int(selected.label.eq("positive").sum()), negative=int(selected.label.eq("negative").sum()),
        unknown=int(selected.label.eq("unknown").sum()),
        large_positive=int((selected.label.eq("positive") & selected.large_peak.eq(True)).sum()))
    if labels.duplicated(["instrument", "event_i"]).any() or any(counts[k] != FIXED[k] for k in counts):
        raise ValueError("Frozen label population/identity changed")
    return counts


def frozen_inputs():
    """Authenticate F and all transitive lineage; do not parse outcome tables."""
    refs, receipts = {}, []
    def check(path, digest):
        path = old.checked(path, digest)
        name = str(path)
        if name in refs and refs[name] != digest:
            raise ValueError("Conflicting historical source hash")
        refs[name] = digest
        return path
    for name, digest in (("prepared_manifest.json", PREPARED_SHA), ("validation_manifest.json", VALIDATION_SHA)):
        receipt = json.loads(check(CACHE_SOURCE / name, digest).read_text())
        if receipt.get("status") != "complete":
            raise ValueError("Incomplete frozen F source")
        for item in receipt["artifacts"]:
            check(item["path"], item["sha256"])
        receipts.append(receipt)
    prepared, validation = receipts
    if (validation["prepared_sha"] != PREPARED_SHA or prepared["config"] != validation["config"]
            or prepared["source_pins"] != validation["source_pins"] or prepared["config"]["cost_bp"] != 20):
        raise ValueError("Historical F execution/schedule contract differs")
    for path, digest in prepared["sources"].items():
        check(path, digest)
    for relative, digest in prepared["source_pins"].items():
        check(ROOT / relative, digest)
    # The exact same execution mathematics is required for cached path reuse.
    for name in ("spike_burst_execution.py", "spike_burst_replay.py"):
        rel = "yoyo/evaluation/" + name
        if prepared["source_pins"].get(rel) != old.sha(ROOT / rel):
            raise ValueError("Cached execution-source contract differs")
    required = [CACHE_SOURCE / name for name in ("matching.json", "signals.csv.gz", "labels.csv.gz",
        "detections.csv.gz", "trade_events.csv.gz", "trade_controls.csv.gz", "structural_holm_family.csv")]
    required += [V3_RESULTS / name for name in ("recall_comparison.csv", "trade_comparison.csv")]
    if any(str(path.resolve()) not in refs for path in required):
        raise ValueError("Missing pinned baseline/cache/Holm artifact")
    matching = json.loads((CACHE_SOURCE / "matching.json").read_text())
    jobs = matching["jobs"]
    if matching["config"] != prepared["config"] or len(jobs) != FIXED["markets"]:
        raise ValueError("Historical F market/config schedule changed")
    if len({j["instrument"] for j in jobs}) != len(jobs) or any(j["minutes"] != 60 or j["asset"] in ("BTC", "ETH") for j in jobs):
        raise ValueError("Frozen 1H altcoin population changed")
    labels = pd.read_csv(CACHE_SOURCE / "labels.csv.gz", float_precision="round_trip")
    for key in ("decision_time", "bar_open"):
        labels[key] = pd.to_datetime(labels[key], utc=True)
    validate_labels(labels)
    return jobs, labels, refs


def economic_parents(signals):
    """No future confirmation status is used to select early economic parents."""
    if not signals.stage.isin(("early", "confirmed")).all():
        raise ValueError("Unknown signal stage")
    selected = signals[signals.stage.eq("early")].copy()
    if selected.event_id.duplicated().any():
        raise ValueError("Duplicate economic parent identity")
    return selected


def event_metadata(frame, state, stage, i):
    """Freeze accepted-parent provenance at p, never read future child/rearm rows."""
    p = int(state.parent_i.iloc[i])
    if p < 0 or p > i or not bool(state.early.iloc[p]) or not 0 <= i-p <= 3:
        raise ValueError("Event has no accepted causal parent in original age0..3")
    if stage == "early" and i != p:
        raise ValueError("No delayed/reassigned economic parent allowed")
    if stage == "confirmed" and (not bool(state.confirmed.iloc[i]) or state.confirm_age.iloc[i] != i-p):
        raise ValueError("Child must keep its real original confirmation age")
    clocks = frame.index + old.HOUR
    result = dict(parent_i=p, economic_parent_i=p, parent_decision_time=clocks[p],
                  parent_close=float(frame.close.iloc[p]), publication_i=i, publication_time=clocks[i])
    for key in G_FIELDS:
        if key in state:
            value = state[key].iloc[p]
            result["parent_" + key] = value.item() if isinstance(value, np.generic) else value
    if "rearm_i" in state and pd.notna(state.rearm_i.iloc[p]):
        if not 0 <= state.rearm_i.iloc[p] <= p:
            raise ValueError("Rearm provenance cannot come from future")
    return result


def causal_controls(frame, machines, instrument):
    """One shared schedule: exclude only current/past12 events across both arms."""
    clocks = frame.index + old.HOUR
    window = (clocks >= old.START) & (clocks < old.END)
    union = sorted({int(i) for state in machines.values() for stage in ("early", "confirmed")
                    for i in np.flatnonzero(state[stage].to_numpy())})
    parents = sorted({int(i) for state in machines.values() for i in np.flatnonzero(state.early.to_numpy()) if window[i]})
    matcher = frame.copy()
    matcher["history_count"] = np.where(frame.ready, frame.history_count, 0)
    return match_controls(matcher, union, parents, instrument, 60, old.START, old.END)


def validate_baseline(signals, labels, detections, exposure):
    """Fail during preparation, before any economic outcomes may be read."""
    validate_labels(labels)
    r = retention_summary(labels, detections, signals, exposure)
    b = r[r.arm.eq("v3") & r.period.eq("full")].set_index("stage")
    counts = dict(parents=int(b.loc["early", "signals"]), children=int(b.loc["confirmed", "signals"]),
        distinct_labels=int(b.loc["early", "distinct_labels"]), timely_positive=int(b.loc["early", "hits_1"]),
        timely_children=int(b.loc["confirmed", "hits_1"]), large_hits=int(b.loc["early", "large_hits_1"]))
    if any(counts[k] != FIXED[k] for k in counts):
        raise ValueError("Frozen V3 baseline/count/clock drift")
    return counts


def rearm_summary(raw_edges, exposure, signals):
    """Observed gate counts and accepted-parent origins, never counterfactual coverage."""
    rows = []
    for period, (start, end) in old.period_windows().items():
        edges = raw_edges[raw_edges.decision_time.ge(start) & raw_edges.decision_time.lt(end)]
        x = exposure[exposure.decision_time.ge(start) & exposure.decision_time.lt(end)]
        p = signals[signals.arm.eq("density_rearm") & signals.stage.eq("early")
                    & signals.decision_time.ge(start) & signals.decision_time.lt(end)]
        rows.append(dict(period=period, raw_edges=len(edges), v3_accepted=int(edges.v3_early.sum()),
            g_accepted=int(edges.g_early.sum()), quota_blocked=int(edges.structure_blocked.sum()),
            cooldown_blocked=int(edges.cooldown_blocked.sum()),
            bootstrap_parents=int(p.parent_accept_origin.eq("bootstrap").sum()),
            rearmed_parents=int(p.parent_accept_origin.eq("density_rearm").sum()),
            density_unknown_raw_edges=int((~edges.density_valid).sum()),
            density_unknown_asset_hours=int(x.density_unknown.sum()), rearm_observations=int(x.rearmed.sum()),
            density_unknown_locked_asset_hours=int(x.density_unknown_locked.sum()),
            quota_locked_asset_hours=int(x.quota_locked.sum()),
            accepted_rearm_wait_median=pd.to_numeric(p.parent_rearm_wait_bars, errors="coerce").median(),
            rejections={str(k):int(v) for k,v in edges.loc[~edges.g_early, "reject_reason"].value_counts().items()},
            coverage_credit=False))
    return pd.DataFrame(rows)


def structure_spans(frame, state, context):
    """Posterior observed ownership spans only; never used by detection or fills.

    Next accepted parent ends the prior observed identity. Gaps and source tail
    censor it; no economic exit or future successful trend defines the end.
    """
    rows=[]
    owners=np.flatnonzero(state.early.to_numpy())
    clocks=frame.index+old.HOUR
    gaps=np.flatnonzero(state.gap_reset.to_numpy())
    for number,p in enumerate(owners):
        later=gaps[gaps>p]
        next_parent=int(owners[number+1]) if number+1<len(owners) else len(frame)
        next_gap=int(later[0]) if len(later) else len(frame)
        end=min(next_parent,next_gap,len(frame))
        reason="new_owner" if end==next_parent and end<len(frame) else "gap_censored" if end==next_gap and end<len(frame) else "sample_end_censored"
        rows.append(dict(context,owner_i=int(p),owner_time=clocks[p],accept_origin=state.accept_origin.iloc[p],
            frozen_high=state.structure_high.iloc[p],frozen_low=state.structure_low.iloc[p],
            observed_last_i=end-1,observed_last_time=clocks[end-1],observed_bars=end-int(p),
            transition_i=end if end<len(frame) else np.nan,
            transition_time=clocks[end] if end<len(frame) else pd.NaT,
            span_end_reason=reason,censored=reason!="new_owner",descriptive_only=True,
            in_study=bool(old.START<=clocks[p]<old.END)))
    return pd.DataFrame(rows,columns=list(CONTEXT)+["owner_i","owner_time","accept_origin","frozen_high","frozen_low",
        "observed_last_i","observed_last_time","observed_bars","transition_i","transition_time","span_end_reason",
        "censored","descriptive_only","in_study"])


def prepare():
    pins = source_pins()
    if OUTPUT.exists() and any(OUTPUT.iterdir()):
        raise ValueError("Refusing overwrite or rescoring")
    jobs, labels, refs = frozen_inputs()
    prior_signals = pd.read_csv(CACHE_SOURCE / "signals.csv.gz", float_precision="round_trip")
    OUTPUT.mkdir(parents=True)
    (OUTPUT / "states").mkdir()
    old.write_json(OUTPUT / "started.json", dict(config=CONFIG, source_pins=pins))
    signals, parents, controls, detections, exposures, states, edges, spans = [], [], [], [], [], [], [], []
    total_bars = 0
    for n, job in enumerate(jobs):
        frame = load_feature(job["features_path"], job["features_sha256"], 60, old.END)
        total_bars += len(frame)
        refs[job["features_path"]] = job["features_sha256"]
        machines = {"v3": v3.detect(frame), "density_rearm": gate.detect(frame)}
        g = machines["density_rearm"]
        if not set(G_FIELDS + BASE_FIELDS).issubset(g.columns):
            raise ValueError("G detector must expose full density quota provenance")
        for state in machines.values():
            if not state.index.equals(frame.index):
                raise ValueError("Detector clock alignment drift")
        if not np.array_equal(g.candidate_edge.to_numpy(), machines["v3"].candidate_edge.to_numpy()):
            raise ValueError("G cannot change original raw-condition edges")
        context = {k:job[k] for k in CONTEXT}
        spans.append(structure_spans(frame,g,context))
        clocks = frame.index + old.HOUR
        window = (clocks >= old.START) & (clocks < old.END)
        random = causal_controls(frame, machines, job["instrument"])
        local_labels = labels[labels.instrument.eq(job["instrument"])]
        for i in np.flatnonzero(g.candidate_edge.to_numpy()):
            i = int(i)
            record = dict(context, decision_i=i, decision_time=clocks[i], in_study=bool(window[i]),
                v3_early=bool(machines["v3"].early.iloc[i]), g_early=bool(g.early.iloc[i]),
                cooldown_blocked=bool(g.cooldown_blocked.iloc[i]))
            record.update({k:g[k].iloc[i] for k in G_FIELDS})
            edges.append(record)
        saved = pd.concat([pd.DataFrame(dict(decision_i=np.arange(len(frame)), decision_time=clocks))] +
                          [state.reset_index(drop=True).add_prefix(arm+"_") for arm,state in machines.items()], axis=1)
        for arm, state in machines.items():
            for stage in ("early", "confirmed"):
                indices = np.flatnonzero(state[stage].to_numpy())
                if arm == "v3":
                    prior = prior_signals[prior_signals.instrument.eq(job["instrument"])
                        & prior_signals.arm.eq("v3") & prior_signals.stage.eq(stage)]
                    if not np.array_equal(indices[window[indices]], prior.decision_i.to_numpy(int)):
                        raise ValueError("Frozen V3 baseline sequence drift")
                    for i, row in zip(indices[window[indices]], prior.itertuples()):
                        if (pd.Timestamp(row.decision_time) != clocks[i] or row.signal_close != frame.close.iloc[i]
                                or int(row.parent_i) != int(state.parent_i.iloc[i])):
                            raise ValueError("Frozen V3 baseline metadata drift")
                dummy = pd.DataFrame(dict(trend_side=np.zeros(len(frame), dtype=int), burst=state[stage]), index=frame.index)
                matched, detected = old.match_signals(local_labels, indices, frame, dummy)
                detected["arm"], detected["stage"], detected["instrument"] = arm, stage, job["instrument"]
                detections.append(detected)
                matchmap = matched.set_index("decision_i").to_dict("index")
                for i in indices:
                    i = int(i)
                    eid = old.identity(job["instrument"], arm+"_"+stage, i)
                    metadata = event_metadata(frame, state, stage, i)
                    if stage == "early":
                        parents.append(dict(context, arm=arm, event_id=eid, decision_i=i,
                            decision_time=clocks[i], in_study=bool(window[i]), **metadata))
                    if not window[i]:
                        continue
                    m = matchmap[i]
                    signals.append(dict(context, arm=arm, stage=stage, event_id=eid,
                        decision_i=i, decision_time=clocks[i], bar_open=frame.index[i], **metadata,
                        parent_event_id=old.identity(job["instrument"], arm+"_early", metadata["parent_i"]),
                        signal_close=float(frame.close.iloc[i]), signal_atr=float(frame.atr.iloc[i]),
                        relative_volume=float(frame.rv.iloc[i]), tr_expansion=float(frame.expansion.iloc[i]),
                        frozen_parent_high=float(state.frozen_parent_high.iloc[i]), confirm_age=state.confirm_age.iloc[i],
                        match_status=m["match_status"], label_i=m["label_i"], lag=m["lag"]))
                    if stage == "early":
                        for number, ri in enumerate(random[i]):
                            controls.append(dict(context, arm=arm, stage=stage, event_id=old.identity(eid,"control",number),
                                matched_event_id=eid, control_number=number, decision_i=int(ri), decision_time=clocks[int(ri)]))
        path = OUTPUT / "states" / ("%03d_%s.csv.gz"%(n, job["asset"]))
        old.write_csv(path, saved)
        states.append(old.artifact(path))
        exposures.append(pd.DataFrame(dict(decision_time=clocks[window], eligible=frame.ready.to_numpy()[window].astype(int),
            density_unknown=(~g.density_valid.to_numpy()[window]).astype(int),
            density_unknown_locked=((~g.density_valid.to_numpy()[window]) & (~g.quota_armed.to_numpy()[window])).astype(int),
            rearmed=g.rearmed.to_numpy()[window].astype(int), quota_locked=(~g.quota_armed.to_numpy()[window]).astype(int))))
        print("prepared",n+1,len(jobs),job["asset"],flush=True)
    if total_bars != FIXED["source_bars"]:
        raise ValueError("Frozen total source bar count drift")
    # Explicit schemas keep all-rejected or zero-control configurations readable.
    signal_columns = list(CONTEXT)+["arm","stage","event_id","decision_i","decision_time","bar_open","parent_i",
        "economic_parent_i","parent_decision_time","parent_close","publication_i","publication_time","parent_event_id",
        "signal_close","signal_atr","relative_volume","tr_expansion","frozen_parent_high","confirm_age",
        "match_status","label_i","lag"]+["parent_"+k for k in G_FIELDS]
    products={"signals.csv.gz":pd.DataFrame(signals).reindex(columns=signal_columns),
        "parent_registry.csv.gz":pd.DataFrame(parents),
        "structure_spans.csv.gz":pd.concat(spans,ignore_index=True),
        "controls.csv.gz":pd.DataFrame(controls,columns=list(CONTEXT)+["arm","stage","event_id","matched_event_id",
            "control_number","decision_i","decision_time"]),
        "detections.csv.gz":pd.concat(detections,ignore_index=True),
        "raw_edges.csv.gz":pd.DataFrame(edges,columns=list(CONTEXT)+["decision_i","decision_time","in_study",
            "v3_early","g_early","cooldown_blocked"]+list(G_FIELDS)),
        "exposure.csv.gz":pd.concat(exposures).groupby("decision_time",as_index=False).sum()}
    baseline=validate_baseline(products["signals.csv.gz"],labels,products["detections.csv.gz"],products["exposure.csv.gz"])
    products["rearm_summary.csv"]=rearm_summary(products["raw_edges.csv.gz"],products["exposure.csv.gz"],products["signals.csv.gz"])
    for name,table in products.items():
        old.write_csv(OUTPUT/name,table)
    old.write_json(OUTPUT/"baseline_contract.json",dict(baseline,source_bars=total_bars,markets=len(jobs)))
    shutil.copyfile(CACHE_SOURCE/"labels.csv.gz",OUTPUT/"labels.csv.gz")
    old.write_json(OUTPUT/"matching.json",dict(jobs=jobs,config=CONFIG,state_artifacts=states))
    old._verify_references(refs)
    if source_pins()!=pins:
        raise ValueError("Builder changed during preparation")
    old.write_json(OUTPUT/"prepared_manifest.json",dict(status="complete",config=CONFIG,source_pins=pins,sources=refs,
        artifacts=[old.artifact(OUTPUT/name) for name in list(products)+["labels.csv.gz","matching.json","baseline_contract.json"]]+states))


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
                source = "current V3/G common controls; parent-only economics"
            rows.append(dict(arm=arm, period=period, parent_signals=r.signals, distinct_labels=labels,
                positive_events=r.positive_events, hits_1=r.hits_1, recall_1=r.recall_1,
                large_recall_1=r.large_recall_1, v3_fresh_hit_retention=fresh_retention,
                natural_win_rate=t.natural_win_rate, mean_net_bp=t.mean_net_bp,
                mean_excess_bp=t.mean_excess_bp, control_comparable_to_new_G=arm != "v1", source=source,
                target_label_drop=.5, target_large_recall=.8, target_v3_fresh_retention=.9))
    return pd.DataFrame(rows)


OUTCOME_KEYS = ("valid invalid_reason reason exit_reason signal_i decision_i entry_i exit_i decision_time entry_time exit_time "
    "exit_time_lower exit_time_upper exit_timing exit_time_exact entry_price exit_price initial_stop initial_risk initial_risk_frac "
    "net_return net_r peak_r natural_exit censored hold_bars fee_return fee_bp signal_close signal_atr exit_at_open mfe_r mfe_return "
    "held_hours_lower held_hours_upper hold_seconds gross_return gross_bp net_bp gross_r final_protection trail_armed peak_return").split()



def structural_holm(frozen_family, current_summary):
    """Exactly six frozen raw p values plus G; never re-use adjusted p as raw."""
    if set(frozen_family.arm) != set(HISTORICAL_ARMS) or frozen_family.arm.duplicated().any():
        raise ValueError("Exactly one frozen A-F hypothesis required")
    rows=[]
    for arm in HISTORICAL_ARMS:
        r=frozen_family[frozen_family.arm.eq(arm)].iloc[0]
        p=r.raw_p
        if pd.notna(p) and (not np.isfinite(p) or not 0<=p<=1):
            raise ValueError("Historical raw p outside [0,1]")
        rows.append(dict(arm=arm,origin="frozen_"+arm,raw_p=p,correction_input_p=float(p) if pd.notna(p) else 1.))
    g=current_summary[current_summary.arm.eq("density_rearm") & current_summary.period.eq("full")]
    if len(g)!=1:
        raise ValueError("Exactly one full-period G p required")
    p=g.permutation_p.iloc[0]
    if pd.notna(p) and (not np.isfinite(p) or not 0<=p<=1):
        raise ValueError("G raw p outside [0,1]")
    rows.append(dict(arm="density_rearm",origin="seventh_exploratory_hypothesis_G",raw_p=p,
                     correction_input_p=float(p) if pd.notna(p) else 1.))
    family=pd.DataFrame(rows)
    family["holm_p_seven"]=v3.holm(family.correction_input_p.to_numpy())
    family["interpretation"]="seven sequential exploratory hypotheses plus thirteen prior gates; not blind OOS"
    return family


def assessment(retention,trades):
    r=retention[retention.arm.eq("density_rearm") & retention.stage.eq("early") & retention.period.eq("full")].iloc[0]
    t=trades[trades.arm.eq("density_rearm") & trades.period.eq("full")].iloc[0]
    if r.baseline_hit_events!=1463 or r.large_positive_events!=947 or r.positive_events!=1660 or r.baseline_signals!=10386:
        raise ValueError("Acceptance denominator must retain original V3 population")
    return dict(label_drop_pass=bool(r.distinct_labels<=6021 and r.label_drop>=.5),
        baseline_retention_pass=bool(r.retained_hit_events>=1317),
        large_recall_pass=bool(r.large_hits_1>=758),
        economic_pass=bool(t.mean_excess_bp>0 and t.holm_p_seven<.01),
        thresholds=CONFIG["acceptance"],account_nav_not_computed=True,live_deployed=False,
        accepted_for_deployment=False,training_eligible=False,production_eligible=False,
        interpretation="Single density-quota hypothesis; fixed original fresh denominator; no coverage substitution")


def prepared_contract(verify_all=True):
    """All entry points fail before outcome reads unless global prepare exists."""
    pins=source_pins()
    path=OUTPUT/"prepared_manifest.json"
    if not path.exists():
        raise ValueError("Globally prepared schedule required before outcomes")
    prepared=json.loads(path.read_text())
    if prepared.get("status")!="complete" or prepared.get("config")!=CONFIG or prepared.get("source_pins")!=pins:
        raise ValueError("Globally prepared exact source/config required before outcomes")
    if verify_all:
        for a in prepared["artifacts"]:old.checked(a["path"],a["sha256"])
        old._verify_references(prepared["sources"])
    return prepared,pins


_CACHE={}
_CACHE_SIGNATURES={}
_SIGNALS=_CONTROLS=None
_EVALUATION_PREPARED_SHA=None


def job_signature(job):
    return (job["features_sha256"],float(job["tick"]),int(job["minutes"]),old.END.isoformat())


def assert_cache_signature(job,signatures):
    if signatures.get(job["instrument"])!=job_signature(job):
        raise ValueError("Cached outcome source/tick/timeframe/cutoff mismatch")


def _same_outcome(left,right):
    if set(left)!=set(right):return False
    return all((pd.isna(left[k]) and pd.isna(right[k])) or left[k]==right[k] for k in left)


def initialize_evaluation():
    global _SIGNALS,_CONTROLS,_CACHE,_CACHE_SIGNATURES,_EVALUATION_PREPARED_SHA
    prepared,_=prepared_contract(verify_all=False)
    if not (OUTPUT/"evaluation_started.json").exists():
        raise ValueError("Evaluation must be explicitly started after prepare")
    current_sha=old.sha(OUTPUT/"prepared_manifest.json")
    started=json.loads((OUTPUT/"evaluation_started.json").read_text())
    if started.get("prepared_sha")!=current_sha or started.get("source_pins")!=prepared["source_pins"]:
        raise ValueError("Evaluation receipt differs from prepared schedule")
    hashes=dict(prepared["sources"])
    hashes.update({a["path"]:a["sha256"] for a in prepared["artifacts"]})
    def read(name,source=OUTPUT):
        p=(source/name).resolve()
        if str(p) not in hashes:raise ValueError("Unpinned evaluation input")
        old.checked(p,hashes[str(p)])
        return p
    _SIGNALS=economic_parents(pd.read_csv(read("signals.csv.gz"),float_precision="round_trip"))
    _CONTROLS=pd.read_csv(read("controls.csv.gz"),float_precision="round_trip")
    if not _CONTROLS.stage.eq("early").all():
        raise ValueError("Quality upgrade cannot create control entries")
    cached_jobs=json.loads(read("matching.json",CACHE_SOURCE).read_text())["jobs"]
    _CACHE_SIGNATURES={j["instrument"]:job_signature(j) for j in cached_jobs}
    if len(_CACHE_SIGNATURES)!=len(cached_jobs):raise ValueError("Duplicate cached instrument")
    _CACHE={}
    for name in ("trade_events.csv.gz","trade_controls.csv.gz"):
        for row in pd.read_csv(read(name,CACHE_SOURCE),float_precision="round_trip").to_dict("records"):
            key=(row["instrument"],int(row["decision_i"]))
            outcome={k:row[k] for k in OUTCOME_KEYS if k in row}
            if key in _CACHE and not _same_outcome(_CACHE[key],outcome):
                raise ValueError("Conflicting cached outcome for same source/decision")
            _CACHE[key]=outcome
    _EVALUATION_PREPARED_SHA=current_sha


def score_job(job):
    if _EVALUATION_PREPARED_SHA is None or _SIGNALS is None or _CONTROLS is None:
        raise ValueError("Prepared evaluation initialization required before any price path")
    assert_cache_signature(job,_CACHE_SIGNATURES)
    frame=load_feature(job["features_path"],job["features_sha256"],60,old.END)
    local,outputs,new={},[],0
    for table in (_SIGNALS,_CONTROLS):
        rows=[]
        for row in table[table.instrument.eq(job["instrument"])].to_dict("records"):
            i=int(row["decision_i"]);key=(job["instrument"],i)
            if i not in local:
                outcome=dict(_CACHE[key]) if key in _CACHE else simulate_trade(frame,i,float(job["tick"]),old.END)
                if key not in _CACHE:new+=1
                local[i]=dict(outcome,**v3.forward_outcome(frame,i))
            rows.append(dict(row,**local[i],relative_volume=float(frame.rv.iloc[i]),tr_expansion=float(frame.expansion.iloc[i])))
        outputs.append(rows)
    return outputs,new,job["asset"]


def evaluate(workers=3):
    if not isinstance(workers,int) or workers<1:raise ValueError("Positive worker count required")
    prepared,pins=prepared_contract()
    if (OUTPUT/"evaluation_started.json").exists() or (OUTPUT/"validation_manifest.json").exists():
        raise ValueError("Refusing rescoring or partial evaluation overwrite")
    old.write_json(OUTPUT/"evaluation_started.json",dict(source_pins=pins,prepared_sha=old.sha(OUTPUT/"prepared_manifest.json")))
    jobs=json.loads((OUTPUT/"matching.json").read_text())["jobs"]
    actual,controls,new_paths=[],[],0
    with ProcessPoolExecutor(max_workers=workers,initializer=initialize_evaluation) as pool:
        for number,(output,new,asset) in enumerate(pool.map(score_job,jobs)):
            actual.extend(output[0]);controls.extend(output[1]);new_paths+=new
            print("evaluated",number+1,len(jobs),asset,"new paths",new,flush=True)
    signal_cols=list(pd.read_csv(OUTPUT/"signals.csv.gz",nrows=0).columns)
    control_cols=list(pd.read_csv(OUTPUT/"controls.csv.gz",nrows=0).columns)
    outcome_cols=OUTCOME_KEYS+["forward_known","forward_success","relative_volume","tr_expansion"]
    actual=pd.DataFrame(actual).reindex(columns=list(dict.fromkeys(signal_cols+outcome_cols)))
    controls=pd.DataFrame(controls).reindex(columns=list(dict.fromkeys(control_cols+outcome_cols)))
    for frame in (actual,controls):frame["decision_time"]=pd.to_datetime(frame.decision_time,utc=True)
    summaries,scores=[],[]
    for arm in ARMS:
        for period,(start,end) in old.period_windows().items():
            a=actual[actual.arm.eq(arm)&actual.decision_time.ge(start)&actual.decision_time.lt(end)]
            c=controls[controls.matched_event_id.isin(a.event_id)]
            stats,features=old.trade_summary(a,c)
            known=a[a.forward_known.eq(True)]
            summaries.append(dict(arm=arm,period=period,**stats,forward_success=pd.to_numeric(known.forward_success).mean()))
            if period=="full":scores.extend(dict(arm=arm,**row) for row in features)
    summary=pd.DataFrame(summaries)
    frozen_family=pd.read_csv(CACHE_SOURCE/"structural_holm_family.csv",float_precision="round_trip")
    family=structural_holm(frozen_family,summary)
    mask=summary.arm.eq("density_rearm")&summary.period.eq("full")
    summary.loc[mask,"holm_p_seven"]=family[family.arm.eq("density_rearm")].holm_p_seven.iloc[0]
    tables={name:pd.read_csv(OUTPUT/(name+".csv.gz"),float_precision="round_trip")
            for name in ("signals","labels","detections","exposure")}
    for name in ("signals","labels","exposure"):tables[name]["decision_time"]=pd.to_datetime(tables[name].decision_time,utc=True)
    baseline=validate_baseline(tables["signals"],tables["labels"],tables["detections"],tables["exposure"])
    expected=json.loads((OUTPUT/"baseline_contract.json").read_text())
    if any(expected[k]!=v for k,v in baseline.items()):raise ValueError("Baseline differs between prepare/evaluate")
    retention=retention_summary(tables["labels"],tables["detections"],tables["signals"],tables["exposure"])
    original_recall=pd.read_csv(V3_RESULTS/"recall_comparison.csv",float_precision="round_trip")
    original_trades=pd.read_csv(V3_RESULTS/"trade_comparison.csv",float_precision="round_trip")
    products={"trade_events.csv.gz":actual,"trade_controls.csv.gz":controls,"trade_summary.csv":summary,
        "score_summary.csv":pd.DataFrame(scores),"retention_summary.csv":retention,"structural_holm_family.csv":family,
        "version_comparison.csv":version_comparison(retention,summary,original_recall,original_trades)}
    for name,table in products.items():old.write_csv(OUTPUT/name,table)
    old.write_json(OUTPUT/"assessment.json",assessment(retention,summary))
    old._verify_references(prepared["sources"])
    for a in prepared["artifacts"]:old.checked(a["path"],a["sha256"])
    if source_pins()!=pins:raise ValueError("Source changed during evaluation")
    old.write_json(OUTPUT/"validation_manifest.json",dict(status="complete",config=CONFIG,source_pins=pins,
        prepared_sha=old.sha(OUTPUT/"prepared_manifest.json"),new_execution_paths=new_paths,prior_F_validation_sha=VALIDATION_SHA,
        artifacts=[old.artifact(OUTPUT/name) for name in list(products)+["assessment.json"]],
        limitations=["G and A-F are seven sequential exploratory hypotheses plus thirteen prior gate explorations; not blind OOS",
        "Frozen A-F raw p values are not rescored; Holm7 includes all six older hypotheses",
        "Rearmed provenance and stage spans never substitute for fresh timely detection",
        "Independent parent events overlap; no account NAV/MDD computed",
        "Original V1 remains its historical benchmark and random schedule, not the new shared controls",
        "Latest V4 confirmation-only UI does not change this original parent-based research",
        "No trained model, source, risk, cost, monitor, notification, ACTIVE or execution configuration promoted"]))


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase",choices=("prepare","evaluate"))
    parser.add_argument("--workers",type=int,default=3)
    args=parser.parse_args()
    prepare() if args.phase=="prepare" else evaluate(args.workers)
