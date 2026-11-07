"""Same-symbol matched time controls for locked Monday breadth candidates.

This matcher accepts either the search lock tables or the exit-follow-up lock
tables. It reads local registered Binance USD-M OHLC only; it does not repair
or download candles. Random entries match UTC month/6h, pre-observation 1h
realized-volatility bucket and trend sign, monitor schedule, and symbol. The
primary control excludes lower first-touches and a symmetric H + W + X shock
guard. An optional past-only sensitivity replaces the latter with shocks
already known in the prior 60 minutes and no overlap with the actual trade.
"""

from __future__ import annotations

import argparse
import json
import zlib
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import monday_breadth_alarm_census as census
from yoyo.evaluation import monday_breadth_reversal_window as base
from yoyo.evaluation.monday_breadth_opt_payoffs import load_full_ohlc


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = ROOT / "experiments/active/exp-monday-breadth-optimization-20261108-v1"
FEATURES = EXPERIMENT / "features"
CONTROL_DIR = EXPERIMENT / "controls"
SOURCE_DIRS = {
    "search": EXPERIMENT / "search",
    "exits": EXPERIMENT / "exits",
    "second_wave": EXPERIMENT / "exits" / "second_wave_requests",
}
DYNAMIC_SOURCES = {"exits", "second_wave"}
BAR = pd.Timedelta(minutes=5)
STEP_NS = BAR.value
SEED = 20261108
MAX_FIXED_DRAWS = 12
MAX_DYNAMIC_DRAWS = 3
MIN_COVERAGE = 0.80
OHLC = ("open", "high", "low", "close")
EXCLUDED_ALT = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "USTCUSDT"}


def _utc(value) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def _asset(value) -> str:
    text = str(value).strip().upper()
    if text.endswith("USDT"):
        text = text[:-4]
    if text in {"ALTS", "ALTCOINS", "ALT_BASKET"}:
        return "ALT"
    return text


def _epoch_rank(value) -> int:
    text = str(value).strip().lower()
    if "2026" in text or text in {"3", "e3", "epoch3", "epoch_3", "holdout"}:
        return 3
    if "2025" in text or text in {"2", "e2", "epoch2", "epoch_2", "validation"}:
        return 2
    if "2023" in text or "2024" in text or text in {"1", "e1", "epoch1", "epoch_1", "development"}:
        return 1
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return 0


def _is_dynamic_source(source: str) -> bool:
    return str(source) in DYNAMIC_SOURCES


def _read_locked_source(source: str) -> pd.DataFrame:
    if source not in SOURCE_DIRS:
        raise ValueError(f"Unknown locked source profile: {source}")
    source_dir = SOURCE_DIRS[source]
    events_path = source_dir / "locked_events.parquet"
    rules_path = source_dir / "locked_rules.csv"
    if not events_path.is_file() or not rules_path.is_file():
        return pd.DataFrame()
    events = pd.read_parquet(events_path)
    rules = pd.read_csv(rules_path)
    if "candidate_id" not in events or "candidate_id" not in rules:
        raise ValueError(f"{source} locked tables must include candidate_id")
    events["candidate_id"] = events.candidate_id.astype(str)
    rules["candidate_id"] = rules.candidate_id.astype(str)
    joined = events.merge(rules, on="candidate_id", how="inner", suffixes=("_event", "_rule"))
    if joined.empty:
        return joined

    for col in ("asset", "minutes", "wait_min", "hold_hours", "role", "policy_id", "epoch", "net_pct"):
        left, right = f"{col}_event", f"{col}_rule"
        if left in joined:
            if right in joined:
                joined[col] = joined[left].where(joined[left].notna(), joined[right])
            else:
                joined[col] = joined[left]
        elif right in joined:
            joined[col] = joined[right]
        elif col not in joined:
            joined[col] = np.nan
    if "decision" not in joined:
        raise ValueError(f"{source} locked_events.parquet is missing decision")
    joined["decision"] = pd.to_datetime(joined.decision, utc=True)
    if "known" in joined:
        joined["known"] = pd.to_datetime(joined.known, utc=True)
    else:
        joined["known"] = pd.NaT
    joined["minutes"] = pd.to_numeric(joined.minutes, errors="coerce")
    joined["wait_min"] = pd.to_numeric(joined.wait_min, errors="coerce")
    joined["hold_hours"] = pd.to_numeric(joined.hold_hours, errors="coerce")
    joined["known"] = joined.known.where(
        joined.known.notna(),
        joined.decision - pd.to_timedelta(joined.wait_min, unit="m"),
    )
    joined["asset"] = joined.asset.map(_asset)
    joined["role"] = joined.role.fillna(source)
    joined["policy_id"] = joined.policy_id.where(joined.policy_id.notna(), joined.candidate_id)
    joined["source"] = source
    joined["epoch_rank"] = joined.epoch.map(_epoch_rank)
    joined["actual_trade_id"] = joined.get("trade_id", joined.candidate_id).astype(str)
    if not (joined.decision == joined.known + pd.to_timedelta(joined.wait_min, unit="m")).all():
        raise ValueError(f"{source} locked event decision must equal known + wait_min")
    joined = joined.dropna(subset=["asset", "minutes", "wait_min", "hold_hours", "known", "decision"])
    joined["minutes"] = joined.minutes.astype(int)
    joined["wait_min"] = joined.wait_min.astype(int)
    joined["hold_hours"] = joined.hold_hours.astype(int)
    return joined.reset_index(drop=True)


def _in_scope(row: pd.Series) -> bool:
    role = str(row.role).lower()
    source = str(row.source)
    if source == "second_wave":
        asset = str(row.asset).upper()
        expected_id = f"wave_{asset}"
        return (role == "dynamic_primary" and asset in {"BTC", "ETH", "SOL", "ALT"}
                and str(row.candidate_id) == expected_id and int(row.hold_hours) == 144)
    if source == "exits":
        return role in {"dynamic_primary", "retention", "case_target", "case_runner"}
    breadth = str(row.get("breadth", "")).upper().replace("-", "_")
    return role == "time_exit_winner" or (role == "baseline" and breadth in {"N35_99", "N50_99"})


def _signal_lookup() -> dict[tuple[int, pd.Timestamp, int], dict]:
    path = FEATURES / "signals.csv.gz"
    if not path.is_file():
        raise FileNotFoundError(path)
    frame = pd.read_csv(path, usecols=["minutes", "known", "wait_min", "monitor_fraction"],
                        parse_dates=["known"])
    return {
        (int(row.minutes), row.known, int(row.wait_min)): row._asdict()
        for row in frame.itertuples(index=False)
    }


