"""Causal breadth, membership, and observability features for the Monday 1.618 search.

All groups reuse the frozen native first-touch ledger. ``known`` is the close of
the W-minute UTC candle ending at that timestamp. ``wait_min`` measures completed
5m touch bars in ``[known, decision)``; no touch at/after ``decision`` is used.
"""

from __future__ import annotations

import json
import numpy as np
import pandas as pd

from yoyo.evaluation import monday_breadth_fixed_timeframes as fixed
from yoyo.evaluation.monday_limit_reclaim_patterns import SRC


previous = fixed.previous
census = previous.census
OUT = census.ACTIVE / "exp-monday-breadth-optimization-20261108-v1"
FEATURES = OUT / "features"
OCTOBER = census.OCTOBER
STEP = fixed.STEP
MINUTES = (5, 10, 15, 30, 60, 240, 720)
WAITS = (0, 5, 10, 15, 20, 30, 45, 60, 90, 120)
MARKET_SHARE_STATUS = "trailing28_any_touch_proxy"


def _opportunity_ledger() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return the canonical touched ledger plus source opportunity coverage.

    ``level`` comes from the frozen weekly opportunity table for the original
    ledger, or the frozen October extension for newly observed touch events.
    """
    fields = [
        "event_id", "symbol", "source_group", "week", "side", "status",
        "level", "monday_high", "monday_low", "touch_interval_open",
    ]
    parts: list[pd.DataFrame] = []
    for path in sorted(SRC.glob("*/opportunities.csv.gz")):
        frame = pd.read_csv(path, usecols=fields)
        frame = frame[
            frame.source_group.eq("crypto_5m")
            & frame.event_id.astype(str).str.count(":").eq(2)
        ].copy()
        if frame.empty:
            continue
        frame["touch_interval_open"] = pd.to_datetime(frame.touch_interval_open, utc=True)
        parts.append(frame)
    if not parts:
        raise FileNotFoundError(f"No crypto_5m opportunities found under {SRC}")
    opportunities = pd.concat(parts, ignore_index=True)
    if opportunities.event_id.duplicated().any():
        raise ValueError("Duplicate weekly event in the original opportunity ledger")
    if opportunities.symbol.nunique() != 626:
        raise ValueError("The original opportunity ledger no longer has the frozen 626-symbol scope")

    # A valid lower rail is known after the NY Monday range closes. Untouched
    # weeks are deliberately retained in coverage diagnostics, but are not
    # treated as observed touches in the trailing-28d causal proxy.
    valid_week = opportunities.status.isin(["admitted", "no_observed_touch"])
    lower_valid = opportunities[valid_week & opportunities.side.eq(1) & opportunities.level.notna()]
    coverage = (
        lower_valid.groupby("week", as_index=False)
        .agg(valid_lower_levels=("symbol", "nunique"), opportunity_rows=("event_id", "size"))
        .sort_values("week")
    )

    old, combined = census.ledger_rows()
    original_hits = opportunities[
        opportunities.status.eq("admitted") & opportunities.touch_interval_open.notna()
    ].rename(columns={
        "event_id": "event",
        "touch_interval_open": "touch",
        "level": "lower_touch_level",
    })
    original_hits = original_hits[[
        "event", "symbol", "side", "week", "touch", "lower_touch_level",
        "monday_high", "monday_low",
    ]].copy()
    original_hits["level_source"] = "original_weekly_opportunities"
    if original_hits.event.duplicated().any():
        raise ValueError("Duplicate admitted event in original opportunity ledger")
    original_check = old.merge(
        original_hits[["event", "symbol", "side", "touch"]],
        on=["event", "symbol", "side", "touch"], how="outer", indicator=True,
        validate="one_to_one",
    )
    if not original_check._merge.eq("both").all():
        raise ValueError("Original opportunity touches do not match the frozen 626-symbol ledger")

    extension = pd.read_csv(OCTOBER, usecols=["event", "symbol", "week", "side", "level", "t"])
    extension["t"] = pd.to_datetime(extension.t, utc=True)
    extension = extension.rename(columns={"t": "touch", "level": "lower_touch_level"})
    extension = extension[["event", "symbol", "week", "side", "touch", "lower_touch_level"]]
    extension["monday_high"] = np.nan
    extension["monday_low"] = np.nan
    extension["level_source"] = "october_touch_extension"
    seam = old.touch.max()
    extension_new = extension[extension.touch.gt(seam)].copy()
    selected = pd.concat([original_hits, extension_new], ignore_index=True)
    if selected.event.duplicated().any():
        raise ValueError("Duplicate event after the original/October ledger seam")
    if not np.isfinite(selected.lower_touch_level).all() or selected.lower_touch_level.le(0).any():
        raise ValueError("A touched weekly lower/upper level is missing or invalid")

    check = combined.merge(
        selected[["event", "symbol", "side", "touch"]],
        on=["event", "symbol", "side", "touch"], how="outer", indicator=True,
        validate="one_to_one",
    )
    if not check._merge.eq("both").all():
        raise ValueError("Price-level bridge does not match the full frozen-plus-October touch ledger")
    return selected.sort_values(["touch", "symbol", "side"]).reset_index(drop=True), coverage


def _monitor_grid(start: pd.Timestamp, end: pd.Timestamp) -> tuple[pd.DatetimeIndex, np.ndarray]:
    """Build a 5m schedule and prefix sum of opens monitored outside NY Monday."""
    first = pd.Timestamp(start).floor("5min")
    stop = pd.Timestamp(end).ceil("5min")
    grid = pd.date_range(first, stop, freq=STEP, inclusive="left")
    observed = (grid.tz_convert("America/New_York").day_of_week != 0).astype(np.int64)
    return grid, np.r_[0, np.cumsum(observed, dtype=np.int64)]


def _monitor_count(grid: pd.DatetimeIndex, prefix: np.ndarray,
                   start: pd.Timestamp, end: pd.Timestamp) -> int:
    if end <= start:
        return 0
    a = int(grid.searchsorted(start, side="left"))
    b = int(grid.searchsorted(end, side="left"))
    return int(prefix[b] - prefix[a])


def _trailing_unique_count(ledger: pd.DataFrame, known_times: pd.DatetimeIndex) -> dict[pd.Timestamp, int]:
    """Count distinct symbols with any-direction touches known in [t-28d, t]."""
    seen = ledger[["symbol", "touch"]].copy()
    # A touch event is known only once its 5m interval has closed.
    seen["available"] = seen.touch + STEP
    seen = seen.sort_values("available")
    times = pd.DatetimeIndex(seen.available)
    names = seen.symbol.to_numpy()
    queries = pd.DatetimeIndex(known_times.unique()).sort_values()
    counts: dict[str, int] = {}
    left = right = 0
    result: dict[pd.Timestamp, int] = {}
    lookback = pd.Timedelta(days=28)
    for known in queries:
        cutoff = known - lookback
        while right < len(times) and times[right] <= known:
            symbol = names[right]
            counts[symbol] = counts.get(symbol, 0) + 1
            right += 1
        while left < right and times[left] < cutoff:
            symbol = names[left]
            counts[symbol] -= 1
            if counts[symbol] == 0:
                del counts[symbol]
            left += 1
        result[known] = len(counts)
    return result


def _shock_ages(ledger: pd.DataFrame, known_times: pd.DatetimeIndex) -> dict[pd.Timestamp, tuple]:
    """Return strict-prior 25+ shock ages and explicit available-history state."""
    counts = ledger.groupby(["touch", "side"]).symbol.nunique().unstack(fill_value=0)
    lower_times = counts.index[counts.get(1, pd.Series(0, index=counts.index)).ge(25)] + STEP
    any_times = counts.index[counts.max(axis=1).ge(25)] + STEP
    lower_times = pd.DatetimeIndex(lower_times).sort_values()
    any_times = pd.DatetimeIndex(any_times).sort_values()
    out: dict[pd.Timestamp, tuple[float, float]] = {}
    for known in pd.DatetimeIndex(known_times.unique()):
        # Strictly prior: a shock completing at this signal's known timestamp
        # belongs to the current group and must not masquerade as prior context.
        li = lower_times.searchsorted(known, side="left") - 1
        ai = any_times.searchsorted(known, side="left") - 1
        lower_age = ((known - lower_times[li]).total_seconds() / 60) if li >= 0 else np.nan
        any_age = ((known - any_times[ai]).total_seconds() / 60) if ai >= 0 else np.nan
        lower_status = "prior_lower_25plus_shock_observed" if li >= 0 else "no_prior_lower_25plus_shock_in_available_ledger"
        any_status = "prior_any_side_25plus_shock_observed" if ai >= 0 else "no_prior_any_side_25plus_shock_in_available_ledger"
        out[known] = (float(lower_age), float(any_age), lower_status, any_status)
    return out


def _build_members(ledger: pd.DataFrame) -> tuple[pd.DataFrame, dict[tuple[int, pd.Timestamp], pd.DataFrame]]:
    lower = ledger[ledger.side.eq(1)].copy().sort_values("touch")
    first_observed = ledger.groupby("symbol").touch.min().rename("first_observed_trigger")
    lower = lower.join(first_observed, on="symbol")
    lower["sample_touch_history_age_days"] = (
        (lower.touch - lower.first_observed_trigger).dt.total_seconds() / 86400
    )
    member_groups: dict[tuple[int, pd.Timestamp], pd.DataFrame] = {}
    rows: list[pd.DataFrame] = []
    for minutes in MINUTES:
        span = pd.Timedelta(minutes=minutes)
        touched = lower.assign(known=lower.touch.dt.floor(span) + span)
        # Match the frozen native bridge: one constituent per symbol, retaining
        # its earliest lower first touch if a W window straddles a NY week seam.
        members = touched.drop_duplicates(["known", "symbol"], keep="first")
        for known, group in touched.groupby("known", sort=True):
            member_groups[(minutes, known)] = group
        exposed = members.copy()
        exposed["minutes"] = minutes
        exposed["age_at_known_min"] = (
            (exposed.known - (exposed.touch + STEP)).dt.total_seconds() / 60
        )
        exposed = exposed.rename(columns={"week": "touch_week"})
        rows.append(exposed[[
            "minutes", "known", "symbol", "event", "touch_week", "touch",
            "lower_touch_level", "level_source", "monday_high", "monday_low",
            "age_at_known_min", "first_observed_trigger", "sample_touch_history_age_days",
        ]])
    members_out = pd.concat(rows, ignore_index=True).sort_values(["minutes", "known", "symbol"])
    if members_out.duplicated(["minutes", "known", "symbol"]).any():
        raise ValueError("Duplicate membership bridge key")
    return members_out.reset_index(drop=True), member_groups


def _build_signals(ledger: pd.DataFrame,
                   member_groups: dict[tuple[int, pd.Timestamp], pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame]:
    lower = ledger[ledger.side.eq(1)].sort_values("touch").reset_index(drop=True)
    touch_times = pd.DatetimeIndex(lower.touch)
    knowns = sorted({known for _, known in member_groups})
    unique_count = _trailing_unique_count(ledger, pd.DatetimeIndex(knowns))
    ages = _shock_ages(ledger, pd.DatetimeIndex(knowns))
    first = min(knowns) - pd.Timedelta(minutes=max(MINUTES))
    last = max(knowns) + pd.Timedelta(minutes=max(WAITS))
    monitor_grid, monitor_prefix = _monitor_grid(first, last)

    rows: list[dict] = []
    coverage: list[dict] = []
    for minutes in MINUTES:
        span = pd.Timedelta(minutes=minutes)
        for known in sorted(k for m, k in member_groups if m == minutes):
            group = member_groups[(minutes, known)]
            initial_names = set(group.symbol)
            initial_n = len(initial_names)
            initial_touch_max = group.touch.max()
            last_touch_age = (known - (initial_touch_max + STEP)).total_seconds() / 60
            initial_observed = _monitor_count(monitor_grid, monitor_prefix, known - span, known)
            initial_bars = minutes // 5
            initial_fraction = initial_observed / initial_bars
            member_age = group.drop_duplicates("symbol", keep="first").sample_touch_history_age_days
            half_age = member_age.to_numpy(float)
            history_median = float(np.nanmedian(half_age)) if np.isfinite(half_age).any() else np.nan
            low_shock_age, any_shock_age, low_shock_status, any_shock_status = ages[known]
            trailing_universe = unique_count[known]
            market_share = (100 * initial_n / trailing_universe) if trailing_universe else np.nan
            band = fixed.previous.LABELS[
                int(np.searchsorted(np.asarray(fixed.previous.CUTS[1:], dtype=float), initial_n, side="left"))
            ]

            # Read the full 120m continuation slice once per group. Keeping the
            # first post-decision touch per new symbol lets each X use two
            # searchsorted calls instead of repeatedly filtering DataFrames.
            follow_start = int(touch_times.searchsorted(known, side="left"))
            follow_end = int(touch_times.searchsorted(known + pd.Timedelta(minutes=max(WAITS)), side="left"))
            follow = lower.iloc[follow_start:follow_end]
            first_new_touches = follow[~follow.symbol.isin(initial_names)].drop_duplicates("symbol", keep="first")
            new_times = pd.DatetimeIndex(first_new_touches.touch)

            for wait_min in WAITS:
                decision = known + pd.Timedelta(minutes=wait_min)
                if decision > fixed.LEDGER_END:
                    continue
                new_n = int(new_times.searchsorted(decision, side="left"))
                midpoint = known + pd.Timedelta(minutes=wait_min / 2)
                first_new = int(new_times[:new_n].searchsorted(midpoint, side="left")) if wait_min else 0
                last_new = new_n - first_new
                wait_bars = wait_min // 5
                observed_wait = _monitor_count(monitor_grid, monitor_prefix, known, decision)
                combined_bars = initial_bars + wait_bars
                observed_combined = initial_observed + observed_wait
                rows.append({
                    "minutes": minutes,
                    "known": known,
                    "wait": wait_min,
                    "wait_min": wait_min,
                    "decision": decision,
                    "N": initial_n,
                    "initial": initial_n,
                    "band": band,
                    "new_coins": new_n,
                    "first_half_new": first_new,
                    "last_half_new": last_new,
                    "last_touch_age_min": float(last_touch_age),
                    "median_member_touch_history_age_days": history_median,
                    "trailing28_universe": int(trailing_universe),
                    "share28_pct": float(market_share),
                    "market_share": float(market_share),
                    "market_share_status": MARKET_SHARE_STATUS,
                    "initial_monitor_fraction": float(initial_fraction),
                    "wait_monitor_fraction": float(observed_wait / wait_bars) if wait_bars else np.nan,
                    "monitor_fraction": float(observed_combined / combined_bars),
                    "monitor_bars": int(observed_wait),
                    "since_prev_25_lower_shock_min": low_shock_age,
                    "since_prev_25_any_side_shock_min": any_shock_age,
                    "lower_shock_history_status": low_shock_status,
                    "any_side_shock_history_status": any_shock_status,
                })
            included = sum(known + pd.Timedelta(minutes=w) <= fixed.LEDGER_END for w in WAITS)
            coverage.append({
                "minutes": minutes,
                "known": known,
                "initial": initial_n,
                "eligible_waits": included,
                "dropped_waits_incomplete_followup": len(WAITS) - included,
            })

    signals = pd.DataFrame(rows).sort_values(["minutes", "known", "wait_min"]).reset_index(drop=True)
    if signals.duplicated(["minutes", "known", "wait_min"]).any():
        raise ValueError("Duplicate signal feature key")
    return signals, pd.DataFrame(coverage)


def _check_frozen(signals: pd.DataFrame) -> pd.DataFrame:
    """Assert every overlapping W / X=30m group matches the frozen study exactly."""
    frozen = pd.read_csv(
        fixed.OUT / "events.csv.gz",
        usecols=["asset", "minutes", "known", "initial", "new_coins", "band",
                 "last_touch_age_min", "monitor_bars", "continued"],
        parse_dates=["known"], dtype={"band": str},
    )
    frozen = frozen[frozen.asset.eq("BTC")].drop(columns="asset")
    current = signals[signals.wait_min.eq(30)].copy()
    keys = ["minutes", "known"]
    merged = frozen.merge(current, on=keys, how="outer", suffixes=("_frozen", "_new"),
                          indicator=True, validate="one_to_one")
    if not merged._merge.eq("both").all():
        raise AssertionError("X=30 coverage keys do not exactly match frozen fixed-timeframe output")
    exact_cols = ["initial", "new_coins", "band", "monitor_bars"]
    for col in exact_cols:
        if not merged[f"{col}_frozen"].astype(str).equals(merged[f"{col}_new"].astype(str)):
            raise AssertionError(f"X=30 frozen feature mismatch: {col}")
    if not np.allclose(merged.last_touch_age_min_frozen, merged.last_touch_age_min_new, equal_nan=True):
        raise AssertionError("X=30 frozen last-touch age mismatch")
    if not merged.continued_frozen.astype(bool).equals(merged.new_coins_new.gt(0)):
        raise AssertionError("X=30 frozen continuation flag mismatch")
    return pd.DataFrame([{
        "minutes": int(minutes),
        "overlap_groups": int(len(group)),
        "all_fields_match": True,
        "wait_min": 30,
    } for minutes, group in merged.groupby("minutes")])


def main() -> None:
    ledger, source_coverage = _opportunity_ledger()
    if ledger.empty or ledger.symbol.nunique() != 626:
        raise ValueError("Canonical frozen-plus-extension ledger is incomplete")
    print(f"Loaded {len(ledger)} frozen-plus-extension touches across {ledger.symbol.nunique()} symbols", flush=True)
    members, member_groups = _build_members(ledger)
    FEATURES.mkdir(parents=True, exist_ok=True)
    members_tmp = FEATURES / "members.csv.gz.tmp"
    members.to_csv(members_tmp, index=False, compression="gzip")
    members_tmp.replace(FEATURES / "members.csv.gz")
    source_coverage.to_csv(FEATURES / "original_weekly_opportunity_coverage.csv", index=False)
    print(f"Saved {len(members)} member bridge rows; building signal features", flush=True)
    signals, group_coverage = _build_signals(ledger, member_groups)
    frozen_check = _check_frozen(signals)

    signals_tmp = FEATURES / "signals.csv.gz.tmp"
    signals.to_csv(signals_tmp, index=False, compression="gzip")
    signals_tmp.replace(FEATURES / "signals.csv.gz")
    group_coverage.to_csv(FEATURES / "coverage_by_group.csv", index=False)
    frozen_check.to_csv(FEATURES / "frozen_x30_check.csv", index=False)
    manifest = {
        "experiment": OUT.name,
        "source_ledger": "monday_breadth_alarm_census.ledger_rows()",
        "original_level_source": str(SRC),
        "extension_level_source": str(OCTOBER),
        "minutes": list(MINUTES),
        "wait_min": list(WAITS),
        "ledger_end_exclusive": fixed.LEDGER_END.isoformat(),
        "signal_key": ["minutes", "known", "wait_min"],
        "member_key": ["minutes", "known", "symbol"],
        "known_definition": "UTC close of the W-minute candle; touch bars use native 5m opens.",
        "wait_definition": "Completed lower-touch bars with opens in [known, decision); decision=known+wait_min.",
        "membership_rule": "Unique initial lower-touch symbols in [known-W, known); earliest touch supplies level when a W group straddles a weekly seam.",
        "monitor_fraction_definition": "Share of scheduled 5m opens in [known-W, decision) outside New York Monday; initial and wait components are also emitted.",
        "first_last_half_definition": "Distinct new symbols by earliest wait touch, split at known+wait_min/2; first half is left-closed/right-open.",
        "market_share_status": MARKET_SHARE_STATUS,
        "market_share_definition": "initial / number of distinct symbols with any-direction ledger touch known in the trailing 28 days through known; percentage, a causal activity-universe proxy, not a complete live listing census.",
        "history_age_definition": "Days since the symbol's first observed first-touch event in the available frozen-plus-extension ledger; not a listing-age claim.",
        "shock_definition": "Age at known of the latest native 5m same-side (or any-side) first-touch bar with at least 25 distinct symbols, strictly before known; missing age has explicit no-prior-in-available-ledger status and is left-censored.",
        "weekly_opportunity_coverage_caveat": "Coverage file is a source diagnostic only; full-week admitted/no-touch status is not used as a causal market-share denominator.",
        "rows": {"signals": int(len(signals)), "members": int(len(members)), "ledger_touches": int(len(ledger))},
        "frozen_x30_check": frozen_check.to_dict(orient="records"),
    }
    (FEATURES / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False, default=str) + "\n")
    print(json.dumps({
        "signals": len(signals), "members": len(members), "ledger_touches": len(ledger),
        "frozen_x30": frozen_check.to_dict(orient="records"),
        "features_dir": str(FEATURES),
    }, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
