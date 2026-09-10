"""Pre-register and evaluate OKX 1H progressive-entry recall, without accounts.

Signals consume only confirmed candles and the frozen V1/progressive replays.
Independent event anchors use ready, close, prior twelve highs and prior anchor
indices (24-bar refractory BEFORE labels). Only labels inspect the next 24
closes/highs: frozen-ATR +4 before -2. Labels never enter the replay or matching.
Random matches use ready, history_count, causal ATR% ranks and same UTC week;
current/past-12 signals are excluded, not future signals or profitable labels.

prepare authenticates the old 61-day study and freezes EVERY label, signal and
control before evaluate can call the unchanged next-open execution engine.
Only small CSV/JSON artifacts are written; original features remain read-only.
This is a previously seen retrospective event study, not a new holdout, account
return, classifier training, or a guarantee of the target 80% timely recall.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_burst_replay as v1
from yoyo.evaluation.spike_burst_dataset import load_feature, match_controls
from yoyo.evaluation.spike_burst_execution import simulate_trade
from yoyo.evaluation.altseason_research import paired_test, holm, rank_auc

ROOT = Path(__file__).resolve().parents[2]
PRIOR = ROOT / "experiments/active/exp-spike-burst-validation-20260910-v1/results"
PRIOR_MANIFEST_SHA = "e3faf5cbe51711a36faf07b5abbfcbb461748a4bfb698019ed1a78cec122ba51"
PRIOR_MATCHING_SHA = "4f56398d228f3a87daf4bbbb7cfc0f34910ab7ab05d8c2a957abd414b5cd09ff"
EXPERIMENT = ROOT / "experiments/active/exp-spike-burst-launch-recall-20260910-v2"
START = pd.Timestamp("2026-07-10T00:00Z")
SPLIT = pd.Timestamp("2026-08-10T00:00Z")
END = pd.Timestamp("2026-09-09T00:00Z")
HOUR = pd.Timedelta(hours=1)
NIGHT = (pd.Timestamp("2026-08-19T10:00Z"), pd.Timestamp("2026-08-19T22:00Z"))
SNAPSHOT_OPENS = (pd.Timestamp("2026-08-19T14:00Z"), pd.Timestamp("2026-08-19T15:00Z"))
ARMS = ("v1", "v2")
CONFIG = dict(schema="spike-progressive-recall-v2", start=START.isoformat(),
    exclusive_end=END.isoformat(), venue="okx", minutes=60,
    anchor_prior_highs=12, refractory_bars=24, future_bars=24,
    label_target_atr=4, label_adverse_atr=2, large_peak_return=.08,
    recall_lags=[0, 1, 2, 6], target_recall=.80, target_max_lag=1,
    precision_match_lags=[0, 6], controls=3, seed=20260910,
    retrospective=True, optimization=False, training=False,
    label_clock="confirmed close", execution="unchanged next real open",
    precision_definition="earliest unique signal matching a positive event in [0,+6]; not trade win rate")
LABEL_COLUMNS = ["event_i", "bar_open", "decision_time", "entry_reference", "frozen_atr",
    "label", "unknown_reason", "target_lag", "adverse_lag", "future_peak_return",
    "large_peak", "in_study"]
SIGNAL_COLUMNS = ["event_id", "instrument", "asset", "symbol", "venue", "minutes",
    "arm", "decision_i", "bar_open", "decision_time", "route", "relative_volume",
    "tr_expansion", "signal_close", "signal_atr", "matched_label_id", "match_status", "lag",
    "move_since_anchor_atr", "move_since_anchor_pct", "in_study"]
CONTROL_COLUMNS = ["event_id", "matched_event_id", "instrument", "asset", "symbol",
    "venue", "minutes", "arm", "control_number", "decision_i", "decision_time"]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def identity(*values):
    return hashlib.sha256(":".join(map(str, values)).encode()).hexdigest()[:28]


def checked(path, digest):
    path = Path(path).resolve()
    if sha(path) != digest:
        raise ValueError("Changed authenticated source: " + str(path))
    return path


def artifact(path):
    path = Path(path).resolve()
    return dict(path=str(path), sha256=sha(path), size_bytes=path.stat().st_size)


def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, (np.integer, np.bool_)):
        return value.item()
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, (pd.Timestamp, Path)):
        return str(value)
    return value


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(clean(value), indent=2, allow_nan=False) + "\n")


def write_csv(path, frame):
    compression = {"method": "gzip", "mtime": 0} if str(path).endswith(".gz") else None
    frame.to_csv(path, index=False, compression=compression)


def _clock(frame):
    index = frame.index
    if (not isinstance(index, pd.DatetimeIndex) or index.tz is None or str(index.tz) not in ("UTC", "Etc/UTC", "GMT")
            or not index.is_unique or not index.is_monotonic_increasing or index.hasnans
            or np.any(index.asi8 % HOUR.value)):
        raise ValueError("Expected unique ordered UTC open-stamped hourly candles")
    return index


def label_events(frame, start=START, end=END):
    """Independent price anchors; outcomes use t+1..t+24 closes, never a wick.

