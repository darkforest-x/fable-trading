"""Rebuild the ten-coin Monday 1.618 trend-breakout account on OKX and Binance.

The Binance 1H input is the frozen, complete-H1 archive (with the official
February 2022 patch where present); its 12H bars are made only from complete
groups of twelve consecutive H1 bars. OKX uses the frozen native H1/12H files.
Signals are first weekly EMA-aligned closes outside the symbol's own completed
NY Monday 1.618 band. Entries are exact H1 opens after confirmation. All fills
and exits are produced by the existing VectorBT native path/account adapters.
"""
from __future__ import annotations

import argparse
import gc
import json
import math
from collections import Counter
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_binance_gap_repair as binance_patch
from yoyo.evaluation import cloud_close_binance_transfer as binance_source
from yoyo.evaluation import cloud_close_breakout_branch as breakout
from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_reward_paths as engine
from yoyo.evaluation import cloud_close_second_reclaim_paths as reclaim


ROOT = wallet.ROOT
EXP = ROOT / "experiments/active/exp-cloud-close-breakout-venue-20261109-v1"
CONFIG_PATH = EXP / "config.json"
HOUR = engine.HOUR_MS
STEP = engine.STEP_12H
DAY_90 = 90 * 24 * HOUR
FIELDS = ["open", "high", "low", "close"]
SYMBOLS = ("BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "LINK", "LTC", "AVAX")
VENUES = ("OKX", "Binance")
MODES = ("immediate", "delayed_12h")
PERIODS = {
    "all": (None, None),
    "dev": (None, "2025-01-01T00:00:00Z"),
    "2025": ("2025-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    "2026_ytd": ("2026-01-01T00:00:00Z", None),
}


def _json_safe(value):
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if pd.isna(value):
        return None
    return value


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(_json_safe(value), ensure_ascii=False, indent=2,
                               allow_nan=False) + "\n", encoding="utf-8")
    temp.replace(path)


def _validate_ohlc(frame: pd.DataFrame, symbol: str, venue: str, tf: str) -> None:
    if (frame.empty or not frame.index.is_unique or not frame.index.is_monotonic_increasing
            or not set(FIELDS).issubset(frame.columns)):
        raise ValueError(f"Invalid {venue} {symbol} {tf} input schema")
    values = frame[FIELDS].to_numpy(dtype=float)
    if (not np.isfinite(values).all() or np.any(values <= 0)
            or np.any(values[:, 2] > values[:, [0, 1, 3]].min(axis=1))
            or np.any(values[:, 1] < values[:, [0, 2, 3]].max(axis=1))):
        raise ValueError(f"Invalid {venue} {symbol} {tf} OHLC values")


def _aggregate_12h(hourly: pd.DataFrame) -> pd.DataFrame:
    """Aggregate only complete UTC 12H bars from consecutive hourly rows."""
    indexed = hourly[FIELDS].copy()
    indexed.index = pd.to_datetime(indexed.index.to_numpy(dtype=np.int64), unit="ms", utc=True)
    group = indexed.resample("12h", origin="epoch", label="left", closed="left")
    result = group.agg(open=("open", "first"), high=("high", "max"),
                       low=("low", "min"), close=("close", "last"), n=("open", "count"))
    result = result[result.n.eq(12)].drop(columns="n")
    result.index = pd.Index(result.index.as_unit("ms").asi8, name="time")
    # H1 timestamps are unique and UTC-hour aligned, so 12 rows in one aligned
    # bin are exactly the twelve consecutive hours required for a complete bar.
    return result


def _source_cfg() -> dict:
    path = ROOT / "experiments/active/exp-cloud-close-breakout-branch-20261109-v1/config.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _binance_path(symbol: str) -> tuple[Path, str]:
    ticker = symbol + "USDT"
    repaired = binance_patch.WORK / ticker / "quotes.npz"
    original = binance_source.WORK / ticker / "quotes.npz"
    if repaired.is_file():
        return repaired, "official_feb2022_patch_plus_frozen_binance_h1"
    if original.is_file():
        return original, "frozen_binance_h1"
    raise FileNotFoundError(f"No frozen Binance H1 quote archive for {ticker}")


def _load_inputs(config: dict) -> tuple[dict, dict, list[dict], dict]:
    cutoff = int(config["data"]["cutoff_ms"])
    source_config = _source_cfg()
    frames: dict[tuple[str, str], tuple[pd.DataFrame, pd.DataFrame]] = {}
    quotes: dict[tuple[str, str], tuple[np.ndarray, np.ndarray]] = {}
    coverage: list[dict] = []
    full_clocks: list[np.ndarray] = []
    per_symbol_first = []

    for symbol in SYMBOLS:
        p12, p1, okx_group = breakout._input_paths(symbol, source_config)
        bars12, bars1, _, okx_path12, okx_path1, _ = breakout._load_symbol(symbol, source_config)
        if okx_group == "retired_documented_h1":
            raise ValueError(f"Unexpected retired source in the fixed ten-coin panel: {symbol}")
        okx_clock_full = bars1.index.to_numpy(dtype=np.int64)
        okx_prices_full = bars1[FIELDS].to_numpy(dtype=float)
        _validate_ohlc(bars1, symbol, "OKX", "1H")
        if not np.equal(okx_clock_full % HOUR, 0).all():
            raise ValueError(f"OKX {symbol} H1 source is not UTC-hour aligned")
        full_clocks.append(okx_clock_full)
        okx_h1 = bars1.loc[bars1.index < cutoff, FIELDS].copy()
        okx_h12 = bars12.loc[(bars12.index + STEP) <= cutoff, FIELDS].copy()
        _validate_ohlc(okx_h1, symbol, "OKX", "1H used")
        _validate_ohlc(okx_h12, symbol, "OKX", "12H used")
        if not np.equal(okx_h12.index.to_numpy(dtype=np.int64) % STEP, 0).all():
            raise ValueError(f"OKX {symbol} 12H bars are not UTC aligned")
        frames[("OKX", symbol)] = (okx_h12, okx_h1)
        quotes[("OKX", symbol)] = (okx_h1.index.to_numpy(dtype=np.int64),
                                   okx_h1[FIELDS].to_numpy(dtype=float))

        bin_path, bin_source_kind = _binance_path(symbol)
        bin_clock_full, bin_prices_full = binance_patch.quotes(symbol)
        bin_clock_full = np.asarray(bin_clock_full, dtype=np.int64)
        bin_prices_full = np.asarray(bin_prices_full, dtype=float)
        bin_h1_all = pd.DataFrame(bin_prices_full, index=pd.Index(bin_clock_full, name="time"),
                                  columns=FIELDS)
        if (not bin_clock_full.ndim == 1 or len(bin_clock_full) != len(bin_prices_full)
                or not np.equal(bin_clock_full % HOUR, 0).all()):
            raise ValueError(f"Binance {symbol} H1 source has invalid clock/shape")
        _validate_ohlc(bin_h1_all, symbol, "Binance", "1H")
        full_clocks.append(bin_clock_full)
        bin_h1 = bin_h1_all.loc[bin_h1_all.index < cutoff, FIELDS].copy()
        bin_h12 = _aggregate_12h(bin_h1)
        bin_h12 = bin_h12.loc[(bin_h12.index + STEP) <= cutoff, FIELDS].copy()
        _validate_ohlc(bin_h1, symbol, "Binance", "1H used")
        _validate_ohlc(bin_h12, symbol, "Binance", "12H aggregated")
        if not np.equal(bin_h12.index.to_numpy(dtype=np.int64) % STEP, 0).all():
            raise ValueError(f"Binance {symbol} derived 12H bars are not UTC aligned")
        frames[("Binance", symbol)] = (bin_h12, bin_h1)
        quotes[("Binance", symbol)] = (bin_h1.index.to_numpy(dtype=np.int64),
                                       bin_h1[FIELDS].to_numpy(dtype=float))

        for venue, clock, used_h1, used_h12, source_path_12, source_path_1, source_kind in (
            ("OKX", okx_clock_full, okx_h1, okx_h12, okx_path12, okx_path1, "native_OKX_12H_and_1H"),
            ("Binance", bin_clock_full, bin_h1, bin_h12,
             "complete_12H_aggregated_from_frozen_H1", str(bin_path.relative_to(ROOT)), bin_source_kind),
        ):
            diffs = np.diff(clock)
            gap_locs = np.flatnonzero(diffs != HOUR)
            coverage.append({
                "venue": venue, "symbol": symbol, "source_kind": source_kind,
                "source_path_12h": source_path_12, "source_path_1h": source_path_1,
                "full_h1_rows": int(len(clock)),
                "full_first_h1_open_ms": int(clock[0]), "full_last_h1_open_ms": int(clock[-1]),
                "full_last_h1_close_ms_exclusive": int(clock[-1] + HOUR),
                "used_h1_rows_before_cutoff": int(len(used_h1)),
                "used_h12_rows_complete_before_cutoff": int(len(used_h12)),
                "used_first_h1_open_ms": int(used_h1.index[0]),
                "used_last_h1_open_ms": int(used_h1.index[-1]),
                "h1_gap_transition_count": int(len(gap_locs)),
                "h1_missing_hour_count": int(sum(max(0, int(diffs[i] // HOUR) - 1) for i in gap_locs)),
                "first_gap_after_ms": int(clock[gap_locs[0]]) if len(gap_locs) else None,
                "source_history_start_is_listing_date": False,
            })
        per_symbol_first.append(max(int(okx_clock_full[0]), int(bin_clock_full[0])))

    common = full_clocks[0]
    for clock in full_clocks[1:]:
        common = np.intersect1d(common, clock, assume_unique=True)
    if len(common) == 0:
        raise ValueError("No exact joint H1 timestamp across the frozen two-venue panel")
    common_last = int(common[-1])
    observed_cutoff = common_last + HOUR
    if observed_cutoff != cutoff:
        raise ValueError(f"Frozen common cutoff {cutoff} differs from source-derived {observed_cutoff}")
    common_first = max(per_symbol_first)
    common_90d_start = common_first + DAY_90
    if common_90d_start != int(config["data"]["common_90d_account_start_ms"]):
        raise ValueError("Frozen 90-day common account start differs from inputs")
    clocks = {key: value[0] for key, value in quotes.items()}
    audit = {"common_last_h1_open_ms": common_last, "common_cutoff_exclusive_ms": observed_cutoff,
             "common_first_panel_h1_open_ms": common_first,
             "common_90d_account_start_ms": common_90d_start,
             "common_90d_required_hourly_bars": int(DAY_90 // HOUR),
             "hourly_clock_count": len(full_clocks)}
    return frames, quotes, coverage, {"audit": audit, "clocks": clocks}


def _has_common_90d(clocks: dict, symbol: str, time_ms: int) -> bool:
    """Require exact same-name H1 coverage on both venues over the prior 90 days."""
    first = int(time_ms) - DAY_90
    expected = int(DAY_90 // HOUR)
    for venue in VENUES:
        clock = clocks[(venue, symbol)]
        right = int(np.searchsorted(clock, time_ms))
        left = int(np.searchsorted(clock, first))
        if right >= len(clock) or int(clock[right]) != int(time_ms):
            return False
        if left >= right or int(clock[left]) != first or right-left != expected:
            return False
        if not np.equal(np.diff(clock[left:right+1]), HOUR).all():
            return False
    return True


def _build_signal_events(venue: str, symbol: str, bars12: pd.DataFrame,
                         bars1: pd.DataFrame, config: dict, source_paths: tuple[str, str],
                         common_clocks: dict, row_counter: list[int]) -> tuple[list[dict], dict, np.ndarray, np.ndarray]:
    q, bar_ms, prepared = engine._prepare_symbol(bars12, bars1)
    features = prepared["features"]
    calendar = reclaim._anchors(bars1, bars12, symbol)
    cutoff = int(config["data"]["cutoff_ms"])
    start = int(config["account"]["full_history_start_ms"])
    by_week: dict[str, dict] = {}
    trigger_bars = 0
    for i, row in enumerate(bars12.itertuples()):
        valid = breakout._valid_key(i, bars12, features, calendar, symbol, cutoff, start)
        if valid is None:
            continue
        window, anchor = valid
        key_open = int(bars12.index[i])
        confirmation = key_open + STEP
        close = float(row.close)
        ema20, ema50 = float(features["ema20"][i]), float(features["ema50"][i])
        side = (1 if close > float(anchor["upper"]) and ema20 > ema50 else
                -1 if close < float(anchor["lower"]) and ema20 < ema50 else 0)
        if not side:
            continue
        trigger_bars += 1
        week = str(window["week"])
        if week in by_week:
            continue
        row_counter[0] += 1
        event = breakout._event_template(
            symbol, "breakout", row_counter[0], key_open, confirmation, week, side, anchor,
            ema20, ema50, float(features["prior_atr_pct"][i]), *source_paths)
        event.update({
            "event_id": f"{venue}|{event['parent_id']}:immediate",
            "parent_id": f"{venue}|{event['parent_id']}",
            "bar_source": ("frozen_native_OKX_12H_and_1H" if venue == "OKX"
                           else "frozen_Binance_H1_then_complete_12H_aggregation"),
            "exchange": venue, "mode": "immediate",
            "anchor_lower": float(anchor["lower"]), "anchor_upper": float(anchor["upper"]),
            "entry_status": "pending",
        })
        # Keep stop anchored at the crossed Monday extension, and separately
        # expose actual entry-to-stop distance after binding the executable open.
        breakout._bind_exact_entry(event, q, bar_ms, start, cutoff)
        event["common_90d_eligible"] = bool(
            confirmation >= int(config["data"]["common_90d_account_start_ms"])
            and _has_common_90d(common_clocks, symbol, confirmation))
        by_week[week] = event

    events = list(by_week.values())
    audit = {
        "venue": venue, "symbol": symbol,
        "valid_ema_aligned_breakout_trigger_bars": int(trigger_bars),
        "first_weekly_breakout_events": int(len(events)),
        "common_90d_eligible_events": int(sum(e["common_90d_eligible"] for e in events)),
        "entry_status_counts": dict(Counter(e["entry_status"] for e in events)),
        "weekly_lock": "first valid EMA-aligned strict close beyond either band, same week shares long/short lock",
        "signals_within_source_cutoff": int(sum(int(e["confirmation_ms"]) < cutoff for e in events)),
    }
    return events, audit, q, bar_ms


def _delayed_events(parents: list[dict], q: np.ndarray, bar_ms: np.ndarray,
                    config: dict, row_counter: list[int]) -> list[dict]:
    cutoff = int(config["data"]["cutoff_ms"])
    delay = int(config["execution"]["delay_bars_12h"]) * STEP
    delayed = []
    for parent in parents:
        row_counter[0] += 1
        event = dict(parent)
        event.update(event_id=f"{parent['event_id']}:delayed12h",
                     source_row_number=row_counter[0], mode="delayed_12h",
                     entry_i=None, entry_ms=None, entry_price=None,
                     stop_price=None, risk_distance=None, risk_fraction=None,
                     initial_entry_ms=parent.get("entry_ms"),
                     initial_entry_price=parent.get("entry_price"),
                     initial_stop_price=parent.get("stop_price"),
                     stop_basis="rebase original immediate entry risk fraction at scheduled +12H H1 open")
        if parent.get("entry_i") is None or parent.get("risk_fraction") is None:
            event["entry_status"] = "no_delayed_parent_without_valid_immediate_entry"
            delayed.append(event)
            continue
        stamp = int(parent["confirmation_ms"]) + delay
        event["scheduled_entry_ms"] = stamp
        if stamp >= cutoff:
            event["entry_status"] = "no_delayed_entry_at_or_after_common_cutoff"
            delayed.append(event)
            continue
        idx = int(np.searchsorted(bar_ms, stamp))
        if idx >= len(bar_ms) or int(bar_ms[idx]) != stamp:
            event["entry_status"] = "no_exact_scheduled_delayed_h1_open"
            delayed.append(event)
            continue
        price = float(q[idx, 0])
        distance = price * float(parent["risk_fraction"])
        stop = price - int(parent["side"]) * distance
        if not (math.isfinite(price) and price > 0 and math.isfinite(distance) and distance > 0
                and stop > 0 and int(parent["side"]) * (price-stop) > 0):
            event["entry_status"] = "no_valid_delayed_risk_geometry"
            delayed.append(event)
            continue
        event.update(entry_i=idx, entry_ms=stamp, entry_price=price,
                     risk_distance=distance, risk_fraction=float(parent["risk_fraction"]),
                     stop_price=stop, entry_source="exact_H1_open_one_12H_after_signal",
                     entry_status="pending")
        delayed.append(event)
    return delayed


def _price_events(events: list[dict], q: np.ndarray, bar_ms: np.ndarray,
                  config: dict) -> tuple[list[dict], dict]:
    batch_size = int(config["execution"]["event_batch_size"])
    for event in events:
        if event.get("entry_i") is None:
            continue
        event["end_i"], event["path_upper_bound_reason"] = engine._safe_path_end(
            int(event["entry_i"]), int(event["side"]), q, bar_ms)
    rows, stats = engine._simulate_symbol(events, q, bar_ms, (0.0,), batch_size)
    by_id = {str(event["event_id"]): event for event in events}
    for row in rows:
        event = by_id[str(row["event_id"])]
        row["venue"] = event["exchange"]
        row["mode"] = event["mode"]
        row["common_90d_eligible"] = bool(event["common_90d_eligible"])
        row["initial_entry_ms"] = event.get("initial_entry_ms")
        row["initial_entry_price"] = event.get("initial_entry_price")
        row["initial_stop_price"] = event.get("initial_stop_price")
        row["scheduled_entry_ms"] = event.get("scheduled_entry_ms")
        row["reference_anchor"] = event.get("reference_anchor")
        row["anchor_lower"] = event.get("anchor_lower")
        row["anchor_upper"] = event.get("anchor_upper")
        row["actual_risk_distance"] = row.get("risk_distance")
        row["actual_risk_fraction"] = row.get("risk_fraction")
    return rows, stats


def _apply_path_status(events: list[dict], rows: list[dict]) -> None:
    status = {str(row["event_id"]): str(row["entry_status"]) for row in rows}
    for event in events:
        if str(event["event_id"]) in status:
            event["entry_status"] = status[str(event["event_id"])]


def _quote_loader(quotes: dict, venue: str):
    @lru_cache(maxsize=10)
    def load(symbol: str):
        return quotes[(venue, symbol)]
    return load


def _account(events: pd.DataFrame, quotes: dict, venue: str,
             start: int, end: int) -> tuple[dict, pd.DataFrame, pd.DataFrame, dict]:
    data = wallet.prepare(events, slots=9, start=int(start), end=int(end),
                          quote_loader=_quote_loader(quotes, venue))
    funded, equity = wallet.account(data, slip=0.0, exposure=1.0)
    metrics = {period: wallet.summarize(funded, equity, *bounds)
               for period, bounds in PERIODS.items()}
    expected_final = 1000.0 + float(funded.cash_pnl.sum())
    if not np.isclose(expected_final, equity.nav.iloc[-1], rtol=1e-8, atol=.005):
        raise ValueError("Venue account cash does not reconcile to final hourly NAV")
    meta = {"input_executed_events": int(len(events)), "admitted": int(len(data["selected"])),
            "funded": int(len(funded)), "rejected": data["skips"],
            "unquoted_position_hours": int(data["gap_marks"]),
            "cash_reconciliation_error_usdt": float(expected_final-equity.nav.iloc[-1])}
    return metrics, funded, equity, meta


def _run_account_set(paths: pd.DataFrame, config: dict, quotes: dict) -> tuple[pd.DataFrame, dict]:
    full_start = int(config["account"]["full_history_start_ms"])
    common_start = int(config["data"]["common_90d_account_start_ms"])
    end = int(config["data"]["cutoff_ms"])
    metric_rows: list[dict] = []
    account_summary = {}
    for venue in VENUES:
        for mode in MODES:
            pool = paths[(paths.venue == venue) & (paths.mode == mode)
                         & paths.status.eq("executed")].copy()
            for sample, start in (("full_observable", full_start), ("common_90d", common_start)):
                selected = pool if sample == "full_observable" else pool[pool.common_90d_eligible.astype(bool)]
                if (selected.entry_ms < start).any() or (selected.entry_ms >= end).any():
                    raise ValueError(f"{venue}/{mode}/{sample}: entry outside account bounds")
                metrics, funded, equity, meta = _account(selected, quotes, venue, start, end)
                key = f"{venue}_{mode}_{sample}"
                account_summary[key] = {"metrics": metrics, "admission": meta,
                                        "starting_usdt": 1000.0, "slots": 9,
                                        "account_start_ms": start, "account_end_exclusive_ms": end}
                for period, values in metrics.items():
                    metric_rows.append({"venue": venue, "mode": mode, "sample": sample,
                                        "period": period, **values})
                funded.to_csv(EXP / f"funded_{key}.csv.gz", index=False, compression="gzip")
                equity.to_csv(EXP / f"hourly_{key}.csv.gz", index_label="hour_end_utc",
                              compression="gzip")
                print(f"account {key}: funded={len(funded)} final={metrics['all']['final_usdt']:.6f} "
                      f"cash_pnl={funded.cash_pnl.sum():.6f} dd_bound={metrics['all']['dd_bound_pct']:.4f}%",
                      flush=True)
                del funded, equity
                gc.collect()
    return pd.DataFrame(metric_rows), account_summary


def preflight(config_path: Path = CONFIG_PATH) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if tuple(config["data"]["symbols"]) != SYMBOLS:
        raise ValueError("Frozen ten-coin panel changed")
    frames, quotes, coverage, meta = _load_inputs(config)
    result = {**meta["audit"], "coverage_rows": len(coverage),
              "source_pairs": len(frames), "loaded_hourly_quotes": len(quotes),
              "venue_gap_counts": {v: sum(r["h1_gap_transition_count"] for r in coverage if r["venue"] == v)
                                   for v in VENUES}}
    print(json.dumps(_json_safe(result), indent=2, ensure_ascii=False), flush=True)
    return result


def run(config_path: Path = CONFIG_PATH) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    experiment_dir = config_path.parent
    if experiment_dir.name != config["experiment_id"]:
        raise ValueError("Unexpected experiment id")
    if (experiment_dir / config["outputs"]["summary_json"]).exists():
        raise FileExistsError("Refusing to overwrite a completed cross-venue run")
    if tuple(config["data"]["symbols"]) != SYMBOLS:
        raise ValueError("Frozen ten-coin panel changed")
    if not np.isclose(float(config["fees"]["market_taker_rate"]), wallet.TAKER, rtol=0, atol=1e-12):
        raise ValueError("Configured market fee does not match the frozen 5bp account adapter")

    frames, quotes, coverage, source_audit = _load_inputs(config)
    pd.DataFrame(coverage).to_csv(EXP / config["outputs"]["source_coverage_csv"], index=False)
    all_paths: list[dict] = []
    candidate_rows: list[dict] = []
    signal_audit = {}
    row_counter = [0]
    for venue in VENUES:
        for symbol in SYMBOLS:
            bars12, bars1 = frames[(venue, symbol)]
            source_path = next(r for r in coverage if r["venue"] == venue and r["symbol"] == symbol)
            signals, audit, q, bar_ms = _build_signal_events(
                venue, symbol, bars12, bars1, config,
                (source_path["source_path_12h"], source_path["source_path_1h"]),
                source_audit["clocks"], row_counter)
            immediate_rows, immediate_exec = _price_events(signals, q, bar_ms, config)
            _apply_path_status(signals, immediate_rows)
            delayed = _delayed_events(signals, q, bar_ms, config, row_counter)
            delayed_rows, delayed_exec = _price_events(delayed, q, bar_ms, config)
            _apply_path_status(delayed, delayed_rows)
            all_paths.extend(immediate_rows)
            all_paths.extend(delayed_rows)
            for event in [*signals, *delayed]:
                candidate_rows.append({key: event.get(key) for key in (
                    "event_id", "parent_id", "source_row_number", "exchange", "mode", "symbol", "week",
                    "side", "key_open_ms", "confirmation_ms", "scheduled_entry_ms", "entry_ms",
                    "entry_price", "initial_entry_ms", "initial_entry_price", "initial_stop_price",
                    "reference_anchor", "anchor_lower", "anchor_upper", "stop_price",
                    "risk_distance", "risk_fraction", "common_90d_eligible", "entry_status", "stop_basis",
                    "source_path_12h", "source_path_1h")})
            audit["immediate_vectorbt"] = immediate_exec
            audit["delayed_vectorbt"] = delayed_exec
            audit["entry_status_counts"] = dict(Counter(e["entry_status"] for e in signals))
            audit["delayed_entry_status_counts"] = dict(Counter(e["entry_status"] for e in delayed))
            signal_audit[f"{venue}/{symbol}"] = audit
            print(f"prepared {venue} {symbol}: signals={len(signals)} "
                  f"immediate={sum(r.get('status') == 'executed' for r in immediate_rows)} "
                  f"delayed={sum(r.get('status') == 'executed' for r in delayed_rows)} "
                  f"common90={audit['common_90d_eligible_events']}", flush=True)
            del q, bar_ms
            gc.collect()

    paths = pd.DataFrame(all_paths)
    if paths.empty or paths.duplicated(["venue", "mode", "event_id"]).any():
        raise ValueError("Empty or duplicated native path identity")
    paths.to_csv(EXP / config["outputs"]["paths_csv_gz"], index=False, compression="gzip")
    candidates = pd.DataFrame(candidate_rows)
    candidates.to_csv(EXP / config["outputs"]["candidate_audit_csv_gz"], index=False,
                      compression="gzip")

    metrics, accounts = _run_account_set(paths, config, quotes)
    metrics.to_csv(EXP / config["outputs"]["account_metrics_csv"], index=False)
    summary = {
        "experiment_id": experiment_dir.name,
        "engine": "VectorBT native path and shared-cash account; no alternate fill simulator",
        "symbols": list(SYMBOLS), "venues": list(VENUES), "modes": list(MODES),
        "account": config["account"], "data": config["data"], "fees": config["fees"],
        "source_audit": source_audit["audit"], "source_coverage_rows": len(coverage),
        "first_weekly_signal_events": {
            venue: sum(v["first_weekly_breakout_events"] for k, v in signal_audit.items()
                       if k.startswith(venue + "/")) for venue in VENUES},
        "path_counts": {venue: {
            mode: {"all_candidates": int(((paths.venue == venue) & (paths.mode == mode)).sum()),
                   "executed": int(((paths.venue == venue) & (paths.mode == mode)
                                    & paths.status.eq("executed")).sum()),
                   "common_90d_executed": int(((paths.venue == venue) & (paths.mode == mode)
                                                & paths.status.eq("executed")
                                                & paths.common_90d_eligible.astype(bool)).sum()),
                   "exit_reasons": paths[(paths.venue == venue) & (paths.mode == mode)
                                         & paths.status.eq("executed")].exit_reason.value_counts().to_dict()}
            for mode in MODES} for venue in VENUES},
        "accounts": accounts, "symbol_audit": signal_audit,
        "limitations": [
            "The Binance cache stores complete H1 candles assembled from frozen 5m archives; 12H is aggregated only from complete consecutive H1 bars. OKX uses native 12H and H1 inputs.",
            "The 2026-09-23 04:00 UTC exclusive end is the last common observed H1 close across all ten symbols and both venues; later OKX and partial Binance bars are excluded.",
            "Source-first timestamps are coverage bounds, not exchange listing dates. The fixed ten-coin panel does not include retired contracts; gaps are preserved and the native engine closes at the first observable gap open.",
            "Binance historical account fee tier was unavailable in the frozen research sources. Both venues use the verified OKX 5bp single-side taker convention as a common cost mapping; no Binance-specific fee claim is made.",
            "Funding, market impact, lot/tick rounding, and exchange-specific leverage or maintenance rules are omitted. These are unlevered fully cash-collateralized simulations.",
            "The one-12H-delayed arm uses the existing fixed-delay convention: retain the immediate signal's relative stop fraction and re-anchor that distance at the scheduled delayed H1 open; no future survival or outcome filter is applied.",
        ],
    }
    _write_json(EXP / config["outputs"]["summary_json"], summary)
    print(metrics[metrics.period.eq("all")].to_string(index=False), flush=True)
    return summary


def main(argv=None) -> None:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--preflight", action="store_true")
    group.add_argument("--run", action="store_true")
    args = parser.parse_args(argv)
    if args.preflight:
        preflight()
    else:
        run()


if __name__ == "__main__":
    main()
