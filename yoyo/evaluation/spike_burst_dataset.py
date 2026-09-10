"""Freeze SPIKE V1 candidates and matched controls before computing returns.

The prior multivenue experiment authenticates source/coverage/1H pickles; this
builder additionally authenticates its 4H pickles and catalog market objects.
New replay inputs are only their OHLCV columns. Old ready/release_side remains
the frozen IMACD entry baseline. Both use independent next-open execution.

Matching uses current/prior ATR/close rolling240/min60 quintiles, a UTC close
week and history_count>=340. A random decision excludes a signal at that bar
or in its preceding12 bars, never a future signal. Matching is nevertheless a
retrospective conditional event-study design, not an executable random system.
All markets' candidates and controls are saved before ANY outcome simulation.
No source catalog, prior experiment, strategy default or live service is edited.
"""
from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.data.altseason_sources import atomic_json, utc_now
from yoyo.evaluation.launch_quality_dataset import (
    ROOT, OLD_EXPERIMENT, load_verified_coverage, sha256, artifact,
    _check, _resolve, _csv, _id, _week,
)
from yoyo.evaluation import spike_burst_replay as replay_engine
from yoyo.evaluation.spike_burst_execution import simulate_trade

EXPERIMENT = ROOT / "experiments/active/exp-spike-burst-validation-20260910-v1"
OUTPUT = EXPERIMENT / "results"
START, END = pd.Timestamp("2026-07-10T00:00:00Z"), pd.Timestamp("2026-09-09T00:00:00Z")
MINUTES = (60, 240)
ARMS = ("burst_trail", "focus_trail")
OHLCV = ("open", "high", "low", "close", "volume", "quote_volume")
CONFIG = dict(schema="spike-burst-dataset-v1", start=START.isoformat(),
    exclusive_end=END.isoformat(), minutes=list(MINUTES), arms=list(ARMS),
    seed=20260910, controls_maximum=3, rank_window=240, rank_minimum=60,
    control_history_minimum=340, candidate_exclusion="current and previous12 only",
    same_decision_arms="share matching positions", control_reuse="allowed across events; disclosed",
    retrospective=True, optimization=False, holdout_consumption=1)
EVENT_COLUMNS = ["event_id", "matched_event_id", "control_number", "arm", "exit_rule",
    "instrument", "asset", "venue", "symbol", "minutes", "tick", "features_path", "decision_i",
    "decision_time", "relative_volume", "tr_expansion", "near_zero_bars", "route",
    "signal_quote_volume", "prior24h_quote_volume", "valid", "invalid_reason",
    "entry_time", "exit_time", "exit_time_lower", "exit_time_upper", "exit_timing",
    "entry_price", "exit_price", "initial_stop", "initial_risk", "initial_risk_frac",
    "net_return", "net_r", "gross_return", "gross_bp", "net_bp", "peak_r",
    "natural_exit", "censored", "exit_reason", "hold_bars"]


def market_hash(market):
    """Match altseason_dataset._json_hash, including its JSON whitespace."""
    return hashlib.sha256(json.dumps(market, sort_keys=True, ensure_ascii=False,
                                    default=str).encode()).hexdigest()


def market_tick(market):
    """Use frozen native order-price tick; never infer it from OHLC prices."""
    venue, raw = market["venue"], market["raw"]
    if venue == "okx":
        value, origin = raw.get("tickSz"), "raw.tickSz"
    elif venue == "binance":
        found = [r for r in raw.get("filters", []) if r.get("filterType") == "PRICE_FILTER"]
        if len(found) != 1:
            raise ValueError("Expected exactly one Binance PRICE_FILTER")
        value, origin = found[0].get("tickSize"), "raw.filters.PRICE_FILTER.tickSize"
    elif venue == "gate":
        value, origin = raw.get("order_price_round"), "raw.order_price_round"
    else:
        raise ValueError("Unsupported catalog venue: " + str(venue))
    try:
        tick = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Missing or invalid native price tick") from exc
    if not tick.is_finite() or tick <= 0 or not np.isfinite(float(tick)) or float(tick) <= 0:
        raise ValueError("Native price tick must be finite and positive")
    return str(tick), origin