Refractory is applied to ALL first-cross anchors, including negative/unknown
ones, across the entire source segment before cutting the reporting window.
Missing full outcome windows stay unknown even if an early barrier was hit.
"""
    index = _clock(frame)
    required = {"high", "close", "atr", "ready"}
    if not required.issubset(frame):
        raise ValueError("Independent labels require high/close/atr/ready")
    previous_high = frame.high.shift(1).rolling(12, min_periods=12).max()
    above = frame.close.gt(previous_high)
    crossing = above & ~above.shift(1, fill_value=False)
    candidates = np.flatnonzero((crossing & frame.ready.eq(True) & frame.atr.gt(0)
                                & np.isfinite(frame.atr) & np.isfinite(frame.close)).to_numpy())
    rows, last = [], -1000000
    for i in candidates:
        if i - last < 24:
            continue
        # A gap in anchor history is not a genuine twelve-hour range.
        if i < 12 or np.any(np.diff(index[i-12:i+1].asi8) != HOUR.value):
            continue
        last = int(i)
        c, atr = float(frame.close.iloc[i]), float(frame.atr.iloc[i])
        closes = frame.close.iloc[i+1:i+25].to_numpy(float)
        highs = frame.high.iloc[i+1:i+25].to_numpy(float)
        stamp = index[i] + HOUR
        reason = ""
        if len(closes) != 24:
            reason = "insufficient_future_24"
        elif np.any(np.diff(index[i:i+25].asi8) != HOUR.value):
            reason = "future_gap"
        elif not np.isfinite(closes).all() or not np.isfinite(highs).all():
            reason = "nonfinite_future"
        target = np.flatnonzero(closes >= c + 4 * atr) if not reason else []
        adverse = np.flatnonzero(closes <= c - 2 * atr) if not reason else []
        target_lag = int(target[0] + 1) if len(target) else None
        adverse_lag = int(adverse[0] + 1) if len(adverse) else None
        positive = target_lag is not None and (adverse_lag is None or target_lag < adverse_lag)
        peak = float(np.max(highs) / c - 1) if not reason and c > 0 else np.nan
        rows.append(dict(event_i=int(i), bar_open=index[i], decision_time=stamp,
            entry_reference=c, frozen_atr=atr, label="unknown" if reason else "positive" if positive else "negative",
            unknown_reason=reason, target_lag=target_lag, adverse_lag=adverse_lag,
            future_peak_return=peak, large_peak=bool(peak >= .08) if np.isfinite(peak) else None,
            in_study=bool(start <= stamp < end)))
    return pd.DataFrame(rows, columns=LABEL_COLUMNS)


def match_signals(labels, signal_indices, frame, state):
    """One earliest signal per positive event, strictly at/after its close.

