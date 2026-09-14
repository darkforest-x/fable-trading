"""Native V1 0.5R BE plus 1.5R-to-0.5R tier-lock paired replay.

This fixed-configuration historical sensitivity uses the immutable 6,185-event
native V1 ledger and already-receipted featured-bar caches from the preceding
BE05 run.  It makes no admission, Pine, live-execution, or data-collection
change.  Baseline and BE05 outcomes are read only from their SHA-pinned paired
ledger; only the tier-lock path is replayed bar by bar.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_burst_replay import path_reference
from yoyo.evaluation import spike_native_v1_be05 as prior

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-spike-v1-v8-tier-lock-20260914-v1"
OUTPUT = EXP / "native_v1"
PRIOR_OUTPUT = ROOT / "experiments/active/exp-spike-v1-v8-be05-20260914-v1/native_v1"
PRIOR_PAIRS = PRIOR_OUTPUT / "paired_trade_ledger.csv.gz"
PRIOR_RECEIPT = PRIOR_OUTPUT / "receipt.json"
EXPECTED_PRIOR_PAIRS_SHA256 = "360909bf905d35b9194bf11d75f109ca21c972adbc45e870fb8a29bb0ae9839a"
BE_R, TIER_R, TIER_LOCK_R, COST = 0.5, 1.5, 0.5, 0.002
METHOD_VERSION = "native-v1-tier-lock-0.5be-1.5to0.5-v1"


def sha256(path: Path) -> str:
    """Return a file identity without relying on a path or timestamp."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: dict[str, Any]) -> None:
    """Persist owned success or failure evidence."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _lock_price(entry: float, initial_risk: float, tick: float, stage: int) -> float:
    """Return the conservative long-side tick-grid protection for one stage."""
    # The first tier is exactly the old BE05 contract: protect the actual
    # following-open entry, even when binary division makes a tick-grid entry
    # look microscopically below its integer tick count.  Only the new 0.5R
    # profit-lock target needs an executable conservative tick conversion.
    if stage == 1:
        return entry
    target = entry + TIER_LOCK_R * initial_risk
    # A long protective stop rounds down: it never claims a higher executable
    # lock than the exchange tick grid permits.
    return math.floor(target / tick) * tick


def _raise_stage(protection: float, entry: float, initial_risk: float, tick: float, stage: int) -> float:
    """Keep a native protection that is already tighter than the new tier."""
    return max(protection, _lock_price(entry, initial_risk, tick, stage))


def _tier_exit(
    bars: pd.DataFrame, signal_time: pd.Timestamp, signal_close: float, signal_risk: float,
    initial_stop: float, tick: float,
) -> dict[str, Any]:
    """Replay one fixed native-long entry with causal, next-bar tier protections.

    V1 native protection is reconstructed from ``path_reference`` using its
    signal-close reference path.  The tier thresholds use only cumulative highs
    from successfully held bars and the actual following-open initial risk.
    Stop tests occur before either threshold can update protection for a bar.
    """
    signal_i = bars.index.get_loc(signal_time)
    entry_i = signal_i + 1
    if entry_i >= len(bars):
        raise ValueError("event lacks following-open entry in featured cache")
    entry = float(bars.open.iloc[entry_i])
    step = bars.index[1] - bars.index[0]
    native, native_peak, native_armed = float(initial_stop), 0.0, False
    initial_risk = entry - float(initial_stop)
    if not math.isfinite(initial_risk) or initial_risk <= 0:
        raise ValueError("native event has invalid actual-entry initial risk")
    stage, peak_high = 0, entry
    be_time: pd.Timestamp | None = None
    tier_time: pd.Timestamp | None = None
    be_count = tier_count = 0
    exit_i: int | None = None
    exit_price, reason = math.nan, "censored"
    if entry <= native:
        exit_i, exit_price, reason = entry_i, entry, "entry_gap_through_stop"
    else:
        for i in range(entry_i, len(bars)):
            bar = bars.iloc[i]
            tier_floor = _lock_price(entry, initial_risk, tick, stage) if stage else -math.inf
            active = max(native, tier_floor)
            # Active prior-close protection, including prior tier stages, wins
            # before the current high can become an observed trigger.
            if float(bar.low) <= active:
                exit_i, exit_price, reason = i, min(float(bar.open), active), "protective_stop"
                break
            peak_high = max(peak_high, float(bar.high))
            path = path_reference(1, signal_close, signal_risk, native, native_peak, native_armed,
                                  float(bar.open), float(bar.high), float(bar.low), float(bar.close), float(bar.atr), tick=tick)
            if not path.alive:
                raise AssertionError("native path disagrees with its active protection")
            native, native_peak, native_armed = path.protection, path.peak_r, path.armed
            mfe_r = (peak_high - entry) / initial_risk
            # A single completed bar can cross both levels.  Both state changes
            # become active only at the next loop iteration.
            if stage < 1 and mfe_r >= BE_R:
                stage, be_time, be_count = 1, bars.index[i], be_count + 1
            if stage < 2 and mfe_r >= TIER_R:
                stage, tier_time, tier_count = 2, bars.index[i], tier_count + 1
    if exit_i is None:
        exit_i, exit_price = len(bars) - 1, float(bars.close.iloc[-1])
        exit_time = min(prior.END, bars.index[-1] + step)
    else:
        exit_time = bars.index[entry_i] if reason == "entry_gap_through_stop" else bars.index[exit_i] + step
    held_end = exit_i if reason == "protective_stop" else exit_i + 1
    held = bars.iloc[entry_i:held_end]
    highs = held.high.to_numpy(float) if len(held) else np.array([entry])
    lows = held.low.to_numpy(float) if len(held) else np.array([entry])
    gross, net = float(exit_price / entry - 1), float(exit_price / entry - 1 - COST)
    risk_fraction = initial_risk / entry
    return {"entry_time": bars.index[entry_i], "entry_price": entry, "initial_stop": float(initial_stop),
            "initial_risk": initial_risk, "exit_time": exit_time, "exit_price": float(exit_price),
            "exit_reason": reason, "gross_return": gross, "net_return": net, "net_r": net / risk_fraction,
            "mfe_r": float((highs.max() - entry) / initial_risk), "mae_r": float((lows.min() - entry) / initial_risk),
            "censored": reason == "censored", "be05_trigger_bar_open": be_time, "tier15_trigger_bar_open": tier_time,
            "be05_trigger_count": be_count, "tier15_trigger_count": tier_count, "tier_stage_final": stage}


def _prior_inputs() -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load baseline/BE05 evidence only if its immutable receipt still matches."""
    receipt = json.loads(PRIOR_RECEIPT.read_text())
    if receipt.get("status") != "complete" or receipt.get("files", {}).get("paired_trade_ledger.csv.gz") != EXPECTED_PRIOR_PAIRS_SHA256:
        raise ValueError("prior native BE05 receipt does not pin the expected paired ledger")
    if sha256(PRIOR_PAIRS) != EXPECTED_PRIOR_PAIRS_SHA256:
        raise ValueError("prior native BE05 paired ledger SHA drift")
    pairs = pd.read_csv(PRIOR_PAIRS)
    if len(pairs) != 6185 or pairs.event_id.nunique() != 6185:
        raise ValueError("prior native paired ledger must contain 6,185 unique events")
    return pairs, receipt