def verify_inputs(old_experiment=OLD_EXPERIMENT, catalog_dir=None):
    """Authenticate all timeframes before deserializing any feature pickle."""
    old = Path(old_experiment).resolve()
    verified = load_verified_coverage(old)
    catalogs, markets, sources = {}, {}, []
    directory = Path(catalog_dir) if catalog_dir else old / "data/catalog"
    for venue in sorted({r["venue"] for r in verified["coverages"]}):
        path = directory / (venue + ".json")
        catalog = json.loads(path.read_text())
        catalogs[venue] = catalog
        sources.append(artifact(path))
        for market in catalog["markets"]:
            key = market["venue"], market["symbol"]
            if key in markets or key[0] != venue:
                raise ValueError("Duplicate/mismatched market in frozen catalog")
            markets[key] = market
    coverages, segments = [], []
    for summary in verified["coverages"]:
        path = _check(summary["coverage_path"], summary["coverage_sha256"])
        row = json.loads(path.read_text())
        key = row["venue"], row["symbol"]
        market = markets.get(key)
        if market is None or market_hash(market) != row["identity"].get("market_hash"):
            raise ValueError("Frozen catalog market hash mismatch: " + str(key))
        tick, tick_origin, tick_error = None, None, ""
        try:
            tick, tick_origin = market_tick(market)
        except ValueError as exc:
            tick_error = str(exc)
        coverage = dict(summary, segments=[], market_hash=market_hash(market),
                        tick=tick, tick_origin=tick_origin, tick_error=tick_error)
        records = {_resolve(a["path"], old): a for a in row["artifacts"]}
        for segment in row["segments"]:
            for minutes in MINUTES:
                feature = (path.parent / ("segment%d_%d_features.pkl.gz" % (segment["segment"], minutes))).resolve()
                if feature not in records:
                    raise ValueError("Timeframe feature absent from frozen aggregate: " + str(feature))
                record = records[feature]
                _check(feature, record["sha256"])
                if "size_bytes" in record and feature.stat().st_size != record["size_bytes"]:
                    raise ValueError("Feature size mismatch: " + str(feature))
                item = dict(venue=row["venue"], symbol=row["symbol"], asset=row["asset"],
                    instrument=segment["instrument"], minutes=minutes,
                    source_features_path=str(feature), source_features_sha256=record["sha256"],
                    tick=tick, tick_origin=tick_origin,
                    exclude_reason=row.get("exclude_reason", ""), tick_error=tick_error,
                    young_source_allowed=bool(segment.get("young_source_allowed", False)))
                coverage["segments"].append(item)
                segments.append(item)
        coverages.append(coverage)
    verified.update(coverages=coverages, segments=segments, catalog_sources=sources)
    return verified


def load_feature(path, expected_hash, minutes, end=END):
    """Verify before pickle load; preserve continuous source positions and warmup."""
    frame = pd.read_pickle(_check(path, expected_hash))
    index = frame.index
    if (not isinstance(index, pd.DatetimeIndex) or index.tz is None
            or str(index.tz) not in ("UTC", "Etc/UTC", "GMT", "Etc/GMT")
            or index.hasnans or not index.is_unique or not index.is_monotonic_increasing):
        raise ValueError("Feature index must be unique chronological UTC")
    index = index.as_unit("ns")
    step = pd.Timedelta(minutes=minutes)
    if np.any(index.asi8 % step.value) or (len(index) > 1 and np.any(np.diff(index.asi8) != step.value)):
        raise ValueError("Feature segment is not continuous/aligned; no gap filling allowed")
    if not frame.columns.is_unique:
        raise ValueError("Feature columns must be unique")
    frame = frame.loc[index + step <= end].copy()
    frame.attrs.update(minutes=minutes, period_seconds=minutes * 60)
    return frame


