"""Describe frozen V3 timely positives lost by the A reference-gated scan.

This is a post-hoc diagnostic, never a new detector or a selection rule. The
positive labels and their future24-bar HIGH peaks are already frozen. At each
label's event_i we inspect A's own causal reference owner, then join ONLY that
accepted A parent's next-open trade. Reference coverage and actual-trade
coverage are distinct. Actual coverage requires entry_i <= event_i < exit_i;
an exit on the same bar is conservatively not coverage. Missing warmup trades
remain unknown rather than borrowing a V3 trade or fabricating an outcome.

Current floating P/L marks an actually live trade to that event's close and
subtracts the original20bp round-trip assumption; it is not realized P/L.
Final natural exits and future-peak rankings are explicitly posterior context.
They never repair the800 fresh misses or change any detection/pass threshold.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = ROOT / "experiments/active/exp-spike-v3-focus-20260910-v1"
SOURCE = EXPERIMENT / "results"
OUTPUT = EXPERIMENT / "miss_diagnostic"
PREPARED_SHA = "a219b7fe8b25ce165121a988fed1b16b572211de932f74abdea1af4de740eeb4"
VALIDATION_SHA = "b94367b06517fd4639473dbf6a4806cdf9d12dddbb7cfa59c3a1f98f5f8d121b"
START = pd.Timestamp("2026-07-10T00:00Z")
END = pd.Timestamp("2026-09-09T00:00Z")
CONFIG = dict(
    schema="v3-focus-frozen-miss-description-v1",
    population="positive labels in study window with V3 early hit_1 and no A early hit_1",
    diagnostic_clock="fixed label event_i; actual missed V3 arrow_i reported separately",
    reference_coverage="active before and after event, same accepted owner, owner_i < event_i",
    actual_coverage="A owner's valid next-open trade with entry_i <= event_i < exit_i",
    same_bar_exit="not coverage; natural stop and boundary mark distinguished",
    current_mark="event close / actual entry - 1 - original fee_return; live only",
    natural_outcomes="posterior description; unique accepted parent economics, not new trades",
    top_examples="top10 future24-bar high peaks, descending; instrument/event_i stable ties",
    fresh_misses_unchanged=True,
    new_hypotheses=0,
    detector_changes=False,
    production_eligible=False,
)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def checked(path, digest):
    path = Path(path)
    if sha(path) != digest:
        raise ValueError("Frozen input hash mismatch: " + str(path))
    return path


def json_safe(value):
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (float, np.floating)):
        return float(value) if math.isfinite(value) else None
    if value is pd.NaT:
        return None
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    return value


def write_json(path, value):
    Path(path).write_text(json.dumps(json_safe(value), ensure_ascii=False,
                                    indent=2, allow_nan=False) + "\n")


def artifact(path):
    path = Path(path).resolve()
    return dict(path=str(path), sha256=sha(path), size_bytes=path.stat().st_size)


def committed_source():
    """Refuse to construct even descriptive artifacts with uncommitted code."""
    path = Path(__file__).resolve()
    relative = str(path.relative_to(ROOT))
    saved = subprocess.check_output(["git", "show", "HEAD:" + relative], cwd=ROOT)
    if saved != path.read_bytes():
        raise ValueError("Commit exact diagnostic builder before running")
    return {relative: sha(path)}


def authenticated_inputs():
    refs = {}
    manifests = []
    for name, digest in (("prepared_manifest.json", PREPARED_SHA),
                         ("validation_manifest.json", VALIDATION_SHA)):
        path = checked(SOURCE / name, digest)
        receipt = json.loads(path.read_text())
        if receipt.get("status") != "complete":
            raise ValueError("Incomplete source receipt")
        refs[str(path)] = digest
        for item in receipt["artifacts"]:
            path = checked(item["path"], item["sha256"])
            refs[str(path)] = item["sha256"]
        manifests.append(receipt)
    prepared, validation = manifests
    if validation["prepared_sha"] != PREPARED_SHA:
        raise ValueError("Economic results refer to a different schedule")
    matching = json.loads((SOURCE / "matching.json").read_text())
    for item in matching["state_artifacts"]:
        path = checked(item["path"], item["sha256"])
        refs[str(path)] = item["sha256"]
    return matching, refs


def actual_status(trade, event_i, owner_known):
    """Posterior occupancy classification, never an entry-selection predicate."""
    if not owner_known:
        return "no_a_parent"
    if trade is None:
        return "unscored_warmup_parent"
    if not bool(trade["valid"]):
        return "invalid_parent_trade"
    entry_i, exit_i = int(trade["entry_i"]), int(trade["exit_i"])
    if entry_i > event_i:
        return "not_entered_yet"
    if exit_i > event_i:
        return "actual_live"
    if exit_i == event_i:
        return "natural_exit_same_bar" if bool(trade["natural_exit"]) else "boundary_mark_same_bar"
    return "exited_before_event"


def reference_covered(state, event_i, owner_i):
    if owner_i is None or event_i <= 0 or owner_i >= event_i:
        return False
    before, current = state.iloc[event_i - 1], state.iloc[event_i]
    return bool(before.reference_reference_active and current.reference_reference_active
                and before.reference_reference_owner_i == owner_i
                and current.reference_reference_owner_i == owner_i)


def mark_net_bp(trade, close, status):
    if trade is None or status != "actual_live":
        return np.nan
    return 10000 * (float(close) / float(trade["entry_price"]) - 1
                    - float(trade["fee_return"]))


def group_summary(frame, key):
    """Event counts plus deduplicated old-parent final outcomes; never NAV."""
    rows = []
    for value, sub in frame.groupby(key, dropna=False, sort=True):
        alive = sub[sub.actual_status.eq("actual_live")]
        natural = (sub[sub.owner_natural_exit.eq(True) & sub.owner_event_id.notna()]
                   .drop_duplicates("owner_event_id"))
        age = pd.to_numeric(sub.owner_age_bars).dropna()
        rows.append(dict(
            group_key=key, group=value, missed_events=len(sub),
            unique_old_parents=sub.owner_event_id.dropna().nunique(),
            reference_covered_events=int(sub.reference_covered.sum()),
            actual_live_events=len(alive),
            same_bar_natural_exits=int(sub.actual_status.eq("natural_exit_same_bar").sum()),
            unscored_warmup_events=int(sub.actual_status.eq("unscored_warmup_parent").sum()),
            owner_age_median_bars=age.median(), owner_age_p10_bars=age.quantile(.1),
            owner_age_p90_bars=age.quantile(.9),
            live_mark_net_bp_median=alive.current_mark_net_bp.median(),
            live_mark_net_bp_mean=alive.current_mark_net_bp.mean(),
            live_positive_mark_fraction=alive.current_mark_net_bp.gt(0).mean(),
            posterior_unique_natural_parents=len(natural),
            posterior_parent_mean_net_bp=natural.owner_natural_net_bp.mean(),
            posterior_parent_net_win_rate=natural.owner_natural_net_bp.gt(0).mean(),
        ))
    return pd.DataFrame(rows)


def build():
    source_pins = committed_source()
    if OUTPUT.exists() and any(OUTPUT.iterdir()):
        raise ValueError("Refusing to overwrite a frozen miss diagnostic")
    matching, refs = authenticated_inputs()
    labels = pd.read_csv(SOURCE / "labels.csv.gz", float_precision="round_trip")
    labels["decision_time"] = pd.to_datetime(labels.decision_time, utc=True)
    detections = pd.read_csv(SOURCE / "detections.csv.gz", float_precision="round_trip")
    signals = pd.read_csv(SOURCE / "signals.csv.gz", float_precision="round_trip")
    trades = pd.read_csv(SOURCE / "trade_events.csv.gz", float_precision="round_trip")
    if not trades.stage.eq("early").all():
        raise ValueError("Children must not be separate trades")
    labels = labels[labels.decision_time.ge(START) & labels.decision_time.lt(END)]
    if len(labels) != 8046 or labels.label.eq("positive").sum() != 1660:
        raise ValueError("Frozen study denominator changed")
    joined = labels.copy()
    for arm in ("v3", "reference"):
        det = detections[detections.arm.eq(arm) & detections.stage.eq("early")]
        if not det.hit_1.isin([True, False]).all():
            raise ValueError("Nonboolean detection status")
        det = det[["instrument", "event_i", "hit_1"]].rename(columns={"hit_1": arm + "_hit_1"})
        joined = joined.merge(det, on=["instrument", "event_i"], validate="one_to_one")
    positives = joined[joined.label.eq("positive")]
    misses = positives[positives.v3_hit_1.eq(True) & positives.reference_hit_1.eq(False)]
    baseline_hits = int(positives.v3_hit_1.sum())
    retained_hits = int((positives.v3_hit_1 & positives.reference_hit_1).sum())
    if (baseline_hits, retained_hits, len(misses)) != (1463, 663, 800):
        raise ValueError("Fresh-miss source accounting changed")
    a_signals = signals[signals.arm.eq("reference") & signals.stage.eq("early")]
    a_trades = trades[trades.arm.eq("reference")]
    if a_signals.duplicated(["instrument", "decision_i"]).any():
        raise ValueError("Duplicate accepted A parent")
    if set(a_trades.event_id) != set(a_signals.event_id):
        raise ValueError("A trades are not exactly A accepted parents")
    signal_map = a_signals.set_index(["instrument", "decision_i"]).to_dict("index")
    trade_map = a_trades.set_index(["instrument", "decision_i"]).to_dict("index")
    baseline_map = signals[signals.arm.eq("v3") & signals.stage.eq("early")].set_index(
        ["instrument", "decision_i"]).to_dict("index")
    state_paths = {job["instrument"]: item["path"] for job, item in
                   zip(matching["jobs"], matching["state_artifacts"])}
    if len(state_paths) != len(matching["jobs"]):
        raise ValueError("State/market identity ambiguity")
    rows = []
    for instrument, events in misses.groupby("instrument", sort=True):
        state = pd.read_csv(state_paths[instrument], float_precision="round_trip")
        if not np.array_equal(state.decision_i, np.arange(len(state))):
            raise ValueError("State indices changed")
        clocks = pd.to_datetime(state.decision_time, utc=True)
        for label in events.itertuples(index=False):
            i = int(label.event_i)
            if clocks.iloc[i] != label.decision_time:
                raise ValueError("Label/state clock mismatch")
            if bool(state.reference_early.iloc[i:i + 2].any()):
                raise ValueError("An allegedly missed event has a timely A parent")
            baseline_indices = [j for j in range(i, min(i + 2, len(state))) if state.v3_early.iloc[j]]
            if not baseline_indices:
                raise ValueError("A baseline hit has no actual V3 arrow")
            arrow_i = baseline_indices[0]
            raw_owner = state.reference_reference_owner_i.iloc[i]
            owner = None if pd.isna(raw_owner) else int(raw_owner)
            if owner is not None and (owner >= i or not state.reference_early.iloc[owner]):
                raise ValueError("Owner is not an earlier accepted A parent")
            key = (instrument, owner)
            signal, trade = signal_map.get(key), trade_map.get(key)
            if owner is not None and signal is None and clocks.iloc[owner] >= START:
                raise ValueError("In-study A owner missing from accepted signal registry")
            if trade is not None and (signal is None or trade["event_id"] != signal["event_id"]):
                raise ValueError("Borrowed or mismatched parent trade")
            status = actual_status(trade, i, owner is not None)
            arrow_status = actual_status(trade, arrow_i, owner is not None)
            close = float(label.entry_reference)
            arrow_signal = baseline_map[(instrument, arrow_i)]
            natural = bool(trade is not None and trade["valid"] and trade["natural_exit"])
            reason = (str(trade["exit_reason"] if trade["valid"] else trade["invalid_reason"])
                      if trade is not None else status)
            rows.append(dict(
                instrument=instrument, asset=arrow_signal["asset"], event_i=i,
                event_time=label.decision_time, event_close=close,
                missed_v3_arrow_i=arrow_i, missed_v3_arrow_time=clocks.iloc[arrow_i],
                missed_v3_arrow_lag=arrow_i - i,
                frozen_positive_label=True, v3_timely_hit=True, a_timely_hit=False,
                future24_high_peak_return=float(label.future_peak_return),
                large_peak=bool(label.large_peak), owner_i=owner,
                owner_event_id=signal["event_id"] if signal is not None else None,
                owner_signal_time=clocks.iloc[owner] if owner is not None else pd.NaT,
                owner_signal_close=signal["signal_close"] if signal is not None else np.nan,
                owner_age_bars=i - owner if owner is not None else np.nan,
                reference_covered=reference_covered(state, i, owner),
                reference_active_at_event=bool(state.reference_reference_active.iloc[i]),
                reference_exit_on_event=bool(state.reference_reference_exit.iloc[i]),
                reference_covered_at_missed_arrow=reference_covered(state, arrow_i, owner),
                actual_status=status, actual_status_at_missed_arrow=arrow_status,
                actual_live=status == "actual_live",
                current_mark_net_bp=mark_net_bp(trade, close, status),
                missed_arrow_mark_net_bp=mark_net_bp(trade, arrow_signal["signal_close"], arrow_status),
                actual_entry_i=trade["entry_i"] if trade is not None else np.nan,
                actual_entry_price=trade["entry_price"] if trade is not None else np.nan,
                actual_initial_stop=trade["initial_stop"] if trade is not None else np.nan,
                actual_initial_risk=trade["initial_risk"] if trade is not None else np.nan,
                actual_exit_i=trade["exit_i"] if trade is not None else np.nan,
                posterior_owner_exit_reason=reason,
                owner_natural_exit=natural,
                owner_censored=bool(trade is not None and trade["censored"]),
                owner_natural_net_bp=trade["net_bp"] if natural else np.nan,
                posterior_owner_exit_time=trade["exit_time"] if trade is not None else None,
            ))
    frame = pd.DataFrame(rows).sort_values(["event_time", "instrument", "event_i"])
    if len(frame) != 800 or frame.duplicated(["instrument", "event_i"]).any():
        raise ValueError("Missed event population drift")
    live = frame[frame.actual_live]
    if not ((live.actual_entry_i <= live.event_i) & (live.actual_exit_i > live.event_i)).all():
        raise ValueError("Actual coverage includes a same-bar exit or invalid entry")
    status_counts = frame.actual_status.value_counts().to_dict()
    not_live = ["no_a_parent", "invalid_parent_trade", "not_entered_yet", "exited_before_event"]
    summary = dict(
        config=CONFIG, baseline_timely_positive_events=baseline_hits,
        retained_timely_positive_events=retained_hits, fresh_misses=len(frame),
        original_timely_retention=retained_hits / baseline_hits,
        actual_status_counts=status_counts,
        reference_covered_events=int(frame.reference_covered.sum()),
        strict_actual_live_events=int(frame.actual_live.sum()),
        strict_actual_live_at_missed_arrow=int(frame.actual_status_at_missed_arrow.eq("actual_live").sum()),
        pure_miss_no_live_actual_trade=int(frame.actual_status.isin(not_live).sum()),
        same_bar_natural_exit_events=int(frame.actual_status.eq("natural_exit_same_bar").sum()),
        same_bar_boundary_mark_events=int(frame.actual_status.eq("boundary_mark_same_bar").sum()),
        unscored_warmup_parent_events=int(frame.actual_status.eq("unscored_warmup_parent").sum()),
        reference_only_events=int((frame.reference_covered & ~frame.actual_live).sum()),
        posterior_context_not_a_new_success_metric=True,
    )
    status_table = group_summary(frame, "actual_status")
    exits = group_summary(frame, "posterior_owner_exit_reason")
    cross = frame.groupby(["reference_covered", "actual_status"], dropna=False).size().reset_index(name="missed_events")
    top = frame.sort_values(["future24_high_peak_return", "instrument", "event_i"],
                            ascending=[False, True, True]).head(10).copy()
    top.insert(0, "posterior_peak_rank", np.arange(1, len(top) + 1))
    OUTPUT.mkdir(parents=True)
    products = {
        "missed_events.csv.gz": frame, "status_summary.csv": status_table,
        "posterior_exit_reason_summary.csv": exits,
        "reference_actual_crosstab.csv": cross, "top10_future_high_peaks.csv": top,
    }
    for name, table in products.items():
        compression = {"method": "gzip", "mtime": 0} if name.endswith(".gz") else None
        table.to_csv(OUTPUT / name, index=False, compression=compression)
    write_json(OUTPUT / "summary.json", summary)
    for path, digest in refs.items():
        checked(path, digest)
    if committed_source() != source_pins:
        raise ValueError("Source changed during diagnostic")
    write_json(OUTPUT / "manifest.json", dict(
        status="complete", generated_at=datetime.now(timezone.utc), config=CONFIG,
        source_pins=source_pins, source_refs=refs,
        code_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        artifacts=[artifact(OUTPUT / n) for n in list(products) + ["summary.json"]],
        limitations=[
            "Miss population is selected from already-frozen future labels; diagnosis only, no causal edge claim",
            "Actual occupancy uses A owner's trade only; same-bar exits excluded conservatively",
            "Warmup parents without scored A trades remain unknown",
            "Current mark is unrealized hypothetical liquidation after fixed20bp, not realized cash",
            "Final natural results and future high peaks are posterior and cannot select entries",
            "Multiple misses can share one prior parent; event counts are not independent trades",
            "No misses are added back to fresh recall/retention; no pass criterion or detector changed",
        ],
    ))
    print(json.dumps(json_safe(summary), ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    build()