def _old_arm_sample(expected: pd.DataFrame, cache_paths: list[Path], tick: float, prior_rows: pd.DataFrame) -> int:
    """Recompute old baseline/BE05 on a small cache sample before tier full run."""
    outcomes = prior._cached_outcomes(expected, cache_paths, tick)
    reference = prior_rows.set_index("event_id")
    if set(expected.ledger_event_id) != set(outcomes):
        raise ValueError("sample expected events absent from prior featured cache")
    for event_id, outcome in outcomes.items():
        row = reference.loc[event_id]
        for arm, prefix in (("baseline", "baseline"), ("be05", "be05")):
            actual = outcome[arm]
            for field, column in (("exit_price", f"{prefix}_exit_price"), ("net_r", f"{prefix}_net_r"),
                                  ("net_return", f"{prefix}_net_return")):
                if not np.isclose(float(actual[field]), float(row[column]), rtol=0, atol=1e-10, equal_nan=True):
                    raise ValueError(f"prior {arm} sample parity failed {event_id} {field}")
            if bool(actual["censored"]) != bool(row[f"{prefix}_censored"]):
                raise ValueError(f"prior {arm} sample censor parity failed {event_id}")
    return len(outcomes)


def _metric_rows(frame: pd.DataFrame, scope: str, arm: str) -> dict[str, Any]:
    closed = frame.loc[~frame[f"{arm}_censored"].astype(bool)].copy()
    values = pd.to_numeric(closed[f"{arm}_net_r"], errors="coerce").dropna()
    positive, negative = values.loc[values > 0].sum(), values.loc[values < 0].sum()
    return {"scope": scope, "arm": arm, "signal_rows": len(frame), "closed": len(values),
            "censored": int(frame[f"{arm}_censored"].astype(bool).sum()), "net_wins": int((values > 0).sum()),
            "net_win_rate": float((values > 0).mean()) if len(values) else np.nan,
            "pf_r": float(positive / abs(negative)) if negative < 0 else np.nan,
            "net_mean_r": float(values.mean()) if len(values) else np.nan, "total_r": float(values.sum()),
            "original_net_ge_10r": int((pd.to_numeric(frame.baseline_net_r, errors="coerce") >= 10).sum()),
            "original_net_ge_10r_retained": int(((pd.to_numeric(frame.baseline_net_r, errors="coerce") >= 10) & (pd.to_numeric(frame[f"{arm}_net_r"], errors="coerce") >= 10)).sum()),
            "rescued_original_losses": int(((pd.to_numeric(frame.baseline_net_r, errors="coerce") < 0) & (pd.to_numeric(frame[f"{arm}_net_r"], errors="coerce") >= 0)).sum()),
            "harmed_original_winners": int(((pd.to_numeric(frame.baseline_net_r, errors="coerce") > 0) & (pd.to_numeric(frame[f"{arm}_net_r"], errors="coerce") < pd.to_numeric(frame.baseline_net_r, errors="coerce"))).sum())}


