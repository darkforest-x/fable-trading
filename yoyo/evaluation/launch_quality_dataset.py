"""Freeze one-hour launch-quality inputs without changing the prior experiment.

Sources are the earlier study's signed-by-SHA coverage snapshot, normalized
OHLCV files, and causal 1H feature pickles. Volume uses current native volume /
previous20 median; expansion uses current TR / previous ATR. Their original
columns and all known-period event fields are retained, never recomputed here.

Earlier decisions close in [2026-05-15, 2026-07-10) UTC. Simulation snapshots
physically end at the last source bar opening before July10; surviving trades
are marked at that boundary. Control selection uses decision-close UTC weeks,
causal rolling240/min60 ATR-percentile quintiles, original candidate +/-12-bar
exclusion, history_count>=340 and at most three seeded controls. Every matching
index is written before ANY earlier return is simulated. No old date constants,
threshold, exit, portfolio, live service, model or notification is modified.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.data.altseason_sources import atomic_json, utc_now
from yoyo.evaluation import altseason_engine as engine

ROOT = Path(__file__).resolve().parents[2]
OLD_EXPERIMENT = ROOT / "experiments/active/exp-altseason-multivenue-20260910-v1"
OUTPUT = ROOT / "experiments/active/exp-launch-quality-20260910-v1/results"
EARLIER_START = pd.Timestamp("2026-05-15T00:00:00Z")
EARLIER_END = pd.Timestamp("2026-07-10T00:00:00Z")
KNOWN_END = pd.Timestamp("2026-09-09T00:00:00Z")
CONFIG = dict(schema="launch-quality-dataset-v1", minutes=60, arm="focus_md",
              earlier_start=EARLIER_START.isoformat(), earlier_exclusive_end=EARLIER_END.isoformat(),
              known_start=EARLIER_END.isoformat(), known_exclusive_end=KNOWN_END.isoformat(),
              control_seed=20260910, control_maximum=3, candidate_exclusion_bars=12,
              rank_window=240, rank_minimum=60, control_history_minimum=340,
              known_event_fields="retained; research_period added",
              simulation_cutoff="last bar open strictly before earlier_exclusive_end")


def sha256(path):
    """Hash bytes incrementally, including compressed source bytes."""
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def artifact(path):
    p = Path(path).resolve()
    return dict(path=str(p), sha256=sha256(p), size_bytes=p.stat().st_size)


def _resolve(path, old_experiment):
    p = Path(path)
    if p.is_absolute():
        return p.resolve()
    if (ROOT / p).exists():
        return (ROOT / p).resolve()
    return (Path(old_experiment) / p).resolve()


def _check(path, expected):
    path = Path(path)
    if not path.is_file() or sha256(path) != expected:
        raise ValueError("Source/artifact SHA mismatch: " + str(path))
    return path


def _csv(path, frame):
    p = Path(path)
    tmp = p.with_name(p.name + ".tmp")
    frame.to_csv(tmp, index=False, compression={"method": "gzip", "mtime": 0} if p.name.endswith(".gz") else None)
    tmp.replace(p)


def _id(*parts):
    return hashlib.sha256("|".join(str(x) for x in parts).encode()).hexdigest()[:28]


def _week(closes):
    days = pd.DatetimeIndex(closes).normalize()
    return days - pd.to_timedelta(days.dayofweek, unit="D")


def match_controls(features, all_candidates, evaluation_candidates, instrument,
                   start=EARLIER_START, end=EARLIER_END):
    """Freeze positions from past-only rank columns, without outcome access.

    The original random protocol intentionally uses history_count>=340;
    actual focus candidates additionally obey the engine's ready boolean.
    ``features`` must already be truncated at ``end``. No constant mutation.
    """
    if not len(evaluation_candidates):
        return {}
    if len(features) and features.index[-1] >= end:
        raise ValueError("Control features extend beyond the earlier cutoff")
    rank = features.atr_pct.rolling(240, min_periods=60).rank(pct=True)
    buckets = np.ceil(rank.to_numpy(dtype=float) * 5)
    closes = features.index + pd.Timedelta(hours=1)
    weeks = _week(closes).asi8
    excluded = np.zeros(len(features), dtype=bool)
    for value in all_candidates.decision_i.unique():
        i = int(value)
        excluded[max(0, i - 12):min(len(features), i + 13)] = True
    pool = (~excluded & np.isfinite(buckets) & (closes >= start) & (closes < end)
            & features.history_count.ge(340).to_numpy())
    pool[-1] = False
    seed = (20260910 + int(hashlib.sha256((instrument + ":60").encode()).hexdigest()[:16], 16)) % (2 ** 64)
    rng = np.random.default_rng(seed)
    matches = {}
    for i in sorted(int(x) for x in evaluation_candidates.decision_i.unique()):
        if not np.isfinite(buckets[i]):
            matches[i] = []
            continue
        choices = np.flatnonzero(pool & (weeks == weeks[i]) & (buckets == buckets[i]))
        matches[i] = [int(x) for x in rng.choice(choices, size=min(3, len(choices)), replace=False)]
    return matches


def load_verified_coverage(old_experiment):
    """Verify frozen coverage, source bytes and every reused1H feature hash.

    Coverage identity/artifact/segment lists are authenticated against the old
    aggregate coverage.csv, itself pinned by RESULTS_MANIFEST.json. A changed
    per-market coverage cannot silently introduce a different feature file.
    """
    old = Path(old_experiment).resolve()
    results_path = old / "RESULTS_MANIFEST.json"
    manifest = json.loads(results_path.read_text())
    paths = {}
    for name in ("coverage.csv", "events.csv.gz", "controls.csv.gz"):
        matches = [x for x in manifest["artifacts"] if Path(x["path"]).name == name]
        if len(matches) != 1:
            raise ValueError("Ambiguous or missing frozen artifact: " + name)
        record = matches[0]
        path = _resolve(record["path"], old)
        _check(path, record["sha256"])
        if "size_bytes" in record and path.stat().st_size != record["size_bytes"]:
            raise ValueError("Frozen artifact size mismatch: " + name)
        paths[name] = path
    frozen = pd.read_csv(paths["coverage.csv"], keep_default_na=False,
                         usecols=["venue", "symbol", "asset", "identity", "artifacts", "segments", "exclude_reason", "errors"])
    if frozen.duplicated(["venue", "symbol"]).any():
        raise ValueError("Duplicate market in frozen coverage")
    lookup = {(r["venue"], r["symbol"]): r for r in frozen.to_dict("records")}
    coverages, feature_index = [], {}
    for path in sorted((old / "results/markets").glob("*/*/coverage.json")):
        row = json.loads(path.read_text())
        key = row["venue"], row["symbol"]
        if key not in lookup:
            raise ValueError("Unregistered market coverage: " + str(path))
        prior = lookup.pop(key)
        for field in ("identity", "artifacts", "segments", "errors"):
            if row[field] != ast.literal_eval(prior[field]):
                raise ValueError("Frozen market coverage changed: " + str(key) + ":" + field)
        for field in ("asset", "exclude_reason"):
            if row.get(field, "") != prior[field]:
                raise ValueError("Frozen market exclusion/identity changed: " + str(key))
        if row["errors"]:
            raise ValueError("Prior coverage has errors: " + str(key))
        identity = row["identity"]
        source = _resolve(identity["source_path"], old)
        _check(source, identity["source_sha256"])
        artifacts = {_resolve(x["path"], old): x for x in row["artifacts"]}
        segments = []
        for segment in row["segments"]:
            feature_path = (path.parent / ("segment%d_60_features.pkl.gz" % segment["segment"])).resolve()
            if feature_path not in artifacts:
                raise ValueError("1H feature missing from frozen artifact list: " + str(feature_path))
            record = artifacts[feature_path]
            _check(feature_path, record["sha256"])
            item = dict(venue=row["venue"], symbol=row["symbol"], asset=row["asset"],
                        instrument=segment["instrument"], features_path=str(feature_path),
                        features_sha256=record["sha256"], exclude_reason=row.get("exclude_reason", ""),
                        young_source_allowed=bool(segment.get("young_source_allowed", False)))
            if str(feature_path) in feature_index:
                raise ValueError("Duplicate feature artifact")
            feature_index[str(feature_path)] = item
            segments.append(item)
        coverages.append(dict(venue=row["venue"], symbol=row["symbol"], asset=row["asset"],
                              exclude_reason=row.get("exclude_reason", ""), source_path=str(source),
                              source_sha256=identity["source_sha256"], coverage_path=str(path.resolve()),
                              coverage_sha256=sha256(path), segments=segments))
    if lookup:
        raise ValueError("Frozen coverage markets missing: " + str(sorted(lookup)[:5]))
    return dict(manifest=manifest, manifest_path=results_path, paths=paths,
                coverages=coverages, feature_index=feature_index)


def _load_features(segment):
    path = _check(segment["features_path"], segment["features_sha256"])
    frame = pd.read_pickle(path)
    if (not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None
            or not frame.index.is_unique or not frame.index.is_monotonic_increasing):
        raise ValueError("Feature clock is not a unique UTC grid")
    if len(frame) and str(frame.index.tz) not in ("UTC", "Etc/UTC", "GMT", "Etc/GMT"):
        raise ValueError("Feature clock is not UTC")
    if len(frame) > 1 and np.any(np.diff(frame.index.asi8) != pd.Timedelta(hours=1).value):
        raise ValueError("Feature segment is discontinuous")
    frame.attrs.update(minutes=60, period_seconds=3600)
    return frame


def freeze_earlier_matching(verified, output):
    """Write the whole study's matching plan before any outcome calculation."""
    jobs, rows = [], []
    for market in verified["coverages"]:
        row = {k: v for k, v in market.items() if k != "segments"}
        row.update(earlier_evaluable_bars=0, earlier_candidates=0, earlier_matched_controls=0,
                   earlier_source_bars=0, segments=len(market["segments"]))
        for segment in market["segments"]:
            features = _load_features(segment)
            features = features.loc[features.index < EARLIER_END].copy()
            row["earlier_source_bars"] += len(features)
            if not len(features):
                continue
            closes = features.index + pd.Timedelta(hours=1)
            inside = (closes >= EARLIER_START) & (closes < EARLIER_END)
            row["earlier_evaluable_bars"] += int((inside & features.ready.to_numpy(dtype=bool)).sum())
            if market["exclude_reason"]:
                continue
            candidates = engine.candidate_events(features, 60)
            if not segment["young_source_allowed"]:
                candidates = candidates.loc[candidates.arm != "young_breakout_sma20"]
            selected = candidates.loc[candidates.arm.eq("focus_md")
                                      & candidates.decision_time.ge(EARLIER_START)
                                      & candidates.decision_time.lt(EARLIER_END)]
            matches = match_controls(features, candidates, selected, segment["instrument"])
            job = dict(segment, last_i=len(features) - 1,
                       last_open_time=features.index[-1].isoformat(),
                       decisions=[int(x) for x in selected.decision_i],
                       matching={str(k): v for k, v in matches.items()})
            if len(selected):
                jobs.append(job)
            row["earlier_candidates"] += len(selected)
            row["earlier_matched_controls"] += sum(len(x) for x in matches.values())
        rows.append(row)
    matching_path = Path(output) / "earlier_matching.json"
    atomic_json(matching_path, dict(config=CONFIG, generated_at=utc_now(), jobs=jobs,
                                   returns_accessed=False, outcomes_simulated=False))
    return jobs, rows, matching_path


