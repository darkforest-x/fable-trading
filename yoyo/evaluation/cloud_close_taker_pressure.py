"""Test native Binance taker-flow opposition at confirmed cloud 12H keys.

The feature uses only Binance USD-M executed 15m kline fields from the frozen
12-major flow pilot. For each signal, it requires the exact 48 bars in that
signal's confirmed 12H key candle and computes
``(2 * taker_buy_base_volume - total_base_volume) / total_base_volume``.
Every Binance signal and control uses its own ``entry_ms`` as confirmation
and ``entry_ms - 12h`` as key open. No bar whose close is after confirmation is
read. Missing, malformed, zero-total-volume,
and buy-volume-over-total windows remain in the audit but cannot qualify.

Prices, stops, fills, fees, and the 9-slot, 2/3-at-2R account are reused from
the frozen two-leg path builder and VectorBT capacity runner; this module does
not simulate fills.
"""
from __future__ import annotations

import argparse
import gc
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_binance_gap_repair as binance
from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_reward_paths as engine
from yoyo.evaluation import cloud_close_volatility_expansion as volatility
from yoyo.evaluation import monday_1618_flow as flow


ROOT = wallet.ROOT
EXP = ROOT / "experiments/active/exp-cloud-close-taker-pressure-20261109-v1"
FLOW_EXP = ROOT / "experiments/active/exp-monday-1618-flow-20261023-v1"
FLOW_INPUT = FLOW_EXP / "inputs"
STEP_15M = 15 * 60_000
STEP_12H = 12 * 60 * 60_000
WINDOW_BARS = 48
FLOW_START_MS = pd.Timestamp("2022-09-01T00:00:00Z").value // 1_000_000
FLOW_END_EXCLUSIVE_MS = pd.Timestamp("2026-10-01T00:00:00Z").value // 1_000_000
SLOTS = 9
FRACTION_2R = 2 / 3
PANEL = (
    "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK",
    "LTC", "DOT", "TRX",
)
PERIODS = ("all", "dev", "2025", "2026")


def _missing(value: object) -> bool:
    return value is None or pd.isna(value)


def _decision_window(row: object) -> tuple[int | None, int | None, str | None]:
    """Use each Binance event's own executable close/open clock."""
    if _missing(row.entry_ms):
        return None, None, "missing_event_clock"
    confirmation_ms = int(row.entry_ms)
    key_open_ms = confirmation_ms - STEP_12H
    if (key_open_ms % STEP_12H or confirmation_ms % STEP_12H
            or confirmation_ms - key_open_ms != STEP_12H):
        return key_open_ms, confirmation_ms, "invalid_12h_clock"
    return key_open_ms, confirmation_ms, None


