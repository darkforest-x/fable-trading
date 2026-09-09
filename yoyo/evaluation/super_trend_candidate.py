"""Offline event-clock prototype for volume/IMACD/momentum confirmation.

This is a research evidence ledger, not a trading strategy or live filter.
All features use closed bars only. Starts keep their original observation
time; a later 4H cross appends an upgrade without rewriting the start. The
strict gate proposed before case calculations is retained even if it misses
the selected winner. Episode memory is an explicit post-case hypothesis,
not a parameter selected or validated on independent outcomes.

Sources: current IMACD feature contract and Owner-linked Momentum 1.0 SMA50.
No API clients, model inference, notification, orders, fitting or future labels.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from yoyo.evaluation.useless_multiscale_case import (
    DEFAULT_SOURCE, aggregate_complete, asof_closed, clean, features, read_bars,
)


def active_lower_episode(frame: pd.DataFrame, decision: pd.Timestamp) -> dict:
    """Replay 15m release/zero/zone events available by decision, without expiry.

    A return inside the original box is recorded, not silently discarded.
    Opposite release, nonpositive md or loss of frozen lower boundary invalidate
    a long episode. This proposed memory rule has not been profit-validated.
    """
    eligible = frame.loc[frame.index + pd.Timedelta(minutes=15) <= decision]
    active = None
    for opened, row in eligible.iterrows():
        closed = opened + pd.Timedelta(minutes=15)
        if row.release_side != 0:
            active = ({"release_close": closed, "reference_price": row.close,
                       "zone_low": row.release_zone_low, "zone_high": row.release_zone_high,
                       "release_momentum": row.momentum10,
                       "release_volume_median20": row.relative_volume_median20,
                       "closes_back_inside": 0} if row.release_side == 1 else None)
        elif active is not None and (row.md <= 0 or row.close < active["zone_low"]):
            active = None
        if active is not None:
            if active["zone_low"] <= row.close <= active["zone_high"]:
                active["closes_back_inside"] += 1
            active["last_close"] = row.close
            active["last_observed_at"] = closed
    if active is None:
        return {"state": "none", "rule_status": "post_case_unvalidated"}
    return {"state": "active_long", "rule_status": "post_case_unvalidated", **active,
            "age_minutes": (decision-active["release_close"]).total_seconds()/60}


def evidence_at(one: pd.DataFrame, lower: pd.DataFrame, higher: pd.DataFrame,
                opened: pd.Timestamp) -> dict:
    """Features only through the specified 1H close; never read future labels."""
    row = one.loc[opened]
    decision = opened + pd.Timedelta(hours=1)
    low = lower.loc[lower.index + pd.Timedelta(minutes=15) <= decision]
    high = asof_closed(higher, 240, decision)
    releases = low.loc[low.release_side.eq(1)]
    last_release = None if releases.empty else releases.index[-1] + pd.Timedelta(minutes=15)
    age = None if last_release is None else (decision-last_release).total_seconds()/60
    checks = {"dense_recent": bool(row.dense_recent),
              "volume_at_least_2x_prior_median20": bool(row.relative_volume_median20 >= 2),
              "momentum_at_least_90_now": bool(row.momentum10 >= 90),
              "price_above_six_mas": bool(row.close_above_all6),
              "15m_release_in_last_60min": bool(age is not None and 0 <= age <= 60)}
    # Formation starts before the arrow. Keep strong momentum observed during
    # that formation, instead of pretending it occurred on the release candle.
    prefix = one.loc[one.index <= opened]
    required = int(row.near_zero_bars) + 1
    formation = prefix.iloc[-required:]
    if len(formation) != required or not (formation.index.to_series().diff().dropna() == pd.Timedelta(hours=1)).all():
        raise ValueError("Full contiguous 1H formation history is required")
    strong = formation.loc[formation.momentum10.ge(90)]
    return {"type": "indicator_start", "observed_at": decision,
            "bar_open": opened, "reference_price": row.close,
            "strict_research_gate": all(checks.values()), "checks": checks,
            "volume_mean20": row.relative_volume_mean20,
            "volume_median20": row.relative_volume_median20,
            "momentum_now": row.momentum10,
            "momentum_strong_observed_during_setup": [x+pd.Timedelta(hours=1) for x in strong.index],
            "15m_age_minutes": age, "lower_episode": active_lower_episode(low, decision),
            "higher_available_close": high.close_time,
            "higher_md": high.md, "higher_sb": high.sb,
            "higher_bullish_now": bool(high.md > high.sb),
            "higher_above_six_mas": bool(high.close_above_all6),
            "missing_independent_yolo_evidence_is_not_a_pass": True,
            "research_only": True}


def replay(one: pd.DataFrame, lower: pd.DataFrame, higher: pd.DataFrame,
           start: pd.Timestamp) -> list[dict]:
    """Append observations in chronological order; no retrospective upgrades."""
    events, active = [], None
    for opened, row in one.iterrows():
        decision = opened + pd.Timedelta(hours=1)
        if decision < start:
            continue
        if active is not None and (row.md <= 0 or row.close < active["zone_low"] or row.release_side != 0):
            events.append({"type": "episode_ended", "observed_at": decision,
                "start_observed_at": active["start"], "reason": "zero_zone_or_new_release",
                "reference_price": row.close, "research_only": True})
            active = None
        if row.release_side == 1:
            events.append(evidence_at(one, lower, higher, opened))
            active = {"start": decision, "zone_low": row.release_zone_low, "upgraded": False}
        if active is not None and not active["upgraded"]:
            high = asof_closed(higher, 240, decision)
            available = pd.Timestamp(high.close_time)
            if bool(high.golden_cross) and available == decision and available > active["start"]:
                events.append({"type": "4h_cross_upgrade", "observed_at": available,
                    "start_observed_at": active["start"], "reference_price": high.close,
                    "hours_after_start": (available-active["start"]).total_seconds()/3600,
                    "research_only": True})
                active["upgraded"] = True
    return clean(events)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise ValueError("Do not overwrite an earlier evidence ledger")
    source = read_bars(args.source)
    frames = {n: features(aggregate_complete(source, n)) for n in (15, 60, 240)}
    result = {"schema": "spike-super-trend-research-v0.1", "production_eligible": False,
        "source_sha256": hashlib.sha256(args.source.read_bytes()).hexdigest(),
        "notes": "No profitability test; strict early hypothesis plus post-case memory diagnostics.",
        "events": replay(frames[60], frames[15], frames[240], pd.Timestamp("2026-08-01T00:00Z"))}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