def _summaries(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Produce individual and strict entry-and-exit time-block ledgers."""
    each = pd.DataFrame([_metric_rows(frame, "each_closed", arm) for arm in ("baseline", "be05", "tier")])
    joint = frame.loc[~frame.baseline_censored.astype(bool) & ~frame.be05_censored.astype(bool) & ~frame.tier_censored.astype(bool)].copy()
    rows = [_metric_rows(joint, "joint_closed", arm) for arm in ("baseline", "be05", "tier")]
    for minutes, part in joint.groupby("timeframe_min", sort=True):
        rows.extend(_metric_rows(part, f"joint_closed_{int(minutes)}m", arm) for arm in ("baseline", "be05", "tier"))
    midpoint = pd.Timestamp("2025-09-10T00:00:00Z")
    for label, start, end in (("2024-09-10..2025-09-10", prior.START, midpoint), ("2025-09-10..2026-09-10", midpoint, prior.END)):
        strict = joint.loc[(joint.entry_time >= start) & (joint.entry_time < end) &
                           (joint.baseline_exit_time >= start) & (joint.baseline_exit_time < end) &
                           (joint.be05_exit_time >= start) & (joint.be05_exit_time < end) &
                           (joint.tier_exit_time >= start) & (joint.tier_exit_time < end)]
        rows.extend(_metric_rows(strict, f"joint_closed_strict_{label}", arm) for arm in ("baseline", "be05", "tier"))
    return each, pd.DataFrame(rows)


def run(output: Path, *, max_streams: int | None = None) -> dict[str, Any]:
    """Build a complete tier ledger; fail closed on every missing expected event."""
    output.mkdir(parents=True, exist_ok=True)
    prior_pairs, prior_receipt = _prior_inputs()
    link = pd.read_csv(prior.LINK_LEDGER)
    immutable = pd.read_csv(prior.IMMUTABLE_LEDGER)
    contract = prior._assert_source_contract(link, immutable)
    old_by_event = prior_pairs.set_index("event_id")
    immutable_by_event = immutable.set_index("event_id")
    catalog = pd.read_json(prior.SOURCE_EXP / "data/catalog.json")
    ticks = catalog.set_index(["venue", "symbol"]).tick.astype(float).to_dict()
    keys = ["venue", "symbol", "timeframe_min", "frozen_ohlc_file", "frozen_ohlc_sha256", "frozen_ohlc_timeframe_min"]
    groups = list(link.groupby(keys, sort=True, dropna=False))
    if max_streams is not None:
        groups = groups[:max_streams]
    cache_index = prior._cache_index()
    receipt: dict[str, Any] = {"status": "running", "generated_at_utc": datetime.now(timezone.utc).isoformat(), "method_version": METHOD_VERSION, "config": {"window_start": prior.START.isoformat(), "window_end_exclusive": prior.END.isoformat(), "direction": "long_only", "round_trip_cost": COST, "be_trigger_r": BE_R, "tier_trigger_r": TIER_R, "tier_lock_r": TIER_LOCK_R, "activation": "completed_bar_to_next_bar", "price_lock_is_not_fee_adjusted": True, "holdout_consumption": 1, "sample_classification": "non_blind_historical"},
                               "prior_be05_pairs_sha256": EXPECTED_PRIOR_PAIRS_SHA256, "source_contract": contract,
                               "builder_sha256": sha256(Path(__file__)), "requested_streams": len(groups), "cache_indexed_streams": sum(len(x) for x in cache_index.values()), "stream_failures": []}
    records: list[dict[str, Any]] = []
    try:
        for number, (key, expected) in enumerate(groups, 1):
            venue, symbol, minutes, raw_path, raw_sha, _ = key
            source_path = Path(raw_path)
            if not source_path.is_file() or sha256(source_path) != raw_sha:
                raise ValueError(f"frozen source SHA mismatch: {venue}/{symbol}/{minutes}")
            tick = ticks.get((venue, symbol))
            if tick is None or not math.isfinite(tick) or tick <= 0:
                raise ValueError(f"invalid frozen tick: {venue}/{symbol}")
            enriched = expected.merge(immutable[["event_id", "signal_close", "reference_signal_risk"]], left_on="ledger_event_id", right_on="event_id", validate="one_to_one")
            cache_paths = cache_index.get((str(venue), str(symbol), int(minutes), str(raw_sha)), [])
            if not cache_paths:
                raise ValueError(f"no verified featured cache for {venue}/{symbol}/{minutes}")
            if number == 1:
                receipt["prior_arm_sample_events"] = _old_arm_sample(enriched, cache_paths, float(tick), old_by_event.reset_index())
            outcomes: dict[str, dict[str, Any]] = {}
            for cache_path in cache_paths:
                cache = pd.read_pickle(cache_path, compression="gzip")
                if float(cache["tick"]) != float(tick):
                    raise ValueError(f"cache tick mismatch: {cache_path}")
                bars = cache["bars"]
                for row in enriched.itertuples(index=False):
                    event_id = str(row.ledger_event_id)
                    signal_time = pd.Timestamp(int(row.signal_bar_open_ms), unit="ms", tz="UTC")
                    if event_id in outcomes or signal_time not in bars.index:
                        continue
                    stop = prior._frozen_stop(float(row.signal_close), float(row.reference_signal_risk), float(tick))
                    outcomes[event_id] = _tier_exit(bars, signal_time, float(row.signal_close), float(row.reference_signal_risk), stop, float(tick)) | {"cache_file": str(cache_path)}
            missing = set(enriched.ledger_event_id).difference(outcomes)
            if missing:
                raise ValueError(f"{venue}/{symbol}/{minutes}: {len(missing)} expected events missing from tier cache replay")
            for event_id, tier in outcomes.items():
                old = old_by_event.loc[event_id]
                # Tier replay must share the exact fixed V1 admission and
                # following-open fill already paired in the old ledger.  Do
                # not silently pair a valid-looking exit with a shifted cache
                # entry or a CSV-reconstructed stop.
                if pd.Timestamp(tier["entry_time"]) != pd.Timestamp(old.entry_time):
                    raise ValueError(f"tier entry-time parity failed {event_id}")
                for field, actual, expected_value in (("entry_price", tier["entry_price"], old.entry_price),
                                                      ("initial_stop", tier["initial_stop"], old.initial_stop)):
                    if not np.isclose(float(actual), float(expected_value), rtol=0, atol=1e-10):
                        raise ValueError(f"tier {field} parity failed {event_id}")
                records.append({"event_id": event_id, "venue": venue, "symbol": symbol, "timeframe_min": int(minutes), "side": 1,
                                "signal_bar_open": old.signal_bar_open,
                                "entry_time": old.entry_time, "entry_price": old.entry_price, "initial_stop": tier["initial_stop"], "initial_risk": tier["initial_risk"],
                                "baseline_exit_time": old.baseline_exit_time, "baseline_exit_price": old.baseline_exit_price, "baseline_exit_reason": old.baseline_exit_reason, "baseline_net_r": old.baseline_net_r, "baseline_net_return": old.baseline_net_return, "baseline_mfe_r": old.baseline_mfe_r, "baseline_censored": old.baseline_censored,
                                "be05_exit_time": old.be05_exit_time, "be05_exit_price": old.be05_exit_price, "be05_exit_reason": old.be05_exit_reason, "be05_net_r": old.be05_net_r, "be05_net_return": old.be05_net_return, "be05_mfe_r": old.be05_mfe_r, "be05_censored": old.be05_censored,
                                "tier_exit_time": tier["exit_time"], "tier_exit_price": tier["exit_price"], "tier_exit_reason": tier["exit_reason"], "tier_net_r": tier["net_r"], "tier_net_return": tier["net_return"], "tier_censored": tier["censored"], "tier_mfe_r": tier["mfe_r"],
                                "be05_trigger_bar_open": tier["be05_trigger_bar_open"], "tier15_trigger_bar_open": tier["tier15_trigger_bar_open"], "be05_trigger_count": tier["be05_trigger_count"], "tier15_trigger_count": tier["tier15_trigger_count"], "tier_stage_final": tier["tier_stage_final"], "source_sha256": raw_sha, "featured_cache_file": tier["cache_file"]})
            if number % 50 == 0 or number == len(groups):
                print(f"tier replay {number}/{len(groups)} streams; {len(records)} events", flush=True)
        result = pd.DataFrame(records)
        if max_streams is None and (len(result) != 6185 or result.event_id.nunique() != 6185):
            raise ValueError("tier result does not contain every expected native V1 event exactly once")
        for name in ("entry_time", "baseline_exit_time", "be05_exit_time", "tier_exit_time"):
            result[name] = pd.to_datetime(result[name], utc=True)
        result = result.sort_values(["entry_time", "event_id"], kind="mergesort")
        result.to_csv(output / "tier_paired_trade_ledger.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
        each, joint = _summaries(result)
        each.to_csv(output / "summary_each_closed.csv", index=False)
        joint.to_csv(output / "summary_joint_closed.csv", index=False)
        receipt.update({"status": "complete", "paired_events": len(result), "completed_streams": len(groups), "raw_fallback_events": 0,
                        "files": {name: sha256(output / name) for name in ("tier_paired_trade_ledger.csv.gz", "summary_each_closed.csv", "summary_joint_closed.csv")}})
        write_json(output / "receipt.json", receipt)
        return receipt
    except Exception as error:
        receipt.update({"status": "failed", "completed_events_before_failure": len(records), "error_type": type(error).__name__, "error": str(error)})
        write_json(output / "failed_run.json", receipt)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--max-streams", type=int, help="Bounded cache/parity timing probe only.")
    args = parser.parse_args()
    print(json.dumps(run(args.output, max_streams=args.max_streams), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