def _measure_window(
    clock_ms: np.ndarray,
    volume: np.ndarray,
    taker_buy: np.ndarray,
    key_open_ms: int,
    confirmation_ms: int,
    side: int,
) -> dict:
    """Measure a complete 48-bar native window, with no missing-data repair."""
    if side not in (-1, 1):
        return _empty_feature("invalid_side")
    if len(clock_ms) != len(volume) or len(clock_ms) != len(taker_buy):
        return _empty_feature("invalid_flow_array_lengths")
    expected = key_open_ms + np.arange(WINDOW_BARS, dtype=np.int64) * STEP_15M
    locations = np.searchsorted(clock_ms, expected)
    in_bounds = locations < len(clock_ms)
    if not in_bounds.all():
        return _empty_feature("missing_or_gapped_15m", observed=int(in_bounds.sum()))
    selected_clock = clock_ms[locations]
    if not np.array_equal(selected_clock, expected):
        return _empty_feature("missing_or_gapped_15m",
                              observed=int(np.count_nonzero(selected_clock == expected)))

    # This equality also documents the causal bar boundary: all 48 bars open
    # at or after key_open and their last close is exactly confirmation_ms.
    if expected[0] != key_open_ms or expected[-1] + STEP_15M != confirmation_ms:
        return _empty_feature("window_boundary_mismatch", observed=WINDOW_BARS)
    vol = volume[locations]
    buy = taker_buy[locations]
    if not np.isfinite(vol).all() or not np.isfinite(buy).all():
        return _empty_feature("unknown_native_flow", observed=WINDOW_BARS)
    if (vol < 0).any() or (buy < 0).any():
        return _empty_feature("negative_native_flow", observed=WINDOW_BARS)
    tolerance = np.maximum(1e-12, vol * 1e-12)
    if (buy - vol > tolerance).any():
        return _empty_feature("taker_buy_exceeds_volume", observed=WINDOW_BARS)
    total_volume = float(vol.sum())
    total_buy = float(buy.sum())
    if not np.isfinite(total_volume) or not np.isfinite(total_buy):
        return _empty_feature("nonfinite_aggregate", observed=WINDOW_BARS)
    if total_volume <= 0:
        return _empty_feature("zero_total_volume", observed=WINDOW_BARS)
    imbalance = (2.0 * total_buy - total_volume) / total_volume
    side_adjusted = side * imbalance
    opposing = bool(side_adjusted < 0.0)
    return {
        "flow_status": "valid_opposing" if opposing else "valid_not_opposing",
        "flow_observed_15m": WINDOW_BARS,
        "flow_total_base_volume": total_volume,
        "flow_taker_buy_base_volume": total_buy,
        "flow_imbalance": float(imbalance),
        "flow_side_adjusted_imbalance": float(side_adjusted),
        "flow_opposes_side": opposing,
    }


def _empty_feature(status: str, *, observed: int = 0) -> dict:
    return {
        "flow_status": status,
        "flow_observed_15m": int(observed),
        "flow_total_base_volume": np.nan,
        "flow_taker_buy_base_volume": np.nan,
        "flow_imbalance": np.nan,
        "flow_side_adjusted_imbalance": np.nan,
        "flow_opposes_side": False,
    }


def _load_native_flow(symbol: str) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
    """Load only the frozen Vision 15m klines; no original OHLCV proxy fallback."""
    bars = flow.klines(FLOW_INPUT, f"{symbol}USDT")
    if bars.empty:
        empty = np.empty(0, dtype=np.int64)
        return empty, np.empty(0), np.empty(0), {
            "rows": 0, "first_ms": None, "last_ms": None, "source": "no_native_rows",
        }
    if not bars.index.is_unique or not bars.index.is_monotonic_increasing:
        raise ValueError(f"{symbol}: flow loader did not return a unique sorted 15m clock")
    # pandas 3 may infer a microsecond index from the second-aligned source.
    # Convert units explicitly before treating asi8 as milliseconds.
    clock_ms = bars.index.tz_convert("UTC").as_unit("ms").asi8
    if (clock_ms % STEP_15M).any():
        raise ValueError(f"{symbol}: native 15m bars are off the UTC grid")
    volume = bars["volume"].to_numpy(dtype=np.float64)
    taker_buy = bars["taker_buy"].to_numpy(dtype=np.float64)
    return clock_ms, volume, taker_buy, {
        "rows": int(len(bars)),
        "first_ms": int(clock_ms[0]),
        "last_ms": int(clock_ms[-1]),
        "source": "Binance Vision USD-M klines15m raw monthly archives",
    }