def match_controls(features, all_candidates, evaluation_candidates, instrument, minutes,
                   start=START, end=END):
    """Select indices from causal ATR%, known-past exclusions, never returns."""
    if len(features) and features.index[-1] + pd.Timedelta(minutes=minutes) > end:
        raise ValueError("Matching feature tail extends beyond cutoff")
    rank = features.atr_pct.rolling(240, min_periods=60).rank(pct=True)
    buckets = np.ceil(rank.to_numpy(dtype=float) * 5)
    closes = features.index + pd.Timedelta(minutes=minutes)
    weeks = _week(closes).asi8
    excluded = np.zeros(len(features), dtype=bool)
    for value in set(all_candidates):
        i = int(value)
        if not 0 <= i < len(features):
            raise ValueError("Candidate index outside source")
        # At random bar j only events in [j-12,j] are known. No future test.
        excluded[i:min(len(features), i + 13)] = True
    pool = (~excluded & np.isfinite(buckets) & (closes >= start) & (closes < end)
            & features.history_count.ge(340).to_numpy())
    if len(pool):
        pool[-1] = False
    seed = (20260910 + int(hashlib.sha256((instrument + ":" + str(minutes)).encode()).hexdigest()[:16], 16)) % (2 ** 64)
    rng = np.random.default_rng(seed)
    matches = {}
    for i in sorted(set(int(x) for x in evaluation_candidates)):
        if not 0 <= i < len(features):
            raise ValueError("Evaluation candidate outside source")
        choices = np.flatnonzero(pool & (weeks == weeks[i]) & (buckets == buckets[i]))
        matches[i] = [int(x) for x in rng.choice(choices, size=min(3, len(choices)), replace=False)]
    return matches


def prepare_job(segment, output, start=START, end=END):
    """Replay only confirmed input history; write a separate immutable feature file."""
    old = load_feature(segment["source_features_path"], segment["source_features_sha256"], segment["minutes"], end)
    required = set(OHLCV) | {"ready", "release_side", "near_zero_bars", "history_count", "atr_pct", "prior24h_quote_volume"}
    if not required.issubset(old):
        raise ValueError("Frozen baseline features missing: " + str(sorted(required - set(old))))
    if not len(old):
        return None
    frame = replay_engine.features(old[list(OHLCV)])
    state = replay_engine.replay(frame, float(segment["tick"]))
    if not frame.index.equals(old.index) or not state.index.equals(old.index):
        raise ValueError("Replay changed original feature index")
    # Replay state columns are prefixed to preserve causal feature meanings.
    frame = frame.join(state.add_prefix("replay_"))
    frame["old_ready"] = old.ready
    frame["old_release_side"] = old.release_side
    frame["old_near_zero_bars"] = old.near_zero_bars
    frame["history_count"] = old.history_count
    frame["atr_pct"] = old.atr_pct
    frame["prior24h_quote_volume"] = old.prior24h_quote_volume
    frame.attrs.update(minutes=segment["minutes"], period_seconds=segment["minutes"] * 60)
    closes = frame.index + pd.Timedelta(minutes=segment["minutes"])
    window = (closes >= start) & (closes < end)
    masks = {"burst_trail": state.burst.eq(True).to_numpy(),
             "focus_trail": (old.ready.eq(True) & old.release_side.eq(1)).to_numpy()}
    all_indices = sorted(set(np.flatnonzero(masks["burst_trail"])) | set(np.flatnonzero(masks["focus_trail"])))
    decisions = {arm: np.flatnonzero(mask & window).astype(int).tolist() for arm, mask in masks.items()}
    matching = match_controls(frame, all_indices, sum(decisions.values(), []), segment["instrument"], segment["minutes"], start, end)
    path = Path(output) / "features" / (_id(segment["instrument"], segment["minutes"]) + ".pkl.gz")
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_pickle(path, compression={"method": "gzip", "mtime": 0})
    return dict(segment, features_path=str(path.resolve()), features_sha256=sha256(path),
                bars=len(frame), first_open=frame.index[0].isoformat(), last_open=frame.index[-1].isoformat(),
                all_candidate_indices=[int(i) for i in all_indices], decisions=decisions,
                matching={str(i): js for i, js in matching.items()})


def schedule_rows(jobs):
    """Expand immutable event identities without evaluating outcome columns."""
    actual, controls = [], []
    for job in jobs:
        for arm in ARMS:
            for i in job["decisions"][arm]:
                eid = _id(job["instrument"], job["minutes"], arm, i)
                base = dict(event_id=eid, matched_event_id="", control_number=-1,
                    instrument=job["instrument"], minutes=job["minutes"], arm=arm, decision_i=i,
                    features_path=job["features_path"])
                actual.append(base)
                for k, j in enumerate(job["matching"][str(i)]):
                    controls.append(dict(base, event_id=_id(eid, "control", k, j),
                        matched_event_id=eid, control_number=k, decision_i=j))
    return actual, controls


