"""Describe frozen V3 next-close locations, then independently join old labels.

This is not another detector or a trading experiment. For each original V3
candidate at t, the interval is the already stored v3_prog_prior_low/high on
that same t row: the twelve bars preceding t, never the rolling t+1 interval.
The observed F adjudication at t+1 supplies only its existing decision close
and data-availability status. No features, candidates, controls or returns are
recomputed. A close equal to either frozen boundary belongs inside the box.

The classify command cannot parse label/outcome tables. Its completed SHA is
required by the separate join command, which describes the existing positive
labels and original V3 hit_1 assignments. A positive label is not a winning
trade. Neither phase creates a filter, changes an acceptance gate, or assigns
the next-close description to the earlier candidate-time feature snapshot.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = ROOT / "experiments/active/exp-spike-v3-retest-diagnostic-20260910-v1"
OUTPUT = EXPERIMENT / "results"
F_SOURCE = ROOT / "experiments/active/exp-spike-v3-price-acceptance-20260910-v1/results"
PREPARED_SHA = "32abdf5316732247e4912cd17e3c7c94353ba6756a33df38c007fd7933d83a42"
VALIDATION_SHA = "a0cb43b3b7d548148938b9404d1744d85b5d1a60ccb8b35ab04edff012b5a570"
START, END = pd.Timestamp("2026-07-10T00:00Z"), pd.Timestamp("2026-09-09T00:00Z")
HOUR = pd.Timedelta(hours=1)
CATEGORIES = ("above", "inside", "below", "unknown")
FIXED_CASES = (("HYPE", "2026-08-19T21:00+08:00"),
               ("NEAR", "2026-08-19T15:00+08:00"),
               ("PEPE", "2026-08-19T21:00+08:00"))
COUNTS = dict(candidates=10386, source_labels=14904, anchors=8046,
              positive=1660, large_positive=947, timely_positive=1463)
CONFIG = dict(schema="spike-v3-frozen-retest-description-v1", descriptive_only=True,
    interval="original candidate t stored prior12 low/high; both boundaries inclusive",
    observation="existing F next closed-bar adjudication; unknown remains unknown",
    clocks="candidate t and observed classification t+1 remain separate; no backdating",
    stages=["classify_without_labels", "join_frozen_original_labels"],
    unknown_is_not_rejection=True, no_new_filter=True, no_new_scoring=True,
    denominators=COUNTS, live_deployed=False, production_eligible=False)
STATE_COLUMNS = ("decision_i", "decision_time", "v3_early", "v3_parent_i",
    "v3_frozen_parent_high", "v3_prog_prior_low", "v3_prog_prior_high",
    "price_acceptance_candidate_i", "price_acceptance_candidate_time",
    "price_acceptance_candidate_close", "price_acceptance_frozen_parent_high",
    "price_acceptance_candidate_status", "price_acceptance_reason",
    "price_acceptance_decision_i", "price_acceptance_decision_time",
    "price_acceptance_decision_close")


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""): digest.update(part)
    return digest.hexdigest()


def checked(path, digest, refs):
    path = Path(path).resolve()
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or sha(path) != digest:
        raise ValueError("Changed authenticated input: " + str(path))
    if str(path) in refs and refs[str(path)] != digest:
        raise ValueError("Conflicting authenticated hash: " + str(path))
    refs[str(path)] = digest
    return path


def clean(value):
    if isinstance(value, dict): return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [clean(v) for v in value]
    if isinstance(value, (np.integer, np.bool_)): return value.item()
    if isinstance(value, (float, np.floating)): return float(value) if np.isfinite(value) else None
    if value is pd.NaT or value is pd.NA: return None
    if isinstance(value, (Path, pd.Timestamp)): return str(value)
    return value


def write_json(path, payload):
    with Path(path).open("x") as stream:
        json.dump(clean(payload), stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")


def artifact(path):
    return dict(path=str(Path(path).resolve()), sha256=sha(path))


def source_pins():
    """The actual passing preflight and exact reviewed sources must be in HEAD."""
    paths = [Path(__file__), ROOT / "tests/test_spike_burst_v3_retest_diagnostic.py",
             EXPERIMENT / "PROJECT_PLAN.md", EXPERIMENT / "qa/preflight_review.json"]
    pins = {}
    for path in paths:
        relative = str(path.resolve().relative_to(ROOT))
        if subprocess.check_output(["git", "show", "HEAD:" + relative], cwd=ROOT) != path.read_bytes():
            raise ValueError("Commit exact source and preflight before building: " + relative)
        pins[relative] = sha(path)
    qa = json.loads(paths[-1].read_text())
    if qa.get("status") != "passed": raise ValueError("Passing actual preflight required")
    for path in paths[:-1]:
        relative = str(path.resolve().relative_to(ROOT))
        if qa.get("source_sha256", {}).get(relative) != pins[relative]:
            raise ValueError("Preflight did not review exact source: " + relative)
    return pins


def authenticate():
    """Verify F receipt graph by bytes, without parsing labels or trade tables."""
    refs = {}
    prepared = json.loads(checked(F_SOURCE / "prepared_manifest.json", PREPARED_SHA, refs).read_text())
    validation = json.loads(checked(F_SOURCE / "validation_manifest.json", VALIDATION_SHA, refs).read_text())
    if (prepared.get("status") != "complete" or validation.get("status") != "complete"
            or validation.get("prepared_sha") != PREPARED_SHA
            or prepared["config"] != validation["config"]
            or prepared["source_pins"] != validation["source_pins"]
            or prepared["config"]["arms"] != ["v3", "price_acceptance"]):
        raise ValueError("Frozen F prepared/validation linkage differs")
    for item in prepared["artifacts"] + validation["artifacts"]:
        checked(item["path"], item["sha256"], refs)
    for path, digest in prepared["sources"].items(): checked(path, digest, refs)
    for path, digest in prepared["source_pins"].items(): checked(ROOT / path, digest, refs)
    for name in ("matching.json", "candidate_registry.csv.gz", "signals.csv.gz", "labels.csv.gz", "detections.csv.gz"):
        if str((F_SOURCE / name).resolve()) not in refs:
            raise ValueError("Missing frozen input artifact: " + name)
    return prepared, validation, refs


def same_number(left, right):
    return bool((pd.isna(left) and pd.isna(right)) or
                (pd.notna(left) and pd.notna(right) and float(left) == float(right)))


def integer(value, name):
    if not np.isfinite(value) or int(value) != value or value < 0:
        raise ValueError("Invalid integer identity: " + name)
    return int(value)


def location(close, low, high, status):
    """Partition at the adjudication clock; invalid/missing inputs are unknown."""
    if status not in ("accepted", "rejected", "unknown"):
        raise ValueError("Final candidate must be accepted, rejected or unknown")
    if status == "unknown": return "unknown"
    if not np.isfinite([close, low, high]).all() or low <= 0 or high < low:
        return "unknown"
    if close > high: return "above"
    if close < low: return "below"
    return "inside"


def classify_rows(registry, state):
    """Read only each original t interval and its already frozen F decision.

    state decision_time is the closed-bar clock, not the bar-open clock.
    The returned t+1 description is not a feature at candidate_time.
    """
    state = state.copy()
    for key in ("decision_time", "price_acceptance_candidate_time", "price_acceptance_decision_time"):
        state[key] = pd.to_datetime(state[key], utc=True)
    if not np.array_equal(state.decision_i.to_numpy(), np.arange(len(state))):
        raise ValueError("Stored row identities must match original positional indices")
    if not state.decision_time.is_monotonic_increasing or state.decision_time.duplicated().any():
        raise ValueError("Stored clocks must be strictly increasing")
    if registry.candidate_i.duplicated().any(): raise ValueError("Duplicate original candidate identity")
    rows = []
    for row in registry.to_dict("records"):
        i = integer(row["candidate_i"], "candidate_i")
        if i >= len(state): raise ValueError("Original candidate is outside its stored state")
        current = state.iloc[i]
        time = pd.Timestamp(row["candidate_time"])
        if time.tzinfo is None: raise ValueError("Candidate clock must be timezone aware")
        time = time.tz_convert("UTC")
        if (not bool(current.v3_early) or current.v3_parent_i != i or current.decision_time != time
                or current.price_acceptance_candidate_i != i
                or current.price_acceptance_candidate_time != time
                or current.price_acceptance_candidate_status != "pending"
                or not same_number(current.price_acceptance_candidate_close, row["candidate_close"])):
            raise ValueError("Candidate t does not match original frozen state and pending clock")
        high, low = float(current.v3_prog_prior_high), float(current.v3_prog_prior_low)
        for frozen in (current.v3_frozen_parent_high, current.price_acceptance_frozen_parent_high,
                       row["frozen_parent_high"]):
            if not same_number(high, frozen): raise ValueError("Candidate frozen high differs from original t prior12 high")
        expected = time + HOUR
        if pd.Timestamp(row["expected_decision_time"]) != expected:
            raise ValueError("Expected next close must be candidate t plus one hour")
        status, reason = row["candidate_status"], row["reason"]
        if status not in ("accepted", "rejected", "unknown"):
            raise ValueError("Final candidate status is not adjudicated")
        observed_time = pd.to_datetime(row["decision_time"], utc=True)
        observed_i, close = row["decision_i"], row["decision_close"]
        if pd.isna(observed_i):
            if (status != "unknown" or reason != "no_next_bar_at_sample_end"
                    or pd.notna(observed_time) or pd.notna(close)):
                raise ValueError("Absent adjudication must remain unknown without a fabricated clock")
        else:
            j = integer(observed_i, "decision_i")
            if j != i + 1 or j >= len(state): raise ValueError("Observed decision is not the next stored bar")
            decision = state.iloc[j]
            if (decision.decision_time != observed_time or observed_time <= time
                    or decision.price_acceptance_candidate_i != i
                    or decision.price_acceptance_decision_i != j
                    or decision.price_acceptance_decision_time != observed_time
                    or decision.price_acceptance_candidate_status != status
                    or decision.price_acceptance_reason != reason
                    or not same_number(decision.price_acceptance_decision_close, close)):
                raise ValueError("Frozen adjudication differs from its actual current state row")
            if status != "unknown" and observed_time != expected:
                raise ValueError("Known classification cannot bridge a missing hour")
            if status == "unknown" and reason not in ("missing_next_hour", "invalid_next_ohlc"):
                raise ValueError("Unknown adjudication reason differs from frozen F contract")
        if status != "unknown":
            if not np.isfinite([close, high]).all(): raise ValueError("Known F adjudication must have finite price and high")
            expected_status = "accepted" if close > high else "rejected"
            if status != expected_status: raise ValueError("Frozen F status contradicts its decision close and high")
        category = location(close, low, high, status)
        context = {key: row[key] for key in ("instrument", "asset", "symbol", "venue", "minutes")}
        rows.append(dict(context, candidate_i=i, candidate_time=time,
            candidate_close=row["candidate_close"], frozen_prior_low=low, frozen_prior_high=high,
            interval_source_i=i, interval_source_time=time,
            expected_decision_time=expected, decision_i=observed_i,
            classification_time=observed_time, decision_close=close,
            f_status=status, f_reason=reason, location=category,
            location_reason=reason if status == "unknown" else
                ("unavailable_original_interval" if category == "unknown" else "frozen_interval_comparison"),
            available_at_candidate=False))
    return pd.DataFrame(rows)


def position_summary(classified):
    if not classified.location.isin(CATEGORIES).all(): raise ValueError("Unknown position category")
    if classified.duplicated(["instrument", "candidate_i"]).any(): raise ValueError("Duplicate classified candidate")
    n = len(classified)
    result = pd.DataFrame([dict(location=key, candidates=int(classified.location.eq(key).sum()),
        candidate_denominator=n, candidate_fraction=float(classified.location.eq(key).sum()) / n if n else np.nan)
        for key in CATEGORIES])
    if result.candidates.sum() != n: raise ValueError("Candidate partition is not conserved")
    return result


def frozen_cases(classified):
    rows = []
    for asset, time in FIXED_CASES:
        selected = classified[classified.asset.eq(asset) & classified.candidate_time.eq(pd.Timestamp(time).tz_convert("UTC"))]
        if len(selected) != 1: raise ValueError("Fixed original-candidate case is missing or ambiguous: " + asset)
        rows.append(selected.iloc[0].to_dict())
    return pd.DataFrame(rows)


def check_raw_candidates(classified, signals):
    raw = signals[signals.arm.eq("v3") & signals.stage.eq("early")].copy()
    if raw.duplicated(["instrument", "decision_i"]).any(): raise ValueError("Duplicate original V3 early event")
    joined = classified.merge(raw[["instrument", "decision_i", "decision_time", "signal_close"]],
        left_on=["instrument", "candidate_i"], right_on=["instrument", "decision_i"],
        how="outer", suffixes=("", "_raw"), indicator=True, validate="one_to_one")
    if (not joined._merge.eq("both").all()
            or not pd.to_datetime(joined.candidate_time, utc=True).equals(pd.to_datetime(joined.decision_time, utc=True))
            or not np.array_equal(joined.candidate_close.to_numpy(float), joined.signal_close.to_numpy(float))):
        raise ValueError("Classified candidates must be the exact frozen original V3 event stream")


def join_anchors(labels, detections, classified):
    """Associate old labels with their true original first signal, not F t+1."""
    anchors = labels.copy()
    anchors["decision_time"] = pd.to_datetime(anchors.decision_time, utc=True)
    anchors = anchors[anchors.decision_time.ge(START) & anchors.decision_time.lt(END)]
    if anchors.duplicated(["instrument", "event_i"]).any(): raise ValueError("Duplicate frozen anchor")
    raw = detections[detections.arm.eq("v3") & detections.stage.eq("early")]
    joined = anchors.merge(raw[["instrument", "event_i", "first_signal_lag", "hit_1"]],
        on=["instrument", "event_i"], how="left", validate="one_to_one", indicator=True)
    if not joined._merge.eq("both").all(): raise ValueError("Missing original detection shrinks fixed denominator")
    joined = joined.drop(columns="_merge")
    hit = joined.hit_1.eq(True)
    if (joined.loc[hit, "label"].ne("positive").any()
            or not joined.loc[hit, "first_signal_lag"].isin([0, 1]).all()):
        raise ValueError("Original timely hit must be positive with actual lag zero or one")
    joined["original_signal_i"] = np.where(hit, joined.event_i + joined.first_signal_lag, np.nan)
    if classified.duplicated(["instrument", "candidate_i"]).any(): raise ValueError("Duplicate classified lookup")
    joined = joined.merge(classified, left_on=["instrument", "original_signal_i"],
        right_on=["instrument", "candidate_i"], how="left", suffixes=("", "_candidate"), validate="many_to_one")
    if joined.loc[hit, "location"].isna().any(): raise ValueError("A timely hit has no original candidate-time classification")
    expected_time = joined.loc[hit, "decision_time"] + pd.to_timedelta(joined.loc[hit, "first_signal_lag"], unit="h")
    actual_time = pd.to_datetime(joined.loc[hit, "candidate_time"], utc=True)
    if not np.array_equal(expected_time.to_numpy(), actual_time.to_numpy()):
        raise ValueError("Timely event must use its original signal clock, not acceptance/publication time")
    joined["timely_location"] = joined.location.where(hit, "not_caught")
    return joined


def label_position_summary(joined):
    positive = joined[joined.label.eq("positive")]
    timely = positive[positive.hit_1.eq(True)]
    large = positive[positive.large_peak.eq(True)]
    result = pd.DataFrame([dict(location=key,
        timely_positive=int(timely.timely_location.eq(key).sum()),
        original_timely_denominator=len(timely),
        fraction_of_original_timely=float(timely.timely_location.eq(key).sum()) / len(timely) if len(timely) else np.nan,
        large_positive_anchors=int(large.timely_location.eq(key).sum()),
        original_large_denominator=len(large),
        fraction_of_original_large=float(large.timely_location.eq(key).sum()) / len(large) if len(large) else np.nan)
        for key in (*CATEGORIES, "not_caught")])
    if result.timely_positive.sum() != len(timely) or result.large_positive_anchors.sum() != len(large):
        raise ValueError("Frozen timely/large label partition is not conserved")
    return result


def verify_refs(refs):
    for path, digest in refs.items(): checked(path, digest, {})


def save_products(output, products):
    artifacts = []
    for name, frame in products.items():
        path = output / name
        compression = {"method": "gzip", "mtime": 0} if name.endswith(".gz") else None
        frame.to_csv(path, index=False, mode="x", compression=compression)
        artifacts.append(artifact(path))
    return artifacts


def classify(output=OUTPUT):
    pins = source_pins()
    output = Path(output).resolve()
    if output == F_SOURCE.resolve() or F_SOURCE.resolve() in output.parents:
        raise ValueError("Cannot write into frozen input results")
    if output.exists() and any(output.iterdir()): raise ValueError("Refusing classification overwrite")
    prepared, _, refs = authenticate()
    matching = json.loads((F_SOURCE / "matching.json").read_text())
    if matching["config"] != prepared["config"]: raise ValueError("Stored state schedule differs from F receipt")
    jobs, states = matching["jobs"], matching["state_artifacts"]
    if len(jobs) != 278 or len(states) != 278: raise ValueError("Frozen 278-instrument population changed")
    registry = pd.read_csv(F_SOURCE / "candidate_registry.csv.gz", float_precision="round_trip")
    registry["candidate_time"] = pd.to_datetime(registry.candidate_time, utc=True)
    selected = registry[registry.candidate_time.ge(START) & registry.candidate_time.lt(END)]
    if len(selected) != COUNTS["candidates"]: raise ValueError("Original 10386-candidate denominator changed")
    if set(selected.instrument) - {job["instrument"] for job in jobs}: raise ValueError("Candidate has no frozen instrument")
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "classification_started.json", dict(config=CONFIG, source_pins=pins))
    frames = []
    for n, (job, item) in enumerate(zip(jobs, states)):
        path = Path(item["path"]).resolve()
        expected_path = F_SOURCE / "states" / ("%03d_%s.csv.gz" % (n, job["asset"]))
        if path != expected_path.resolve() or refs.get(str(path)) != item["sha256"]:
            raise ValueError("Stored state path/hash is not its frozen instrument schedule")
        local = selected[selected.instrument.eq(job["instrument"])]
        if local.empty: continue
        for key in ("asset", "symbol", "venue", "minutes"):
            if not local[key].eq(job[key]).all(): raise ValueError("Candidate instrument metadata drift")
        state = pd.read_csv(path, usecols=STATE_COLUMNS, float_precision="round_trip")
        frames.append(classify_rows(local, state))
    classified = pd.concat(frames, ignore_index=True).sort_values(["instrument", "candidate_i"]).reset_index(drop=True)
    if len(classified) != COUNTS["candidates"]: raise ValueError("Candidate population changed during classification")
    check_raw_candidates(classified, pd.read_csv(F_SOURCE / "signals.csv.gz", float_precision="round_trip"))
    summary = position_summary(classified)
    crosstab = classified.groupby(["location", "f_status", "f_reason"], dropna=False).size().rename("candidates").reset_index()
    artifacts = save_products(output, {"classified_candidates.csv.gz": classified,
        "position_summary.csv": summary, "f_status_crosstab.csv": crosstab, "fixed_examples.csv": frozen_cases(classified)})
    verify_refs(refs)
    if source_pins() != pins: raise ValueError("Reviewed source changed during classification")
    write_json(output / "classification_manifest.json", dict(status="complete", config=CONFIG,
        phase="classification_before_label_join", labels_parsed=False, source_pins=pins, sources=refs,
        prepared_sha=PREPARED_SHA, validation_sha=VALIDATION_SHA, candidate_count=len(classified), artifacts=artifacts))
    return artifact(output / "classification_manifest.json")


def join(output, classification_sha):
    pins = source_pins()
    output = Path(output).resolve()
    refs = {}
    receipt_path = checked(output / "classification_manifest.json", classification_sha, refs)
    receipt = json.loads(receipt_path.read_text())
    if (receipt["status"] != "complete" or receipt["source_pins"] != pins or receipt["config"] != CONFIG
            or receipt["labels_parsed"] is not False or receipt["prepared_sha"] != PREPARED_SHA
            or receipt["validation_sha"] != VALIDATION_SHA):
        raise ValueError("Must join only the completed pre-label classification with the same reviewed source")
    if (output / "join_started.json").exists() or (output / "joined_manifest.json").exists():
        raise ValueError("Refusing label-join overwrite")
    _, _, frozen_refs = authenticate()
    if frozen_refs != receipt["sources"]: raise ValueError("F inputs changed between classification and label join")
    refs.update(frozen_refs)
    for item in receipt["artifacts"]: checked(item["path"], item["sha256"], refs)
    classified_path = output / "classified_candidates.csv.gz"
    if str(classified_path) not in refs: raise ValueError("Missing frozen classification artifact")
    classified = pd.read_csv(classified_path, float_precision="round_trip")
    if len(classified) != COUNTS["candidates"]: raise ValueError("Frozen candidate partition changed")
    position_summary(classified)
    write_json(output / "join_started.json", dict(classification_sha=classification_sha, source_pins=pins))
    # Labels are first parsed here, after the classification manifest is fixed.
    labels = pd.read_csv(F_SOURCE / "labels.csv.gz", float_precision="round_trip")
    detections = pd.read_csv(F_SOURCE / "detections.csv.gz", float_precision="round_trip")
    joined = join_anchors(labels, detections, classified)
    counts = dict(source_labels=len(labels), anchors=len(joined), positive=int(joined.label.eq("positive").sum()),
        large_positive=int((joined.label.eq("positive") & joined.large_peak.eq(True)).sum()),
        timely_positive=int((joined.label.eq("positive") & joined.hit_1.eq(True)).sum()))
    if any(counts[key] != COUNTS[key] for key in counts): raise ValueError("Original fixed label/hit denominators changed")
    artifacts = save_products(output, {"joined_anchors.csv.gz": joined,
        "timely_positive_positions.csv": label_position_summary(joined)})
    verify_refs(refs)
    if source_pins() != pins: raise ValueError("Reviewed source changed during label join")
    write_json(output / "joined_manifest.json", dict(status="complete", config=CONFIG, source_pins=pins,
        classification_sha=classification_sha, prepared_sha=PREPARED_SHA, validation_sha=VALIDATION_SHA,
        counts=counts, sources=refs, artifacts=artifacts,
        interpretation="Descriptive reused labels, not win rate, new gate, causality proof or a new recall score"))
    return artifact(output / "joined_manifest.json")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("classify", "join"))
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--classification-sha")
    args = parser.parse_args()
    if args.phase == "classify":
        if args.classification_sha: parser.error("classify does not accept a future classification SHA")
        result = classify(args.output)
    else:
        if not args.classification_sha: parser.error("join requires --classification-sha from the completed first phase")
        result = join(args.output, args.classification_sha)
    print(json.dumps(result, indent=2))


if __name__ == "__main__": main()