def event_features(paths: pd.DataFrame, flow_by_symbol: dict[str, tuple]) -> pd.DataFrame:
    """Return one causal flow audit row per frozen 12H path event."""
    events = paths.loc[paths.tp_r.eq(0)].copy()
    if events.event_id.duplicated().any():
        raise ValueError("The tp_r=0 path must contain one row per event_id")
    output = []
    for row in events.itertuples(index=False):
        key_open_ms, confirmation_ms, clock_error = _decision_window(row)
        if row.symbol not in PANEL:
            feature = _empty_feature("outside_fixed_panel")
        elif clock_error is not None:
            feature = _empty_feature(clock_error)
        elif key_open_ms < FLOW_START_MS or confirmation_ms > FLOW_END_EXCLUSIVE_MS:
            feature = _empty_feature("outside_common_observation_window")
        else:
            series = flow_by_symbol.get(row.symbol)
            if series is None or not len(series[0]):
                feature = _empty_feature("no_native_history")
            else:
                feature = _measure_window(
                    series[0], series[1], series[2], key_open_ms,
                    confirmation_ms, int(row.side),
                )
        output.append({
            "event_id": row.event_id,
            "parent_id": row.parent_id,
            "symbol": row.symbol,
            "is_control": bool(row.is_control),
            "side": int(row.side),
            "entry_ms": None if _missing(row.entry_ms) else int(row.entry_ms),
            "flow_key_open_ms": key_open_ms,
            "flow_confirmation_ms": confirmation_ms,
            **feature,
        })
    result = pd.DataFrame(output)
    if result.event_id.duplicated().any() or len(result) != len(events):
        raise ValueError("Flow feature join is not one-to-one with frozen event ids")
    return result


def _event_sets(events: pd.DataFrame) -> dict[str, set[str]]:
    """Build coverage and independently clocked actual/control event cohorts."""
    actual = events.loc[~events.is_control].copy()
    controls = events.loc[events.is_control].copy()
    controls = controls.loc[controls.parent_id.isin(actual.parent_id)]

    actual_valid = actual.loc[actual.flow_status.str.startswith("valid_")]
    control_valid = controls.loc[controls.flow_status.str.startswith("valid_")
                                 & controls.parent_id.isin(actual_valid.parent_id)]
    available_pairs = set(actual_valid.parent_id) & set(control_valid.parent_id)

    actual_opposing = actual_valid.loc[actual_valid.flow_opposes_side]
    control_opposing = controls.loc[
        controls.flow_status.str.startswith("valid_")
        & controls.flow_opposes_side
        & controls.parent_id.isin(actual_valid.parent_id)
    ]
    opposing_pairs = set(actual_opposing.parent_id) & set(control_opposing.parent_id)

    return {
        "available_actual": set(actual_valid.event_id),
        "available_control": set(control_valid.event_id),
        "available_paired_actual": set(actual_valid.loc[actual_valid.parent_id.isin(available_pairs), "event_id"]),
        "available_paired_control": set(control_valid.loc[control_valid.parent_id.isin(available_pairs), "event_id"]),
        "opposing_actual": set(actual_opposing.event_id),
        "opposing_control": set(control_opposing.event_id),
        "opposing_paired_actual": set(actual_opposing.loc[actual_opposing.parent_id.isin(opposing_pairs), "event_id"]),
        "opposing_paired_control": set(control_opposing.loc[control_opposing.parent_id.isin(opposing_pairs), "event_id"]),
    }


def _empty_metrics() -> dict[str, dict]:
    names = ("final_usdt", "multiple", "dd_mark_pct", "dd_bound_pct", "trades",
             "win_pct", "payoff", "payoff_usdt", "median_net_pct")
    return {period: {name: None for name in names} for period in PERIODS}


def _run_account(events: pd.DataFrame, slip: float = 0.0) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    if events.empty:
        return _empty_metrics(), pd.DataFrame(), pd.DataFrame()
    metrics, trades, equity = volatility.fund("Binance", events, slip)
    return metrics, trades, equity


def _gate(row: dict, gate_config: dict) -> bool:
    return bool(
        row.get("win_pct") is not None
        and row["win_pct"] >= gate_config["win_pct_min"]
        and (row.get("payoff") or 0.0) >= gate_config["payoff_min"]
        and (row.get("payoff_usdt") or 0.0) >= gate_config["payoff_usdt_min"]
        and row.get("dd_bound_pct") is not None
        and row["dd_bound_pct"] <= gate_config["dd_bound_pct_max"]
        and row.get("trades", 0) >= gate_config["trades_min"]
    )


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    temp.replace(path)


