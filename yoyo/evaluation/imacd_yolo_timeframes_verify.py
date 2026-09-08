"""Independent saved-ledger review of the frozen 1H/4H YOLO transfer pilot.

Source: exp-imacd-yolo-timeframes-20260908-v1/PROJECT_PLAN.md and the runner's
persisted ledger schema. This verifier does not import the selection code,
load a model, read raw OHLCV, render images, or run inference. It reads only
summary.json and that experiment's saved candidates, proposals, decisions and
p..p+10 audit traces. Commit this source before executing its artifact writer.

For each candidate p, the frozen setup is [setup_start_i,p-1]. The independently
reconstructed proposal pool uses same symbol and timeframe, endpoints p..p+9,
core lengths 4/5, post lengths 2..9 and at least one shared integer candle with
[setup_start_i,p]. The first nonfinite md or side*md<=0 in trace[p:p+9] cancels
the event permanently, including confirmation on that same candle. Selection
then takes the earliest surviving same-direction endpoint, with confidence,
window length and detection ID as the recorded deterministic tie breakers.
trace[p+10] is entry-only for a confirmation at p+9; its md cannot cancel an
earlier confirmation. Saved next-open prices are joined back to trace[p+1]
and trace[e+1]. No future price affects the reconstruction of confirmation.

The audit verifies frozen setup identities and arithmetic, not the original
pre-signal focus state: traces do not contain that earlier md/sb/ATR history.
It also does not independently reproduce source aggregation, MA values, image
pixels or YOLO boxes. Source continuity is supported by saved trace clocks and
metadata, not a second raw-file read. The separately recomputed direction null
tests conditional label association, not profitability or false-alert rates.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data/imacd_yolo_timeframes_20260908_v1"
RESULTS = ROOT / "experiments/active/exp-imacd-yolo-timeframes-20260908-v1/results"
PERIODS = (60, 240)
SYMBOLS = ("BTC", "ETH")
SEED = 20260908
PERMUTATIONS = 10000
START = pd.Timestamp("2026-05-04", tz="UTC")
END = pd.Timestamp("2026-07-01", tz="UTC")
MODEL_SHA = "862705b999594355c1133640acc540f4de19b561889e89d9e050ddad5c6db838"
CANDIDATE_COLUMNS = [
    "event_id", "symbol", "timeframe_min", "signal_i", "side", "setup_start_i",
    "setup_bars", "signal_open_at", "signal_available_at", "signal_close",
    "complete_followup",
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


def verify_saved(data_dir: Path = DATA, results_dir: Path = RESULTS) -> dict:
    """Review saved evidence only; return each check and its actual count."""
    data_dir, results_dir = Path(data_dir), Path(results_dir)
    summary = json.loads((results_dir / "summary.json").read_text())
    candidates = pd.read_csv(data_dir / "candidates.csv")
    decisions = pd.read_csv(data_dir / "decisions.csv")
    traces = pd.read_csv(data_dir / "traces.csv.gz")
    checks, groups, replay_failures = {}, {}, {}

    def check(name, value):
        checks[name] = bool(value)

    check("frozen_configuration", (
        pd.Timestamp(summary["start"]) == START
        and pd.Timestamp(summary["end_exclusive"]) == END
        and summary["max_wait_bars"] == 9
        and summary["model_sha256"] == MODEL_SHA
        and summary["holdout_consumptions"] == {"60": 1, "240": 1}
        and summary["economic_evaluation"] is False
        and summary["training_eligible"] is False
        and summary["production_eligible"] is False
    ))
    expected_names = {"candidates.csv", "decisions.csv", "traces.csv.gz"}
    for symbol in SYMBOLS:
        for minutes in PERIODS:
            key = f"{symbol}_{minutes}"
            groups[key] = {
                "candidates": pd.read_csv(data_dir / f"{key}_candidates.csv"),
                "decisions": pd.read_csv(data_dir / f"{key}_decisions.csv"),
                "proposals": pd.read_csv(data_dir / f"{key}_proposals.csv.gz"),
                "trace": pd.read_csv(data_dir / f"{key}_trace.csv.gz"),
            }
            expected_names.update({
                f"{key}_candidates.csv", f"{key}_decisions.csv",
                f"{key}_proposals.csv.gz", f"{key}_trace.csv.gz",
            })
    paths = {name: (ROOT / name).resolve() for name in summary["files"]}
    permitted = all(path.parent == data_dir.resolve() for path in paths.values())
    check("hash_manifest_covers_saved_ledgers", permitted and (
        {path.name for path in paths.values()} == expected_names
        and len(paths) == len(expected_names)
    ))
    # A malformed manifest must never redirect this audit into a raw data file.
    check("saved_ledger_hashes", permitted and all(
        path.is_file()
        and hashlib.sha256(path.read_bytes()).hexdigest() == summary["files"][name]
        for name, path in paths.items()
    ))
    proposals = pd.concat([group["proposals"] for group in groups.values()], ignore_index=True)
    for name, frame in (("candidates", candidates), ("decisions", decisions), ("trace", traces)):
        check("combined_" + name + "_matches_parts", _same_frames(
            frame, pd.concat([group[name] for group in groups.values()], ignore_index=True),
        ))
    check("candidate_decision_frozen_fields", _same_frames(
        candidates[CANDIDATE_COLUMNS], decisions[CANDIDATE_COLUMNS],
    ))
    check("unique_event_detection_trace_identities", (
        not candidates.event_id.duplicated().any()
        and not decisions.event_id.duplicated().any()
        and not proposals.detection_id.duplicated().any()
        and not traces.duplicated(["event_id", "bar_i"]).any()
    ))
    check("trace_event_set", set(traces.event_id) == set(candidates.event_id))
    check("decision_boolean_fields", all(
        decisions[name].isin([True, False]).all()
        for name in ("complete_followup", "can_long", "can_short")
    ))
    check("candidate_integer_fields", _integer_columns(candidates, [
        "signal_i", "side", "setup_start_i", "setup_bars", "timeframe_min",
    ]))
    check("proposal_integer_fields", _integer_columns(proposals, [
        "window_len", "window_start_i", "window_end_i", "core_start_i",
        "core_end_i", "core_length_bars", "confirmation_bars", "timeframe_min",
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
    check("trace_integer_fields", _integer_columns(traces, ["bar_i", "timeframe_min"]))
    trace_prices = traces[["open", "close"]].to_numpy(float)
    check("trace_prices_finite_positive", np.isfinite(trace_prices).all() and (trace_prices > 0).all())
    check("summary_group_coverage", (
        set(summary["inputs"]) == set(groups)
        and len(summary["table"]) == len(groups)
        and {(row["symbol"], row["timeframe_min"]) for row in summary["table"]}
        == {(symbol, minutes) for symbol in SYMBOLS for minutes in PERIODS}
    ))

    for symbol in SYMBOLS:
        for minutes in PERIODS:
            key = f"{symbol}_{minutes}"
            group = groups[key]
            events, boxes, trace = group["decisions"], group["proposals"], group["trace"]
            metadata = summary["inputs"][key]
            step = pd.Timedelta(minutes=minutes)
            first, last = pd.Timestamp(metadata["parsed_first"]), pd.Timestamp(metadata["parsed_last"])
            rows = int(metadata["parsed_rows"])
            check(key + "/group_identity", all(
                frame.symbol.eq(symbol).all() and frame.timeframe_min.eq(minutes).all()
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
                and last + step <= END
                and metadata["discarded_partial_source_rows"]
                == metadata["raw_rows"] - rows * (minutes // 15)
                and 0 <= metadata["discarded_partial_source_rows"] <= 2 * (minutes // 15 - 1)
            ))
            opens = pd.to_datetime(events.signal_open_at, utc=True)
            available = pd.to_datetime(events.signal_available_at, utc=True)
            check(key + "/candidate_clock_identity_setup", (
                available.between(START, END, inclusive="left").all()
                and (available - opens).eq(step).all()
                and (opens == first + pd.to_timedelta(events.signal_i * minutes, unit="min")).all()
                and events.signal_i.between(0, rows - 1).all()
                and events.side.isin([-1, 1]).all()
                and events.setup_start_i.ge(0).all()
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
                and (trace_times + step <= END).all()
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
            complete = events[events.complete_followup.eq(True)]
            kept = complete[complete.status.eq("confirmed")]
            expected_table = dict(
                max_wait_hours=9 * minutes / 60, arrows=len(events), complete=len(complete),
                confirmed=len(kept), invalidated=int(complete.status.eq("invalidated").sum()),
                expired=int(complete.status.eq("expired").sum()), censored=len(events) - len(complete),
                pass_rate_pct=100 * len(kept) / len(complete) if len(complete) else None,
                delay_median_minutes=float(kept.delay_bars.median() * minutes) if len(kept) else None,
                displacement_median_bp=float(kept.displacement_bp.median()) if len(kept) else None,
                displacement_max_bp=float(kept.displacement_bp.max()) if len(kept) else None,
            )
            matching_rows = [row for row in summary["table"]
                             if row["symbol"] == symbol and row["timeframe_min"] == minutes]
            check(key + "/summary_table", len(matching_rows) == 1 and all(
                _equal_number(matching_rows[0][name], value) for name, value in expected_table.items()
            ))
            validation = summary["validation"][key]
            check(key + "/reported_validation_count", (
                validation["passed"] is True
                and validation["confirmed_events_checked"] == len(kept)
            ))

    nulls = {}
    for minutes in PERIODS:
        frame = decisions[decisions.timeframe_min.eq(minutes)]
        nulls[str(minutes)] = _direction_null(frame)
        actual = summary["direction_null"][str(minutes)]
        check(f"{minutes}/conditional_direction_null", all(
            _equal_number(actual[name], value) for name, value in nulls[str(minutes)].items()
        ) and nulls[str(minutes)]["observed"] == int(frame.status.eq("confirmed").sum()))
    ordered = sorted(nulls, key=lambda key: nulls[key]["p_one_sided"])
    running = 0.0
    for rank, key in enumerate(ordered):
        running = max(running, min(1.0, (2 - rank) * nulls[key]["p_one_sided"]))
        nulls[key]["p_holm"] = running
    check("holm_two_timeframes", set(summary["direction_null"]) == {"60", "240"} and all(
        _equal_number(summary["direction_null"][key]["p_holm"], value["p_holm"])
        for key, value in nulls.items()
    ))
    accepted = decisions[decisions.status.eq("confirmed")]
    invalidation_count = int(decisions.first_invalid_i.notna().sum())
    return dict(
        source_commit=summary["source_commit"], checks=checks, n_checks=len(checks),
        all_passed=all(checks.values()), failed_checks=[name for name, passed in checks.items() if not passed],
        n_events=len(decisions), n_proposals=len(proposals), n_trace_rows=len(traces),
        n_confirmed=len(accepted), statuses=decisions.status.value_counts().to_dict(),
        replay_failures=replay_failures, direction_null_recomputed=nulls,
        reused_core_confirmations=int(accepted.core_identity.duplicated().sum()),
        trace_replay_coverage=dict(
            complete_events=int(decisions.complete_followup.eq(True).sum()),
            censored_events=int(decisions.status.eq("censored_end").sum()),
            events_with_observed_invalidation=invalidation_count,
            invalidated_events=int(decisions.status.eq("invalidated").sum()),
            confirmed_before_later_invalidation=int((
                accepted.first_invalid_i.notna()
                & accepted.first_invalid_i.gt(accepted.confirmation_i)
            ).sum()),
        ),
        limitations=[
            "Saved traces independently support md invalidation, direction pools, "
            "earliest confirmation and next-open price joins; the raw source is not re-read.",
            "Frozen setup boundaries are checked across ledgers and by index arithmetic. "
            "Pre-signal md/sb/ATR/focus history is absent, so original focusRelease is not independently recomputed.",
            "Aggregation metadata and trace clocks are checked, but raw OHLCV aggregation, "
            "MA history, input pixels and YOLO predictions are not independently reproduced.",
            "Direction permutation and Holm adjustment quantify conditional label association. "
            "They do not establish profitability, launch accuracy or fewer false alerts.",
            "Incomplete-follow-up candidates follow the frozen whole-event censoring policy, "
            "including when an early confirmation might already have been observable.",
            ("No saved candidate has an observed md invalidation in this run; "
             "permanent-invalidation branches remain supported by code and synthetic tests only."
             if invalidation_count == 0 else
             "Observed invalidation branch counts are reported under trace_replay_coverage; "
             "a passing audit does not imply every recovery or boundary case occurred in this run."),
        ],
    )


def main() -> None:
    """Write only this experiment's review; commit the verifier before running."""
    review = verify_saved()
    path = RESULTS / "independent_review.json"
    path.write_text(json.dumps(review, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({
        "path": str(path), "n_checks": review["n_checks"],
        "all_passed": review["all_passed"], "failed_checks": review["failed_checks"],
    }))
    if not review["all_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
