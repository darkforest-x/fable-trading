"""Frozen structural early warning followed by a causal three-bar confirmation.

Research only: closed OKX 1H bars from authenticated V1 features and unchanged
V2 event labels. The early alert uses ready, current close, prior12 highs and
current SMA20/EMA20. Its rising edge and twelve-bar cooldown are independent of
any reference holding. A child can confirm on parent age0..3 using its frozen
range high, prior density, three-bar advance/volume and current MD/SB/ZLEMA.
Quality tags are descriptive and never silently act as early-alert filters.

All rolling windows and parent/cooldown state reset at missing-hour boundaries.
Same-close universe breadth uses only valid observed current/prior prices and
fast MAs, not future returns. Future24-bar barriers appear only in outcome
columns. This is a previously inspected retrospective pool, not a blind test.
prepare freezes both arms and their random controls globally before evaluate
may simulate unchanged next-open trades with the historical20bp cost contract.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_burst_recall_study as old
from yoyo.evaluation.spike_burst_progressive import progressive_fields
from yoyo.evaluation.spike_burst_dataset import load_feature, match_controls
from yoyo.evaluation.spike_burst_execution import simulate_trade
from yoyo.evaluation.altseason_research import holm

ROOT, HOUR, START, END = old.ROOT, old.HOUR, old.START, old.END
EXPERIMENT = ROOT / "experiments/active/exp-spike-burst-early-warning-20260910-v3"
V2 = old.EXPERIMENT / "results"
V2_PREPARED_SHA = "bfc3f89a6b0f3dd2fba20832fde8d414f2656316bcc6c583bb00d2ce5cf1d94f"
V2_VALIDATION_SHA = "7a88b0f3603281d051c368c1f59d5a0def18f0d2636fa07e40e634b1428827a9"
ARMS = ("early", "confirmed")
CONFIG = dict(old.CONFIG, schema="spike-early-warning-v3", arms=list(ARMS),
    early="ready & close>prior12high & close>max(SMA20,EMA20); rising edge",
    early_cooldown_bars=12, confirmation_max_age=3,
    confirmation="close>parentHigh & recentDense & advance>=1.5 & volume>=1.5 & md>=sb & middleRising",
    holding_suppression=False, breadth="same-confirmed-hour descriptive only; valid denominator",
    labels="exact authenticated V2 labels, never regenerated", primary_tests=2)
TAGS = ("tag_recent_density", "tag_above_six", "tag_md_ge_signal", "tag_middle_rising",
        "tag_advance", "tag_volume", "tag_efficiency", "tag_close_position")


def early_fields(frame):
    """Causal fields from OHLCV, ready, s20/e20 and existing V1 fields.

    Each contiguous hour segment computes prior12 range/density and three-bar
    progress afresh; no forward fill bridges a gap. Prior volume baseline20
    skips missing volume as the existing Pine contract does within a segment.
    """
    index = old._clock(frame)
    if not frame.columns.is_unique or not {"s20", "e20"}.issubset(frame):
        raise ValueError("Unique columns including s20/e20 required")
    segments = np.r_[0, np.cumsum(np.diff(index.asi8) != HOUR.value)] if len(frame) else []
    pieces = []
    for _, part in frame.groupby(segments, sort=False):
        fields = progressive_fields(part)
        # Existing pastWidth/pastCrosses are prior12 summaries. Do not let a
        # carried input summary from before a gap seed the new density window.
        fields.iloc[:12, fields.columns.get_loc("prog_dense")] = False
        fields["prog_dense_hits"] = fields.prog_dense.astype(float).shift().rolling(12, min_periods=12).sum()
        fields["prog_recent_dense"] = fields.prog_dense_hits.gt(0)
        fields["fast_high"] = part[["s20", "e20"]].max(axis=1, skipna=False)
        fields["early_condition"] = (part.ready.eq(True) & part.close.gt(fields.prog_prior_high)
                                     & part.close.gt(fields.fast_high))
        fields["tag_recent_density"] = fields.prog_recent_dense
        fields["tag_above_six"] = part.close.gt(part.ropeHigh)
        fields["tag_md_ge_signal"] = part.md.ge(part.sb)
        fields["tag_middle_rising"] = part.middle.gt(part.middle.shift())
        fields["tag_advance"] = fields.prog_advance.ge(1.5)
        fields["tag_volume"] = fields.prog_volume_ratio.ge(1.5)
        fields["tag_efficiency"] = fields.prog_efficiency.ge(.55)
        fields["tag_close_position"] = fields.prog_close_position.ge(.65)
        fields["breadth_valid"] = (part.ready.eq(True) & np.isfinite(part.close)
            & np.isfinite(part.close.shift()) & part.close.shift().gt(0)
            & np.isfinite(fields.fast_high))
        fields["breadth_above_fast"] = part.close.gt(fields.fast_high) & fields.breadth_valid
        fields["breadth_positive_return"] = part.close.gt(part.close.shift()) & fields.breadth_valid
        fields["breadth_joint"] = fields.breadth_above_fast & fields.breadth_positive_return
        pieces.append(fields)
    if not pieces:
        raise ValueError("At least one source bar is required")
    return pd.concat(pieces).reindex(frame.index)


def detect(frame):
    """Return independent parent/child alerts with original close timestamps.

    Columns parent_i and frozen_parent_high remain present only while the child
    window is open; confirm_age is populated only on actual confirmation. A
    failed age3 parent expires without backfilling an earlier marker.
    """
    fields = early_fields(frame)
    last_accepted, parent, parent_high = None, None, np.nan
    previous_condition, confirmed = False, False
    rows = []
    for i, row in enumerate(fields.itertuples()):
        if i and frame.index[i] - frame.index[i-1] != HOUR:
            last_accepted, parent, parent_high = None, None, np.nan
            previous_condition, confirmed = False, False
        condition = bool(row.early_condition)
        early = condition and not previous_condition and (last_accepted is None or i-last_accepted >= 12)
        if early:
            last_accepted, parent, parent_high, confirmed = i, i, float(row.prog_prior_high), False
        if parent is not None and i-parent > 3:
            parent, parent_high, confirmed = None, np.nan, False
        age = i-parent if parent is not None else None
        child = bool(parent is not None and not confirmed and frame.ready.iloc[i]
            and frame.close.iloc[i] > parent_high and row.tag_recent_density
            and row.tag_advance and row.tag_volume and row.tag_md_ge_signal and row.tag_middle_rising)
        if child:
            confirmed = True
        rows.append(dict(early=bool(early), confirmed=child,
            parent_i=float(parent) if parent is not None else np.nan,
            frozen_parent_high=parent_high, confirm_age=float(age) if child else np.nan,
            candidate_edge=bool(condition and not previous_condition), cooldown_blocked=bool(condition
                and not previous_condition and not early)))
        previous_condition = condition
    return fields.join(pd.DataFrame(rows, index=frame.index))


def source_pins():
    """Both exact builder and pre-registered project plan must be committed."""
    paths = [Path(__file__).resolve(), EXPERIMENT / "PROJECT_PLAN.md"]
    paths += [ROOT / "yoyo/evaluation" / (name + ".py") for name in (
        "spike_burst_recall_study", "spike_burst_progressive", "spike_burst_replay",
        "spike_burst_dataset", "spike_burst_execution", "altseason_research", "launch_quality_dataset")]
    paths.append(ROOT / "yoyo/data/altseason_sources.py")
    for path in paths:
        rel = str(path.relative_to(ROOT))
        if subprocess.check_output(["git", "show", "HEAD:" + rel], cwd=ROOT) != path.read_bytes():
            raise ValueError("Commit exact builder/plan before preparing: " + rel)
    return {str(p.relative_to(ROOT)): old.sha(p) for p in paths}


def authenticated_prior(v2=V2):
    """Authenticate V1 inputs and frozen V2 labels/results before any pickle."""
    v2 = Path(v2).resolve()
    references = {}
    for name, digest in (("prepared_manifest.json", V2_PREPARED_SHA),
                         ("validation_manifest.json", V2_VALIDATION_SHA)):
        path = old.checked(v2 / name, digest)
        receipt = json.loads(path.read_text())
        if receipt.get("status") != "complete":
            raise ValueError("Prior phase incomplete")
        references[str(path)] = digest
        if name.startswith("validation") and receipt["prepared_manifest_sha256"] != V2_PREPARED_SHA:
            raise ValueError("V2 phase linkage mismatch")
        for item in receipt["artifacts"]:
            path = old.checked(item["path"], item["sha256"])
            references[str(path)] = item["sha256"]
        for rel, digest in receipt["source_pins"].items():
            references[str(old.checked(ROOT / rel, digest))] = digest
    jobs, coverage, v1refs = old.load_prior()
    references.update(v1refs)
    labels = pd.read_csv(v2 / "labels.csv.gz")
    for col in ("bar_open", "decision_time"):
        labels[col] = pd.to_datetime(labels[col], utc=True)
    if labels.duplicated(["instrument", "event_i"]).any():
        raise ValueError("Duplicated frozen label identities")
    return jobs, coverage, references, labels


def recall_summary(labels, detections, signals, exposure):
    """Local two-arm summary; old ARMS and frozen denominator never mutate."""
    rows = []
    for arm in ARMS:
        joined = labels.merge(detections.loc[detections.arm.eq(arm)], on=["instrument", "event_i"], how="left", validate="one_to_one")
        for period, (start, end) in old.period_windows().items():
            g = joined.loc[joined.decision_time.ge(start) & joined.decision_time.lt(end)]
            s = signals.loc[signals.arm.eq(arm) & signals.decision_time.ge(start) & signals.decision_time.lt(end)]
            e = exposure.loc[exposure.decision_time.ge(start) & exposure.decision_time.lt(end)]
            positives = g.loc[g.label.eq("positive")]
            known = s.loc[~s.match_status.eq("unknown")]
            matched = s.loc[s.match_status.eq("matched")]
            hours = float(e.eligible_bars.sum())
            row = dict(arm=arm, period=period, start=start, end=end, anchors=len(g),
                positive_events=len(positives), negative_events=int(g.label.eq("negative").sum()),
                unknown_events=int(g.label.eq("unknown").sum()), already_tracking=0,
                new_tracking_opportunities=len(positives), signals=len(s), unique_matched=len(matched),
                unmatched=int(s.match_status.eq("unmatched").sum()), duplicates=int(s.match_status.eq("duplicate").sum()),
                unknown_signals=int(s.match_status.eq("unknown").sum()),
                precision_all=len(matched)/len(s) if len(s) else np.nan,
                precision_adjudicated=len(matched)/len(known) if len(known) else np.nan,
                eligible_asset_hours=int(hours), alerts_per_day=len(s)/((end-start)/pd.Timedelta(days=1)),
                alerts_per_100_asset_days=2400*len(s)/hours if hours else np.nan,
                false_alerts_per_100_asset_days=2400*s.match_status.eq("unmatched").sum()/hours if hours else np.nan,
                matched_median_lag=matched.lag.median(), matched_p90_lag=matched.lag.quantile(.9),
                child_median_age=s.confirm_age.median() if arm == "confirmed" else np.nan)
            for lag in (0, 1, 2, 6):
                row["hits_"+str(lag)] = int(positives["hit_"+str(lag)].sum())
                row["recall_"+str(lag)] = positives["hit_"+str(lag)].mean()
            big = positives.loc[positives.large_peak.eq(True)]
            row.update(large_positive_events=len(big), large_recall_1=big.hit_1.mean(),
                       target_80_met=bool(len(positives) and positives.hit_1.mean() >= .8))
            rows.append(row)
    return pd.DataFrame(rows)


def prepare(output=EXPERIMENT / "results", v2=V2):
    """Freeze signal parents, children, labels, controls and breadth globally."""
    output = Path(output).resolve()
    pins = source_pins()
    if output.exists() and any(output.iterdir()):
        raise ValueError("Refusing to overwrite prepared artifacts")
    jobs, coverage, references, labels = authenticated_prior(v2)
    output.mkdir(parents=True, exist_ok=True)
    old.write_json(output / "prepare_started.json", dict(status="running", config=CONFIG, source_pins=pins))
    signals, controls, detections, exposure, breadth, snapshots, cases, schedules, parents = [], [], [], [], [], [], [], [], [], []
    for job in jobs:
        if job["asset"] in ("BTC", "ETH"):
            continue
        frame = load_feature(job["features_path"], job["features_sha256"], 60, END)
        state = detect(frame)
        context = {k: job[k] for k in ("instrument", "asset", "symbol", "venue", "minutes")}
        current_labels = labels.loc[labels.instrument.eq(job["instrument"])].copy()
        if len(current_labels) and current_labels.event_i.max() >= len(frame):
            raise ValueError("Frozen label index exceeds authenticated input")
        decisions = {arm: np.flatnonzero(state[arm].to_numpy()) for arm in ARMS}
        union = sorted(set(decisions["early"]) | set(decisions["confirmed"]))
        closes = frame.index + HOUR
        window = (closes >= START) & (closes < END)
        # A study-start child may refer to a warmup parent. Keep every causal
        # parent identity so that no child is an unauditable orphan; this table
        # is descriptive and does not add warmup alerts to study denominators.
        for i in decisions["early"]:
            parents.append(dict(context, event_id=old.identity(job["instrument"], "early", int(i)),
                decision_i=int(i), bar_open=frame.index[i], decision_time=closes[i],
                frozen_parent_high=state.frozen_parent_high.iloc[i], in_study=bool(window[i])))
        evaluated = [int(i) for i in union if window[i]]
        matcher = frame.copy()
        matcher["history_count"] = np.where(frame.ready.eq(True), frame.history_count, 0)
        random_indices = match_controls(matcher, union, evaluated, job["instrument"], 60, START, END)
        for arm in ARMS:
            dummy = pd.DataFrame(dict(trend_side=0, burst=state[arm]), index=frame.index)
            matches, detection = old.match_signals(current_labels, decisions[arm], frame, dummy)
            detection["arm"], detection["instrument"] = arm, job["instrument"]
            detections.append(detection)
            matchmap = matches.set_index("decision_i").to_dict("index")
            for i in decisions[arm]:
                if not window[i]:
                    continue
                record = matchmap[int(i)]
                parent = int(state.parent_i.iloc[i])
                eid = old.identity(job["instrument"], arm, int(i))
                anchor = record["label_i"]
                anchor_close = float(frame.close.iloc[int(anchor)]) if pd.notna(anchor) else np.nan
                anchor_atr = float(frame.atr.iloc[int(anchor)]) if pd.notna(anchor) else np.nan
                signals.append(dict(context, event_id=eid, parent_event_id=old.identity(job["instrument"], "early", parent),
                    parent_i=parent, parent_decision_time=closes[parent], frozen_parent_high=state.frozen_parent_high.iloc[i],
                    confirm_age=state.confirm_age.iloc[i], arm=arm, decision_i=int(i), bar_open=frame.index[i],
                    decision_time=closes[i], route=arm, relative_volume=float(frame.rv.iloc[i]),
                    tr_expansion=float(frame.expansion.iloc[i]), signal_close=float(frame.close.iloc[i]),
                    signal_atr=float(frame.atr.iloc[i]), matched_label_id=old.identity(job["instrument"], "label", int(anchor)) if pd.notna(anchor) else "",
                    match_status=record["match_status"], lag=record["lag"],
                    move_since_anchor_atr=(frame.close.iloc[i]-anchor_close)/anchor_atr if anchor_atr > 0 else np.nan,
                    move_since_anchor_pct=100*(frame.close.iloc[i]/anchor_close-1) if anchor_close > 0 else np.nan,
                    in_study=True, **{k: state[k].iloc[i] for k in TAGS}))
                for k, j in enumerate(random_indices[int(i)]):
                    controls.append(dict(context, event_id=old.identity(eid, "control", k), matched_event_id=eid,
                        arm=arm, control_number=k, decision_i=int(j), decision_time=closes[j]))
        local = pd.DataFrame(dict(decision_time=closes[window],
            eligible_bars=frame.ready.to_numpy()[window].astype(int)))
        exposure.append(local)
        breadth.append(pd.DataFrame(dict(decision_time=closes[window],
            valid_denominator=state.breadth_valid.to_numpy()[window].astype(int),
            above_fast=state.breadth_above_fast.to_numpy()[window].astype(int),
            positive_return=state.breadth_positive_return.to_numpy()[window].astype(int),
            joint=state.breadth_joint.to_numpy()[window].astype(int))))
        case_mask = ((frame.index >= pd.Timestamp("2026-08-17T16:00Z"))
            & (frame.index < pd.Timestamp("2026-08-21T16:00Z"))) if job["asset"] in ("HYPE", "NEAR", "PEPE") else np.zeros(len(frame), bool)
        snap_mask = frame.index.isin(old.SNAPSHOT_OPENS)
        for i in np.flatnonzero(case_mask | snap_mask):
            row = dict(context, decision_i=int(i), bar_open=frame.index[i], confirmed_at=closes[i],
                **{k: frame[k].iloc[i] for k in ("open", "high", "low", "close", "volume", "atr", "rv", "expansion", "ready",
                    "s20", "e20", "s60", "e60", "s120", "e120", "md", "sb", "middle")},
                **state.iloc[i].to_dict())
            if snap_mask[i]:
                snapshots.append(row)
            if case_mask[i]:
                cases.append(row)
        schedules.append(dict(job, signals={arm: int((state[arm].to_numpy() & window).sum()) for arm in ARMS}))
        print("prepared", job["instrument"], flush=True)
    signal_frame = pd.DataFrame(signals)
    control_frame = pd.DataFrame(controls, columns=old.CONTROL_COLUMNS)
    detection_frame = pd.concat(detections, ignore_index=True)
    exposure_frame = pd.concat(exposure).groupby("decision_time", as_index=False).eligible_bars.sum()
    breadth_frame = pd.concat(breadth).groupby("decision_time", as_index=False).sum()
    for key in ("above_fast", "positive_return", "joint"):
        breadth_frame["share_"+key] = (breadth_frame[key] / breadth_frame.valid_denominator).where(breadth_frame.valid_denominator.gt(0))
    signal_frame = signal_frame.merge(breadth_frame, on="decision_time", how="left", validate="many_to_one")
    summary = recall_summary(labels, detection_frame, signal_frame, exposure_frame)
    prior_recall = pd.read_csv(Path(v2) / "recall_summary.csv")
    products = {"labels.csv.gz": labels, "signals.csv.gz": signal_frame, "controls.csv.gz": control_frame,
        "parent_registry.csv.gz": pd.DataFrame(parents),
        "detections.csv.gz": detection_frame, "exposure.csv.gz": exposure_frame, "breadth.csv.gz": breadth_frame,
        "snapshots.csv": pd.DataFrame(snapshots), "case_slices.csv": pd.DataFrame(cases),
        "coverage.csv": coverage.loc[coverage.venue.eq("okx") & (coverage.minutes.eq(60) | coverage.minutes.isna())],
        "recall_summary.csv": summary, "recall_comparison.csv": pd.concat([prior_recall, summary], ignore_index=True)}
    paths = []
    for name, table in products.items():
        path = output / name
        if name == "labels.csv.gz":
            shutil.copyfile(Path(v2) / name, path)
            old.checked(path, old.sha(Path(v2) / name))
        else:
            old.write_csv(path, table)
        paths.append(path)
    path = output / "matching.json"
    old.write_json(path, dict(jobs=schedules, source_pins=pins, config=CONFIG,
        signals=len(signal_frame), controls=len(control_frame), no_execution_scored=True))
    paths.append(path)
    old._verify_references(references)
    if source_pins() != pins:
        raise ValueError("Source changed while preparing")
    receipt = dict(status="complete", config=CONFIG, source_pins=pins, sources=references,
        label_source_sha256=old.sha(Path(v2) / "labels.csv.gz"), label_rows=len(labels),
        code_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        segments=len(schedules), artifacts=[old.artifact(p) for p in paths],
        limitations=["Seen retrospective historical pool, not all OKX or blind OOS", "Early warning is not a trade recommendation",
            "Breadth is synchronous observed context, not a tested filter", "Independent signal events can overlap; no portfolio or live promotion"])
    old.write_json(output / "prepared_manifest.json", receipt)
    return receipt


def forward_outcome(frame, i):
    """Outcome only: next24 complete closes, same frozen +4/-2 ATR barriers."""
    future = frame.iloc[i+1:i+25]
    if len(future) != 24 or not np.isfinite(future.close).all():
        return dict(forward_known=False, forward_success=np.nan)
    a, c = float(frame.atr.iloc[i]), float(frame.close.iloc[i])
    if not np.isfinite(a) or a <= 0 or np.any(np.diff(frame.index[i:i+25].asi8) != HOUR.value):
        return dict(forward_known=False, forward_success=np.nan)
    target = np.flatnonzero(future.close.to_numpy() >= c+4*a)
    adverse = np.flatnonzero(future.close.to_numpy() <= c-2*a)
    return dict(forward_known=True, forward_success=bool(len(target) and (not len(adverse) or target[0] < adverse[0])))


def evaluate(output=EXPERIMENT / "results"):
    """Score only frozen candidates/controls with the unchanged execution code."""
    output = Path(output).resolve()
    pins = source_pins()
    if (output / "evaluation_started.json").exists() or (output / "validation_manifest.json").exists():
        raise ValueError("Refusing second scoring/overwrite")
    path = output / "prepared_manifest.json"
    prepared_hash = old.sha(path)
    prepared = json.loads(path.read_text())
    if prepared.get("status") != "complete" or prepared["config"] != CONFIG or prepared["source_pins"] != pins:
        raise ValueError("Prepared source/plan mismatch")
    for item in prepared["artifacts"]:
        old.checked(item["path"], item["sha256"])
    old._verify_references(prepared["sources"])
    matching = json.loads((output / "matching.json").read_text())
    signals, controls = pd.read_csv(output / "signals.csv.gz"), pd.read_csv(output / "controls.csv.gz")
    old.write_json(output / "evaluation_started.json", dict(status="running", prepared_manifest_sha256=prepared_hash,
        source_pins=pins, config=CONFIG, event_study_only=True))
    actual_rows, control_rows = [], []
    for job in matching["jobs"]:
        frame = load_feature(job["features_path"], job["features_sha256"], 60, END)
        cache = {}
        for table, destination in ((signals, actual_rows), (controls, control_rows)):
            for row in table.loc[table.instrument.eq(job["instrument"])].to_dict("records"):
                i = int(row["decision_i"])
                if i not in cache:
                    cache[i] = dict(simulate_trade(frame, i, float(job["tick"]), END), **forward_outcome(frame, i))
                destination.append(dict(row, relative_volume=float(frame.rv.iloc[i]), tr_expansion=float(frame.expansion.iloc[i]), **cache[i]))
        print("scored frozen", job["instrument"], flush=True)
    actual, random = pd.DataFrame(actual_rows), pd.DataFrame(control_rows)
    for table in (actual, random):
        table["decision_time"] = pd.to_datetime(table.decision_time, utc=True)
    summaries, scores = [], []
    for arm in ARMS:
        for period, (start, end) in old.period_windows().items():
            a = actual.loc[actual.arm.eq(arm) & actual.decision_time.ge(start) & actual.decision_time.lt(end)]
            c = random.loc[random.matched_event_id.isin(a.event_id)]
            row, feature_rows = old.trade_summary(a, c)
            known_a, known_c = a.loc[a.forward_known.eq(True)], c.loc[c.forward_known.eq(True)]
            row.update(forward_known=len(known_a), forward_success_rate=pd.to_numeric(known_a.forward_success).mean(),
                control_forward_known=len(known_c), control_forward_success_rate=pd.to_numeric(known_c.forward_success).mean())
            summaries.append(dict(arm=arm, period=period, **row))
            if period == "full":
                scores.extend(dict(arm=arm, **r) for r in feature_rows)
    summary = pd.DataFrame(summaries)
    primary = summary.period.eq("full")
    pvalues = summary.loc[primary, "permutation_p"].to_numpy(float)
    if len(pvalues) != 2:
        raise ValueError("Exactly two primary tests required")
    adjusted = holm(np.where(np.isfinite(pvalues), pvalues, 1.))
    adjusted[~np.isfinite(pvalues)] = np.nan
    summary.loc[primary, "holm_p"] = adjusted
    paths = []
    for name, table in (("trade_events.csv.gz", actual), ("trade_controls.csv.gz", random),
        ("trade_summary.csv", summary), ("score_summary.csv", pd.DataFrame(scores)),
        ("trade_comparison.csv", pd.concat([pd.read_csv(V2 / "trade_summary.csv"), summary], ignore_index=True))):
        path = output / name
        old.write_csv(path, table)
        paths.append(path)
    old.checked(output / "prepared_manifest.json", prepared_hash)
    old._verify_references(prepared["sources"])
    for item in prepared["artifacts"]:
        old.checked(item["path"], item["sha256"])
    if source_pins() != pins:
        raise ValueError("Source changed during evaluation")
    receipt = dict(status="complete", source_pins=pins, config=CONFIG, primary_tests=2,
        prepared_manifest_sha256=prepared_hash, artifacts=[old.artifact(p) for p in paths],
        costs="Unchanged20bp entry-notional; funding/impact unmodeled", account_return_not_computed=True,
        limitations=["Descriptive seen-data event outcomes, not blind OOS or portfolio returns",
            "Early/confirmation arms are related; controls can repeat across events", "Same-night co-movement is not independent evidence"])
    old.write_json(output / "validation_manifest.json", receipt)
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "evaluate"))
    parser.add_argument("--results", type=Path, default=EXPERIMENT / "results")
    args = parser.parse_args()
    prepare(args.results) if args.phase == "prepare" else evaluate(args.results)