def _context(features, i):
    row = {name: features[name].iloc[i] for name in engine.CONTEXT_COLUMNS if name in features}
    row.update(signal_i=i, decision_i=i, minutes=60, decision_bar_open_time=features.index[i],
               decision_time=features.index[i] + pd.Timedelta(hours=1),
               signal_quote_volume=float(features.quote_volume.iloc[i]),
               higher_permission=features.higher_permission.iloc[i] if "higher_permission" in features else np.nan)
    return row


def simulate_earlier_jobs(jobs):
    """Replay only the frozen positions with snapshots cut off before July10."""
    events, controls = [], []
    for job in jobs:
        features = _load_features(job)
        features = features.loc[features.index < EARLIER_END].copy()
        last_i = job["last_i"]
        if len(features) - 1 != last_i or features.index[-1].isoformat() != job["last_open_time"]:
            raise ValueError("Frozen earlier boundary changed")
        prepared = engine.prepare_simulation(features, features, last_i)
        common = {k: job[k] for k in ("venue", "symbol", "asset", "instrument", "features_path")}
        common.update(arm="focus_md", minutes=60, exit_rule="md", period="earlier", research_period="earlier")
        cache = {}

        def outcome(i):
            if i not in cache:
                cache[i] = engine.simulate_prepared(prepared, i, "md", last_i)
            return dict(cache[i])

        for i in job["decisions"]:
            event_id = _id(job["instrument"], 60, "focus_md", int(features.index[i].value))
            event = dict(_context(features, i), **outcome(i))
            event.update(common, event_id=event_id)
            valid_net = []
            for number, control_i in enumerate(job["matching"][str(i)]):
                control = dict(_context(features, control_i), **outcome(control_i))
                control.update(common, event_id=_id(event_id, "control", number, control_i),
                               matched_event_id=event_id, matched_decision_i=i,
                               matched_decision_time=event["decision_time"], control_number=number)
                controls.append(control)
                if control.get("valid"):
                    valid_net.append(float(control["net_bp"]))
            event["matched_control_count"] = len(job["matching"][str(i)])
            event["control_count"] = len(valid_net)
            event["control_mean_net_bp"] = float(np.mean(valid_net)) if valid_net else np.nan
            event["excess_bp"] = event.get("net_bp", np.nan) - event["control_mean_net_bp"] if event.get("valid") else np.nan
            events.append(event)
    base_columns = ["event_id", "venue", "symbol", "asset", "instrument", "features_path", "minutes", "arm", "exit_rule", "period", "research_period", "decision_i", "decision_time", "valid", "net_bp", "signal_quote_volume"]
    return (pd.DataFrame(events) if events else pd.DataFrame(columns=base_columns),
            pd.DataFrame(controls) if controls else pd.DataFrame(columns=base_columns + ["matched_event_id", "control_number"]))


