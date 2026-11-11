"""ETC first-touch versus 12H-rearmed monthly first-week reversal accounts.

Signals are confirmed only on complete UTC 12H candles. 1H candles execute
entries/exits through the accepted monthly VectorBT path adapter; account cash
and marks use the accepted VectorBT shared-wallet replay. This is a single-coin
historical comparison, not independent strategy validation.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation.month_first_week_alt_entries import prices, universe
from yoyo.evaluation.month_first_week_alt_trades import run_month_first_week_alt_trades
from yoyo.evaluation.month_first_week_touch_review import bounds


EXP = Path(__file__).resolve().parent
ENTRY_SOURCE = ROOT / "experiments/active/exp-alt-month-first-week-entries-20261110-v1"
HOUR = pd.Timedelta(hours=1)
TWELVE_HOURS = pd.Timedelta(hours=12)
TAKER_FEE = 0.0005
MAKER_FEE = 0.0002
MARKET_SLIPPAGE_BP = (1, 5)
START_USDT = 1000.0
NAV_FRACTION = 0.10
STOP_PCT = 0.05
TIMEOUT_HOURS = 168
SPLIT = pd.Timestamp("2025-01-01T00:00:00Z")
ARMS = ("first_touch", "rearmed_retests")


def ms(value) -> int:
    return pd.Timestamp(value).value // 1_000_000


def source_bars() -> tuple[pd.DataFrame, object]:
    manifest = universe().set_index("symbol", drop=False)
    if "ETC" not in manifest.index:
        raise ValueError("ETC is absent from the frozen OKX hourly universe")
    spec = manifest.loc["ETC"]
    expected = ROOT / "experiments/active/exp-cloud-allmarket-screen-20261108-v1/inputs/ETC/bars1h.csv.gz"
    if Path(spec.file).resolve() != expected.resolve():
        raise ValueError(f"ETC source path changed: {spec.file}")
    bars = prices(spec)
    if not bars.index.is_monotonic_increasing or not bars.index.is_unique:
        raise ValueError("ETC hourly index must be ordered and unique")
    if not bars.index.to_series().diff().dropna().eq(HOUR).all():
        raise ValueError("ETC hourly source has missing bars in the studied interval")
    return bars, spec


def anchors_and_touch_audit(bars: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    frozen_months = pd.read_csv(ENTRY_SOURCE / "months.csv.gz")
    frozen_events = pd.read_csv(ENTRY_SOURCE / "events.csv.gz", parse_dates=["touch"])
    frozen_months = frozen_months[frozen_months.symbol.eq("ETC")].copy()
    frozen_events = frozen_events[frozen_events.symbol.eq("ETC")].copy()
    if len(frozen_months) != 57 or not frozen_months.complete.astype(bool).all():
        raise ValueError("Expected all 57 complete ETC month coverage rows")
    if frozen_events.event_id.duplicated().any():
        raise ValueError("Duplicate frozen ETC event id")

    anchors = []
    recomputed = []
    for month_row in frozen_months.itertuples(index=False):
        month = month_row.month
        start, ready, end = bounds(month)
        expected_week = pd.date_range(start, ready, freq="h", inclusive="left")
        week = bars.reindex(expected_week)
        if week.isna().any().any() or len(week) != len(expected_week):
            raise ValueError(f"Incomplete NY first-week anchor bars: {month}")
        high = float(week.high.max())
        low = float(week.low.min())
        width = high - low
        if not np.isfinite(width) or width <= 0:
            raise ValueError(f"Invalid first-week range: {month}")
        levels = {
            "upper": high + 0.618 * width,
            "lower": low - 0.618 * width,
        }
        anchors.append(dict(month=month, range_start_utc=start, range_ready_utc=ready,
                            month_end_utc=end, week_high=high, week_low=low,
                            upper=levels["upper"], lower=levels["lower"],
                            frozen_complete=bool(month_row.complete),
                            frozen_has_touch=bool(month_row.has_touch)))

        month_hours = bars.loc[(bars.index >= ready) & (bars.index < end)]
        month_sides = []
        for side, level in levels.items():
            touched = (month_hours.high >= level) if side == "upper" else (month_hours.low <= level)
            hit = month_hours.index[touched]
            first = hit[0] if len(hit) else pd.NaT
            month_sides.append((side, first, level))
            recomputed.append(dict(month=month, side=side, first_h1_touch_utc=first,
                                   level=level, week_high=high, week_low=low))
        frozen_month = frozen_events[frozen_events.month.eq(month)]
        expected_sides = set(frozen_month.side)
        actual_sides = {side for side, first, _ in month_sides if not pd.isna(first)}
        if expected_sides != actual_sides:
            raise ValueError(f"Frozen H1 touches differ for {month}: {expected_sides} != {actual_sides}")
        if bool(month_row.has_touch) != bool(actual_sides):
            raise ValueError(f"Frozen month touch flag differs for {month}")
        for row in frozen_month.itertuples(index=False):
            side_first = dict((side, first) for side, first, _ in month_sides)[row.side]
            if side_first != row.touch:
                raise ValueError(f"Frozen first H1 touch time differs: {row.event_id}")
            anchor = next(item for item in anchors if item["month"] == month)
            for name, frozen_value in (("week_high", row.high), ("week_low", row.low),
                                       ("level", row.level)):
                if not np.isclose(anchor[name] if name != "level" else anchor[row.side],
                                  float(frozen_value), rtol=0, atol=1e-10):
                    raise ValueError(f"Frozen ETC anchor differs: {row.event_id} {name}")
    if len(frozen_events) != 34:
        raise ValueError(f"Expected 34 frozen ETC side-month touches, got {len(frozen_events)}")
    return pd.DataFrame(anchors), pd.DataFrame(recomputed)


def twelve_hour_bars(bars: pd.DataFrame) -> dict[pd.Timestamp, dict]:
    """Return complete UTC 12H candles only; missing constituents are omitted."""
    out = {}
    groups = bars.groupby(bars.index.floor("12h"), sort=True)
    for start, group in groups:
        expected = pd.date_range(start, start + TWELVE_HOURS, freq="h", inclusive="left")
        if not group.index.equals(expected):
            continue
        out[start] = dict(start=start, close_time=start + TWELVE_HOURS,
                          open=float(group.open.iloc[0]), high=float(group.high.max()),
                          low=float(group.low.min()), close=float(group.close.iloc[-1]))
    return out


def first_h1_touch(bars: pd.DataFrame, h12_start: pd.Timestamp, ready: pd.Timestamp,
                   month_end: pd.Timestamp, side: str, level: float):
    lo = max(h12_start, ready)
    hi = min(h12_start + TWELVE_HOURS, month_end)
    part = bars.loc[(bars.index >= lo) & (bars.index < hi)]
    condition = part.high >= level if side == "upper" else part.low <= level
    matches = part.index[condition]
    return matches[0] if len(matches) else pd.NaT


def target_hit_during_wait(bars: pd.DataFrame, touch_hour: pd.Timestamp,
                           entry_time: pd.Timestamp, side: str, target: float) -> bool:
    part = bars.loc[(bars.index >= touch_hour) & (bars.index < entry_time)]
    if side == "upper":
        return bool((part.low <= target).any())
    return bool((part.high >= target).any())


def build_entries(bars: pd.DataFrame, anchors: pd.DataFrame,
                  frozen_events: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    h12 = twelve_hour_bars(bars)
    entries: list[dict] = []
    candidates: list[dict] = []
    frozen_first = {(r.month, r.side): r.touch for r in frozen_events.itertuples(index=False)}
    for anchor in anchors.itertuples(index=False):
        month = anchor.month
        ready = pd.Timestamp(anchor.range_ready_utc)
        month_end = pd.Timestamp(anchor.month_end_utc)
        for side in ("upper", "lower"):
            rail = float(getattr(anchor, side))
            target = float(anchor.week_high if side == "upper" else anchor.week_low)
            signal_bars = [h12[k] for k in sorted(h12)
                           if k < month_end and h12[k]["close_time"] > ready]
            touch_events = []
            for candle in signal_bars:
                touch = (candle["high"] >= rail) if side == "upper" else (candle["low"] <= rail)
                if not touch:
                    continue
                hour = first_h1_touch(bars, candle["start"], ready, month_end, side, rail)
                if pd.isna(hour):
                    continue
                touch_events.append((candle, hour))

            frozen = frozen_first.get((month, side), pd.NaT)
            if (not pd.isna(frozen)) != bool(touch_events):
                raise ValueError(f"First-touch coverage differs for {month}/{side}")
            if touch_events and touch_events[0][1] != frozen:
                raise ValueError(f"First 12H touch does not contain frozen first H1 touch: {month}/{side}")

            # The first-touch comparison admits just the first monthly touch.
            # Retests re-arm only after a later complete 12H candle is strictly
            # inside the relevant rail; one attempt can confirm on its touch
            # candle or either of the next two complete 12H closes.
            for arm in ARMS:
                if not touch_events:
                    entries.append(dict(arm=arm, month=month, side=side, touch_sequence=0,
                                        event_id="", touch_12h_start_utc=pd.NaT,
                                        first_touch_hour_utc=pd.NaT, reclaim_12h_start_utc=pd.NaT,
                                        confirm_close_utc=pd.NaT, entry_time=pd.NaT,
                                        wait_12h_bars=np.nan, rail=rail, target=target,
                                        week_high=anchor.week_high, week_low=anchor.week_low,
                                        matches_frozen_first_touch=False,
                                        status="no_touch"))
                    continue

                if arm == "first_touch":
                    selected_touch_events = touch_events[:1]
                else:
                    selected_touch_events = []
                    armed = True
                    pending_start = None
                    touch_by_start = {candle["start"]: (candle, hour)
                                      for candle, hour in touch_events}
                    for candle in signal_bars:
                        current = candle["start"]
                        if pending_start is not None and current > pending_start + 2 * TWELVE_HOURS:
                            pending_start = None
                        full_inside = (candle["high"] < rail if side == "upper"
                                       else candle["low"] > rail)
                        # A complete 12H candle back inside re-arms only after
                        # the previous episode. Its close also resolves a still
                        # pending reclaim, which is evaluated below.
                        if pending_start is not None and full_inside:
                            pending_start = None
                            armed = True
                        elif not armed and pending_start is None and full_inside:
                            armed = True
                        if armed and current in touch_by_start:
                            selected_touch_events.append(touch_by_start[current])
                            pending_start = current
                            armed = False

                if not selected_touch_events:
                    entries.append(dict(arm=arm, month=month, side=side, touch_sequence=0,
                                        event_id="", touch_12h_start_utc=pd.NaT,
                                        first_touch_hour_utc=pd.NaT, reclaim_12h_start_utc=pd.NaT,
                                        confirm_close_utc=pd.NaT, entry_time=pd.NaT,
                                        wait_12h_bars=np.nan, rail=rail, target=target,
                                        week_high=anchor.week_high, week_low=anchor.week_low,
                                        matches_frozen_first_touch=False,
                                        status="no_touch"))
                    continue

                for sequence, (touch_candle, touch_hour) in enumerate(selected_touch_events, 1):
                    event_id = f"ETC|{month}|{side}|{arm}|{sequence:03d}|{ms(touch_hour)}"
                    touch_start = touch_candle["start"]
                    touch_pos = signal_bars.index(touch_candle)
                    found = None
                    for wait_idx in range(0, 3):
                        pos = touch_pos + wait_idx
                        if pos >= len(signal_bars):
                            break
                        confirm = signal_bars[pos]
                        reclaimed = confirm["close"] < rail if side == "upper" else confirm["close"] > rail
                        if reclaimed:
                            found = (confirm, wait_idx + 1)
                            break
                    base = dict(arm=arm, month=month, side=side, touch_sequence=sequence,
                                event_id=event_id, touch_12h_start_utc=touch_start,
                                first_touch_hour_utc=touch_hour, reclaim_12h_start_utc=pd.NaT,
                                confirm_close_utc=pd.NaT, entry_time=pd.NaT,
                                wait_12h_bars=np.nan, rail=rail, target=target,
                                week_high=anchor.week_high, week_low=anchor.week_low,
                                matches_frozen_first_touch=bool(touch_hour == frozen),
                                status="no_strict_reclaim_within_3_closes")
                    if found is None:
                        entries.append(base)
                        continue
                    confirm, wait_count = found
                    entry_time = confirm["close_time"]
                    base.update(reclaim_12h_start_utc=confirm["start"],
                                confirm_close_utc=entry_time, entry_time=entry_time,
                                wait_12h_bars=wait_count)
                    if entry_time >= month_end:
                        base.update(status="month_expired_before_next_open")
                        entries.append(base)
                        continue
                    if entry_time not in bars.index:
                        base.update(status="no_next_hour_open")
                        entries.append(base)
                        continue
                    if target_hit_during_wait(bars, touch_hour, entry_time, side, target):
                        base.update(status="near_side_reached_while_waiting")
                        entries.append(base)
                        continue
                    entry_open = float(bars.at[entry_time, "open"])
                    already_at_target = (entry_open <= target if side == "upper"
                                         else entry_open >= target)
                    if already_at_target:
                        base.update(status="near_side_at_entry_open")
                        entries.append(base)
                        continue
                    base.update(status="path_candidate")
                    entries.append(base)
                    candidates.append(dict(trade_id=event_id, event_id=event_id,
                                           symbol="ETC", arm=arm, month=month,
                                           side=side, touch=touch_hour,
                                           touch_12h_start_utc=touch_start,
                                           entry_time=entry_time, first_touch_hour_utc=touch_hour,
                                           rail=rail, low=float(anchor.week_low),
                                           high=float(anchor.week_high), target=target,
                                           week_high=float(anchor.week_high),
                                           week_low=float(anchor.week_low),
                                           wait_12h_bars=wait_count,
                                           confirm_close_utc=entry_time))

    return pd.DataFrame(entries), pd.DataFrame(candidates)


def execute_paths(bars: pd.DataFrame, candidates: pd.DataFrame) -> pd.DataFrame:
    if candidates.empty:
        return candidates.copy()
    output = []
    # Existing adapter supports both directions, one independent VectorBT
    # column per event, with same-hour stop priority and seven-day expiry.
    for arm in ARMS:
        sample = candidates[candidates.arm.eq(arm)].copy()
        if sample.empty:
            continue
        path = run_month_first_week_alt_trades(
            {"ETC": bars}, sample, taker_fee=TAKER_FEE, maker_fee=MAKER_FEE,
            timeout_hours=TIMEOUT_HOURS, stop_pct=STOP_PCT)
        path["arm"] = arm
        output.append(path)
    return pd.concat(output, ignore_index=True) if output else candidates.copy()


def market_events(path: pd.DataFrame) -> pd.DataFrame:
    closed = path[path.status.eq("closed")].copy()
    if closed.empty:
        return pd.DataFrame(columns=["event_id", "symbol", "entry_ms", "exit_bar_ms",
                                     "side", "entry_price", "exit_price", "exit_fee_rate",
                                     "exit_phase"])
    closed["entry_ms"] = closed.entry_time.map(ms).astype("int64")
    closed["exit_bar_ms"] = closed.exit_time.map(ms).astype("int64")
    closed["side"] = np.where(closed.side.eq("lower"), 1, -1)
    closed["entry_price"] = closed.entry_fill.astype(float)
    closed["exit_price"] = closed.exit_price.astype(float)
    closed["exit_fee_rate"] = np.where(closed.exit_reason.eq("target"), MAKER_FEE, TAKER_FEE)
    closed["exit_phase"] = np.where(closed.exit_time_resolution.eq("hour_open"), "open", "intrabar")
    return closed[["event_id", "symbol", "entry_ms", "exit_bar_ms", "side", "entry_price",
                   "exit_price", "exit_fee_rate", "exit_phase", "arm"]]


def funded_account(sample: pd.DataFrame, bars: pd.DataFrame, slip_bp: int):
    start = ms(bars.index[0])
    end = ms(bars.index[-1] + HOUR)
    clock = bars.index.as_unit("ms").asi8
    prices = bars[["open", "high", "low", "close"]].to_numpy(dtype=float)

    def quote_loader(symbol):
        if symbol != "ETC":
            raise ValueError(symbol)
        return clock, prices

    if sample.empty:
        empty = pd.DataFrame(columns=["event_id", "symbol", "entry_ms", "exit_bar_ms",
                                      "side", "entry_price", "exit_price", "exit_fee_rate",
                                      "exit_phase"])
        index = pd.date_range(bars.index[0] + HOUR, bars.index[-1] + HOUR, freq="h")
        equity = pd.DataFrame(dict(nav=START_USDT, open_nav=START_USDT,
                                   open_exit_nav=START_USDT, lower_nav=START_USDT,
                                   upper_nav=START_USDT), index=index)
        return empty, equity, set(), {"same_contract": 0, "all_slots_busy": 0}
    data = wallet.prepare(sample, slots=1, start=start, end=end,
                          quote_loader=quote_loader)
    if data["gap_marks"]:
        raise ValueError(f"Active ETC position has missing H1 account marks: {data['gap_marks']}")
    slip = slip_bp / 10_000.0
    funded, equity = wallet.account(data, slip=slip, exposure=NAV_FRACTION)

    # The accepted wallet replay marks intrabar extrema conservatively, then
    # reserves market exit costs in the lower drawdown envelope.
    intrabar = funded[funded.exit_phase.eq("intrabar")]
    market = intrabar[intrabar.exit_fee_rate.eq(TAKER_FEE)]
    close_cost = market.quantity * market.exit_price * (
        slip + TAKER_FEE * (1.0 + slip))
    hour_ends = pd.to_datetime(market.exit_bar_ms + wallet.HOUR, unit="ms", utc=True)
    reserve = close_cost.groupby(hour_ends).sum().reindex(equity.index, fill_value=0.0)
    equity["lower_nav"] -= reserve
    if not (equity.lower_nav <= equity.nav + 1e-8).all():
        raise ValueError("Conservative intrabar NAV bound exceeds realized NAV")
    selected_ids = set(data["selected"].event_id)
    return funded, equity, selected_ids, data["skips"]


def summary_rows(arm: str, slip_bp: int, funded: pd.DataFrame,
                 equity: pd.DataFrame, path: pd.DataFrame,
                 all_candidate_count: int, skip_counts: dict) -> list[dict]:
    periods = [("all", None, None), ("pre_2025", None, SPLIT), ("2025_plus", SPLIT, None)]
    rows = []
    trades = funded.copy()
    outcomes = path.set_index("event_id", drop=False)
    for period, lo, hi in periods:
        metrics = wallet.summarize(trades, equity, lo, hi)
        lms = ms(equity.index[0] - pd.Timedelta(hours=1) if lo is None else lo)
        hms = ms(equity.index[-1] + pd.Timedelta(hours=1) if hi is None else hi)
        completed = trades[(trades.entry_ms >= lms) & (trades.exit_bar_ms < hms)].copy()
        pnl = completed.cash_pnl
        path_rows = outcomes.reindex(completed.event_id).dropna(subset=["mfe_pct", "mae_pct"])
        wins = int((pnl > 1e-8).sum())
        losses = int((pnl < -1e-8).sum())
        flats = int((pnl.abs() <= 1e-8).sum())
        metrics.update(dict(arm=arm, slippage_bp=slip_bp, period=period,
                            initial_usdt=metrics["final_usdt"] / metrics["multiple"],
                            return_pct=100 * (metrics["multiple"] - 1),
                            candidate_intents=int(all_candidate_count), funded_trades=len(completed),
                            wins=wins, losses=losses, flats=flats,
                            mfe_mean_pct=float(path_rows.mfe_pct.mean()) if len(path_rows) else None,
                            mfe_median_pct=float(path_rows.mfe_pct.median()) if len(path_rows) else None,
                            mae_mean_pct=float(path_rows.mae_pct.mean()) if len(path_rows) else None,
                            mae_median_pct=float(path_rows.mae_pct.median()) if len(path_rows) else None,
                            skipped_same_contract=int(skip_counts.get("same_contract", 0)),
                            skipped_all_slots_busy=int(skip_counts.get("all_slots_busy", 0)),
                            taker_fee_per_market_fill=TAKER_FEE,
                            maker_fee_per_target_fill=MAKER_FEE,
                            market_slippage_per_fill=slip_bp / 10_000,
                            funding_included=False))
        rows.append(metrics)
    return rows


def main() -> None:
    EXP.mkdir(parents=True, exist_ok=True)
    bars, spec = source_bars()
    anchors, recomputed_touches = anchors_and_touch_audit(bars)
    frozen_events = pd.read_csv(ENTRY_SOURCE / "events.csv.gz", parse_dates=["touch"])
    frozen_events = frozen_events[frozen_events.symbol.eq("ETC")].copy()
    entries, candidates = build_entries(bars, anchors, frozen_events)
    paths = execute_paths(bars, candidates)
    if len(paths) and paths.trade_id.duplicated().any():
        raise ValueError("Duplicate VectorBT path trade id")

    account_rows: list[dict] = []
    account_fills: dict[tuple[str, int], pd.DataFrame] = {}
    account_selected: dict[tuple[str, int], set[str]] = {}
    account_skips: dict[tuple[str, int], dict] = {}
    candidate_counts = candidates.groupby("arm").size().to_dict() if len(candidates) else {}
    for arm in ARMS:
        arm_path = paths[paths.arm.eq(arm)].copy() if len(paths) else paths
        events = market_events(arm_path)
        for slip_bp in MARKET_SLIPPAGE_BP:
            funded, equity, selected, skips = funded_account(events, bars, slip_bp)
            account_fills[(arm, slip_bp)] = funded
            account_selected[(arm, slip_bp)] = selected
            account_skips[(arm, slip_bp)] = skips
            equity.to_csv(EXP / f"equity_{arm}_{slip_bp}bp.csv.gz", index_label="hour_end_utc")
            account_rows.extend(summary_rows(arm, slip_bp, funded, equity, arm_path,
                                             int(candidate_counts.get(arm, 0)), skips))

    path_rows = paths.copy()
    if len(path_rows):
        for arm in ARMS:
            mask = path_rows.arm.eq(arm)
            ids_by_slip = {bp: set(account_fills[(arm, bp)].event_id)
                           for bp in MARKET_SLIPPAGE_BP}
            for bp in MARKET_SLIPPAGE_BP:
                path_rows.loc[mask, f"account_funded_{bp}bp"] = (
                    path_rows.loc[mask, "event_id"].isin(ids_by_slip[bp]))
                fill_map = account_fills[(arm, bp)].set_index("event_id")
                for col in ("cash_pnl", "net_return", "quantity", "notional"):
                    target_col = f"{col}_{bp}bp"
                    path_rows.loc[mask, target_col] = path_rows.loc[mask, "event_id"].map(fill_map[col])
                path_rows.loc[mask, f"account_reference_entry_price_{bp}bp"] = (
                    path_rows.loc[mask, "event_id"].map(fill_map.entry_price))
                path_rows.loc[mask, f"account_reference_exit_price_{bp}bp"] = (
                    path_rows.loc[mask, "event_id"].map(fill_map.exit_price))
    if len(paths):
        admission = {}
        for arm in ARMS:
            for bp in MARKET_SLIPPAGE_BP:
                admission[(arm, bp)] = account_selected[(arm, bp)]
        for arm in ARMS:
            mask = path_rows.arm.eq(arm)
            path_rows.loc[mask, "account_admitted_1bp"] = path_rows.loc[mask, "event_id"].isin(
                admission[(arm, 1)])
            path_rows.loc[mask, "account_admitted_5bp"] = path_rows.loc[mask, "event_id"].isin(
                admission[(arm, 5)])
            closed = mask & path_rows.status.eq("closed")
            path_rows.loc[closed & path_rows.account_admitted_1bp.fillna(False), "status"] = "account_funded"
            path_rows.loc[closed & path_rows.account_admitted_1bp.fillna(False).eq(False),
                          "status"] = "account_skipped_busy"
    paths_by_id = path_rows.set_index("event_id") if len(path_rows) else pd.DataFrame()
    if len(entries):
        entries["path_status"] = entries.event_id.map(paths_by_id.status) if len(paths_by_id) else None
        entries["entry_fill"] = entries.event_id.map(paths_by_id.entry_fill) if len(paths_by_id) else np.nan
        entries["exit_time"] = entries.event_id.map(paths_by_id.exit_time) if len(paths_by_id) else pd.NaT
        entries["exit_reason"] = entries.event_id.map(paths_by_id.exit_reason) if len(paths_by_id) else None
        entries["account_admitted_1bp"] = False
        entries["account_admitted_5bp"] = False
        if len(paths_by_id):
            for arm in ARMS:
                mask = entries.arm.eq(arm)
                entries.loc[mask, "account_admitted_1bp"] = entries.loc[mask, "event_id"].isin(
                    account_selected[(arm, 1)])
                entries.loc[mask, "account_admitted_5bp"] = entries.loc[mask, "event_id"].isin(
                    account_selected[(arm, 5)])
            entries.loc[entries.status.eq("path_candidate") & entries.path_status.eq("account_skipped_busy"),
                        "status"] = "account_busy_skipped"
            entries.loc[entries.status.eq("path_candidate") & entries.path_status.eq("account_funded"),
                        "status"] = "account_funded"

    entries.to_csv(EXP / "entries.csv", index=False)
    path_rows.to_csv(EXP / "trades.csv", index=False)
    pd.DataFrame(account_rows).to_csv(EXP / "accounts.csv", index=False)
    anchors.to_csv(EXP / "anchors.csv", index=False)
    recomputed_touches.to_csv(EXP / "recomputed_first_touches.csv", index=False)
    receipt = dict(experiment_id=EXP.name, source=str(spec.file.relative_to(ROOT)),
                   cutoff_utc=str(spec.cutoff), source_first_hour_utc=str(bars.index[0]),
                   source_last_open_utc=str(bars.index[-1]), source_hour_bars=len(bars),
                   months=len(anchors), frozen_h1_first_touches=34,
                   event_rows=len(entries), path_candidates=len(candidates),
                   vectorbt_version=__import__("vectorbt").__version__,
                   costs=dict(taker_fee=TAKER_FEE, maker_fee=MAKER_FEE,
                              market_slippage_bp=list(MARKET_SLIPPAGE_BP), funding="excluded"),
                   account=dict(initial_usdt=START_USDT, per_trade_nav_fraction=NAV_FRACTION,
                                slots=1, stop_pct=STOP_PCT, timeout_hours=TIMEOUT_HOURS),
                   execution="accepted month_first_week_alt_trades VectorBT path adapter; accepted cloud_close_reward_account VectorBT wallet replay",
                   completion="Only complete hourly and UTC 12H candles. H1 bars with both stop and target in one candle use stop-first VectorBT semantics.",
                   same_open_priority="The accepted wallet's deterministic sort selects the lexical event_id after CRC32(symbol) priority; a second same-symbol event at that open is skipped as same_contract.",
                   validation="ETC monthly anchors recomputed from the complete NY first week and exactly reconciled to all 34 frozen H1 first-touch event rows.",
                   training_eligible=False, production_eligible=False)
    (EXP / "run_receipt.json").write_text(json.dumps(receipt, indent=2, allow_nan=False) + "\n")
    print("anchor months", len(anchors), "frozen first touches", 34,
          "entry rows", len(entries), "path candidates", len(paths), flush=True)
    print(pd.DataFrame(account_rows)[[
        "arm", "slippage_bp", "period", "funded_trades", "wins", "losses",
        "initial_usdt", "final_usdt", "dd_mark_pct", "dd_bound_pct",
        "mfe_mean_pct", "mae_mean_pct"]].to_string(index=False), flush=True)


if __name__ == "__main__":
    main()