def _member_lookup(keys: set[tuple[int, pd.Timestamp]]) -> dict[tuple[int, pd.Timestamp], list[str]]:
    path = FEATURES / "members.csv.gz"
    members = pd.read_csv(path, usecols=["minutes", "known", "symbol"], parse_dates=["known"])
    if not keys:
        return {}
    key_frame = pd.DataFrame(list(keys), columns=["minutes", "known"])
    selected = members.merge(key_frame, on=["minutes", "known"], how="inner", validate="many_to_one")
    selected = selected[~selected.symbol.isin(EXCLUDED_ALT)]
    return {
        (int(minutes), known): sorted(group.symbol.astype(str).unique())
        for (minutes, known), group in selected.groupby(["minutes", "known"], sort=False)
    }


def _canonical_candidates(sources: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    tables = [_read_locked_source(source) for source in sources]
    tables = [table for table in tables if not table.empty]
    if not tables:
        raise FileNotFoundError("No requested locked_events.parquet + locked_rules.csv pair is available")
    candidates = pd.concat(tables, ignore_index=True, sort=False)
    candidates["rule_event_id"] = candidates.apply(
        lambda r: "|".join((str(r.source), str(r.candidate_id), str(r.policy_id), str(r.role), r.decision.isoformat())),
        axis=1,
    )
    if candidates.rule_event_id.duplicated().any():
        candidates = candidates.drop_duplicates("rule_event_id", keep="first")
    candidates["scope_selected"] = candidates.apply(_in_scope, axis=1)
    candidates["scope_status"] = np.where(candidates.scope_selected, "selected_for_matching", "outside_requested_scope_not_matched")
    return candidates, candidates[candidates.scope_selected].copy()


def _ledger_controls() -> tuple[dict[str, pd.DatetimeIndex], pd.DatetimeIndex]:
    _, ledger = census.ledger_rows()
    lower: dict[str, pd.DatetimeIndex] = {}
    for symbol, group in ledger[ledger.side.eq(1)].groupby("symbol", sort=False):
        lower[str(symbol)] = pd.DatetimeIndex(group.touch.sort_values().unique())
    counts = ledger.groupby(["touch", "side"]).symbol.nunique().unstack(fill_value=0)
    shocks = counts.index[counts.max(axis=1).ge(25)] + BAR
    return lower, pd.DatetimeIndex(shocks).sort_values()


def _monitor_prefix(start: pd.Timestamp, end: pd.Timestamp) -> tuple[pd.DatetimeIndex, np.ndarray]:
    grid = pd.date_range(start.floor("5min"), end.ceil("5min"), freq=BAR, inclusive="left")
    monitored = (grid.tz_convert("America/New_York").day_of_week != 0).astype(np.int64)
    return grid, np.r_[0, np.cumsum(monitored, dtype=np.int64)]


def _monitor_counts(grid: pd.DatetimeIndex, prefix: np.ndarray,
                    starts: pd.DatetimeIndex, ends: pd.DatetimeIndex) -> np.ndarray:
    a = grid.searchsorted(starts, side="left")
    b = grid.searchsorted(ends, side="left")
    return prefix[b] - prefix[a]


def _pre_observation_features(frame: pd.DataFrame, knowns: pd.DatetimeIndex) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Compute causal 1h realized-volatility bucket and trend sign at known."""
    index = frame.index
    index_ns = index.as_unit("ns").asi8
    known_ns = pd.DatetimeIndex(knowns).as_unit("ns").asi8
    kpos = index.searchsorted(pd.DatetimeIndex(knowns), side="left")
    exact = (kpos < len(index))
    exact[exact] &= index_ns[kpos[exact]] == known_ns[exact]
    pos = kpos - 1  # last completed 5m bar at observation start
    valid = exact & (pos >= 12)
    vol_bucket = np.full(len(knowns), -1, dtype=np.int16)
    trend_bucket = np.full(len(knowns), 9, dtype=np.int8)
    if not valid.any():
        return vol_bucket, trend_bucket, valid
    target = np.flatnonzero(valid)
    p = pos[target]
    locations = p[:, None] - np.arange(12, -1, -1)[None, :]
    history_ns = index_ns[locations]
    contiguous = (np.diff(history_ns, axis=1) == STEP_NS).all(axis=1)
    close = frame.close.to_numpy(dtype=float)
    prices = close[locations]
    finite = np.isfinite(prices).all(axis=1) & (prices > 0).all(axis=1) & contiguous
    target = target[finite]
    prices = prices[finite]
    if len(target):
        logret = np.diff(np.log(prices), axis=1)
        vol = np.sqrt(np.square(logret).sum(axis=1)) * 100
        trend = prices[:, -1] / prices[:, 0] - 1
        vol_bucket[target] = np.digitize(vol, [.25, .5, 1, 2, 4])
        trend_bucket[target] = np.sign(trend).astype(np.int8)
        valid[:] = False
        valid[target] = True
    else:
        valid[:] = False
    return vol_bucket, trend_bucket, valid


def _complete_horizon(frame: pd.DataFrame, decisions: pd.DatetimeIndex, hold_hours: int) -> tuple[np.ndarray, np.ndarray]:
    index = frame.index
    positions = index.get_indexer(decisions)
    n = int(hold_hours) * 12
    good = (positions >= 0) & (positions + n < len(index))
    result = np.zeros(len(positions), dtype=bool)
    out_positions = np.full(len(positions), -1, dtype=np.int64)
    if not good.any():
        return result, out_positions
    ids = np.flatnonzero(good)
    p = positions[ids]
    ns = index.as_unit("ns").asi8
    contiguous = np.ones(len(ids), dtype=bool)
    # Price history paths must contain every scheduled 5m open through the exit open.
    clock_bad = np.r_[False, np.diff(ns) != STEP_NS]
    clock_prefix = np.r_[0, np.cumsum(clock_bad, dtype=np.int64)]
    contiguous = (clock_prefix[p + n + 1] - clock_prefix[p + 1]) == 0
    valid_bar = frame.loc[:, OHLC].notna().all(axis=1).to_numpy()
    bad_prefix = np.r_[0, np.cumsum(~valid_bar, dtype=np.int64)]
    full_path = (bad_prefix[p + n] - bad_prefix[p]) == 0
    exit_open = frame.open.to_numpy(dtype=float)[p + n]
    result[ids] = contiguous & full_path & np.isfinite(exit_open) & (exit_open > 0)
    out_positions[ids] = p
    return result, out_positions


def _shock_free(shocks: pd.DatetimeIndex, decisions: pd.DatetimeIndex, guard: pd.Timedelta) -> np.ndarray:
    if not len(shocks):
        return np.ones(len(decisions), dtype=bool)
    left = shocks.searchsorted(decisions - guard, side="left")
    right = shocks.searchsorted(decisions + guard, side="right")
    return left == right


def _past_shock_free(shocks: pd.DatetimeIndex, decisions: pd.DatetimeIndex,
                     lookback: pd.Timedelta = pd.Timedelta(minutes=60)) -> np.ndarray:
    """Exclude shocks known within the prior 60 minutes, including the boundary."""
    if not len(shocks):
        return np.ones(len(decisions), dtype=bool)
    left = shocks.searchsorted(decisions - lookback, side="left")
    # At an exact bar boundary the prior 5m close is already available, so a
    # shock whose known timestamp equals the random entry must be included.
    right = shocks.searchsorted(decisions, side="right")
    return left == right


def _nonoverlap_actual(decisions: pd.DatetimeIndex, actual_decision: pd.Timestamp,
                       hold_hours: int) -> np.ndarray:
    """Return controls whose [entry, exit) does not overlap actual [entry, exit)."""
    actual_decision = _utc(actual_decision)
    actual_exit = actual_decision + pd.Timedelta(hours=int(hold_hours))
    control_exit = decisions + pd.Timedelta(hours=int(hold_hours))
    return (decisions >= actual_exit) | (control_exit <= actual_decision)


def _no_lower_touch(touches: pd.DatetimeIndex, starts: pd.DatetimeIndex,
                    decisions: pd.DatetimeIndex) -> np.ndarray:
    if not len(touches):
        return np.ones(len(decisions), dtype=bool)
    left = touches.searchsorted(starts, side="left")
    right = touches.searchsorted(decisions, side="left")
    return left == right


def _candidate_pool(task: pd.Series, frame: pd.DataFrame,
                    lower_touches: dict[str, pd.DatetimeIndex], shocks: pd.DatetimeIndex,
                    monitor_grid: pd.DatetimeIndex, monitor_prefix: np.ndarray,
                    *, past_only: bool = False) -> tuple[np.ndarray, dict, str]:
    symbol = str(task.symbol)
    known, decision = _utc(task.known), _utc(task.decision)
    minutes, wait_min = int(task.minutes), int(task.wait_min)
    hold_hours = int(task.get("match_horizon_hours", task.hold_hours))
    candidate_vol, candidate_trend, candidate_ok = _pre_observation_features(frame, pd.DatetimeIndex([known]))
    if not candidate_ok[0]:
        return np.empty(0, dtype=np.int64), {}, "missing_candidate_pre_observation_context"
    candidate_monitor_n = int(_monitor_counts(monitor_grid, monitor_prefix,
        pd.DatetimeIndex([known - pd.Timedelta(minutes=minutes)]), pd.DatetimeIndex([decision]))[0])
    total_bars = (minutes + wait_min) // 5
    candidate_monitor_fraction = candidate_monitor_n / total_bars if total_bars else 1.0
    ny = known.tz_convert("America/New_York").day_of_week
    decision_ny = decision.tz_convert("America/New_York").day_of_week

    index = frame.index
    month_start = pd.Timestamp(year=decision.year, month=decision.month, day=1, tz="UTC")
    month_end = month_start + pd.DateOffset(months=1)
    session = decision.hour // 6
    candidates = index[(index >= month_start) & (index < month_end) & (index.hour // 6 == session)]
    if not len(candidates):
        return np.empty(0, dtype=np.int64), {"candidate_vol_bucket": int(candidate_vol[0]),
            "candidate_trend_bucket": int(candidate_trend[0]), "monitor_fraction": candidate_monitor_fraction}, "empty_month_session"
    control_knowns = candidates - pd.Timedelta(minutes=wait_min)
    vol_bucket, trend_bucket, context_ok = _pre_observation_features(frame, control_knowns)
    expected_monitor = int(round(candidate_monitor_fraction * total_bars))
    starts = control_knowns - pd.Timedelta(minutes=minutes)
    monitor_counts = _monitor_counts(monitor_grid, monitor_prefix, starts, candidates)
    ctrl_ny = control_knowns.tz_convert("America/New_York").day_of_week
    ctrl_decision_ny = candidates.tz_convert("America/New_York").day_of_week
    path_ok, positions = _complete_horizon(frame, candidates, hold_hours)
    if past_only:
        shock_ok = _past_shock_free(shocks, candidates)
        overlap_ok = _nonoverlap_actual(candidates, decision, hold_hours)
    else:
        guard = pd.Timedelta(hours=hold_hours) + pd.Timedelta(minutes=minutes + wait_min)
        shock_ok = _shock_free(shocks, candidates, guard)
        overlap_ok = np.ones(len(candidates), dtype=bool)
    touch_ok = _no_lower_touch(lower_touches.get(symbol, pd.DatetimeIndex([])), starts, candidates)
    valid = context_ok & path_ok & shock_ok & overlap_ok & touch_ok
    valid &= vol_bucket == int(candidate_vol[0])
    valid &= trend_bucket == int(candidate_trend[0])
    valid &= monitor_counts == expected_monitor
    valid &= ctrl_ny == ny
    valid &= ctrl_decision_ny == decision_ny
    # A candidate's own realized entry can never be used as its random control.
    valid &= candidates != decision
    pool_positions = positions[valid]
    context = {
        "candidate_vol_bucket": int(candidate_vol[0]),
        "candidate_trend_bucket": int(candidate_trend[0]),
        "candidate_monitor_fraction": float(candidate_monitor_fraction),
        "candidate_monitor_bars": candidate_monitor_n,
        "monitor_session_ny_known_weekday": int(ny),
        "monitor_session_ny_decision_weekday": int(decision_ny),
        "candidate_entry_price": float(frame.open.loc[decision]) if decision in frame.index else np.nan,
        "matching_policy": "past_only" if past_only else "symmetric_shock_guard",
    }
    if not len(pool_positions):
        return pool_positions, context, "no_exact_stratum_control_times"
    return pool_positions, context, "matched_pool_available"


def _draw_positions(pool: np.ndarray, task: pd.Series, n_draws: int) -> np.ndarray:
    n = min(int(n_draws), len(pool))
    if not n:
        return np.empty(0, dtype=np.int64)
    symbol_crc = zlib.crc32(str(task.symbol).encode("utf-8"))
    source_crc = zlib.crc32(str(task.source).encode("utf-8"))
    sequence = np.random.SeedSequence([
        SEED, symbol_crc, source_crc, int(_utc(task.known).value // 1_000_000_000),
        int(_utc(task.decision).value // 1_000_000_000), int(task.minutes), int(task.wait_min),
    ])
    rng = np.random.default_rng(sequence)
    return np.sort(rng.choice(pool, size=n, replace=False))


def _build_task_rows(candidates: pd.DataFrame, selected: pd.DataFrame,
                     signals: dict, members: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    task_rows: list[dict] = []
    event_rows: list[dict] = []
    for row in selected.itertuples(index=False):
        event = pd.Series(row._asdict())
        key = (int(event.minutes), _utc(event.known), int(event.wait_min))
        signal = signals.get(key)
        event_key = event.rule_event_id
        if signal is None:
            event_rows.append({"rule_event_id": event_key, "candidate_id": event.candidate_id,
                               "decision": event.decision, "asset": event.asset,
                               "source": event.source, "role": event.role, "policy_id": event.policy_id,
                               "epoch": event.epoch, "status": "missing_signal_feature_row",
                               "expected_symbols": 0})
            continue
        if event.asset == "ALT":
            symbols = members.get((int(event.minutes), _utc(event.known)), [])
            expected_symbols = len(symbols)
            if not symbols:
                event_rows.append({"rule_event_id": event_key, "candidate_id": event.candidate_id,
                                   "decision": event.decision, "asset": event.asset,
                                   "source": event.source, "role": event.role, "policy_id": event.policy_id,
                                   "epoch": event.epoch, "status": "no_returnable_initial_members",
                                   "expected_symbols": 0})
                continue
            weights = {symbol: 1.0 / expected_symbols for symbol in symbols}
        else:
            symbol = f"{event.asset}USDT"
            if symbol not in {"BTCUSDT", "ETHUSDT", "SOLUSDT"}:
                event_rows.append({"rule_event_id": event_key, "candidate_id": event.candidate_id,
                                   "decision": event.decision, "asset": event.asset,
                                   "source": event.source, "role": event.role, "policy_id": event.policy_id,
                                   "epoch": event.epoch, "status": "unsupported_asset", "expected_symbols": 0})
                continue
            symbols, expected_symbols, weights = [symbol], 1, {symbol: 1.0}
        event_rows.append({
            "rule_event_id": event_key, "candidate_id": event.candidate_id,
            "decision": _utc(event.decision), "known": _utc(event.known),
            "asset": event.asset, "source": event.source, "role": event.role,
            "policy_id": event.policy_id, "epoch": event.epoch,
            "epoch_rank": int(event.epoch_rank), "minutes": int(event.minutes),
            "wait_min": int(event.wait_min), "hold_hours": int(event.hold_hours),
            "candidate_net_pct": pd.to_numeric(event.get("net_pct"), errors="coerce"),
            "candidate_trade_id": event.actual_trade_id,
            "status": "awaiting_controls", "expected_symbols": expected_symbols,
            "candidate_monitor_fraction": float(signal["monitor_fraction"]),
        })
        draw_limit = (MAX_DYNAMIC_DRAWS if _is_dynamic_source(event.source) else MAX_FIXED_DRAWS)
        for symbol in symbols:
            task_rows.append({
                "rule_event_id": event_key, "candidate_id": event.candidate_id,
                "candidate_trade_id": event.actual_trade_id,
                "decision": _utc(event.decision), "known": _utc(event.known),
                "asset": event.asset, "source": event.source, "role": event.role,
                "policy_id": str(event.policy_id), "epoch": event.epoch,
                "epoch_rank": int(event.epoch_rank), "minutes": int(event.minutes),
                "wait_min": int(event.wait_min), "hold_hours": int(event.hold_hours),
                "candidate_net_pct": pd.to_numeric(event.get("net_pct"), errors="coerce"),
                "symbol": symbol, "weight": float(weights[symbol]),
                "expected_symbols": expected_symbols, "draw_limit": draw_limit,
                "candidate_monitor_fraction": float(signal["monitor_fraction"]),
            })
    return pd.DataFrame(task_rows), pd.DataFrame(event_rows)


def _process_symbol_month(tasks: pd.DataFrame, lower_touches: dict,
                          shocks: pd.DatetimeIndex, *, past_only: bool = False,
                          price_frame: pd.DataFrame | None = None,
                          price_receipt: dict | None = None) -> tuple[list[dict], list[dict]]:
    tasks = tasks.copy()
    dynamic = tasks.source.isin(DYNAMIC_SOURCES)
    tasks["match_horizon_hours"] = tasks.hold_hours.astype(int)
    if dynamic.any():
        dynamic_key = ["asset", "decision", "known", "minutes", "wait_min", "symbol"]
        tasks.loc[dynamic, "match_horizon_hours"] = tasks.loc[dynamic].groupby(dynamic_key).hold_hours.transform("max")
    symbol = str(tasks.symbol.iloc[0])
    decisions = pd.DatetimeIndex(tasks.decision)
    month_start = pd.Timestamp(year=decisions[0].year, month=decisions[0].month, day=1, tz="UTC")
    month_end = month_start + pd.DateOffset(months=1)
    max_w = int(tasks.minutes.max())
    max_x = int(tasks.wait_min.max())
    max_h = int(tasks.match_horizon_hours.max())
    warmup = pd.Timedelta(minutes=max(max_w + max_x, 65) + 5)
    start = month_start - warmup
    end = month_end + pd.Timedelta(hours=max_h)
    if price_frame is None:
        frame, receipt = load_full_ohlc(symbol, start, end, needed_intervals=None, repair_missing=False)
    else:
        frame = price_frame
        receipt = price_receipt or {}
    monitor_grid, monitor_prefix = _monitor_prefix(start, month_end)
    match_rows: list[dict] = []
    receipt_row = {"symbol": symbol, "month": month_start.strftime("%Y-%m"),
                   "rows": int(len(frame)), "start": start.isoformat(), "end": end.isoformat(),
                   "local_sources": receipt.get("local_sources", []),
                   "remaining_missing_ranges": receipt.get("rest", {}).get("remaining_missing_ranges_utc", [])}
    sampled_starts: list[tuple[int, int, int]] = []
    candidate_starts: list[tuple[tuple[str, str], int, int]] = []
    candidate_net_by: dict[tuple[str, str], float] = {}
    for task in tasks.itertuples(index=False):
        task_series = pd.Series(task._asdict())
        pool, context, status = _candidate_pool(task_series, frame, lower_touches, shocks,
                                                monitor_grid, monitor_prefix, past_only=past_only)
        draws = MAX_DYNAMIC_DRAWS if _is_dynamic_source(task.source) else MAX_FIXED_DRAWS
        sampled = _draw_positions(pool, task_series, draws) if len(pool) else np.empty(0, dtype=np.int64)
        is_fixed = task.source == "search"
        candidate_valid, candidate_positions = _complete_horizon(frame, pd.DatetimeIndex([task.decision]),
                                                                   int(task.hold_hours)) if is_fixed else (np.array([False]), np.array([-1]))
        candidate_pos = int(candidate_positions[0])
        candidate_exit_pos = candidate_pos + int(task.hold_hours) * 12 if candidate_pos >= 0 else -1
        candidate_entry_price = float(frame.open.iloc[candidate_pos]) if candidate_valid[0] else np.nan
        candidate_exit_price = float(frame.open.iloc[candidate_exit_pos]) if candidate_valid[0] else np.nan
        candidate_gross_pct = (candidate_exit_price / candidate_entry_price - 1) * 100 if candidate_valid[0] else np.nan
        candidate_key = (str(task.rule_event_id), symbol)
        if is_fixed and candidate_valid[0]:
            candidate_starts.append((candidate_key, candidate_pos, int(task.hold_hours) * 12))
        for draw, pos in enumerate(sampled):
            random_entry = frame.index[int(pos)]
            exit_position = int(pos) + int(task.hold_hours) * 12
            if exit_position >= len(frame):
                continue
            entry_price = float(frame.open.iloc[int(pos)])
            exit_price = float(frame.open.iloc[exit_position]) if is_fixed else np.nan
            gross = (exit_price / entry_price - 1) * 100 if is_fixed else np.nan
            row_index = len(match_rows)
            match_rows.append({
                "rule_event_id": task.rule_event_id,
                "candidate_id": task.candidate_id,
                "candidate_trade_id": task.candidate_trade_id,
                "decision": task.decision,
                "actual_decision": task.decision,
                "month": month_start.strftime("%Y-%m"),
                "known": task.known,
                "random_entry": random_entry,
                "entry_time": random_entry,
                "control_decision": random_entry,
                "control_known": random_entry - pd.Timedelta(minutes=int(task.wait_min)),
                "exit_time": random_entry + pd.Timedelta(hours=int(task.hold_hours)),
                "trade_id": f"{task.rule_event_id}|{task.symbol}|draw{draw}",
                "control_trade_id": f"{task.rule_event_id}|{task.symbol}|draw{draw}",
                "asset": task.asset, "symbol": task.symbol, "source": task.source,
                "role": task.role, "policy_id": task.policy_id, "epoch": task.epoch,
                "epoch_rank": task.epoch_rank, "minutes": task.minutes,
                "wait_min": task.wait_min, "hold_hours": task.hold_hours,
                "match_horizon_hours": task.match_horizon_hours,
                "weight": task.weight, "draw": int(draw),
                "entry_price": entry_price, "exit_price": exit_price,
                "gross_pct": float(gross),
                "candidate_member_entry_price": candidate_entry_price,
                "candidate_member_exit_price": candidate_exit_price,
                "candidate_member_gross_pct": float(candidate_gross_pct),
                "candidate_member_net_pct": np.nan,
                "candidate_member_status": "complete" if candidate_valid[0] else "missing_candidate_horizon",
                "net_pct": np.nan,
                "match_pool_n": int(len(pool)),
                "member_expected_n": int(task.expected_symbols),
                "candidate_net_pct": task.candidate_net_pct,
                "candidate_vol_bucket": context.get("candidate_vol_bucket"),
                "candidate_trend_bucket": context.get("candidate_trend_bucket"),
                "candidate_monitor_fraction": context.get("candidate_monitor_fraction"),
                "monitor_session_ny_known_weekday": context.get("monitor_session_ny_known_weekday"),
                "monitor_session_ny_decision_weekday": context.get("monitor_session_ny_decision_weekday"),
                "match_status": status,
                "matching_policy": "past_only" if past_only else "symmetric_shock_guard",
            })
            if is_fixed:
                sampled_starts.append((row_index, int(pos), int(task.hold_hours) * 12))
        task_coverage = {
            "rule_event_id": task.rule_event_id, "symbol": task.symbol,
            "candidate_id": task.candidate_id, "decision": task.decision,
            "source": task.source, "role": task.role, "policy_id": task.policy_id,
            "epoch": task.epoch, "minutes": task.minutes, "wait_min": task.wait_min,
            "hold_hours": task.hold_hours, "weight": task.weight,
            "expected_symbols": task.expected_symbols, "draw_limit": draws,
            "pool_n": int(len(pool)), "selected_draws": int(len(sampled)),
            "symbol_status": status,
            **context,
        }
        receipt_row.setdefault("task_coverage", []).append(task_coverage)

    # Replay fixed outcomes once over the locally loaded symbol-month matrix.
    # Dynamic rows retain entry coordinates only; their policy exits are applied
    # by the owner through the same exit engine and need no fixed-time replay.
    if sampled_starts or candidate_starts:
        holds = sorted({hold for _, _, hold in sampled_starts} | {hold for _, _, hold in candidate_starts})
        opens = frame.open.to_numpy(dtype=float)
        for hold in holds:
            rows_for_hold = [(row_i, pos) for row_i, pos, h in sampled_starts if h == hold]
            candidates_for_hold = [(key, pos) for key, pos, h in candidate_starts if h == hold]
            starts = np.unique([pos for _, pos in rows_for_hold] + [pos for _, pos in candidates_for_hold])
            returns = base.trade_returns(opens, starts, hold_bars=hold)
            lookup = returns.to_dict()
            for row_i, pos in rows_for_hold:
                match_rows[row_i]["net_pct"] = float(lookup[pos])
            for key, pos in candidates_for_hold:
                candidate_net_by[key] = float(lookup[pos])
    for row in match_rows:
        candidate_net = candidate_net_by.get((row["rule_event_id"], row["symbol"]))
        if candidate_net is not None:
            row["candidate_member_net_pct"] = candidate_net
    return match_rows, [receipt_row]


def _scope_only_rows(candidates: pd.DataFrame) -> pd.DataFrame:
    frame = candidates.copy()
    return frame.loc[~frame.scope_selected, ["candidate_id", "asset", "decision", "role", "policy_id", "epoch", "source", "scope_status"]]


def _paired_fixed(matches: pd.DataFrame, event_meta: pd.DataFrame) -> pd.DataFrame:
    meta_cols = ["rule_event_id", "candidate_id", "decision", "asset", "epoch", "role", "policy_id",
                 "candidate_net_pct", "expected_symbols", "minutes", "wait_min", "hold_hours"]
    meta = event_meta.loc[event_meta.source.eq("search"), meta_cols].copy()
    if meta.empty:
        return pd.DataFrame()
    draws = meta.loc[meta.index.repeat(MAX_FIXED_DRAWS)].copy()
    draws["draw"] = np.tile(np.arange(MAX_FIXED_DRAWS), len(meta))
    fixed = matches[matches.source.eq("search")].copy() if not matches.empty else pd.DataFrame()
    if not fixed.empty:
        fixed = fixed.dropna(subset=["net_pct", "candidate_member_net_pct"])
        fixed["control_weighted_pct"] = fixed.weight * fixed.net_pct
        fixed["actual_weighted_pct"] = fixed.weight * fixed.candidate_member_net_pct
        grouped = fixed.groupby(["rule_event_id", "draw"], as_index=False).agg(
            matched_symbols=("symbol", "nunique"), matched_weight=("weight", "sum"),
            control_weighted_pct=("control_weighted_pct", "sum"),
            actual_weighted_pct=("actual_weighted_pct", "sum"), min_pool_n=("match_pool_n", "min"),
        )
        draws = draws.merge(grouped, on=["rule_event_id", "draw"], how="left", validate="one_to_one")
    else:
        for column in ("matched_symbols", "matched_weight", "control_weighted_pct",
                       "actual_weighted_pct", "min_pool_n"):
            draws[column] = np.nan
    draws["matched_symbols"] = draws.matched_symbols.fillna(0).astype(int)
    draws["coverage_pct"] = np.where(draws.expected_symbols.gt(0),
                                      100 * draws.matched_symbols / draws.expected_symbols, 0.0)
    draws["control_net_pct"] = draws.control_weighted_pct / draws.matched_weight
    draws["candidate_member_net_pct"] = draws.actual_weighted_pct / draws.matched_weight
    draws["partial_original_weight_pct"] = draws.control_weighted_pct
    draws["partial_actual_original_weight_pct"] = draws.actual_weighted_pct
    draws["paired_excess_pp"] = draws.candidate_member_net_pct - draws.control_net_pct
    draws["eligible_coverage"] = draws.coverage_pct.ge(100 * MIN_COVERAGE) & draws.matched_weight.gt(0)
    draws["coverage_status"] = np.select(
        [draws.coverage_pct.ge(100), draws.eligible_coverage, draws.matched_symbols.eq(0)],
        ["full", "incomplete_at_least_80pct", "no_matched_members"],
        default="below_80pct_not_eligible",
    )
    return draws.sort_values(["decision", "candidate_id", "draw"]).reset_index(drop=True)


def _coverage_table(event_meta: pd.DataFrame, task_coverage: pd.DataFrame,
                    fixed_paired: pd.DataFrame) -> pd.DataFrame:
    if event_meta.empty:
        return event_meta
    if task_coverage.empty:
        coverage = pd.DataFrame(columns=["rule_event_id", "pool_min", "pool_max", "symbol_rows"])
    else:
        coverage = task_coverage.groupby("rule_event_id", as_index=False).agg(
            pool_min=("pool_n", "min"), pool_max=("pool_n", "max"),
            selected_draws_min=("selected_draws", "min"), symbol_rows=("symbol", "nunique"),
            no_pool_symbols=("pool_n", lambda s: int(s.eq(0).sum())),
        )
    out = event_meta.merge(coverage, on="rule_event_id", how="left", validate="one_to_one")
    if not fixed_paired.empty:
        fixed_cov = fixed_paired.groupby("rule_event_id", as_index=False).agg(
            min_draw_coverage_pct=("coverage_pct", "min"), max_draw_coverage_pct=("coverage_pct", "max"),
            eligible_draws=("eligible_coverage", "sum"), fixed_draw_rows=("draw", "nunique"),
        )
        out = out.merge(fixed_cov, on="rule_event_id", how="left", validate="one_to_one")
    return out


def _self_test() -> dict:
    """Deterministic fixed fixture covering matching, exclusion, and replay."""
    index = pd.date_range("2025-01-01", "2025-02-08", freq=BAR, tz="UTC", inclusive="left")
    close = np.full(len(index), 100.0)
    frame = pd.DataFrame({"open": close, "high": close + .1, "low": close - .1, "close": close}, index=index)
    known = pd.Timestamp("2025-01-04T06:00:00Z")
    decision = known + pd.Timedelta(minutes=30)
    task = pd.Series({"symbol": "TESTUSDT", "known": known, "decision": decision,
                      "minutes": 30, "wait_min": 30, "hold_hours": 6,
                      "candidate_id": "fixture", "source": "search"})
    grid, prefix = _monitor_prefix(index[0], index[-1] + BAR)
    pool, _, status = _candidate_pool(task, frame, {}, pd.DatetimeIndex([]), grid, prefix)
    if status != "matched_pool_available" or len(pool) < 3:
        raise AssertionError("Fixed matcher fixture did not produce a usable pool")
    first = _draw_positions(pool, task, 3)
    second = _draw_positions(pool, task, 3)
    if not np.array_equal(first, second) or np.any(frame.index[first] == decision):
        raise AssertionError("Fixed matcher draws are not deterministic or included the target event")
    dynamic_a = task.copy()
    dynamic_a.update({"source": "exits", "candidate_id": "policy-a", "hold_hours": 6,
                      "match_horizon_hours": 24})
    dynamic_b = task.copy()
    dynamic_b.update({"source": "exits", "candidate_id": "policy-b", "hold_hours": 24,
                      "match_horizon_hours": 24})
    pool_a, _, _ = _candidate_pool(dynamic_a, frame, {}, pd.DatetimeIndex([]), grid, prefix)
    pool_b, _, _ = _candidate_pool(dynamic_b, frame, {}, pd.DatetimeIndex([]), grid, prefix)
    if not np.array_equal(pool_a, pool_b) or not np.array_equal(
        _draw_positions(pool_a, dynamic_a, MAX_DYNAMIC_DRAWS),
        _draw_positions(pool_b, dynamic_b, MAX_DYNAMIC_DRAWS),
    ):
        raise AssertionError("Dynamic policies for one event did not reuse the same three random draws")
    probe_times = pd.DatetimeIndex([
        pd.Timestamp("2025-01-04T06:00:00Z"),
        pd.Timestamp("2025-01-04T07:00:00Z"),
        pd.Timestamp("2025-01-04T07:35:00Z"),
        pd.Timestamp("2025-01-04T06:30:00Z"),
    ])
    recent_shock = pd.DatetimeIndex([pd.Timestamp("2025-01-04T06:30:00Z")])
    if not np.array_equal(_past_shock_free(recent_shock, probe_times), [True, False, True, False]):
        raise AssertionError("Past-only shock rule used a future or stale shock")
    overlap_decisions = pd.DatetimeIndex([
        decision - pd.Timedelta(hours=6), decision - pd.Timedelta(hours=5),
        decision, decision + pd.Timedelta(hours=6),
    ])
    if not np.array_equal(_nonoverlap_actual(overlap_decisions, decision, 6), [True, False, False, True]):
        raise AssertionError("Past-only controls did not exclude actual-position overlap")
    if _shock_free(recent_shock, pd.DatetimeIndex([probe_times[1]]), pd.Timedelta(hours=7))[0]:
        raise AssertionError("Primary symmetric shock rule changed during past-only sensitivity work")
    selected_time = frame.index[int(first[0])]
    control_known = selected_time - pd.Timedelta(minutes=30)
    blocked_touch = control_known - pd.Timedelta(minutes=10)
    filtered, _, _ = _candidate_pool(task, frame, {"TESTUSDT": pd.DatetimeIndex([blocked_touch])},
                                     pd.DatetimeIndex([]), grid, prefix)
    if int(first[0]) in filtered:
        raise AssertionError("Same-symbol lower-touch exclusion failed")
    ret = base.trade_returns(frame.open.to_numpy(), first[:1], hold_bars=6*12)
    if len(ret) != 1 or not np.isfinite(ret.iloc[0]):
        raise AssertionError("Fixed VectorBT return replay failed")
    month_task = pd.DataFrame([{
        "rule_event_id": "reuse-fixture", "candidate_id": "reuse-fixture",
        "candidate_trade_id": "reuse-fixture", "decision": decision, "known": known,
        "asset": "BTC", "source": "search", "role": "time_exit_winner",
        "policy_id": "fixture", "epoch": 3, "epoch_rank": 3,
        "minutes": 30, "wait_min": 30, "hold_hours": 6,
        "candidate_net_pct": np.nan, "symbol": "TESTUSDT", "weight": 1.0,
        "expected_symbols": 1, "draw_limit": MAX_FIXED_DRAWS,
        "candidate_monitor_fraction": 1.0,
    }])
    original_loader = load_full_ohlc
    try:
        def unexpected_price_reload(*args, **kwargs):
            raise AssertionError("Injected per-month OHLC frame should bypass the local loader")
        globals()["load_full_ohlc"] = unexpected_price_reload
        reused_rows, _ = _process_symbol_month(
            month_task, {}, pd.DatetimeIndex([]), price_frame=frame,
            price_receipt={"local_sources": ["fixture"]},
        )
    finally:
        globals()["load_full_ohlc"] = original_loader
    if not reused_rows or not np.isfinite(reused_rows[0]["net_pct"]):
        raise AssertionError("Per-symbol cached OHLC frame did not produce fixed controls")
    empty_match = pd.DataFrame(columns=["source"])
    no_pair_meta = pd.DataFrame([{
        "rule_event_id": "no-pair", "candidate_id": "no-pair", "decision": decision,
        "asset": "ALT", "epoch": "epoch3", "role": "baseline", "policy_id": "n35",
        "candidate_net_pct": 2.0, "expected_symbols": 5, "minutes": 30,
        "wait_min": 30, "hold_hours": 6, "source": "search",
    }])
    no_pairs = _paired_fixed(empty_match, no_pair_meta)
    if len(no_pairs) != MAX_FIXED_DRAWS or no_pairs.coverage_pct.ne(0).any() or no_pairs.control_net_pct.notna().any():
        raise AssertionError("Unmatched fixed candidate did not retain NaN draw rows")
    partial_meta = no_pair_meta.assign(rule_event_id="partial", candidate_id="partial")
    partial_rows = pd.DataFrame([{
        "rule_event_id": "partial", "candidate_id": "partial", "decision": decision,
        "asset": "ALT", "epoch": "epoch3", "role": "baseline", "policy_id": "n35",
        "candidate_net_pct": 8.0, "weight": .2, "symbol": f"COIN{i}USDT", "draw": 0,
        "net_pct": 5.0, "candidate_member_net_pct": 10.0, "match_pool_n": 24, "source": "search",
    } for i in range(4)])
    partial = _paired_fixed(partial_rows, partial_meta).query("draw == 0").iloc[0]
    if partial.coverage_pct != 80 or not partial.eligible_coverage or partial.paired_excess_pp != 5:
        raise AssertionError("ALT paired returns did not use the same 80% matched member subset")
    return {"status": "passed", "fixture": "constant_5m_ohlc", "pool_n": int(len(pool)),
            "draws": first.tolist(), "sample_net_pct": float(ret.iloc[0]),
            "dynamic_draw_reuse": True,
            "past_only_causality": True,
            "reused_ohlc_frame": True,
            "unmatched_draw_rows": int(len(no_pairs)), "partial_pair_coverage_pct": float(partial.coverage_pct)}


def run(*, sources: list[str], epoch3_only: bool = False, past_only: bool = False) -> dict:
    if "second_wave" in sources and len(sources) != 1:
        raise ValueError("The second_wave profile must run separately to keep its outputs isolated")
    candidates, selected = _canonical_candidates(sources)
    if epoch3_only:
        selected = selected[selected.epoch_rank.eq(3)].copy()
    if selected.empty:
        raise ValueError("No selected primary/baseline or dynamic locked candidates in the requested epoch scope")
    signals = _signal_lookup()
    alt_keys = {
        (int(row.minutes), _utc(row.known)) for row in selected.itertuples(index=False)
        if row.asset == "ALT"
    }
    members = _member_lookup(alt_keys)
    tasks, event_meta = _build_task_rows(candidates, selected, signals, members)
    if tasks.empty:
        raise ValueError("No per-symbol matching tasks could be constructed")
    lower_touches, shocks = _ledger_controls()

    matches: list[dict] = []
    task_coverage_rows: list[dict] = []
    receipts: list[dict] = []
    tasks["month"] = tasks.decision.dt.strftime("%Y-%m")
    symbol_groups = tasks.groupby("symbol", sort=True)
    total_symbols = int(tasks.symbol.nunique())
    total_month_groups = int(tasks.groupby(["symbol", "month"], sort=True).ngroups)
    month_i = 0
    for symbol_i, (symbol, symbol_tasks) in enumerate(symbol_groups, 1):
        symbol_tasks = symbol_tasks.copy()
        month_values = sorted(symbol_tasks.month.unique())
        month_starts = [pd.Timestamp(f"{month}-01", tz="UTC") for month in month_values]
        last_month_end = pd.Timestamp(f"{month_values[-1]}-01", tz="UTC") + pd.DateOffset(months=1)
        max_w = int(symbol_tasks.minutes.max())
        max_x = int(symbol_tasks.wait_min.max())
        max_h = int(symbol_tasks.hold_hours.max())
        warmup = pd.Timedelta(minutes=max(max_w + max_x, 65) + 5)
        symbol_start = month_starts[0] - warmup
        symbol_end = last_month_end + pd.Timedelta(hours=max_h)
        full_frame, price_receipt = load_full_ohlc(
            str(symbol), symbol_start, symbol_end, needed_intervals=None, repair_missing=False
        )
        for month, group in symbol_tasks.groupby("month", sort=True):
            current_month_start = pd.Timestamp(f"{month}-01", tz="UTC")
            current_month_end = current_month_start + pd.DateOffset(months=1)
            slice_start = current_month_start - warmup
            slice_end = current_month_end + pd.Timedelta(hours=max_h)
            frame = full_frame.loc[(full_frame.index >= slice_start) & (full_frame.index <= slice_end)]
            rows, proofs = _process_symbol_month(
                group, lower_touches, shocks, past_only=past_only,
                price_frame=frame, price_receipt=price_receipt,
            )
            matches.extend(rows)
            for proof in proofs:
                task_coverage_rows.extend(proof.pop("task_coverage", []))
                proof["sources"] = sorted(group.source.astype(str).unique())
                proof["symbol_load_range"] = [symbol_start.isoformat(), symbol_end.isoformat()]
                proof["symbol_load_months"] = len(month_values)
                receipts.append(proof)
            month_i += 1
            if month_i % 25 == 0 or month_i == total_month_groups:
                print(f"matched {month_i}/{total_month_groups} symbol-months; symbol {symbol_i}/{total_symbols}; rows={len(matches)}", flush=True)
        del full_frame

    match_frame = pd.DataFrame(matches)
    if match_frame.empty:
        match_frame = pd.DataFrame(columns=["candidate_id", "decision", "symbol", "random_entry",
                                            "weight", "draw", "source", "rule_event_id"])
    task_coverage = pd.DataFrame(task_coverage_rows)
    if not task_coverage.empty:
        draw_counts = match_frame.groupby("rule_event_id").draw.nunique() if not match_frame.empty else pd.Series(dtype=int)
        event_meta = event_meta.copy()
        event_meta["status"] = np.where(event_meta.rule_event_id.isin(draw_counts.index), "has_random_matches", event_meta.status)
    fixed_paired = _paired_fixed(match_frame, event_meta)
    coverage = _coverage_table(event_meta, task_coverage, fixed_paired)
    if sources == ["second_wave"]:
        output_dir = CONTROL_DIR / "second_wave"
        if past_only:
            output_dir = output_dir / "past_only"
    else:
        output_dir = CONTROL_DIR / "past_only" if past_only else CONTROL_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    fixed_frame = match_frame[match_frame.source.eq("search")].copy()
    dynamic_frame = match_frame[match_frame.source.isin(DYNAMIC_SOURCES)].copy()
    if "search" in sources:
        temp = output_dir / "fixed_matches.parquet.tmp"
        fixed_frame.to_parquet(temp, index=False)
        temp.replace(output_dir / "fixed_matches.parquet")
        fixed_paired.to_csv(output_dir / "fixed_paired.csv.tmp", index=False)
        (output_dir / "fixed_paired.csv.tmp").replace(output_dir / "fixed_paired.csv")
    if "exits" in sources or "second_wave" in sources:
        temp = output_dir / "dynamic_matches.parquet.tmp"
        dynamic_frame.to_parquet(temp, index=False)
        temp.replace(output_dir / "dynamic_matches.parquet")

    old_coverage_path = output_dir / "coverage.csv"
    if old_coverage_path.is_file() and set(sources) != {"search", "exits"}:
        old_coverage = pd.read_csv(old_coverage_path)
        old_coverage = old_coverage[~old_coverage.source.isin(sources)] if "source" in old_coverage else old_coverage
        coverage = pd.concat([old_coverage, coverage], ignore_index=True, sort=False)
    coverage.to_csv(output_dir / "coverage.csv.tmp", index=False)
    (output_dir / "coverage.csv.tmp").replace(old_coverage_path)

    scope_path = output_dir / "out_of_scope_locked_candidates.csv"
    scope_rows = _scope_only_rows(candidates)
    if scope_path.is_file() and set(sources) != {"search", "exits"}:
        old_scope = pd.read_csv(scope_path)
        old_scope = old_scope[~old_scope.source.isin(sources)] if "source" in old_scope else old_scope
        scope_rows = pd.concat([old_scope, scope_rows], ignore_index=True, sort=False)
    scope_rows.to_csv(scope_path.with_suffix(".csv.tmp"), index=False)
    scope_path.with_suffix(".csv.tmp").replace(scope_path)

    receipts_path = output_dir / "price_source_receipts.json"
    if receipts_path.is_file() and set(sources) != {"search", "exits"}:
        old_receipts = json.loads(receipts_path.read_text())
        old_receipts = [r for r in old_receipts if not set(r.get("sources", [])) & set(sources)]
        receipts = old_receipts + receipts
    receipt_tmp = output_dir / "price_source_receipts.json.tmp"
    receipt_tmp.write_text(json.dumps(receipts, indent=2, ensure_ascii=False, default=str) + "\n")
    receipt_tmp.replace(receipts_path)

    manifest_path = output_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
    source_runs = manifest.get("source_runs", {})
    source_runs["+".join(sources)] = {
        "epoch3_only": bool(epoch3_only), "selected_rule_events": int(len(selected)),
        "symbol_month_tasks": int(total_month_groups), "symbols_loaded_once": int(total_symbols),
        "control_rows": int(len(match_frame)),
        "fixed_rows": int(len(fixed_frame)), "dynamic_rows": int(len(dynamic_frame)),
    }
    manifest.update({
        "source_runs": source_runs,
        "max_fixed_draws": MAX_FIXED_DRAWS,
        "max_dynamic_draws": MAX_DYNAMIC_DRAWS,
        "minimum_member_coverage_pct": 100*MIN_COVERAGE,
        "same_symbol_lower_touch_rule": "No lower first-touch open in [control_known-minutes, control_decision).",
        "matching_policy": "past_only_sensitivity" if past_only else "primary_symmetric_shock_guard",
        "shock_exclusion": ("Exclude only if a same-side 25+ ledger shock was already known in [random decision - 60m, random decision); also exclude controls whose hold interval overlaps the candidate actual hold interval. Future shocks are allowed."
                            if past_only else "Exclude controls within +/- (hold_hours + minutes + wait_min) of any completed 5m same-side 25+ first-touch shock known time."),
        "matching_strata": ["same symbol", "same UTC decision month", "same UTC 6h decision block", "same prior-1h realized-vol bucket [.25,.5,1,2,4]%", "same prior-1h trend sign before known", "same NY weekday at known and decision", "same observable 5m-bar fraction over W+X"],
        "price_data": "monday_breadth_opt_payoffs.load_full_ohlc(..., needed_intervals=None, repair_missing=False); once per symbol over selected months then sliced per month in memory; registered local/cache only, no REST.",
        "fixed_fees": "VectorBT fixed-control returns use existing two-taker-fill fees and are replayed from the already loaded local month matrix.",
        "dynamic_behavior": "Dynamic matches store only random entry coordinates and weights; no fixed-horizon VectorBT replay or second price read.",
    })
    (output_dir / "manifest.json.tmp").write_text(json.dumps(manifest, indent=2, ensure_ascii=False, default=str) + "\n")
    (output_dir / "manifest.json.tmp").replace(manifest_path)
    return manifest


def main() -> None:
    cli = argparse.ArgumentParser()
    cli.add_argument("--source", choices=("search", "exits", "second_wave", "all"), default="all")
    cli.add_argument("--epoch3-only", action="store_true", help="Prioritize the 2026 locked validation epoch only")
    cli.add_argument("--past-only", action="store_true", help="Run the past-only shock-exclusion sensitivity into controls/past_only/")
    cli.add_argument("--self-test", action="store_true", help="Run deterministic matcher fixtures")
    args = cli.parse_args()
    if args.self_test:
        print(json.dumps(_self_test(), ensure_ascii=False))
        return
    sources = ["search", "exits"] if args.source == "all" else [args.source]
    print(json.dumps(run(sources=sources, epoch3_only=args.epoch3_only, past_only=args.past_only), ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