def _assert_builder_committed(config_path: Path) -> None:
    source_rel = str(Path(__file__).resolve().relative_to(ROOT))
    config_rel = str(config_path.resolve().relative_to(ROOT))
    for path in (source_rel, config_rel):
        subprocess.run(["git", "ls-files", "--error-unmatch", path], cwd=ROOT,
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    changed = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", source_rel, config_rel],
                             cwd=ROOT).returncode
    if changed:
        raise RuntimeError("Commit the frozen builder and config before the official run")


def _validate_config(config: dict) -> None:
    """Reject config/code drift before reading the frozen paths or raw bars."""
    universe = config["universe"]
    account = config["account"]
    fees = config["fees_and_gates"]
    if tuple(universe["symbols"]) != PANEL:
        raise ValueError("Config symbols differ from the frozen twelve-major panel")
    if (ROOT / universe["data_root"]).resolve() != (FLOW_INPUT / "klines15m").resolve():
        raise ValueError("Config flow source does not resolve to the frozen flow-pilot 15m archives")
    common = universe["common_event_window_utc"]
    if (pd.Timestamp(common["start_inclusive"]).value // 1_000_000 != FLOW_START_MS
            or pd.Timestamp(common["end_exclusive"]).value // 1_000_000 != FLOW_END_EXCLUSIVE_MS):
        raise ValueError("Config common observation window differs from the frozen 15m panel")
    if (account["slots"] != SLOTS or account["two_leg_split"]
            != "2/3 exits at the original 2R resting limit; 1/3 follows the original cloud exit"):
        raise ValueError("Config account sizing differs from the frozen two-thirds cloud account")
    if account["entry_priority"] != "low_prior_atr":
        raise ValueError("Config entry priority differs from the frozen low-prior-ATR account")
    if "cloud_close_binance_gap_repair.assembled" not in config["parent_paths"]["source"]:
        raise ValueError("Config paths must use the corrected native Binance account builder")
    if not np.isclose(float(fees["frozen_path_taker_fee_per_fill"]), wallet.TAKER):
        raise ValueError("Config taker fee differs from frozen paths")
    if not np.isclose(float(fees["frozen_path_resting_maker_fee_per_fill"]), engine.MAKER):
        raise ValueError("Config maker fee differs from frozen paths")
    if (config["factor"]["window"] != "Exactly 48 continuous UTC 15m bars in [key_open_ms, confirmation_ms); the final bar closes exactly at confirmation_ms."
            or config["factor"]["formula"] != "imbalance=(2*sum(taker_buy_volume)-sum(total_volume))/sum(total_volume); qualify iff side*imbalance<0"):
        raise ValueError("Config factor definition differs from the implemented feature")


def _coverage_table(features: pd.DataFrame) -> pd.DataFrame:
    clocks = pd.to_datetime(features.flow_confirmation_ms, unit="ms", utc=True, errors="coerce")
    data = features.assign(year=clocks.dt.year, month=clocks.dt.strftime("%Y-%m"))
    return (data.groupby(["symbol", "year", "month", "is_control", "flow_status"],
                         dropna=False).size().rename("events").reset_index())


def run(config_path: Path = EXP / "config.json") -> None:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    _validate_config(config)
    _assert_builder_committed(config_path)
    if (EXP / "summary.json").exists():
        raise FileExistsError(f"Frozen results already exist at {EXP}")

    # Reuse the corrected Binance 12H V1.7 paths; signal and control rows each
    # keep their own executable 12H confirmation/open timestamp.
    paths = binance.assembled(set(binance.patch.affected_tickers()))
    if not paths.eligible.all():
        raise ValueError("Binance source returned paths outside own-time rank eligibility")
    if "entry_order" not in paths or paths.entry_order.isna().any():
        raise ValueError("Corrected Binance paths do not retain low-prior-ATR entry priority")
    paths = paths.loc[paths.symbol.isin(PANEL)].copy()
    if paths.empty:
        raise ValueError("No 90-day eligible paths intersect the twelve-symbol flow panel")

    symbols = list(PANEL)
    flow_by_symbol: dict[str, tuple] = {}
    source_coverage = {}
    for symbol in symbols:
        clock, volume, buy, meta = _load_native_flow(symbol)
        flow_by_symbol[symbol] = (clock, volume, buy)
        source_coverage[symbol] = meta
        print(f"loaded {symbol} native 15m flow rows={meta['rows']}", flush=True)

    features = event_features(paths, flow_by_symbol)
    features.to_csv(EXP / "event_flow_screen.csv.gz", index=False)
    coverage = _coverage_table(features)
    coverage.to_csv(EXP / "flow_coverage_by_symbol_month.csv", index=False)
    paths = paths.merge(features[["event_id", "flow_status", "flow_opposes_side",
                                 "flow_imbalance", "flow_confirmation_ms"]],
                        on="event_id", how="left", validate="many_to_one")
    if paths.flow_status.isna().any():
        raise ValueError("Not all frozen path events received a flow audit row")

    cohorts = _event_sets(features)
    paths2 = paths.loc[paths.tp_r.isin([0, 2])].copy()
    account_specs = (
        "available_actual", "available_paired_actual", "available_paired_control",
        "opposing_actual", "opposing_control", "opposing_paired_actual",
        "opposing_paired_control",
    )
    rows = []
    counts = {}
    for scenario in account_specs:
        ids = cohorts[scenario]
        events = paths2.loc[paths2.event_id.isin(ids)].copy()
        if events.duplicated(["event_id", "tp_r"]).any():
            raise ValueError(f"{scenario}: duplicate event/tp leg")
        if not events.empty and not events.groupby("event_id").tp_r.apply(
                lambda x: set(x) == {0, 2}).all():
            raise ValueError(f"{scenario}: incomplete 0R/2R two-leg path")
        if not events.empty and not events.groupby("event_id").is_control.nunique().eq(1).all():
            raise ValueError(f"{scenario}: event contains mixed signal/control legs")
        metrics, trades, equity = _run_account(
            events, float(config["fees_and_gates"]["additional_slippage"])
        )
        counts[scenario] = {
            "events": int(events.event_id.nunique()),
            "paths": int(len(events)),
            "funded": int(len(trades)),
        }
        for period, values in metrics.items():
            rows.append({"scenario": scenario, "period": period, **values})
        if scenario in ("available_actual", "opposing_actual") and not trades.empty:
            trades.to_csv(EXP / f"funded_{scenario}.csv.gz", index=False)
            equity.to_csv(EXP / f"hourly_{scenario}.csv.gz", index_label="hour_end_utc")
        del metrics, trades, equity, events
        gc.collect()

    metrics = pd.DataFrame(rows)
    metrics.to_csv(EXP / "account_metrics.csv", index=False)
    gates = {}
    gate_config = config["fees_and_gates"]["gates"]
    for scenario in ("available_actual", "opposing_actual"):
        table = metrics.loc[metrics.scenario.eq(scenario)].set_index("period")
        gates[scenario] = {period: _gate(table.loc[period].to_dict(), gate_config)
                           for period in ("dev", "all")}
    status_counts = features.flow_status.value_counts(dropna=False).to_dict()
    panel_actual = features.loc[~features.is_control]
    panel_control = features.loc[features.is_control]
    pairing = {}
    for label, actual_key, control_key, pair_actual_key, pair_control_key in (
        ("flow_available", "available_actual", "available_control",
         "available_paired_actual", "available_paired_control"),
        ("opposing_factor", "opposing_actual", "opposing_control",
         "opposing_paired_actual", "opposing_paired_control"),
    ):
        actual_count = len(cohorts[actual_key])
        control_count = len(cohorts[control_key])
        pair_actual_count = len(cohorts[pair_actual_key])
        pair_control_count = len(cohorts[pair_control_key])
        pairing[label] = {
            "actual_events": actual_count,
            "control_events_independently_eligible": control_count,
            "paired_actual_events": pair_actual_count,
            "paired_control_events": pair_control_count,
            "unmatched_actual_events": actual_count - pair_actual_count,
            "unmatched_control_events": control_count - pair_control_count,
        }
    summary = {
        "config_id": config["id"],
        "symbols_loaded": symbols,
        "source_coverage": source_coverage,
        "input_events": {
            "eligible_panel_actual": int(len(panel_actual)),
            "eligible_panel_control": int(len(panel_control)),
            "panel_actual_parent_ids": int(panel_actual.parent_id.nunique()),
            "panel_parent_ids_with_control": int(
                len(set(panel_actual.parent_id) & set(panel_control.parent_id))
            ),
        },
        "flow_status_counts": {str(key): int(value) for key, value in status_counts.items()},
        "pairing": pairing,
        "scenario_counts": counts,
        "dev_and_all_gates": gates,
        "fees_and_paths": {
            "taker_per_fill": wallet.TAKER,
            "resting_maker_per_fill": engine.MAKER,
            "two_leg_paths": "frozen tp_r=0 and tp_r=2; all original fills and exit reasons retained",
            "runner": "cloud_close_volatility_expansion.fund('Binance') -> corrected Binance quotes -> cloud_close_scaleout.simulate -> VectorBT",
        },
        "limitations": [
            "Coverage is restricted to the fixed 12-major Binance USD-M 15m archive panel, 2022-09 through 2026-09.",
            "This tests recorded taker flow as an event-time association; it does not establish historical publication latency.",
            "Unavailable or anomalous flow is audited and excluded; no OHLCV proxy is used.",
        ],
        "coverage_by_symbol_month_csv": str((EXP / "flow_coverage_by_symbol_month.csv").relative_to(ROOT)),
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "training_eligible": False,
        "production_eligible": False,
    }
    _write_json(EXP / "audit.json", {
        "source": str(FLOW_INPUT.relative_to(ROOT)),
        "symbols": symbols,
        "source_coverage": source_coverage,
        "flow_status_counts": summary["flow_status_counts"],
        "pairing": pairing,
        "coverage_rows": int(len(coverage)),
        "flow_feature_rule": config["factor"],
    })
    _write_json(EXP / "summary.json", summary)
    print(json.dumps({"counts": counts, "gates": gates,
                      "flow_status_counts": summary["flow_status_counts"]},
                     ensure_ascii=False, indent=2), flush=True)


def self_check() -> None:
    """Synthetic contract checks for direction, completeness and bad flow."""
    start = 1_700_000_000_000
    # Make a valid 12H-aligned key for the clock validation in the integration
    # layer; the kernel itself tests exact 48-bar boundaries.
    start = start // STEP_12H * STEP_12H
    from types import SimpleNamespace

    own_control_key = start + 24 * STEP_12H
    control_row = SimpleNamespace(
        is_control=True,
        entry_ms=own_control_key + STEP_12H,
        key_open_ms=start,
        confirmation_ms=start + STEP_12H,
    )
    if _decision_window(control_row) != (
            own_control_key, own_control_key + STEP_12H, None):
        raise AssertionError("A control must use its own matched 12H clock")
    clock = start + np.arange(WINDOW_BARS, dtype=np.int64) * STEP_15M
    volume = np.full(WINDOW_BARS, 10.0)
    buy = np.full(WINDOW_BARS, 8.0)
    long_against_buy = _measure_window(clock, volume, buy, int(start),
                                       int(start + STEP_12H), 1)
    if long_against_buy["flow_status"] != "valid_not_opposing":
        raise AssertionError("Buy majority must not oppose a long")
    short_against_buy = _measure_window(clock, volume, buy, int(start),
                                        int(start + STEP_12H), -1)
    if short_against_buy["flow_status"] != "valid_opposing":
        raise AssertionError("Buy majority must oppose a short")
    sell = np.full(WINDOW_BARS, 2.0)
    long_against_sell = _measure_window(clock, volume, sell, int(start),
                                        int(start + STEP_12H), 1)
    if long_against_sell["flow_status"] != "valid_opposing":
        raise AssertionError("Sell majority must oppose a long")
    balanced = _measure_window(clock, volume, np.full(WINDOW_BARS, 5.0),
                               int(start), int(start + STEP_12H), 1)
    if balanced["flow_opposes_side"]:
        raise AssertionError("Balanced flow must not satisfy the strict inequality")
    gap = _measure_window(clock[:-1], volume[:-1], buy[:-1], int(start),
                          int(start + STEP_12H), -1)
    if gap["flow_status"] != "missing_or_gapped_15m":
        raise AssertionError("A missing 15m bar must invalidate the feature")
    zero = _measure_window(clock, np.zeros(WINDOW_BARS), np.zeros(WINDOW_BARS),
                           int(start), int(start + STEP_12H), -1)
    if zero["flow_status"] != "zero_total_volume":
        raise AssertionError("Zero total volume must invalidate the feature")
    over = buy.copy()
    over[0] = 10.1
    invalid = _measure_window(clock, volume, over, int(start),
                              int(start + STEP_12H), -1)
    if invalid["flow_status"] != "taker_buy_exceeds_volume":
        raise AssertionError("Taker-buy above total volume must invalidate the feature")
    unknown_buy = buy.copy()
    unknown_buy[3] = np.nan
    unknown = _measure_window(clock, volume, unknown_buy, int(start),
                              int(start + STEP_12H), -1)
    if unknown["flow_status"] != "unknown_native_flow":
        raise AssertionError("Proxy/unknown flow must not qualify")
    cohort_fixture = pd.DataFrame([
        {"event_id": "real-1", "parent_id": "p1", "is_control": False,
         "flow_status": "valid_opposing", "flow_opposes_side": True},
        {"event_id": "control-1", "parent_id": "p1", "is_control": True,
         "flow_status": "valid_not_opposing", "flow_opposes_side": False},
        {"event_id": "real-2", "parent_id": "p2", "is_control": False,
         "flow_status": "valid_not_opposing", "flow_opposes_side": False},
        {"event_id": "control-2", "parent_id": "p2", "is_control": True,
         "flow_status": "valid_opposing", "flow_opposes_side": True},
    ])
    cohorts = _event_sets(cohort_fixture)
    if cohorts["opposing_actual"] != {"real-1"} or cohorts["opposing_control"] != {"control-2"}:
        raise AssertionError("Controls must be screened independently on their own flow clock")
    if cohorts["opposing_paired_actual"] or cohorts["opposing_paired_control"]:
        raise AssertionError("Paired factor comparison must require both sides to pass independently")
    # A bar opening exactly at confirmation is future to the completed key and
    # must not be requested as one of its 48 expected rows.
    after = np.r_[clock, start + STEP_12H]
    after_volume = np.r_[volume, 1e12]
    after_buy = np.r_[buy, 1e12]
    unchanged = _measure_window(after, after_volume, after_buy, int(start),
                                int(start + STEP_12H), -1)
    if unchanged["flow_imbalance"] != short_against_buy["flow_imbalance"]:
        raise AssertionError("A confirmation-time open leaked into the completed 12H feature")
    print("synthetic flow contract: PASS")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=EXP / "config.json")
    parser.add_argument("--self-check", action="store_true",
                        help="run only in-memory flow contract assertions")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return
    run(args.config.resolve())


if __name__ == "__main__":
    main()