def score_jobs(jobs, output, end=END):
    """Second pass: require frozen global matching before reading any outcomes."""
    output = Path(output)
    frozen = json.loads((output / "matching.json").read_text())
    if frozen["jobs"] != jobs:
        raise ValueError("Frozen matching does not match scoring jobs")
    for item in frozen["schedule_artifacts"]:
        _check(item["path"], item["sha256"])
    actual_schedule, control_schedule = schedule_rows(jobs)
    by_path = {}
    for row in actual_schedule + control_schedule:
        by_path.setdefault(row["features_path"], []).append(row)
    actual, controls = [], []
    for job in jobs:
        path = job["features_path"]
        frame = load_feature(path, job["features_sha256"], job["minutes"], end)
        cache = {}
        for identity in by_path.get(path, []):
            i = identity["decision_i"]
            if i not in cache:
                cache[i] = simulate_trade(frame, i, float(job["tick"]), end)
            source = frame.iloc[i]
            control = bool(identity["matched_event_id"])
            burst = identity["arm"] == "burst_trail"
            row = dict(identity, asset=job["asset"], venue=job["venue"], symbol=job["symbol"], tick=float(job["tick"]),
                exit_rule="burst_trail", decision_time=frame.index[i] + pd.Timedelta(minutes=job["minutes"]),
                relative_volume=float(source.rv), tr_expansion=float(source.expansion),
                near_zero_bars=float(source.replay_quiet_bars if burst else source.old_near_zero_bars),
                route="matched_random" if control else (str(source.replay_route) if burst else "focus_release"),
                signal_quote_volume=float(source.quote_volume), prior24h_quote_volume=float(source.prior24h_quote_volume))
            row.update(cache[i])
            row.setdefault("gross_return", np.nan)
            row.setdefault("gross_bp", np.nan)
            row.setdefault("net_bp", np.nan)
            (controls if control else actual).append(row)
        print("scored", job["instrument"], job["minutes"], "unique decisions", len(cache), flush=True)
    return pd.DataFrame(actual).reindex(columns=list(dict.fromkeys(EVENT_COLUMNS + [k for r in actual for k in r]))), \
        pd.DataFrame(controls).reindex(columns=list(dict.fromkeys(EVENT_COLUMNS + [k for r in controls for k in r])))


def _verify_committed():
    """Require exact committed builders, protocol and Pine before any dataset run."""
    paths = [Path(__file__), Path(replay_engine.__file__), ROOT / "yoyo/evaluation/spike_burst_execution.py",
             ROOT / "yoyo/evaluation/launch_quality_dataset.py", ROOT / "yoyo/data/altseason_sources.py",
             EXPERIMENT / "PROJECT_PLAN.md", ROOT / "yoyo/evaluation/pine/spike_burst_v1.pine"]
    for path in paths:
        relative = path.resolve().relative_to(ROOT)
        saved = subprocess.check_output(["git", "show", "HEAD:" + str(relative)], cwd=ROOT)
        if saved != path.read_bytes():
            raise ValueError("Commit exact source before dataset build: " + str(relative))
    pine = paths[-1]
    if sha256(pine) != replay_engine.SOURCE_SHA256:
        raise ValueError("Frozen Pine/replay source hash mismatch")
    return {str(p.resolve().relative_to(ROOT)): sha256(p) for p in paths}


