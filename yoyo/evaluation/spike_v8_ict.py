"""Frozen ICT entry-clock helpers for the six-timeframe original V8 study.

This module deliberately has no runner, source reader, cash account, queue, or
forced-close rule.  It filters already-replayed full opportunity receipts
*before* serial admission.  An opportunity therefore retains its original
exit, including a closed exit outside an ICT session or a censored boundary
receipt.  Session classification uses the actual next observed bar open, not a
nominal signal-close timestamp.

The matching covariate is ``ATR / close`` at a closed signal bar.  Its tertile
is the signal value compared with the 1/3 and 2/3 quantiles of the preceding
120 closed signal-bar values (rows ``i-120`` through ``i-1``).  No price after
the signal bar is used to create that covariate; future bars are used only by
the supplied frozen replay callback to obtain an exit outcome.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Hashable, Iterable, Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_recovery_cash import eligible


NY_ZONE = "America/New_York"
SESSION_NAMES = frozenset(("all", "union"))
_DETAIL_COLUMNS = (
    "signal_i", "side", "entry_time", "month", "ny_hour", "weekend",
    "vol_bucket", "actual_net_r", "random_net_r", "excess_net_r", "matched",
    "attempts", "rejected_censored", "rejected_invalid", "status", "draws",
    "draw_receipts",
)


def _utc_index(times: Iterable[object], *, name: str = "times") -> pd.DatetimeIndex:
    """Return UTC-aware timestamps, rejecting invalid or missing entry clocks."""
    result = pd.DatetimeIndex(pd.to_datetime(list(times), utc=True, errors="coerce"))
    if result.isna().any():
        raise ValueError(f"{name} must contain valid UTC timestamps")
    return result


def entry_times(frame: pd.DataFrame) -> pd.DatetimeIndex:
    """Map each signal row to its actual next observed UTC bar open.

    The returned index is aligned to ``frame`` and ends in ``NaT`` because the
    final signal has no observed entry bar.  The function requires a unique,
    increasing, timezone-aware frame index; it does not infer an entry from a
    timeframe duration, so source gaps remain visible to callers.
    """
    index = pd.DatetimeIndex(frame.index)
    if index.tz is None or not index.is_unique or not index.is_monotonic_increasing:
        raise ValueError("frame index must be unique, increasing, and timezone-aware")
    index = index.tz_convert("UTC")
    if not len(index):
        return pd.DatetimeIndex([], tz="UTC", name=index.name)
    values = list(index[1:]) + [pd.NaT]
    return pd.DatetimeIndex(values, tz="UTC", name=index.name)


def session_annotation(times: Iterable[object]) -> np.ndarray:
    """Label IANA New York entry clocks as london, lunch, new_york, or outside.

    The windows are inclusive at 02:00/05:00/08:00 and exclusive at
    05:00/08:00/11:00.  Weekends and DST dates are intentionally treated like
    every other date.  Seconds and sub-minute timestamps are retained by the
    timestamp conversion, while classification is by local wall-clock hour.
    """
    local = _utc_index(times).tz_convert(NY_ZONE)
    minute = local.hour * 60 + local.minute
    labels = np.full(len(local), "outside", dtype=object)
    labels[(minute >= 120) & (minute < 300)] = "london"
    labels[(minute >= 300) & (minute < 480)] = "lunch"
    labels[(minute >= 480) & (minute < 660)] = "new_york"
    return labels


def session_mask(times: Iterable[object], session: str) -> np.ndarray:
    """Return the frozen all-day or ICT-union admission mask for entry clocks."""
    if session not in SESSION_NAMES:
        raise ValueError(f"unknown frozen ICT session: {session}")
    labels = session_annotation(times)
    return np.ones(len(labels), dtype=bool) if session == "all" else labels != "outside"


def annotate_opportunities(opportunities: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Copy full receipts and add ``ict_session`` from each actual ``entry_time``."""
    labels = session_annotation(row["entry_time"] for row in opportunities)
    return [dict(row, ict_session=str(label)) for row, label in zip(opportunities, labels)]