def read_known(verified):
    """Select original1H focus rows with round-trip float parsing; no replay."""
    frames = []
    for name in ("events.csv.gz", "controls.csv.gz"):
        # Already hashed before matching freeze; recheck immediately before read.
        path = verified["paths"][name]
        record = next(x for x in verified["manifest"]["artifacts"] if Path(x["path"]).name == name)
        _check(path, record["sha256"])
        f = pd.read_csv(path, float_precision="round_trip")
        f = f.loc[f.minutes.eq(60) & f.arm.eq("focus_md")].copy()
        if f.event_id.duplicated().any():
            raise ValueError("Duplicate known event id")
        clocks = pd.to_datetime(f.decision_time, utc=True)
        if not (clocks.ge(EARLIER_END) & clocks.lt(KNOWN_END)).all():
            raise ValueError("Known decisions cross the frozen window")
        for (path_value, instrument, venue, symbol, asset), group in f.groupby(
                ["features_path", "instrument", "venue", "symbol", "asset"], dropna=False):
            path_value = str(Path(path_value).resolve())
            if path_value not in verified["feature_index"]:
                raise ValueError("Known event points to an unverified feature artifact")
            meta = verified["feature_index"][path_value]
            if (meta["instrument"], meta["venue"], meta["symbol"], meta["asset"]) != (instrument, venue, symbol, asset) or meta["exclude_reason"]:
                raise ValueError("Known event violates frozen identity/exclusion")
        f["research_period"] = "known"
        frames.append(f)
    if not set(frames[1].matched_event_id).issubset(set(frames[0].event_id)):
        raise ValueError("Known controls have unmatched focus event ids")
    return tuple(frames)