def build_dataset(old_experiment=OLD_EXPERIMENT, output=OUTPUT, catalog_dir=None):
    """Authenticate, freeze the complete schedule, then simulate the fixed arms."""
    old, output = Path(old_experiment).resolve(), Path(output).resolve()
    if output == old or old in output.parents:
        raise ValueError("Output must not modify the previous experiment")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Refusing frozen output overwrite; use a fresh explicit result directory")
    sources = _verify_committed()
    output.mkdir(parents=True, exist_ok=True)
    started = dict(status="started", generated_at=utc_now(), config=CONFIG,
        code_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        source_hashes=sources)
    atomic_json(output / "dataset_started.json", started)
    verified = verify_inputs(old, catalog_dir)
    prior_manifest = artifact(verified["manifest_path"])
    prior_aggregate_sources = [artifact(path) for path in verified["paths"].values()]
    jobs, coverage_rows = [], []
    for coverage in verified["coverages"]:
        base = {k: coverage[k] for k in ("venue", "symbol", "asset", "exclude_reason", "tick", "tick_origin", "tick_error")}
        if not coverage["segments"]:
            coverage_rows.append(dict(base, status="no_continuous_segments", minutes=None))
        for segment in coverage["segments"]:
            status = "excluded" if segment["exclude_reason"] else ("missing_native_tick" if segment["tick_error"] else "included")
            job = prepare_job(segment, output) if status == "included" else None
            if status == "included" and job is None:
                status = "no_complete_bars_before_end"
            if job:
                jobs.append(job)
                if len(jobs) % 100 == 0:
                    print("frozen feature/matching jobs", len(jobs), flush=True)
            coverage_rows.append(dict(base, instrument=segment["instrument"], minutes=segment["minutes"],
                source_features_path=segment["source_features_path"], status=status,
                bars=job["bars"] if job else 0,
                burst_candidates=len(job["decisions"]["burst_trail"]) if job else 0,
                focus_candidates=len(job["decisions"]["focus_trail"]) if job else 0))
    actual, controls = schedule_rows(jobs)
    schedules = []
    schedule_columns = ["event_id", "matched_event_id", "control_number", "instrument", "minutes", "arm", "decision_i", "features_path"]
    for name, rows in (("candidate_schedule.csv.gz", actual), ("control_schedule.csv.gz", controls)):
        path = output / name
        _csv(path, pd.DataFrame(rows, columns=schedule_columns))
        schedules.append(artifact(path))
    coverage_path = output / "coverage.csv"
    _csv(coverage_path, pd.DataFrame(coverage_rows))
    reused = Counter((r["instrument"], r["minutes"], r["decision_i"]) for r in controls)
    matching = dict(schema=CONFIG["schema"], generated_at=utc_now(), jobs=jobs,
        schedule_artifacts=schedules, source_hashes=sources,
        candidate_rows=len(actual), control_rows=len(controls), unique_control_positions=len(reused),
        reused_control_positions=sum(n > 1 for n in reused.values()),
        maximum_control_reuse=max(reused.values(), default=0),
        caveat="Same-week retrospective matching; cross-event/arm reuse is not independent evidence")
    atomic_json(output / "matching.json", matching)
    # No execution function can be reached until every job and match above exists.
    events, controls_frame = score_jobs(jobs, output)
    for name, frame in (("events.csv.gz", events), ("controls.csv.gz", controls_frame)):
        _csv(output / name, frame)
    if _verify_committed() != sources:
        raise ValueError("Source changed during dataset build")
    # Keep the receipt tied to the inputs actually authenticated, not a newer
    # catalog/manifest that another process might have written during scoring.
    for item in [prior_manifest] + prior_aggregate_sources + verified["catalog_sources"]:
        _check(item["path"], item["sha256"])
    feature_sources = [artifact(job["features_path"]) for job in jobs]
    output_paths = [output / name for name in ("dataset_started.json", "matching.json", "coverage.csv",
        "candidate_schedule.csv.gz", "control_schedule.csv.gz", "events.csv.gz", "controls.csv.gz")]
    manifest = dict(started, status="complete", completed_at=utc_now(),
        prior_manifest=prior_manifest,
        prior_aggregate_sources=prior_aggregate_sources,
        catalog_sources=verified["catalog_sources"],
        source_feature_artifacts=[dict(path=s["source_features_path"], sha256=s["source_features_sha256"],
            instrument=s["instrument"], minutes=s["minutes"], exclude_reason=s["exclude_reason"])
            for s in verified["segments"]],
        coverage_market_count=len(verified["coverages"]), coverage_job_count=len(coverage_rows),
        prepared_jobs=len(jobs), event_count=len(events), control_count=len(controls_frame),
        coverage_status_counts=dict(Counter(r["status"] for r in coverage_rows)),
        control_reuse={k: matching[k] for k in ("unique_control_positions", "reused_control_positions", "maximum_control_reuse")},
        feature_sources=feature_sources, artifacts=[artifact(p) for p in output_paths] + feature_sources,
        limitations=["Retrospective previously viewed period, not unseen out-of-sample validation",
            "As-of catalog is not a complete historical delisted universe",
            "Frozen catalog ticks do not reconstruct historical tick-size changes",
            "20bp entry-notional round-trip cost excludes complete funding and impact",
            "Random controls are conditional retrospective matches, with disclosed reuse"])
    atomic_json(output / "dataset_manifest.json", manifest)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-experiment", type=Path, default=OLD_EXPERIMENT)
    parser.add_argument("--out", type=Path, default=OUTPUT)
    parser.add_argument("--catalog-dir", type=Path)
    args = parser.parse_args()
    build_dataset(args.old_experiment, args.out, args.catalog_dir)