def filter_opportunities(
    opportunities: Sequence[Mapping[str, Any]], session: str,
) -> list[Mapping[str, Any]]:
    """Filter complete candidate receipts by actual entry clock before serial admission.

    Rows are returned unchanged, including exits after 11:00 New York, censored
    marks, and all frozen-exit fields.  There is no delayed entry or scheduled
    session close in this operation.
    """
    rows = list(opportunities)
    mask = session_mask((row["entry_time"] for row in rows), session)
    return [row for row, keep in zip(rows, mask) if keep]


def serial(
    opportunities: Sequence[Mapping[str, Any]],
    *,
    eligible_fn: Callable[[Mapping[str, Any], Mapping[str, Any] | None], bool] = eligible,
) -> list[Mapping[str, Any]]:
    """Admit only filtered candidate rows using the frozen serial rule.

    Excluded rows are absent before this function runs and therefore cannot
    create a shadow position that blocks a later candidate.
    """
    accepted: list[Mapping[str, Any]] = []
    previous: Mapping[str, Any] | None = None
    for row in sorted(opportunities, key=lambda item: (int(item["signal_i"]), int(item["side"]))):
        if eligible_fn(row, previous):
            accepted.append(row)
            previous = row
    return accepted


def filter_then_serial(
    opportunities: Sequence[Mapping[str, Any]], session: str,
    *, eligible_fn: Callable[[Mapping[str, Any], Mapping[str, Any] | None], bool] = eligible,
) -> list[Mapping[str, Any]]:
    """Convenience composition that makes the required filter-before-serial order explicit."""
    return serial(filter_opportunities(opportunities, session), eligible_fn=eligible_fn)


def _entry_bounds(start: object, end: object) -> tuple[pd.Timestamp, pd.Timestamp]:
    start_utc, end_utc = pd.Timestamp(start), pd.Timestamp(end)
    if start_utc.tzinfo is None or end_utc.tzinfo is None:
        raise ValueError("start and end must be timezone-aware UTC bounds")
    start_utc, end_utc = start_utc.tz_convert("UTC"), end_utc.tz_convert("UTC")
    if not start_utc < end_utc:
        raise ValueError("start must precede end")
    return start_utc, end_utc


def _causal_volatility_buckets(frame: pd.DataFrame) -> np.ndarray:
    """Compute causal prior-120 ATR/close tertiles for each closed signal bar.

    ``atr`` and ``close`` at row ``i`` form the compared relative volatility;
    rolling quantile boundaries use only rows ``i-120`` through ``i-1``.  A
    nonfinite input or incomplete 120-bar history receives bucket ``-1`` and
    cannot enter a matched-control pool.
    """
    if not {"atr", "close"}.issubset(frame.columns):
        raise ValueError("frame requires atr and close for matched controls")
    relative = pd.to_numeric(frame["atr"], errors="coerce") / pd.to_numeric(frame["close"], errors="coerce")
    lower = relative.shift(1).rolling(120, min_periods=120).quantile(1 / 3)
    upper = relative.shift(1).rolling(120, min_periods=120).quantile(2 / 3)
    usable = relative.notna() & np.isfinite(relative) & lower.notna() & upper.notna()
    buckets = np.full(len(frame), -1, dtype=int)
    values = relative.to_numpy(dtype=float)
    low_values, high_values = lower.to_numpy(dtype=float), upper.to_numpy(dtype=float)
    buckets[usable.to_numpy() & (values <= low_values)] = 0
    buckets[usable.to_numpy() & (values > low_values) & (values <= high_values)] = 1
    buckets[usable.to_numpy() & (values > high_values)] = 2
    return buckets


def _finite_net(row: Mapping[str, Any] | None) -> bool:
    if row is None:
        return False
    try:
        return bool(np.isfinite(float(row["net_r"])))
    except (KeyError, TypeError, ValueError):
        return False


