"""Independent CSV-only V9 arithmetic, saved-clock and intention-ledger audit.

This verifier never imports the simulator/Study, reads raw OHLCV, recomputes MA,
selects parameters, or writes artifacts. It independently checks formulas from
saved fills and sampled-clock diagnostics. Those records cannot prove which
raw intrabar barrier occurred first or independently reconstruct the first
actual colour flip: that would require another authorized raw-price replay.

The frozen complete run has 286 cases, 849 controls, 959 source intentions and
three unassigned cases. Unknown outcomes fail complete-run verification; the
three legitimately unknown matched excesses stay NaN, never zero. All means
are event/intent sums divided by explicit counts, not compounded account P/L.
UTC parsing and clock arithmetic follow the repository's pandas 2.3 contract:
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.to_datetime.html
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.Timestamp.floor.html
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


ARMS = ("5m_native40", "5m_native40_check15m")
DEFAULT_RESULTS = Path(__file__).resolve().parents[2]/"experiments/active/exp-btcusdtp-1h-decision-cadence-preholdout-20260906-v9/results"
FIVE, QUARTER = pd.Timedelta(minutes=5), pd.Timedelta(minutes=15)
LIMITATION = "Saved-ledger audit only; not an independent raw-OHLC/MA replay or proof of intrabar/first-flip ordering."
STATIC = ["decision_time", "entry_time", "entry_price", "direction", "initial_stop", "signal_atr", "risk_pct", "risk_atr", "fold"]
TRIGGER_TIMES = ["transition_trigger_previous_open_time", "transition_trigger_open_time", "transition_trigger_available_at"]
TRADE_REQUIRED = STATIC + ["exit_time", "exit_price", "gross_return", "net_return", "net_r", "hold_minutes", "closed", "outcome"] + TRIGGER_TIMES


class VerificationError(ValueError):
    """A saved result violates the frozen audit contract."""


def _check(condition, message):
    if not bool(condition):
        raise VerificationError(message)


def _table(frame, required, label):
    _check(frame.columns.is_unique, label+": duplicate columns")
    missing = set(["event_id"]+list(required))-set(frame)
    _check(not missing, label+": missing columns "+str(sorted(missing)))
    ids = frame.event_id
    _check(ids.notna().all() and ids.is_unique and ids.map(lambda x: isinstance(x, str) and bool(x.strip())).all(), label+": invalid/duplicate identities")
    return frame.copy().set_index("event_id").sort_index()


def _times(frame, columns, label, nullable=()):
    for column in columns:
        frame[column] = pd.to_datetime(frame[column], utc=True, errors="raise", format="mixed")
        if column not in nullable:
            _check(frame[column].notna().all(), label+": missing "+column)


def _booleans(series, label):
    _check(series.map(lambda x: isinstance(x, (bool, np.bool_))).all(), label+": explicit boolean required")


def _equal_numbers(actual, expected, label):
    try:
        np.testing.assert_allclose(np.asarray(actual, dtype=float), np.asarray(expected, dtype=float),
                                   rtol=1e-12, atol=1e-12, equal_nan=True)
    except (AssertionError, TypeError, ValueError) as error:
        raise VerificationError(label+": numerical mismatch") from error


def _same(left, right, columns, label):
    _check(left.index.equals(right.index), label+": identity mismatch")
    for column in columns:
        _check(column in left and column in right, label+": missing invariant "+column)
        # All entry values are serialized by the same writer: no entry drift
        # tolerance is needed. In particular a 1ns time change cannot pass.
        _check(left[column].equals(right[column]), label+": invariant changed: "+column)


def _trades(frame, cadence, expected_count, label):
    required = TRADE_REQUIRED + (["transition_trigger_previous_available_at"] if cadence == 15 else [])
    t = _table(frame, required, label)
    _check(len(t) == expected_count, label+": unexpected population count")
    times = ["decision_time", "entry_time", "exit_time"]+TRIGGER_TIMES
    nullable = list(TRIGGER_TIMES)
    if cadence == 15:
        times.append("transition_trigger_previous_available_at")
        nullable.append("transition_trigger_previous_available_at")
    _times(t, times, label, nullable)
    _booleans(t.closed, label+" closed")
    _check(t.closed.all(), label+": incomplete outcomes; unknown returns must not become zero")
    numeric = ["entry_price", "exit_price", "direction", "initial_stop", "signal_atr", "risk_pct", "risk_atr", "gross_return", "net_return", "net_r", "hold_minutes"]
    _check(np.isfinite(t[numeric].to_numpy(dtype=float)).all(), label+": nonfinite economics")
    _check(t.direction.isin([-1, 1]).all(), label+": invalid direction")
    _check(t[["entry_price", "exit_price", "initial_stop", "signal_atr", "risk_pct", "risk_atr"]].gt(0).all().all(), label+": invalid price/risk")
    _check(t.entry_time.eq(t.decision_time).all(), label+": entry decision changed")
    _check(t.entry_time.eq(t.entry_time.dt.floor("5min")).all(), label+": entry off raw5 grid")
    _check(t.exit_time.eq(t.exit_time.dt.floor("5min")).all(), label+": exit off raw5 grid")
    duration = t.exit_time-t.entry_time
    _check(duration.gt(pd.Timedelta(0)).all() and duration.le(pd.Timedelta(hours=72)).all(), label+": invalid holding clock")
    gross = t.direction*(t.exit_price/t.entry_price-1)
    risk = t.direction*(t.entry_price-t.initial_stop)
    _equal_numbers(t.gross_return, gross, label+" gross formula")
    _equal_numbers(t.net_return, gross-.002, label+" 20bp net formula")
    _equal_numbers(t.risk_pct, risk/t.entry_price, label+" risk_pct formula")
    _equal_numbers(t.risk_atr, risk/t.signal_atr, label+" risk_atr formula")
    _equal_numbers(t.net_r, (gross-.002)/(risk/t.entry_price), label+" net_r formula")
    _equal_numbers(t.hold_minutes, duration.dt.total_seconds()/60, label+" hold formula")
    for col, expected in (("partial_fraction", 0.), ("exit_remaining_fraction", 1.), ("realised_partial_gross_return", 0.)):
        if col in t:
            _equal_numbers(t[col], np.full(len(t), expected), label+" "+col)
    if "funding_modelled" in t:
        _booleans(t.funding_modelled, label+" funding")
        _check(not t.funding_modelled.any(), label+": funding contract changed")
    allowed = ["transition_colour_exit", "hard_stop", "hard_stop_gap", "time_exit"]
    _check(t.outcome.isin(allowed).all(), label+": unsupported/incomplete outcome")
    flip = t.loc[t.outcome.eq("transition_colour_exit")]
    _check(flip[TRIGGER_TIMES].notna().all().all(), label+": missing flip diagnostics")
    _check((flip.transition_trigger_open_time+FIVE).eq(flip.exit_time).all()
           and flip.transition_trigger_available_at.eq(flip.exit_time).all(), label+": flip availability clock")
    _check(flip.transition_trigger_open_time.ge(flip.entry_time).all(), label+": pre-entry trigger")
    _check(flip.direction.mul(flip.exit_price-flip.initial_stop).gt(0).all(), label+": resting stop should precede flip fill")
    if cadence == 5:
        _check((flip.transition_trigger_previous_open_time+FIVE).eq(flip.transition_trigger_open_time).all(), label+": native5 adjacent clock")
    else:
        previous = flip.transition_trigger_previous_available_at
        _check(previous.notna().all() and (flip.transition_trigger_previous_open_time+FIVE).eq(previous).all(), label+": previous sample availability")
        first = previous.eq(flip.entry_time) & flip.exit_time.eq(flip.entry_time.dt.floor("15min")+QUARTER)
        subsequent = previous.gt(flip.entry_time) & previous.eq(previous.dt.floor("15min")) & flip.exit_time.sub(previous).eq(QUARTER)
        _check(flip.exit_time.eq(flip.exit_time.dt.floor("15min")).all() and (first | subsequent).all(), label+": quarter sample adjacency")
    nonflip = t.loc[~t.outcome.eq("transition_colour_exit")]
    _check(nonflip[TRIGGER_TIMES].isna().all().all(), label+": nonflip carries stale trigger")
    if cadence == 15:
        _check(nonflip.transition_trigger_previous_available_at.isna().all(), label+": nonflip carries stale sampled trigger")
    hard = t.loc[t.outcome.eq("hard_stop")]
    _equal_numbers(hard.exit_price, hard.initial_stop, label+" fixed hard stop")
    gap = t.loc[t.outcome.eq("hard_stop_gap")]
    _check(gap.direction.mul(gap.exit_price-gap.initial_stop).le(0).all(), label+": gap-stop fill on wrong side")
    deadline = t.loc[t.outcome.eq("time_exit")]
    _check(deadline.exit_time.sub(deadline.entry_time).eq(pd.Timedelta(hours=72)).all(), label+": time exit before/after frozen deadline")
    return t, {"rows": len(t), "mean_net_bp": float(t.net_return.sum()/len(t)*1e4),
               "flip_exits": len(flip), "hard_stops": len(hard), "gap_stops": len(gap),
               "stops_off_quarter": int(pd.concat([hard, gap]).exit_time.ne(pd.concat([hard, gap]).exit_time.dt.floor("15min")).sum())}


def _matching(frame, cases, controls, unmatched, label):
    pair = _table(frame, ["mother_decision_time", "fold", "event_net_return", "assigned_controls", "control_mean_return", "excess"], label)
    _times(pair, ["mother_decision_time"], label)
    _check(pair.index.equals(cases.index), label+": fixed case identities changed")
    _check(pair.mother_decision_time.eq(cases.decision_time).all() and pair.fold.equals(cases.fold), label+": fixed case context changed")
    _check("parent_event_id" in controls and controls.parent_event_id.isin(cases.index).all(), label+": foreign/missing control parent")
    counts = controls.groupby("parent_event_id").size().reindex(cases.index, fill_value=0)
    _check(counts.isin([0, 3]).all() and counts.eq(0).sum() == unmatched, label+": not fixed full control triplets")
    means = controls.groupby("parent_event_id").net_return.mean().reindex(cases.index)
    _equal_numbers(pair.assigned_controls, counts, label+" control assignment count")
    _equal_numbers(pair.event_net_return, cases.net_return, label+" matched case return")
    _equal_numbers(pair.control_mean_return, means, label+" independent control means")
    _equal_numbers(pair.excess, cases.net_return-means, label+" excess formula")
    _check(controls.direction.eq(controls.parent_event_id.map(cases.direction)).all(), label+": transferred direction changed")
    _check(controls.fold.eq(controls.parent_event_id.map(cases.fold)).all(), label+": control crossed fold")
    _equal_numbers(controls.risk_atr, controls.parent_event_id.map(cases.risk_atr), label+" transferred risk ATR")
    return pair, cases.net_return-means


def _serial(frame, cases, expected_zones, label):
    cols = ["zone_id", "mother_decision_time", "fold", "status", "entry_event_id", "episode_net_return", "portfolio_selected", "observed", "occupied_until", "terminal_time"]
    ledger = _table(frame, cols, label)
    _times(ledger, ["mother_decision_time", "occupied_until", "terminal_time"], label)
    _check(len(ledger) == expected_zones and ledger.zone_id.eq(ledger.index).all(), label+": original source-zone denominator changed")
    _booleans(ledger.portfolio_selected, label+" selected")
    _booleans(ledger.observed, label+" observed")
    _check(ledger.observed.all() and np.isfinite(ledger.episode_net_return).all(), label+": unknown source outcome cannot be filled with zero")
    emitted = ledger.status.eq("request_emitted")
    known_zero = ledger.status.isin(["first_release_unqualified", "expired_no_release"])
    _check((emitted | known_zero).all(), label+": unknown source status")
    ids = ledger.loc[emitted, "entry_event_id"]
    _check(ids.is_unique and set(ids) == set(cases.index), label+": source-to-case linkage changed")
    _check(ledger.loc[known_zero, "entry_event_id"].isna().all(), label+": nonentry source has trade")
    _equal_numbers(ledger.loc[emitted, "episode_net_return"], ids.map(cases.net_return), label+" source trade return")
    _equal_numbers(ledger.loc[known_zero, "episode_net_return"], np.zeros(known_zero.sum()), label+" observed nonentry zero")
    _check(ledger.loc[emitted, "occupied_until"].eq(ids.map(cases.exit_time)).all(), label+": occupied-until trade clock")
    _check(ledger.loc[known_zero, "occupied_until"].eq(ledger.loc[known_zero, "terminal_time"]).all(), label+": occupied-until nonentry clock")
    _check(ledger.occupied_until.ge(ledger.mother_decision_time).all(), label+": negative occupancy")
    # Independent greedy reservation; skipped intentions do not extend the lock.
    selected = pd.Series(False, index=ledger.index)
    for _, part in ledger.groupby("fold"):
        free_at = None
        for event_id, row in part.sort_values(["mother_decision_time", "zone_id"]).iterrows():
            if free_at is None or row.mother_decision_time >= free_at:
                selected.at[event_id] = True
                free_at = row.occupied_until.ceil("5min")
    _check(selected.equals(ledger.portfolio_selected), label+": serial selection differs from independent occupancy replay")
    values = ledger.episode_net_return.where(selected, 0.)
    return ledger, values, {"original_zones": len(ledger), "selected_zones": int(selected.sum()),
                           "selected_trades": int((selected & emitted).sum()), "skipped_emitted": int((~selected & emitted).sum()),
                           "mean_net_bp_per_original_zone": float(values.sum()/len(ledger)*1e4)}


def _delta(frame, before, after, times, label):
    saved = _table(frame, ["mother_decision_time", "before", "after", "difference"], label)
    _times(saved, ["mother_decision_time"], label)
    _check(saved.index.equals(before.index) and saved.mother_decision_time.eq(times).all(), label+": denominator/clock changed")
    _equal_numbers(saved.before, before, label+" before")
    _equal_numbers(saved.after, after, label+" after")
    _equal_numbers(saved.difference, after-before, label+" difference")
    values = after-before
    finite = np.isfinite(values)
    return {"rows": len(values), "finite_pairs": int(finite.sum()), "unknown_pairs": int((~finite).sum()),
            "mean_difference_bp": float(values.loc[finite].sum()/finite.sum()*1e4) if finite.any() else None}


def verify_tables(arms, deltas, *, expected_counts=(286, 849, 959), expected_unmatched=3):
    """Pure testable ledger audit; nondefault expected support is explicit in output."""
    _check(set(arms) == set(ARMS), "Exactly the two frozen V9 arms are required")
    prepared, report = {}, {}
    for arm, cadence in zip(ARMS, (5, 15)):
        prepared[arm], report[arm] = {}, {}
        for population, count in zip(("case", "control"), expected_counts[:2]):
            t, info = _trades(arms[arm][population], cadence, count, arm+" "+population)
            prepared[arm][population], report[arm][population] = t, info
        a = prepared[arm]
        a["pairs"], a["excess"] = _matching(arms[arm]["matched"], a["case"], a["control"], expected_unmatched, arm)
        a["serial"], a["serial_value"], report[arm]["serial"] = _serial(arms[arm]["serial"], a["case"], expected_counts[2], arm)
    before, after = (prepared[arm] for arm in ARMS)
    for population in ("case", "control"):
        invariant = STATIC + (["parent_event_id"] if population == "control" else [])
        # When available, every native entry-state diagnostic is frozen too.
        invariant += [column for column in before[population] if column.startswith(("mg_entry_", "ltf_entry_"))]
        _same(before[population], after[population], invariant, population+" arm invariants")
    _same(before["serial"], after["serial"], ["zone_id", "mother_decision_time", "fold", "status", "entry_event_id"], "serial identities")
    effects = {
        "case_delta": _delta(deltas["case_delta"], before["case"].net_return, after["case"].net_return, before["case"].decision_time, "case_delta"),
        "excess_delta": _delta(deltas["excess_delta"], before["excess"], after["excess"], before["case"].decision_time, "excess_delta"),
        "serial_delta": _delta(deltas["serial_delta"], before["serial_value"], after["serial_value"], before["serial"].mother_decision_time, "serial_delta"),
    }
    return {"status": "passed", "scope": LIMITATION, "original_counts": dict(zip(("cases", "controls", "zones"), expected_counts)),
            "unmatched_cases": expected_unmatched, "roundtrip_cost_fraction": .002, "arms": report, "effects": effects}


def verify_results(results=DEFAULT_RESULTS, *, expected_counts=(286, 849, 959), expected_unmatched=3):
    """Read only the explicit saved CSV ledgers under one results directory."""
    path = Path(results)
    names = {"case": "case_trades.csv.gz", "control": "control_trades.csv.gz",
             "matched": "matched_request_outcomes.csv", "serial": "single_pending_zone_ledger.csv.gz"}
    arms = {arm: {key: pd.read_csv(path/arm/name) for key, name in names.items()} for arm in ARMS}
    deltas = {name: pd.read_csv(path/(name+".csv")) for name in ("case_delta", "excess_delta", "serial_delta")}
    return verify_tables(arms, deltas, expected_counts=expected_counts, expected_unmatched=expected_unmatched)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    args = parser.parse_args(argv)
    try:
        report = verify_results(args.results)
    except (VerificationError, ValueError, TypeError, KeyError, OSError) as error:
        print(json.dumps({"status": "failed", "error": str(error), "scope": LIMITATION}, ensure_ascii=False, allow_nan=False))
        return 1
    print(json.dumps(report, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