Active tracking is reported separately; it is never substituted for an arrow.
Unknown tail signals stay in the raw precision denominator and are also split
out so readers can see precision among adjudicated signals.
"""
    _clock(frame)
    signals = sorted(set(int(i) for i in signal_indices))
    matched, used = [], set()
    positive = labels.loc[labels.label.eq("positive")]
    unknown = labels.loc[labels.label.eq("unknown")]
    for i in signals:
        eligible = positive.loc[positive.event_i.le(i) & positive.event_i.ge(i - 6)]
        if len(eligible) > 1:
            raise ValueError("Overlapping positive events violate refractory")
        label_i, lag = None, None
        if len(eligible):
            label_i = int(eligible.iloc[0].event_i)
            lag = i - label_i
            status = "duplicate" if label_i in used else "matched"
            used.add(label_i)
        elif len(unknown.loc[unknown.event_i.le(i) & unknown.event_i.ge(i - 6)]) or i + 24 >= len(frame):
            status = "unknown"
        else:
            status = "unmatched"
        matched.append(dict(decision_i=i, match_status=status, label_i=label_i, lag=lag))
    detections = []
    for label in labels.itertuples():
        i = int(label.event_i)
        hits = [row for row in matched if row["label_i"] == i and row["match_status"] == "matched"]
        lag = hits[0]["lag"] if hits else None
        active = bool(i > 0 and state.trend_side.iloc[i-1] == 1 and state.trend_side.iloc[i] == 1
                      and not bool(state.burst.iloc[i]))
        detections.append(dict(event_i=i, first_signal_lag=lag, already_tracking=active,
                               **{"hit_" + str(n): bool(lag is not None and lag <= n) for n in (0, 1, 2, 6)}))
    return pd.DataFrame(matched, columns=["decision_i", "match_status", "label_i", "lag"]), pd.DataFrame(
        detections, columns=["event_i", "first_signal_lag", "already_tracking", "hit_0", "hit_1", "hit_2", "hit_6"])


def period_windows():
    periods = {"full": (START, END), "first31": (START, SPLIT), "last30": (SPLIT, END), "case_night": NIGHT}
    monday = START.normalize() - pd.Timedelta(days=START.weekday())
    while monday < END:
        periods["week_" + monday.strftime("%Y-%m-%d")] = (max(START, monday), min(END, monday + pd.Timedelta(days=7)))
        monday += pd.Timedelta(days=7)
    return periods


def recall_summary(labels, detections, signals, exposure):
    """Denominators are independent events and all alerts, not trade outcomes."""
    rows = []
    for arm in ARMS:
        joined = labels.merge(detections.loc[detections.arm.eq(arm)], on=["instrument", "event_i"], how="left")
        for period, (start, end) in period_windows().items():
            g = joined.loc[joined.decision_time.ge(start) & joined.decision_time.lt(end)]
            s = signals.loc[signals.arm.eq(arm) & signals.decision_time.ge(start) & signals.decision_time.lt(end)]
            e = exposure.loc[exposure.decision_time.ge(start) & exposure.decision_time.lt(end)]
            positives = g.loc[g.label.eq("positive")]
            ready_new = positives.loc[~positives.already_tracking.fillna(False).astype(bool)]
            known_signals = s.loc[~s.match_status.eq("unknown")]
            nmatched = int(s.match_status.eq("matched").sum())
            row = dict(arm=arm, period=period, start=start, end=end, anchors=len(g),
                positive_events=len(positives), negative_events=int(g.label.eq("negative").sum()),
                unknown_events=int(g.label.eq("unknown").sum()),
                already_tracking=int(positives.already_tracking.fillna(False).sum()),
                new_tracking_opportunities=len(ready_new), signals=len(s), unique_matched=nmatched,
                unmatched=int(s.match_status.eq("unmatched").sum()), duplicates=int(s.match_status.eq("duplicate").sum()),
                unknown_signals=int(s.match_status.eq("unknown").sum()),
                precision_all=nmatched / len(s) if len(s) else np.nan,
                precision_adjudicated=nmatched / len(known_signals) if len(known_signals) else np.nan,
                eligible_asset_hours=int(e.eligible_bars.sum()),
                false_alerts_per_100_asset_days=2400 * s.match_status.eq("unmatched").sum() / e.eligible_bars.sum() if e.eligible_bars.sum() else np.nan)
            for lag in (0, 1, 2, 6):
                row["hits_" + str(lag)] = int(positives["hit_" + str(lag)].sum())
                row["recall_" + str(lag)] = positives["hit_" + str(lag)].mean()
                row["new_tracking_recall_" + str(lag)] = ready_new["hit_" + str(lag)].mean()
            big = positives.loc[positives.large_peak.eq(True)]
            row.update(large_positive_events=len(big), large_recall_1=big.hit_1.mean(),
                       target_80_met=bool(len(positives) and positives.hit_1.mean() >= .8))
            rows.append(row)
    return pd.DataFrame(rows)


def load_prior(prior=PRIOR):
    """Authenticate matching itself AND each referenced frame before pickle use."""
    prior = Path(prior).resolve()
    mp = prior / "dataset_manifest.json"
    manifest_digest = sha(mp)
    checked(mp, PRIOR_MANIFEST_SHA)
    manifest = json.loads(mp.read_text())
    if manifest.get("status") != "complete":
        raise ValueError("Prior dataset is not complete")
    auth = {str(Path(row["path"]).resolve()): row["sha256"] for row in manifest["artifacts"] + manifest["feature_sources"]}
    matching_path = prior / "matching.json"
    checked(matching_path, PRIOR_MATCHING_SHA)
    coverage_path = prior / "coverage.csv"
    for path in (matching_path, coverage_path):
        checked(path, auth[str(path)])
    matching = json.loads(matching_path.read_text())
    if matching["source_hashes"] != manifest["source_hashes"]:
        raise ValueError("Old matching/source pin mismatch")
    for name in ("spike_burst_replay.py", "spike_burst_execution.py"):
        path = ROOT / "yoyo/evaluation" / name
        checked(path, manifest["source_hashes"][str(path.relative_to(ROOT))])
    jobs = [j for j in matching["jobs"] if j["venue"] == "okx" and int(j["minutes"]) == 60]
    if len({j["instrument"] for j in jobs}) != len(jobs):
        raise ValueError("Duplicate source segment")
    references = {str(mp): manifest_digest, str(matching_path): auth[str(matching_path)], str(coverage_path): auth[str(coverage_path)]}
    for j in jobs:
        path = str(Path(j["features_path"]).resolve())
        if auth.get(path) != j["features_sha256"]:
            raise ValueError("Feature not authenticated by prior manifest")
        checked(path, j["features_sha256"])
        references[path] = j["features_sha256"]
    return jobs, pd.read_csv(coverage_path), references


def source_pins():
    """Require committed exact builders/plan before either real-data phase."""
    names = ("spike_burst_recall_study", "spike_burst_progressive", "spike_burst_replay",
             "spike_burst_execution", "spike_burst_dataset", "altseason_research", "launch_quality_dataset")
    paths = [ROOT / ("yoyo/evaluation/" + name + ".py") for name in names]
    paths += [ROOT / "yoyo/data/altseason_sources.py", ROOT / "yoyo/evaluation/pine/spike_burst_v1.pine",
              ROOT / "yoyo/evaluation/pine/spike_burst_v2_progressive.pine", EXPERIMENT / "PROJECT_PLAN.md"]
    for path in paths:
        rel = str(path.relative_to(ROOT))
        if subprocess.check_output(["git", "show", "HEAD:" + rel], cwd=ROOT) != path.read_bytes():
            raise ValueError("Commit exact builder/plan before preparing: " + rel)
    return {str(p.relative_to(ROOT)): sha(p) for p in paths}


def _verify_references(references):
    for path, digest in references.items():
        checked(path, digest)


def prepare(output=EXPERIMENT / "results", prior=PRIOR):
    """Freeze all global signal/label/control plans; never simulate a trade."""
    from yoyo.evaluation import spike_burst_progressive as progressive
    output = Path(output).resolve()
    pins = source_pins()
    if output.exists() and any(output.iterdir()):
        raise ValueError("Refusing to overwrite prepared artifacts")
    output.mkdir(parents=True, exist_ok=True)
    jobs, old_coverage, references = load_prior(prior)
    write_json(output / "prepare_started.json", dict(status="running", source_pins=pins, config=CONFIG))
    all_labels, all_signals, all_controls, all_detections = [], [], [], []
    exposure, snapshots, changes, cases, job_rows = [], [], [], [], []
    for job in jobs:
        if job["asset"] in ("BTC", "ETH"):
            continue
        frame = load_feature(job["features_path"], job["features_sha256"], 60, END)
        context = {k: job[k] for k in ("instrument", "asset", "symbol", "venue", "minutes")}
        states = {"v1": v1.replay(frame, float(job["tick"]))}
        if "replay_burst" in frame and not np.array_equal(states["v1"].burst.to_numpy(), frame.replay_burst.to_numpy()):
            raise ValueError("V1 arrows differ from authenticated frozen replay")
        enhanced = pd.concat([frame, progressive.progressive_fields(frame)], axis=1)
        if not enhanced.columns.is_unique:
            raise ValueError("Progressive fields replaced original columns")
        states["v2"] = progressive.replay(enhanced, float(job["tick"]))
        if any(not state.index.equals(frame.index) for state in states.values()):
            raise ValueError("Replay changed source clock")
        closes = frame.index + HOUR
        window = (closes >= START) & (closes < END)
        labels = label_events(frame)
        labels["label_id"] = [identity(job["instrument"], "label", i) for i in labels.event_i]
        for key, value in context.items():
            labels[key] = value
        all_labels.append(labels)
        decisions = {arm: np.flatnonzero(state.burst.eq(True).to_numpy()) for arm, state in states.items()}
        union = sorted(set(decisions["v1"]) | set(decisions["v2"]))
        evaluated = [i for i in union if window[i]]
        matcher = frame.copy()
        matcher["history_count"] = np.where(frame.ready.eq(True), frame.history_count, 0)
        controls = match_controls(matcher, union, evaluated, job["instrument"], 60, START, END)
        for arm, state in states.items():
            # Warmup arrows can count only for pre-period context events, never period alerts.
            matches, detection = match_signals(labels, decisions[arm], frame, state)
            detection["arm"], detection["instrument"] = arm, job["instrument"]
            all_detections.append(detection)
            matchmap = matches.set_index("decision_i").to_dict("index")
            for i in decisions[arm]:
                if not window[i]:
                    continue
                record = matchmap[i]
                label_i = record["label_i"]
                eid = identity(job["instrument"], arm, int(i))
                anchor_close = float(frame.close.iloc[int(label_i)]) if pd.notna(label_i) else np.nan
                anchor_atr = float(frame.atr.iloc[int(label_i)]) if pd.notna(label_i) else np.nan
                all_signals.append(dict(context, event_id=eid, arm=arm, decision_i=int(i),
                    bar_open=frame.index[i], decision_time=closes[i], route=str(state.route.iloc[i]),
                    relative_volume=float(frame.rv.iloc[i]), tr_expansion=float(frame.expansion.iloc[i]),
                    signal_close=float(frame.close.iloc[i]), signal_atr=float(frame.atr.iloc[i]),
                    matched_label_id=identity(job["instrument"], "label", int(label_i)) if pd.notna(label_i) else "",
                    match_status=record["match_status"], lag=record["lag"],
                    move_since_anchor_atr=(float(frame.close.iloc[i]) - anchor_close) / anchor_atr if anchor_atr > 0 else np.nan,
                    move_since_anchor_pct=100 * (float(frame.close.iloc[i]) / anchor_close - 1) if anchor_close > 0 else np.nan,
                    in_study=True))
                for k, j in enumerate(controls[int(i)]):
                    all_controls.append(dict(context, event_id=identity(eid, "control", k), matched_event_id=eid,
                        arm=arm, control_number=k, decision_i=int(j), decision_time=closes[j]))
        for i in evaluated:
            first, second = bool(states["v1"].burst.iloc[i]), bool(states["v2"].burst.iloc[i])
            changes.append(dict(context, decision_time=closes[i], decision_i=int(i), v1=first, v2=second,
                change="shared" if first and second else "gained" if second else "lost",
                v1_route=str(states["v1"].route.iloc[i]), v2_route=str(states["v2"].route.iloc[i])))
        # One row per available confirmed hour, aggregated globally after every job.
        for stamp in closes[window & frame.ready.eq(True).to_numpy()]:
            exposure.append(dict(decision_time=stamp, eligible_bars=1))
        for i, op in enumerate(frame.index):
            snap = op in SNAPSHOT_OPENS or closes[i] in SNAPSHOT_OPENS
            case = job["asset"] in ("HYPE", "NEAR", "PEPE") and pd.Timestamp("2026-08-19T06:00Z") <= op < pd.Timestamp("2026-08-20T02:00Z")
            if not snap and not case:
                continue
            row = dict(context, bar_open=op, confirmed_at=closes[i],
                requested_bar_open=bool(op in SNAPSHOT_OPENS), requested_confirmed_cutoff=bool(closes[i] in SNAPSHOT_OPENS),
                **{key: frame[key].iloc[i] for key in ("open", "high", "low", "close", "atr", "rv", "expansion", "ready")})
            for arm, state in states.items():
                row.update({arm + "_" + key: state[key].iloc[i] for key in ("burst", "route", "trend_side", "quiet_bars", "pending_side")})
            if snap:
                snapshots.append(row)
            if case:
                cases.append(row)
        job_rows.append(dict(job, study_eligible_bars=int((window & frame.ready.eq(True).to_numpy()).sum()),
            label_anchors=int(labels.in_study.sum()), signals={a: int((states[a].burst.eq(True).to_numpy() & window).sum()) for a in ARMS}))
        print("prepared", job["instrument"], flush=True)
    labels = pd.concat(all_labels, ignore_index=True) if all_labels else pd.DataFrame(columns=LABEL_COLUMNS + ["instrument", "label_id"])
    detections = pd.concat(all_detections, ignore_index=True) if all_detections else pd.DataFrame(columns=["instrument", "event_i", "arm"])
    signals = pd.DataFrame(all_signals, columns=SIGNAL_COLUMNS)
    control_frame = pd.DataFrame(all_controls, columns=CONTROL_COLUMNS)
    exp = pd.DataFrame(exposure, columns=["decision_time", "eligible_bars"])
    exp = exp.groupby("decision_time", as_index=False).eligible_bars.sum()
    change_frame = pd.DataFrame(changes)
    coverage = old_coverage.loc[old_coverage.venue.eq("okx") & (old_coverage.minutes.eq(60) | old_coverage.minutes.isna())].copy()
    products = {"labels.csv.gz": labels, "signals.csv.gz": signals, "controls.csv.gz": control_frame,
        "detections.csv.gz": detections, "exposure.csv.gz": exp, "coverage.csv": coverage,
        "snapshots.csv": pd.DataFrame(snapshots), "case_slices.csv": pd.DataFrame(cases), "signal_changes.csv.gz": change_frame,
        "recall_summary.csv": recall_summary(labels, detections, signals, exp)}
    night_labels = labels.loc[labels.decision_time.ge(NIGHT[0]) & labels.decision_time.lt(NIGHT[1])].assign(record_type="label")
    night_signals = signals.loc[signals.decision_time.ge(NIGHT[0]) & signals.decision_time.lt(NIGHT[1])].assign(record_type="signal")
    products["night_events.csv.gz"] = pd.concat([night_labels, night_signals], ignore_index=True)
    paired = detections.pivot(index=["instrument", "event_i"], columns="arm", values="first_signal_lag").reindex(columns=list(ARMS))
    paired.columns = ["first_signal_lag_" + str(c) for c in paired.columns]
    paired = paired.reset_index()
    for arm in ARMS:
        paired["first_signal_lag_" + arm] = pd.to_numeric(paired["first_signal_lag_" + arm], errors="coerce")
    paired["earlier_v2"] = paired.first_signal_lag_v2.lt(paired.first_signal_lag_v1)
    products["event_timing_changes.csv.gz"] = labels.merge(paired, on=["instrument", "event_i"], how="left")
    paths = []
    for name, table in products.items():
        path = output / name
        write_csv(path, table)
        paths.append(path)
    schedule = output / "matching.json"
    write_json(schedule, dict(jobs=job_rows, source_pins=pins, config=CONFIG,
        signals=len(signals), controls=len(control_frame), no_execution_scored=True))
    paths.append(schedule)
    _verify_references(references)
    if source_pins() != pins:
        raise ValueError("Source changed during preparation")
    receipt = dict(status="complete", source_pins=pins, config=CONFIG, sources=references,
        code_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        segments=len(job_rows), unique_assets=len({j["asset"] for j in job_rows}),
        unique_symbols=len({j["symbol"] for j in job_rows}),
        old_okx_market_records=int(old_coverage.loc[old_coverage.venue.eq("okx"), "symbol"].nunique()),
        background_excluded=["BTC", "ETH"], artifacts=[artifact(p) for p in paths],
        limitations=["Already-viewed retrospective pool, not all currently traded OKX markets or a blind test",
            "Recall is conditional on source coverage/readiness; missing/new listings remain in coverage",
            "Precision is event matching, not profitable-trade win rate", "Tracking is separate from a new arrow",
            "Same-night assets and cross-period repeats are correlated; 80% is a target not a guarantee"])
    write_json(output / "prepared_manifest.json", receipt)
    return receipt


def trade_summary(actual, controls):
    """Asset-block paired excess and descriptive outcomes, never account NAV."""
    valid = actual.loc[actual.valid.eq(True)].copy()
    random = controls.loc[controls.valid.eq(True)].copy()
    for column in ("net_bp", "gross_bp", "net_return", "net_r", "peak_r", "relative_volume", "tr_expansion"):
        valid[column] = pd.to_numeric(valid[column], errors="coerce")
    random["net_bp"] = pd.to_numeric(random.net_bp, errors="coerce")
    mean = random.groupby("matched_event_id").net_bp.mean()
    valid["control_mean_net_bp"] = valid.event_id.map(mean)
    valid["excess_bp"] = valid.net_bp - valid.control_mean_net_bp
    matched = valid.loc[valid.excess_bp.notna()]
    p, units, balanced = paired_test(valid)
    natural = valid.loc[valid.natural_exit.eq(True)]
    row = dict(candidates=len(actual), valid=len(valid), matched=len(matched),
        invalid=len(actual) - len(valid), natural_exits=len(natural), censored=int(valid.censored.sum()),
        mean_net_bp=valid.net_bp.mean(), mean_gross_bp=valid.gross_bp.mean(),
        wins=int(valid.net_return.gt(0).sum()), win_rate=valid.net_return.gt(0).mean(),
        natural_wins=int(natural.net_return.gt(0).sum()), natural_win_rate=natural.net_return.gt(0).mean(),
        median_peak_r=valid.peak_r.median(), median_net_r=valid.net_r.median(),
        max_peak_r=valid.peak_r.max(), max_net_r=valid.net_r.max(),
        paired_actual_net_bp=matched.net_bp.mean(), paired_random_net_bp=matched.control_mean_net_bp.mean(),
        mean_excess_bp=matched.excess_bp.mean(), asset_balanced_excess_bp=balanced,
        permutation_assets=units, permutation_p=p, account_return_not_computed=True)
    scores = []
    for feature in ("relative_volume", "tr_expansion"):
        finite = valid.loc[np.isfinite(pd.to_numeric(valid[feature], errors="coerce"))].copy()
        finite = finite.sort_values([feature, "event_id"], ascending=[False, True])
        top = finite.head(max(1, int(np.ceil(len(finite) * .1))))
        scores.append(dict(feature=feature, n=len(finite), top_n=len(top),
            descriptive_auc=rank_auc(finite[feature], finite.net_return.gt(0)),
            top_gross_bp=top.gross_bp.mean(), top_net_bp=top.net_bp.mean(),
            top_win_rate=top.net_return.gt(0).mean(), top_matched_actual_bp=top.loc[top.excess_bp.notna(), "net_bp"].mean(),
            top_random_bp=top.control_mean_net_bp.mean(), top_excess_bp=top.excess_bp.mean(),
            val_auc="not_applicable_no_training"))
    return row, scores


def evaluate(output=EXPERIMENT / "results"):
    """Only consume the globally frozen schedule; do not regenerate any signal."""
    output = Path(output).resolve()
    pins = source_pins()
    receipt_path = output / "validation_manifest.json"
    if receipt_path.exists() or (output / "evaluation_started.json").exists():
        raise ValueError("Refusing a second scoring run/overwrite")
    prepared_path = output / "prepared_manifest.json"
    prepared_hash = sha(prepared_path)
    prepared = json.loads(prepared_path.read_text())
    if prepared.get("status") != "complete" or prepared["source_pins"] != pins or prepared["config"] != CONFIG:
        raise ValueError("Prepared plan/source mismatch")
    for item in prepared["artifacts"]:
        checked(item["path"], item["sha256"])
    _verify_references(prepared["sources"])
    matching = json.loads((output / "matching.json").read_text())
    signals = pd.read_csv(output / "signals.csv.gz")
    controls = pd.read_csv(output / "controls.csv.gz")
    write_json(output / "evaluation_started.json", dict(status="running", prepared_manifest_sha256=prepared_hash,
        source_pins=pins, config=CONFIG, event_study_only=True))
    actual_rows, control_rows = [], []
    for job in matching["jobs"]:
        frame = load_feature(job["features_path"], job["features_sha256"], 60, END)
        cache = {}
        for source, destination in ((signals, actual_rows), (controls, control_rows)):
            for row in source.loc[source.instrument.eq(job["instrument"])].to_dict("records"):
                i = int(row["decision_i"])
                if i not in cache:
                    cache[i] = simulate_trade(frame, i, float(job["tick"]), END)
                result = cache[i]
                destination.append(dict(row, relative_volume=float(frame.rv.iloc[i]), tr_expansion=float(frame.expansion.iloc[i]), **result))
        print("scored frozen", job["instrument"], flush=True)
    # Invalid candidates retain missing outcomes instead of fabricated zero returns.
    numeric = ["net_bp", "gross_bp", "net_return", "net_r", "peak_r"]
    required = ["event_id", "arm", "asset", "decision_time", "valid", "natural_exit", "censored", "relative_volume", "tr_expansion"] + numeric
    actual = pd.DataFrame(actual_rows)
    random = pd.DataFrame(control_rows)
    for table in (actual, random):
        for col in required + ["matched_event_id"]:
            if col not in table:
                table[col] = pd.Series(index=table.index, dtype="object" if col not in numeric else float)
        table["decision_time"] = pd.to_datetime(table.decision_time, utc=True)
    summaries, scores = [], []
    for arm in ARMS:
        for period, (start, end) in period_windows().items():
            a = actual.loc[actual.arm.eq(arm) & actual.decision_time.ge(start) & actual.decision_time.lt(end)]
            c = random.loc[random.matched_event_id.isin(a.event_id)]
            row, feature_rows = trade_summary(a, c)
            summaries.append(dict(arm=arm, period=period, **row))
            if period == "full":
                scores.extend(dict(arm=arm, **item) for item in feature_rows)
    summary = pd.DataFrame(summaries)
    primary = summary.period.eq("full")
    pvalues = summary.loc[primary, "permutation_p"].to_numpy(float)
    if len(pvalues) != 2:
        raise ValueError("Exactly two preregistered primary tests required")
    adjusted = holm(np.where(np.isfinite(pvalues), pvalues, 1.))
    adjusted[~np.isfinite(pvalues)] = np.nan
    summary.loc[primary, "holm_p"] = adjusted
    paths = []
    for name, table in (("trade_events.csv.gz", actual), ("trade_controls.csv.gz", random),
                        ("trade_summary.csv", summary), ("score_summary.csv", pd.DataFrame(scores))):
        path = output / name
        write_csv(path, table)
        paths.append(path)
    _verify_references(prepared["sources"])
    checked(prepared_path, prepared_hash)
    for item in prepared["artifacts"]:
        checked(item["path"], item["sha256"])
    if source_pins() != pins:
        raise ValueError("Source changed during evaluation")
    receipt = dict(status="complete", prepared_manifest_sha256=prepared_hash,
        source_pins=pins, config=CONFIG, primary_tests=2,
        artifacts=[artifact(p) for p in paths], costs="Frozen 20bp entry-notional; funding/impact unmodeled",
        limitations=["Event outcomes are not capacity-constrained account returns", "Previously seen retrospective sample",
            "Asset-block tests do not remove all common-night market beta", "Random matches can repeat across events; not independent replications"])
    write_json(receipt_path, receipt)
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "evaluate"))
    parser.add_argument("--results", type=Path, default=EXPERIMENT / "results")
    parser.add_argument("--prior", type=Path, default=PRIOR)
    args = parser.parse_args()
    prepare(args.results, args.prior) if args.phase == "prepare" else evaluate(args.results)