def _empty_detail() -> pd.DataFrame:
    return pd.DataFrame({column: pd.Series(dtype=object) for column in _DETAIL_COLUMNS})


def matched_controls(
    frame: pd.DataFrame,
    accepted: Sequence[Mapping[str, Any]],
    policy: Any,
    *,
    policy_key: Hashable,
    session: str,
    start: object,
    end: object,
    seed: int,
    replay_fn: Callable[[int, Any, int], Mapping[str, Any] | None],
    controls_per_trade: int = 5,
    permutations: int = 9999,
    cache: dict[tuple[Hashable, int, int], Mapping[str, Any] | None] | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Return independent five-draw controls and descriptive monthly sign-flips.

    ``replay_fn(i, policy, side)`` must delegate to the frozen original-V8
    replay helper for a candidate signal and forced side.  Replays are
    cached by ``(policy_key, side, signal_i)`` and may be shared across both
    windows and both session arms for one timeframe.  Each target gets a fresh
    seeded permutation of its eligible pool, so draws remain independent across
    targets even when a cached replay outcome is reused.

    Pool members require their actual next observed entry in ``[start, end)``,
    the requested session, identical UTC month, New York entry hour, weekend
    flag, and causal prior-120 relative-volatility bucket.  Target receipts are
    validated against that same next-observed-entry clock.  Censored and invalid
    target receipts remain explicit detail rows but never enter natural matched
    summaries.  Incomplete draws are reported as missing rather than treated as
    five-match controls.  ``p`` is a descriptive, one-sided 9,999-draw monthly
    sign-flip result, not a trading decision rule.
    """
    if session not in SESSION_NAMES:
        raise ValueError(f"unknown frozen ICT session: {session}")
    if not isinstance(controls_per_trade, int) or controls_per_trade != 5:
        raise ValueError("this frozen study requires exactly five controls per trade")
    if not isinstance(permutations, int) or permutations != 9999:
        raise ValueError("this frozen study requires exactly 9999 sign-flips")
    start_utc, end_utc = _entry_bounds(start, end)
    entries = entry_times(frame)
    buckets = _causal_volatility_buckets(frame)
    valid_entry = ~entries.isna()
    # Build metadata without formatting the terminal NaT entry slot.
    entry_local = entries[:-1].tz_convert(NY_ZONE)
    months = np.full(len(entries), "", dtype=object)
    hours = np.full(len(entries), -1, dtype=int)
    weekends = np.zeros(len(entries), dtype=bool)
    months[:-1] = entry_local.tz_convert("UTC").strftime("%Y-%m").to_numpy(dtype=object)
    hours[:-1] = entry_local.hour.to_numpy(dtype=int)
    weekends[:-1] = np.asarray(entry_local.dayofweek >= 5, dtype=bool)
    allowed = np.zeros(len(entries), dtype=bool)
    if len(entries):
        allowed[:-1] = session_mask(entries[:-1], session)
    in_bounds = (entries >= start_utc) & (entries < end_utc)
    eligible_pool = valid_entry & in_bounds & allowed & (buckets >= 0)
    pools: dict[tuple[str, int, bool, int], np.ndarray] = {}
    replay_cache = cache if cache is not None else {}
    details: list[dict[str, Any]] = []

    for target in accepted:
        i, side = int(target["signal_i"]), int(target["side"])
        if not 0 <= i < len(frame) - 1:
            raise ValueError("accepted signal_i has no next observed entry")
        if int(target.get("entry_i", -1)) != i + 1:
            raise ValueError("opportunity entry_i must be signal_i + 1 on the full source grid")
        expected_entry = entries[i]
        actual_entry = _utc_index([target["entry_time"]], name="opportunity entry_time")[0]
        if actual_entry != expected_entry:
            raise ValueError("opportunity entry_time must equal frame.index[signal_i + 1]")
        if not bool(in_bounds[i]) or not bool(allowed[i]):
            raise ValueError("accepted opportunity entry lies outside requested window or session")
        group = (str(months[i]), int(hours[i]), bool(weekends[i]), int(buckets[i]))
        if group not in pools:
            pools[group] = np.flatnonzero(
                eligible_pool
                & (months == group[0])
                & (hours == group[1])
                & (weekends == group[2])
                & (buckets == group[3])
            )
        base = dict(
            signal_i=i, side=side, entry_time=actual_entry.isoformat(), month=group[0],
            ny_hour=group[1], weekend=group[2], vol_bucket=group[3],
            actual_net_r=target.get("net_r"), random_net_r=np.nan, excess_net_r=np.nan,
            matched=0, attempts=0, rejected_censored=0, rejected_invalid=0,
            status="", draws="[]", draw_receipts="[]",
        )
        if bool(target.get("censored", False)):
            details.append(dict(base, status="target_censored"))
            continue
        if not _finite_net(target):
            details.append(dict(base, status="target_invalid"))
            continue

        rng = np.random.default_rng(np.random.SeedSequence([int(seed), i, side + 1]))
        draws: list[dict[str, Any]] = []
        receipts: list[dict[str, Any]] = []
        rejected_censored = rejected_invalid = attempts = 0
        for j in rng.permutation(pools[group]):
            if int(j) == i:
                continue
            attempts += 1
            key = (policy_key, side, int(j))
            if key not in replay_cache:
                replay_cache[key] = replay_fn(int(j), policy, side)
            receipt = replay_cache[key]
            if not _finite_net(receipt):
                rejected_invalid += 1
                receipts.append(dict(signal_i=int(j), status="invalid"))
                continue
            if bool(receipt.get("censored", False)):
                rejected_censored += 1
                receipts.append(dict(signal_i=int(j), status="censored", net_r=float(receipt["net_r"])))
                continue
            net_r = float(receipt["net_r"])
            draw = dict(signal_i=int(j), net_r=net_r)
            draws.append(draw)
            receipts.append(dict(draw, status="selected"))
            if len(draws) == controls_per_trade:
                break
        random_mean = float(np.mean([draw["net_r"] for draw in draws])) if draws else np.nan
        status = "complete" if len(draws) == controls_per_trade else "partial"
        details.append(dict(
            base, random_net_r=random_mean,
            excess_net_r=float(target["net_r"]) - random_mean if np.isfinite(random_mean) else np.nan,
            matched=len(draws), attempts=attempts, rejected_censored=rejected_censored,
            rejected_invalid=rejected_invalid, status=status, draws=json.dumps(draws),
            draw_receipts=json.dumps(receipts),
        ))

    detail = pd.DataFrame(details, columns=_DETAIL_COLUMNS) if details else _empty_detail()
    natural = detail.loc[detail.status.isin(("complete", "partial"))]
    full = detail.loc[detail.status == "complete"]
    blocks = full.groupby("month")["excess_net_r"].sum().to_numpy(dtype=float) if len(full) else np.array([])
    if len(blocks):
        rng = np.random.default_rng(int(seed))
        null = (rng.choice((-1, 1), size=(permutations, len(blocks))) * blocks).sum(axis=1)
        p = float((1 + (null >= blocks.sum()).sum()) / (1 + permutations))
    else:
        p = None
    metrics = dict(
        matched_n=int(len(full)),
        matched_missing=int(len(natural) - len(full)),
        random_mean_net_r=float(full.random_net_r.mean()) if len(full) else None,
        excess_net_r=float(full.excess_net_r.mean()) if len(full) else None,
        p=p,
        months=int(len(blocks)),
        target_censored=int((detail.status == "target_censored").sum()),
        target_invalid=int((detail.status == "target_invalid").sum()),
        natural_target_n=int(len(natural)),
        controls_per_trade=controls_per_trade,
        permutations=permutations,
    )
    return detail, metrics
