"""Independent saved-ledger audit for the frozen 54-symbol IMACD/YOLO expansion.

Source: exp-imacd-yolo-expanded-20260908-v1/PROJECT_PLAN.md and its persisted
schema. Adapted from the independent 1H/4H verifier, without importing any
selection or inference implementation. It reads saved ledgers and metadata
only; no model, images, raw market file or indicator calculation is loaded.

Each symbol/timeframe/fold trace contains p..p+10 where available. Permanent
invalidation uses only p..p+9. The last row can supply the entry open following
a p+9 confirmation; its md must never cancel an earlier decision. The selected
core must overlap the frozen [setup_start_i,p] by actual candle indexes, and
the earliest surviving same-direction model endpoint wins. The frozen whole-
event censor rule is retained at each cohort boundary.

The pre-signal focus state, aggregation, MA values and image/model outputs are
not independently recreated. Setup boundaries are verified as saved frozen
identities. January-May overlaps the model validation calendar; the later
cohort is previously exposed holdout, including repeated BTC/ETH candidates.
The four conditional direction permutations and Holm adjustment are engineering
association checks, not profitability, trend accuracy or false-alert evidence.
Commit this source before invoking main, which writes only independent_review.json.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-imacd-yolo-expanded-20260908-v1"
DATA = ROOT / "data/imacd_yolo_expanded_20260908_v1"
RESULTS = EXP / "results"
PERIODS = (60, 240)
SEED = 20260908
PERMUTATIONS = 10000
START = pd.Timestamp("2026-01-01", tz="UTC")
CUT = pd.Timestamp("2026-05-04", tz="UTC")
END = pd.Timestamp("2026-07-01", tz="UTC")
FOLDS = {"pre_holdout": (START, CUT), "holdout_review": (CUT, END)}
MODEL_SHA = "862705b999594355c1133640acc540f4de19b561889e89d9e050ddad5c6db838"

CANDIDATE_COLUMNS = [
    "event_id", "symbol", "timeframe_min", "signal_i", "side", "setup_start_i",
    "setup_bars", "signal_open_at", "signal_available_at", "signal_close",
    "complete_followup", "fold",
]
CONFIRMATION_FIELDS = [
    "model_detection_id", "confirmation_i", "confirmation_available_at", "delay_bars",
    "model_confidence", "confirmed_next_open", "displacement_bp", "core_start_i",
    "core_end_i", "overlap_bars", "core_overlap_fraction",
    "core_end_from_arrow_bars", "core_identity",
]


def _same_frames(left: pd.DataFrame, right: pd.DataFrame) -> bool:
    """CSV empty columns can infer different dtypes; compare actual cells."""
    try:
        pd.testing.assert_frame_equal(
            left.reset_index(drop=True), right.reset_index(drop=True),
            check_dtype=False, rtol=1e-12, atol=1e-12,
        )
        return True
    except AssertionError:
        return False


def _equal_number(actual, expected) -> bool:
    if expected is None:
        return bool(pd.isna(actual))
    return bool(pd.notna(actual) and np.isclose(actual, expected, rtol=1e-10, atol=1e-10))


def _integer_columns(frame: pd.DataFrame, columns: list[str]) -> bool:
    for name in columns:
        values = pd.to_numeric(frame[name], errors="coerce").to_numpy(float)
        if not (np.isfinite(values).all() and np.equal(values, np.floor(values)).all()):
            return False
    return True


def _direction_null(frame: pd.DataFrame) -> dict:
    """Recompute the frozen per-timeframe symbol/month direction permutation."""
    complete = frame.loc[frame.complete_followup.eq(True)].reset_index(drop=True)
    sides = complete.side.to_numpy(int)
    longs = complete.can_long.to_numpy(bool)
    shorts = complete.can_short.to_numpy(bool)
    groups = complete.groupby([
        complete.symbol,
        pd.to_datetime(complete.signal_available_at, utc=True).dt.strftime("%Y-%m"),
    ]).indices
    rng = np.random.default_rng(SEED)
    observed = int((((sides == 1) & longs) | ((sides == -1) & shorts)).sum())
    counts = np.zeros(PERMUTATIONS, dtype=int)
    for j in range(PERMUTATIONS):
        shuffled = sides.copy()
        for indexes in groups.values():
            shuffled[indexes] = rng.permutation(sides[indexes])
        counts[j] = int((((shuffled == 1) & longs) | ((shuffled == -1) & shorts)).sum())
    return dict(
        observed=observed, permutations=PERMUTATIONS, seed=SEED,
        null_mean=float(counts.mean()), null_sd=float(counts.std()),
        p_one_sided=float((1 + (counts >= observed).sum()) / (PERMUTATIONS + 1)),
    )


def _audit_group(key, group, summary, symbol, minutes, fold, check, replay_failures):
    """Reconstruct selection from one saved shard without calling its runner."""
    start, end = FOLDS[fold]
    events, boxes, trace = group["decisions"], group["proposals"], group["trace"]
    metadata = summary["inputs"][key]
    step = pd.Timedelta(minutes=minutes)
    first, last = pd.Timestamp(metadata["parsed_first"]), pd.Timestamp(metadata["parsed_last"])
    rows = int(metadata["parsed_rows"])
    check(key + "/group_identity", all(
        frame.symbol.eq(symbol).all() and frame.timeframe_min.eq(minutes).all() and frame.fold.eq(fold).all()
        for frame in group.values()
    ))
    check(key + "/metadata_clock_and_partial_groups", (
        metadata["minutes"] == minutes and rows > 0
        and first.tzinfo is not None and last.tzinfo is not None
        and first.utcoffset().total_seconds() == 0
        and last.utcoffset().total_seconds() == 0
        and first == first.floor(f"{minutes}min")
        and last == last.floor(f"{minutes}min")
        and first + (rows - 1) * step == last
        and last + step == end
        and metadata["discarded_partial_source_rows"]
        == metadata["raw_rows"] - int((END-first)/step) * (minutes // 15)
        and 0 <= metadata["discarded_partial_source_rows"] <= 2 * (minutes // 15 - 1)
    ))
    opens = pd.to_datetime(events.signal_open_at, utc=True)
    available = pd.to_datetime(events.signal_available_at, utc=True)
    check(key + "/candidate_clock_identity_setup", (
        available.between(start, end, inclusive="left").all()
        and (available - opens).eq(step).all()
        and (opens == first + pd.to_timedelta(events.signal_i * minutes, unit="min")).all()
        and events.signal_i.between(0, rows - 1).all()
        and events.side.isin([-1, 1]).all()
        and events.setup_start_i.ge(340).all()
        and events.setup_start_i.lt(events.signal_i).all()
        and events.setup_bars.ge(12).all()
        and (events.signal_i - events.setup_start_i).eq(events.setup_bars).all()
        and all(row.event_id == f"{symbol}_{minutes}_{int(pd.Timestamp(row.signal_open_at).timestamp())}"
                for row in events.itertuples())
    ))
    check(key + "/complete_followup_boundary", (
        events.complete_followup.eq(events.signal_i + 10 < rows).all()
    ))
    endpoint_set = {
        int(p) + delay
        for p in events.loc[events.complete_followup.eq(True), "signal_i"]
        for delay in range(10)
    }
    check(key + "/proposal_endpoint_clock_and_counts", (
        boxes.window_end_i.isin(endpoint_set).all()
        and boxes.window_start_i.ge(0).all()
        and boxes.window_end_i.lt(rows).all()
        and (pd.to_datetime(boxes.available_at, utc=True)
             == first + pd.to_timedelta((boxes.window_end_i + 1) * minutes, unit="min")).all()
        and metadata["events"] == len(events)
        and metadata["candidate_endpoints"] == len(endpoint_set)
        and metadata["windows_scored"] == 2 * len(endpoint_set)
        and metadata["raw_boxes"] == len(boxes)
        and metadata["structural_boxes"] == int(boxes.structural_pass.sum())
        and metadata["windows_with_boxes"]
        == len(boxes[["window_end_i", "window_len"]].drop_duplicates())
    ))
    trace_times = pd.to_datetime(trace.bar_open_at, utc=True)
    check(key + "/trace_clock_bounds", (
        (trace_times == first + pd.to_timedelta(trace.bar_i * minutes, unit="min")).all()
        and trace.bar_i.between(0, rows - 1).all()
        and (trace_times + step <= end).all()
    ))
    event_checks = {name: [] for name in (
        "trace_coverage_and_signal_price", "first_invalid_from_trace",
        "censoring", "direction_pools", "status_and_first_match",
        "confirmation_clock_geometry", "next_open_trace_prices",
    )}
    bad_events = {name: [] for name in event_checks}

    def record(name, event_id, value):
        event_checks[name].append(bool(value))
        if not value:
            bad_events[name].append(event_id)

    for event in events.itertuples(index=False):
        p = int(event.signal_i)
        own_trace = trace[trace.event_id.eq(event.event_id)].sort_values("bar_i")
        expected_indexes = list(range(p, min(p + 11, rows)))
        coverage = own_trace.bar_i.tolist() == expected_indexes
        if coverage:
            coverage = _equal_number(event.signal_close, own_trace.iloc[0].close)
        record("trace_coverage_and_signal_price", event.event_id, coverage)
        if not coverage:
            continue
        indexed_trace = own_trace.set_index("bar_i")
        decision_trace = own_trace[own_trace.bar_i.le(p + 9)]
        md = decision_trace.md.to_numpy(float)
        bad = ~np.isfinite(md) | (event.side * md <= 0)
        first_invalid = int(decision_trace.loc[bad, "bar_i"].iloc[0]) if bad.any() else None
        record("first_invalid_from_trace", event.event_id,
               _equal_number(event.first_invalid_i, first_invalid))
        if not event.complete_followup:
            censored = (
                event.status == "censored_end" and not event.can_long and not event.can_short
                and pd.isna(event.baseline_next_open)
                and all(pd.isna(getattr(event, name)) for name in CONFIRMATION_FIELDS)
            )
            record("censoring", event.event_id, censored)
            continue
        record("censoring", event.event_id, event.status != "censored_end")
        pool = boxes[
            boxes.symbol.eq(symbol) & boxes.timeframe_min.eq(minutes)
            & boxes.core_length_bars.isin([4, 5])
            & boxes.confirmation_bars.between(2, 9)
            & boxes.window_end_i.between(p, p + 9)
            & boxes.core_start_i.le(p) & boxes.core_end_i.ge(event.setup_start_i)
        ]
        if first_invalid is not None:
            pool = pool[pool.window_end_i.lt(first_invalid)]
        pool = pool.sort_values(
            ["window_end_i", "confidence", "window_len", "detection_id"],
            ascending=[True, False, True, True],
        )
        record("direction_pools", event.event_id, (
            event.can_long == bool(pool.direction.eq("long").any())
            and event.can_short == bool(pool.direction.eq("short").any())
        ))
        same = pool[pool.direction.eq("long" if event.side == 1 else "short")]
        expected_status = "confirmed" if len(same) else (
            "invalidated" if first_invalid is not None else "expired"
        )
        selected = same.iloc[0] if len(same) else None
        record("status_and_first_match", event.event_id, (
            event.status == expected_status
            and (event.model_detection_id == selected.detection_id if selected is not None
                 else all(pd.isna(getattr(event, name)) for name in CONFIRMATION_FIELDS))
        ))
        baseline = float(indexed_trace.loc[p + 1, "open"])
        if selected is None:
            record("next_open_trace_prices", event.event_id,
                   _equal_number(event.baseline_next_open, baseline))
            continue
        endpoint = int(selected.window_end_i)
        a, b = int(selected.core_start_i), int(selected.core_end_i)
        overlap = min(b, p) - max(a, int(event.setup_start_i)) + 1
        confirmation_time = pd.Timestamp(indexed_trace.loc[endpoint, "bar_open_at"]) + step
        record("confirmation_clock_geometry", event.event_id, (
            event.confirmation_i == endpoint and event.delay_bars == endpoint - p
            and pd.Timestamp(event.confirmation_available_at) == confirmation_time
            and pd.Timestamp(selected.available_at) == confirmation_time
            and confirmation_time == pd.Timestamp(event.signal_available_at) + (endpoint - p) * step
            and event.core_start_i == a and event.core_end_i == b
            and overlap >= 1 and event.overlap_bars == overlap
            and _equal_number(event.core_overlap_fraction, overlap / (b - a + 1))
            and event.core_end_from_arrow_bars == b - p
            and event.core_identity == f"{symbol}_{minutes}_{event.side}_{a}_{b}"
            and _equal_number(event.model_confidence, selected.confidence)
        ))
        entry = float(indexed_trace.loc[endpoint + 1, "open"])
        record("next_open_trace_prices", event.event_id, (
            _equal_number(event.baseline_next_open, baseline)
            and _equal_number(event.confirmed_next_open, entry)
            and _equal_number(event.displacement_bp, event.side * (entry / baseline - 1) * 10000)
            and pd.Timestamp(indexed_trace.loc[endpoint + 1, "bar_open_at"]) == confirmation_time
        ))
    for name, values in event_checks.items():
        check(key + "/" + name, all(values))
    replay_failures[key] = {name: ids for name, ids in bad_events.items() if ids}
    earliest = max(0, int((start - first) / step) - 1)
    expected_bars = max(0, rows - 1 - earliest)
    maximum_ready = max(0, rows - 1 - max(earliest, 340))
    check(key + "/evaluation_coverage_metadata", (
        metadata["evaluation_bars"] == expected_bars
        and 0 <= metadata["evaluation_ready_bars"] <= maximum_ready <= expected_bars
    ))


def _stats(frame: pd.DataFrame) -> dict:
    """Independently aggregate the saved engineering counters and prices."""
    full = frame[frame.complete_followup.eq(True)]
    kept = full[full.status.eq("confirmed")]
    delayed = kept[kept.delay_bars.gt(0)]
    return dict(
        arrows=len(frame), complete=len(full), confirmed=len(kept),
        invalidated=int(full.status.eq("invalidated").sum()),
        expired=int(full.status.eq("expired").sum()), censored=len(frame) - len(full),
        pass_rate_pct=100 * len(kept) / len(full) if len(full) else None,
        confirmed_long=int(kept.side.eq(1).sum()), confirmed_short=int(kept.side.eq(-1).sum()),
        immediate=int(kept.delay_bars.eq(0).sum()), delayed=len(delayed),
        confirmed_symbols=int(kept.symbol.nunique()),
        delay_median_bars=float(kept.delay_bars.median()) if len(kept) else None,
        delay_max_bars=float(kept.delay_bars.max()) if len(kept) else None,
        displacement_median_bp=float(kept.displacement_bp.median()) if len(kept) else None,
        displacement_max_bp=float(kept.displacement_bp.max()) if len(kept) else None,
        delayed_displacement_median_bp=float(delayed.displacement_bp.median()) if len(delayed) else None,
        adverse_delays=int(delayed.displacement_bp.gt(0).sum()),
        favorable_delays=int(delayed.displacement_bp.lt(0).sum()),
    )


def _stats_match(actual: dict, expected: dict) -> bool:
    return all(name in actual and _equal_number(actual[name], value)
               for name, value in expected.items())


def _grouped_stats_match(frame, columns, actual) -> bool:
    expected = {}
    for identity, group in frame.groupby(columns, sort=True):
        identity = identity if isinstance(identity, tuple) else (identity,)
        expected[identity] = _stats(group)
    observed = {tuple(row[name] for name in columns): row for row in actual}
    return len(actual) == len(observed) and set(observed) == set(expected) and all(
        _stats_match(observed[identity], stats) for identity, stats in expected.items()
    )


def _old_comparison(decisions, proposals, traces) -> dict:
    """Describe the repeated BTC/ETH cohort; never require YOLO byte equality.

    Only windows with saved boxes have pixel hashes. A missing detection does
    not establish a missing/different input image. MPS batch-composition effects
    are possible explanations for output differences, not assumed diagnoses.
    """
    old_data = ROOT / "data/imacd_yolo_timeframes_20260908_v1"
    old_summary_path = ROOT / (
        "experiments/active/exp-imacd-yolo-timeframes-20260908-v1/results/summary.json"
    )
    old_summary_bytes = old_summary_path.read_bytes()
    old_summary = json.loads(old_summary_bytes)
    old_paths = [old_data / "decisions.csv", old_data / "traces.csv.gz"] + [
        old_data / f"{symbol}_{minutes}_proposals.csv.gz"
        for symbol in ("BTC", "ETH") for minutes in PERIODS
    ]
    for path in old_paths:
        relative = str(path.relative_to(ROOT))
        expected = old_summary["files"].get(relative)
        if expected is None or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"prior comparison ledger hash mismatch: {relative}")
    old = pd.read_csv(old_data / "decisions.csv")
    old_trace = pd.read_csv(old_data / "traces.csv.gz")
    old_boxes = pd.concat([
        pd.read_csv(old_data / f"{symbol}_{minutes}_proposals.csv.gz")
        for symbol in ("BTC", "ETH") for minutes in PERIODS
    ], ignore_index=True)
    current = decisions[
        decisions.symbol.isin(["BTC", "ETH"]) & decisions.fold.eq("holdout_review")
    ]
    current_boxes = proposals[
        proposals.symbol.isin(["BTC", "ETH"]) & proposals.fold.eq("holdout_review")
    ]
    current_trace = traces[
        traces.symbol.isin(["BTC", "ETH"]) & traces.fold.eq("holdout_review")
    ]
    shared_ids = sorted(set(old.event_id) & set(current.event_id))
    frozen_columns = [name for name in CANDIDATE_COLUMNS if name != "fold"]
    old_events = old.set_index("event_id")
    new_events = current.set_index("event_id")
    changed = []
    for event_id in shared_ids:
        before, after = old_events.loc[event_id], new_events.loc[event_id]
        fields = []
        for name in old.columns:
            if name == "event_id":
                continue
            a, b = before[name], after[name]
            if pd.isna(a) and pd.isna(b):
                continue
            equal = (pd.notna(a) and pd.notna(b) and
                     (_equal_number(a, b) if isinstance(a, (int, float, np.number))
                      and not isinstance(a, (bool, np.bool_)) else a == b))
            if not equal:
                fields.append(name)
        if fields:
            changed.append(dict(event_id=event_id, fields=fields,
                                old_status=before.status, new_status=after.status))
    window_key = ["symbol", "timeframe_min", "window_end_i", "window_len"]
    pixels_before = old_boxes[window_key + ["input_pixel_sha256"]].drop_duplicates()
    pixels_after = current_boxes[window_key + ["input_pixel_sha256"]].drop_duplicates()
    pixels = pixels_before.merge(pixels_after, on=window_key, how="outer",
                                 suffixes=("_old", "_new"), indicator=True)
    common = pixels[pixels._merge.eq("both")]
    pixel_changes = common[
        common.input_pixel_sha256_old.ne(common.input_pixel_sha256_new)
    ]
    shared_boxes = old_boxes.merge(current_boxes, on="detection_id", suffixes=("_old", "_new"))
    changed_confidence = (~np.isclose(
        shared_boxes.confidence_old, shared_boxes.confidence_new, rtol=1e-10, atol=1e-10,
    )).sum()
    return dict(
        scope="Repeated old cohort is descriptive; detection/pixel differences are not new tests.",
        prior_summary_sha256=hashlib.sha256(old_summary_bytes).hexdigest(),
        prior_ledger_hashes_verified=len(old_paths),
        previous_events=len(old), repeated_event_ids=len(shared_ids),
        old_only_event_ids=sorted(set(old.event_id) - set(current.event_id)),
        new_only_btc_eth_holdout_event_ids=sorted(set(current.event_id) - set(old.event_id)),
        frozen_candidates_equal=_same_frames(
            old[frozen_columns].sort_values("event_id"),
            current[frozen_columns].sort_values("event_id"),
        ),
        traces_equal=_same_frames(
            old_trace.sort_values(["event_id", "bar_i"]),
            current_trace[old_trace.columns].sort_values(["event_id", "bar_i"]),
        ),
        all_decision_fields_equal=not changed and len(shared_ids) == len(old) == len(current),
        decision_changes=changed, previous_boxes=len(old_boxes), repeated_cohort_boxes=len(current_boxes),
        shared_detection_ids=len(set(old_boxes.detection_id) & set(current_boxes.detection_id)),
        old_only_detection_ids=len(set(old_boxes.detection_id) - set(current_boxes.detection_id)),
        new_only_detection_ids=len(set(current_boxes.detection_id) - set(old_boxes.detection_id)),
        shared_detection_confidence_changes=int(changed_confidence),
        common_windows_with_saved_pixel_hashes=len(common),
        differing_common_pixel_hashes=len(pixel_changes),
        pixel_difference_windows=pixel_changes[window_key].to_dict("records"),
        old_only_box_windows=int(pixels._merge.eq("left_only").sum()),
        new_only_box_windows=int(pixels._merge.eq("right_only").sum()),
        pixel_limit="No-box windows do not have a saved pixel hash; no claim of full image revalidation.",
    )


def verify_saved(data_dir: Path = DATA, results_dir: Path = RESULTS) -> dict:
    """Audit saved shards, trace state and summaries; do not score market data."""
    data_dir, results_dir = Path(data_dir), Path(results_dir)
    exp = results_dir.parent
    summary = json.loads((results_dir / "summary.json").read_text())
    universe = json.loads((exp / "universe.json").read_text())
    started = json.loads((results_dir / "run_started.json").read_text())
    source_manifest_sha = hashlib.sha256((exp / "source_manifest.json").read_bytes()).hexdigest()
    sources = universe["sources"]
    symbols = [source["symbol"] for source in sources]
    old_universe_path = ROOT / universe["source_manifest"]
    old_universe = json.loads(old_universe_path.read_text())
    old_sources = [{name: row[name] for name in ("symbol", "path", "sha256")}
                   for row in old_universe["sources"]]
    checks, replay_failures, groups, receipts = {}, {}, {}, {}

    def check(name, value):
        checks[name] = bool(value)

    check("frozen_54_symbol_universe", (
        len(sources) == len(set(symbols)) == 54 and sources == old_sources
        and hashlib.sha256(old_universe_path.read_bytes()).hexdigest() == universe["source_manifest_sha256"]
        and summary["symbols"] == symbols and summary["symbol_count"] == 54
    ))
    check("frozen_configuration_and_run_identity", (
        pd.Timestamp(summary["start"]) == START and pd.Timestamp(summary["end_exclusive"]) == END
        and summary["max_wait_bars"] == 9 and summary["model_sha256"] == MODEL_SHA
        and summary["holdout_consumptions"] == {"60": 2, "240": 2}
        and summary["this_expansion_holdout_passes"] == 1
        and summary["source_manifest_sha256"] == source_manifest_sha
        and all(name in summary and summary[name] == value for name, value in started.items())
        and summary["economic_evaluation"] is False
        and summary["training_eligible"] is False and summary["production_eligible"] is False
    ))
    candidates = pd.read_csv(data_dir / "candidates.csv")
    decisions = pd.read_csv(data_dir / "decisions.csv")
    proposals = pd.read_csv(data_dir / "proposals.csv.gz")
    traces = pd.read_csv(data_dir / "traces.csv.gz")
    expected_names = {"candidates.csv", "decisions.csv", "proposals.csv.gz", "traces.csv.gz"}
    for symbol in symbols:
        for minutes in PERIODS:
            for fold in FOLDS:
                key = f"{symbol}_{minutes}_{fold}"
                receipts[key] = json.loads((data_dir / f"{key}_receipt.json").read_text())
                groups[key] = {
                    "candidates": pd.read_csv(data_dir / f"{key}_candidates.csv"),
                    "decisions": pd.read_csv(data_dir / f"{key}_decisions.csv"),
                    "proposals": pd.read_csv(data_dir / f"{key}_proposals.csv.gz"),
                    "trace": pd.read_csv(data_dir / f"{key}_trace.csv.gz"),
                }
                expected_names.update({
                    f"{key}_receipt.json", f"{key}_candidates.csv", f"{key}_decisions.csv",
                    f"{key}_proposals.csv.gz", f"{key}_trace.csv.gz",
                })
    paths = {name: (ROOT / name).resolve() for name in summary["files"]}
    permitted = all(path.parent == data_dir.resolve() for path in paths.values())
    check("complete_216_shards_and_hash_manifest", (
        len(groups) == 216 and permitted and len(paths) == len(expected_names)
        and {path.name for path in paths.values()} == expected_names
        and set(summary["inputs"]) == set(summary["validation"]) == set(groups)
    ))
    check("all_saved_ledger_and_receipt_hashes", permitted and all(
        path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == summary["files"][name]
        for name, path in paths.items()
    ))
    for name, frame in (("candidates", candidates), ("decisions", decisions),
                        ("proposals", proposals), ("trace", traces)):
        check("combined_" + name + "_matches_shards", _same_frames(
            frame, pd.concat([group[name] for group in groups.values()], ignore_index=True),
        ))
    check("candidate_decision_frozen_fields", _same_frames(
        candidates[CANDIDATE_COLUMNS], decisions[CANDIDATE_COLUMNS],
    ))
    check("unique_identities_and_trace_event_set", (
        not candidates.event_id.duplicated().any() and not decisions.event_id.duplicated().any()
        and not proposals.detection_id.duplicated().any()
        and not traces.duplicated(["event_id", "bar_i"]).any()
        and set(traces.event_id) == set(candidates.event_id)
    ))
    check("decision_boolean_and_candidate_integer_fields", (
        all(decisions[name].isin([True, False]).all()
            for name in ("complete_followup", "can_long", "can_short"))
        and _integer_columns(candidates, ["signal_i", "side", "setup_start_i", "setup_bars", "timeframe_min"])
    ))
    check("proposal_integer_fields", _integer_columns(proposals, [
        "window_len", "window_start_i", "window_end_i", "core_start_i", "core_end_i",
        "core_length_bars", "confirmation_bars", "timeframe_min",
    ]))
    check("proposal_structure_and_model_values", (
        proposals.direction.isin(["long", "short"]).all()
        and proposals.confidence.between(.25, 1).all()
        and proposals.window_len.isin([18, 19]).all()
        and (proposals.window_end_i - proposals.window_start_i + 1).eq(proposals.window_len).all()
        and (proposals.core_end_i - proposals.core_start_i + 1).eq(proposals.core_length_bars).all()
        and (proposals.window_end_i - proposals.core_end_i).eq(proposals.confirmation_bars).all()
        and proposals.core_start_i.ge(proposals.window_start_i).all()
        and proposals.core_end_i.le(proposals.window_end_i).all()
        and proposals.structural_pass.eq(
            proposals.core_length_bars.isin([4, 5]) & proposals.confirmation_bars.between(2, 9)
        ).all()
    ))
    prices = traces[["open", "close"]].to_numpy(float)
    check("trace_integer_fields_and_prices", (
        _integer_columns(traces, ["bar_i", "timeframe_min"])
        and np.isfinite(prices).all() and (prices > 0).all()
    ))
    table_index = {(row["symbol"], row["timeframe_min"], row["fold"]): row for row in summary["table"]}
    check("summary_table_covers_all_shards_including_empty", (
        len(summary["table"]) == len(table_index) == 216
        and set(table_index) == {(symbol, minutes, fold) for symbol in symbols
                                for minutes in PERIODS for fold in FOLDS}
    ))
    for source in sources:
        symbol = source["symbol"]
        same_source_metadata = [summary["inputs"][f"{symbol}_{minutes}_{fold}"]
                                for minutes in PERIODS for fold in FOLDS]
        check(symbol + "/shared_raw_prefix_identity", (
            all(metadata["raw_path"] == source["path"] for metadata in same_source_metadata)
            and len({metadata["raw_bounded_ohlcv_sha256"] for metadata in same_source_metadata}) == 1
            and len({metadata["raw_rows"] for metadata in same_source_metadata}) == 1
        ))
        for minutes in PERIODS:
            for fold in FOLDS:
                key = f"{symbol}_{minutes}_{fold}"
                group, receipt = groups[key], receipts[key]
                metadata = summary["inputs"][key]
                expected_shard_files = {
                    f"{key}_{suffix}" for suffix in (
                        "candidates.csv", "decisions.csv", "proposals.csv.gz", "trace.csv.gz",
                    )
                }
                check(key + "/receipt_identity_and_metadata", (
                    receipt["identity"] == dict(source_manifest_sha256=source_manifest_sha,
                        raw_bounded_ohlcv_sha256=metadata["raw_bounded_ohlcv_sha256"], key=key)
                    and receipt["input"] == metadata and metadata["fold"] == fold
                    and receipt["validation"] == summary["validation"][key]
                    and set(receipt["files"]) == expected_shard_files
                ))
                check(key + "/receipt_file_hashes", set(receipt["files"]) == expected_shard_files and all(
                    hashlib.sha256((data_dir / name).read_bytes()).hexdigest() == expected
                    for name, expected in receipt["files"].items()
                ))
                _audit_group(key, group, summary, symbol, minutes, fold, check, replay_failures)
                expected_stats = _stats(group["decisions"])
                table_row = table_index.get((symbol, minutes, fold), {})
                check(key + "/receipt_and_summary_statistics", (
                    _stats_match(table_row, expected_stats) and _stats_match(receipt["table"], expected_stats)
                    and all(receipt["table"][name] == value for name, value in (
                        ("symbol", symbol), ("timeframe_min", minutes), ("fold", fold),
                    ))
                    and receipt["validation"]["passed"] is True
                    and receipt["validation"]["confirmed_events_checked"] == expected_stats["confirmed"]
                ))
    expanded = decisions.copy()
    expanded["month"] = pd.to_datetime(expanded.signal_available_at, utc=True).dt.strftime("%Y-%m")
    for name, columns in (
        ("by_timeframe", ["timeframe_min"]), ("by_fold_timeframe", ["fold", "timeframe_min"]),
        ("by_month_timeframe", ["month", "timeframe_min"]),
        ("by_direction_timeframe", ["side", "timeframe_min"]),
    ):
        check(name + "/independent_statistics", _grouped_stats_match(expanded, columns, summary[name]))
    nulls = {}
    for fold in FOLDS:
        for minutes in PERIODS:
            key = f"{minutes}_{fold}"
            frame = decisions[decisions.fold.eq(fold) & decisions.timeframe_min.eq(minutes)]
            nulls[key] = _direction_null(frame)
            actual = summary["direction_null"][key]
            check(key + "/independent_conditional_null", all(
                _equal_number(actual[name], value) for name, value in nulls[key].items()
            ) and nulls[key]["observed"] == int(frame.status.eq("confirmed").sum()))
    previous = 0.0
    for rank, key in enumerate(sorted(nulls, key=lambda key: nulls[key]["p_one_sided"])):
        previous = max(previous, min(1.0, (4 - rank) * nulls[key]["p_one_sided"]))
        nulls[key]["p_holm"] = previous
    check("holm_four_predeclared_tests", set(summary["direction_null"]) == set(nulls) and all(
        _equal_number(summary["direction_null"][key]["p_holm"], null["p_holm"])
        for key, null in nulls.items()
    ))
    accepted = decisions[decisions.status.eq("confirmed")]
    old_comparison = _old_comparison(decisions, proposals, traces)
    return dict(
        source_commit=summary["source_commit"], checks=checks, n_checks=len(checks),
        all_passed=all(checks.values()), failed_checks=[name for name, passed in checks.items() if not passed],
        n_shards=len(groups), n_events=len(decisions), n_proposals=len(proposals),
        n_trace_rows=len(traces), n_confirmed=len(accepted), statuses=decisions.status.value_counts().to_dict(),
        replay_failures={key: failed for key, failed in replay_failures.items() if failed},
        direction_null_recomputed=nulls, prior_btc_eth_comparison=old_comparison,
        events_outside_prior_btc_eth_cohort=len(decisions) - old_comparison["repeated_event_ids"],
        reused_core_confirmations=int(accepted.core_identity.duplicated().sum()),
        trace_replay_coverage=dict(
            complete_events=int(decisions.complete_followup.eq(True).sum()),
            censored_events=int(decisions.status.eq("censored_end").sum()),
            events_with_observed_invalidation=int(decisions.first_invalid_i.notna().sum()),
            invalidated_events=int(decisions.status.eq("invalidated").sum()),
            confirmed_before_later_invalidation=int((
                accepted.first_invalid_i.notna() & accepted.first_invalid_i.gt(accepted.confirmation_i)
            ).sum()),
        ),
        limitations=[
            "Only saved ledgers and metadata were read. No model inference or raw market evaluation was repeated.",
            "Trace md independently reconstructs permanent invalidation and first confirmation, "
            "but the pre-signal focus state and original aggregation/MA calculations are not recreated.",
            "Evaluation-ready coverage is bounded by recorded clocks and warmup; its original feature values are not replayed.",
            "Prior BTC/ETH candidates are repeated evidence; pixel and detection differences are descriptive, not an extra optimized test.",
            "January-May overlaps the model validation calendar. The later cohort and model were already exposed; new event count is not a count of statistically independent observations.",
            "The four shuffled-direction tests and Holm adjustment concern conditional label agreement, "
            "not profitability, genuine launch rates or fewer false alerts.",
            "Whole-event boundary censoring is the frozen research policy, not a prefix-invariant online notification state machine.",
        ],
    )


def main() -> None:
    """Write this experiment's review only after the verifier source is committed."""
    review = verify_saved()
    path = RESULTS / "independent_review.json"
    path.write_text(json.dumps(review, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    print(json.dumps({
        "path": str(path), "n_checks": review["n_checks"],
        "all_passed": review["all_passed"], "failed_checks": review["failed_checks"],
    }))
    if not review["all_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