def build_dataset(old_experiment=OLD_EXPERIMENT, output=OUTPUT):
    """Produce frozen earlier/known event-control CSVs and SHA lineage."""
    out = Path(output).resolve()
    old = Path(old_experiment).resolve()
    if out == old or old in out.parents:
        raise ValueError("New outputs must not overwrite the previous experiment")
    out.mkdir(parents=True, exist_ok=True)
    if (out / "dataset_started.json").exists() or (out / "dataset_manifest.json").exists() or (out / "earlier_matching.json").exists():
        raise ValueError("Existing evaluation attempt; preserve it and use a separately recorded attempt directory")
    atomic_json(out / "dataset_started.json", dict(generated_at=utc_now(), holdout_consumption=1,
        code_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        status="started", old_experiment=str(old)))
    verified = load_verified_coverage(old_experiment)
    jobs, coverage, matching_path = freeze_earlier_matching(verified, out)
    # The matching file exists for all markets before this first simulation.
    matching_hash = sha256(matching_path)
    earlier_events, earlier_controls = simulate_earlier_jobs(jobs)
    known_events, known_controls = read_known(verified)
    outputs = [matching_path]
    for name, frame in (("earlier_events", earlier_events), ("earlier_controls", earlier_controls),
                        ("known_events", known_events), ("known_controls", known_controls)):
        path = out / (name + ".csv.gz")
        _csv(path, frame)
        outputs.append(path)
    known_counts = known_events.groupby(["venue", "symbol"]).size().to_dict()
    for row in coverage:
        row["known_candidates"] = int(known_counts.get((row["venue"], row["symbol"]), 0))
    coverage_path = out / "coverage.csv"
    _csv(coverage_path, pd.DataFrame(coverage))
    outputs.append(coverage_path)
    if sha256(matching_path) != matching_hash:
        raise ValueError("Matching plan changed during simulation")
    manifest = dict(config=CONFIG, generated_at=utc_now(),
                    old_experiment=str(Path(old_experiment).resolve()),
                    old_results_manifest=artifact(verified["manifest_path"]),
                    old_input_artifacts=[artifact(p) for p in verified["paths"].values()],
                    feature_sources=[dict(path=path, sha256=meta["features_sha256"])
                                     for path, meta in verified["feature_index"].items()],
                    markets=len(coverage), feature_artifacts_verified=len(verified["feature_index"]),
                    earlier_events=len(earlier_events), earlier_controls=len(earlier_controls),
                    known_events=len(known_events), known_controls=len(known_controls),
                    matching_frozen_before_returns=True, artifacts=[artifact(p) for p in outputs],
                    code_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                    code_sources=[artifact(Path(__file__)), artifact(Path(engine.__file__))],
                    holdout_consumption=1, optimization=False, production_eligible=False, training_eligible=False)
    atomic_json(out / "dataset_manifest.json", manifest)
    return manifest


def _verify_committed(old_experiment):
    prior = json.loads((Path(old_experiment) / "RESULTS_MANIFEST.json").read_text())
    for path in (Path(__file__).resolve(), Path(engine.__file__).resolve()):
        rel = str(path.relative_to(ROOT))
        committed = subprocess.check_output(["git", "show", "HEAD:" + rel], cwd=ROOT)
        if committed != path.read_bytes():
            raise ValueError("Commit builder before historical scoring: " + rel)
    expected = prior["evaluation"]["source_hashes"]["yoyo/evaluation/altseason_engine.py"]
    _check(Path(engine.__file__), expected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-experiment", type=Path, default=OLD_EXPERIMENT)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    _verify_committed(args.old_experiment)
    print(json.dumps(build_dataset(args.old_experiment, args.output), ensure_ascii=False, default=str), flush=True)


if __name__ == "__main__":
    main()
