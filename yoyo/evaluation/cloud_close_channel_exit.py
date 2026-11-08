"""Compare causal prior-channel close exits on the frozen OKX V1.7 ledger.

The only changed exit is the EMA trend exit. Channel conditions are formed
from completed native UTC12H bars and encoded into the frozen VectorBT path
builder's q[:, 5] flip field. Its existing order callback still prices every
entry, stop, resting 2R target, gap/stale liquidation and file-tail close.
"""
from __future__ import annotations

import argparse
import gc
import json
import math
import time
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
import vectorbt as vbt

from yoyo.evaluation import cloud_close_eligibility_repair as repair
from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_reward_paths as engine
from yoyo.evaluation import cloud_close_slots as capacity
from yoyo.evaluation import cloud_close_thirds as thirds

ROOT = wallet.ROOT
EXP = ROOT / "experiments/active/exp-cloud-close-channel-exit-20261109-v1"
CONFIG = EXP / "config.json"
THIRDS_EXP = ROOT / "experiments/active/exp-cloud-close-eligibility-repair-20261109-v1/thirds"
LOOKBACKS = (10, 20)
TP_RS = (0.0, 2.0)
FRACTION = 2.0 / 3.0
SLOTS = 9
INITIAL_CASH = 1000.0
EXPECTED_REAL = 11780
EXPECTED_CONTROL = 10816
EXPECTED_ORPHAN_CONTROLS_REMOVED = 17
EXPECTED_FUNDED_BASELINE = 1569
EXPECTED_BASELINE_FINAL = 8788.773972912815
PERIODS = {"all": (None, None), "dev": (None, "2025-01-01T00:00Z"),
           "2025": ("2025-01-01T00:00Z", "2026-01-01T00:00Z"),
           "2026": ("2026-01-01T00:00Z", None)}

EVENT_FIELDS = [
    "tp_r", "event_id", "id", "parent_id", "source_row_number", "symbol", "week",
    "side", "is_control", "rank", "cohort", "is_current", "was_top20", "bar_source",
    "key_open_ms", "confirmation_ms", "confirmed_bjt", "confirmation_12h_bar_present",
    "entry_ms", "entry_price", "stop_price", "risk_distance", "risk_fraction",
    "entry_status", "control_match_status", "control_confirmation_ms",
    "control_12h_open_ms", "control_atr_relative_difference", "control_prior_atr_pct",
    "control_match_reason", "stop_basis", "source_path_12h", "source_path_1h",
]


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
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(_json_safe(value), ensure_ascii=False,
                                    indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _read_config(path: Path = CONFIG) -> dict:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if cfg.get("experiment_id") != "exp-cloud-close-channel-exit-20261109-v1":
        raise ValueError("Unexpected channel-exit registration")
    if tuple(cfg["channel_exit"]["candidate_lookbacks_completed_utc12h"]) != LOOKBACKS:
        raise ValueError("Prespecified channel lengths changed")
    if (cfg["frozen_account"]["slots"], cfg["frozen_account"]["initial_cash_usdt"],
            cfg["frozen_account"]["fraction_at_2r"]) != (SLOTS, INITIAL_CASH, FRACTION):
        raise ValueError("Frozen cash account contract changed")
    return cfg


def _check_population(config: dict) -> pd.DataFrame:
    """Return the verified 0R/2R first-entry event pool with low-ATR priority."""
    paths = thirds.okx_paths()
    repair.check_parent_sets(paths)
    paths = paths[paths.tp_r.isin(TP_RS)].copy()
    if paths.duplicated(["event_id", "tp_r"]).any():
        raise ValueError("Duplicate frozen source paths")
    if not paths.groupby("event_id").size().eq(2).all():
        raise ValueError("Frozen source must contain exactly the 0R and 2R legs")
    if not paths.groupby("event_id").tp_r.apply(lambda x: set(x) == set(TP_RS)).all():
        raise ValueError("The two frozen target legs have different identities")

    real = paths[~paths.is_control]
    raw_controls = paths[paths.is_control]
    real_ids = set(real.event_id)
    real_parents = set(real.parent_id)
    orphan_controls = raw_controls[~raw_controls.parent_id.isin(real_parents)]
    paths = paths[~paths.is_control | paths.parent_id.isin(real_parents)].copy()
    control = paths[paths.is_control]
    control_ids = set(control.event_id)
    if len(real_ids) != EXPECTED_REAL or len(control_ids) != EXPECTED_CONTROL:
        raise ValueError(f"Expected {EXPECTED_REAL} real / {EXPECTED_CONTROL} paired controls, "
                         f"found {len(real_ids)} / {len(control_ids)}")
    if not set(control.parent_id).issubset(real_parents):
        raise ValueError("An unmatched control entered the eligible pool")
    if orphan_controls.event_id.nunique() != EXPECTED_ORPHAN_CONTROLS_REMOVED:
        raise ValueError("The rank-valid control orphan count changed from the frozen 17")
    paths.attrs["rank_eligible_orphan_control_events_removed"] = int(
        orphan_controls.event_id.nunique())

    source = pd.read_csv(wallet.SOURCE / "signals.csv", usecols=["prior_atr_pct"])
    source.index = pd.RangeIndex(1, len(source) + 1)
    joined_priority = paths.source_row_number.map(source.prior_atr_pct)
    if (paths.source_row_number.isna().any()
            or not paths.source_row_number.isin(source.index).all()):
        raise ValueError("Low-prior-ATR priority failed to join to every frozen parent row")
    priority = joined_priority.fillna(np.inf)
    paths["entry_order"] = priority.to_numpy()

    base_path = THIRDS_EXP / "account_metrics.csv"
    if not base_path.is_file():
        raise FileNotFoundError(f"Frozen first-entry baseline missing: {base_path}")
    baseline = pd.read_csv(base_path)
    baseline = baseline[(baseline.venue == "OKX") & baseline.scenario.eq("actual")]
    if set(baseline.period) != set(PERIODS) or baseline.period.duplicated().any():
        raise ValueError("Frozen baseline must contain one row per registered period")
    all_row = baseline[baseline.period.eq("all")].iloc[0]
    if (int(all_row.trades) != EXPECTED_FUNDED_BASELINE
            or not np.isclose(all_row.final_usdt, EXPECTED_BASELINE_FINAL, rtol=0, atol=.01)):
        raise ValueError("Frozen first-entry baseline no longer matches its registered account")
    summary = json.loads((THIRDS_EXP / "summary.json").read_text())
    if summary["counts"]["OKX"] != {"actual": EXPECTED_REAL, "pairs": EXPECTED_CONTROL}:
        raise ValueError("Frozen account opportunity counts changed")
    funded = pd.read_csv(THIRDS_EXP / "funded_OKX.csv.gz", usecols=["event_id"])
    if len(funded) != EXPECTED_FUNDED_BASELINE or funded.event_id.duplicated().any():
        raise ValueError("Frozen baseline funded roster is incomplete")
    return paths


def _event_metadata(event_ids: set[str]) -> dict[str, dict]:
    """Read only the frozen tp=0 source rows needed to retain full lineage."""
    frame = pd.read_csv(wallet.EXP / "paths.csv.gz", usecols=EVENT_FIELDS,
                        dtype={"symbol": str, "event_id": str, "parent_id": str})
    frame = frame[frame.tp_r.eq(0) & frame.event_id.isin(event_ids)]
    if frame.event_id.duplicated().any() or set(frame.event_id) != event_ids:
        raise ValueError("Frozen path metadata is not one-to-one with the eligible pool")
    return {str(r.event_id): r._asdict() for r in frame.itertuples(index=False)}


def _bool(value) -> bool:
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if pd.isna(value):
        return False
    return str(value).strip().lower() in {"1", "true", "yes"}


def channel_break_flags(bars12: pd.DataFrame, lookback: int) -> tuple[np.ndarray, np.ndarray, int]:
    """Strict close breakouts against the preceding N complete, contiguous bars.

    The current row's high/low are excluded from its channel. A discontinuous
    native 12H history cannot form a channel until N contiguous previous bars
    plus the just-completed current bar are available.
    """
    if lookback < 1:
        raise ValueError("Channel lookback must be positive")
    count = len(bars12)
    long_break = np.zeros(count, dtype=bool)
    short_break = np.zeros(count, dtype=bool)
    clock = bars12.index.to_numpy(dtype=np.int64)
    high = bars12.high.to_numpy(float)
    low = bars12.low.to_numpy(float)
    close = bars12.close.to_numpy(float)
    contiguous_skips = 0
    for i in range(lookback, count):
        if not np.all(np.diff(clock[i-lookback:i+1]) == engine.STEP_12H):
            contiguous_skips += 1
            continue
        previous_low = float(np.min(low[i-lookback:i]))
        previous_high = float(np.max(high[i-lookback:i]))
        long_break[i] = close[i] < previous_low
        short_break[i] = close[i] > previous_high
        if long_break[i] and short_break[i]:
            raise ValueError("A valid OHLC close cannot break both channel sides")
    return long_break, short_break, contiguous_skips


def _channel_q(bars12: pd.DataFrame, q: np.ndarray, bar_ms: np.ndarray,
               lookback: int) -> tuple[np.ndarray, dict]:
    """Encode completed channel breakouts in the existing q[:,5] flip field."""
    long_break, short_break, discontinuous_windows = channel_break_flags(bars12, lookback)
    channel_q = q.copy()
    # q[:,4] carries the native EMA cross level; NaN disables it. q[:,5] is
    # consumed by the frozen callback as a strict adverse sign for the event.
    channel_q[:, 4] = np.nan
    channel_q[:, 5] = 0.0
    time12 = bars12.index.to_numpy(dtype=np.int64)
    marker_counts = Counter()
    missing_after_tail = 0
    for i in np.flatnonzero(long_break | short_break):
        close_ms = int(time12[i] + engine.STEP_12H)
        h1_i = int(np.searchsorted(bar_ms, close_ms, side="left"))
        if h1_i >= len(bar_ms):
            missing_after_tail += 1
            continue
        side = 1 if long_break[i] else -1
        marker = -float(side)
        if channel_q[h1_i, 5] not in (0.0, marker):
            raise AssertionError("Conflicting channel exit markers at one H1 open")
        channel_q[h1_i, 5] = marker
        marker_counts["long" if side == 1 else "short"] += 1
        marker_counts["exact_h1_open" if bar_ms[h1_i] == close_ms else "next_observed_h1_open"] += 1
    return channel_q, {
        "lookback": lookback,
        "native_12h_long_breaks": int(long_break.sum()),
        "native_12h_short_breaks": int(short_break.sum()),
        "channel_markers_written": int(sum(v for k, v in marker_counts.items()
                                            if k in ("long", "short"))),
        "marker_fill_clock_counts": {k: int(v) for k, v in marker_counts.items()
                                     if k in ("exact_h1_open", "next_observed_h1_open")},
        "breaks_after_hourly_tail": missing_after_tail,
        "noncontiguous_native_windows_skipped": discontinuous_windows,
    }


def _build_event_dict(base: pd.Series, metadata: dict,
                      q: np.ndarray, bar_ms: np.ndarray) -> dict:
    event = dict(metadata)
    event.update(base.to_dict())
    event["symbol"] = str(base.symbol)
    event["event_id"] = str(base.event_id)
    event["parent_id"] = str(base.parent_id)
    event["source_row_number"] = int(base.source_row_number)
    event["side"] = int(base.side)
    event["is_control"] = bool(base.is_control)
    event["entry_ms"] = int(base.entry_ms)
    event["entry_price"] = float(base.entry_price)
    event["stop_price"] = float(base.stop_price)
    event["risk_distance"] = float(base.entry_price - base.stop_price) * int(base.side)
    event["risk_fraction"] = event["risk_distance"] / event["entry_price"]
    if event["risk_distance"] <= 0:
        raise ValueError(f"Stop is on the wrong side of entry: {event['event_id']}")
    start = int(np.searchsorted(bar_ms, event["entry_ms"], side="left"))
    if start >= len(bar_ms) or int(bar_ms[start]) != event["entry_ms"]:
        raise ValueError(f"Frozen exact entry H1 open missing: {event['event_id']}")
    if not np.isclose(q[start, 0], event["entry_price"], rtol=1e-9, atol=1e-12):
        raise ValueError(f"Frozen entry differs from the source H1 open: {event['event_id']}")
    if not np.isclose(abs(event["entry_price"] - event["stop_price"]),
                      event["risk_distance"], rtol=1e-9, atol=1e-12):
        raise ValueError(f"Risk distance mismatch: {event['event_id']}")
    event["entry_i"] = start
    event["entry_status"] = "executed"
    return event


def _channel_path_rows(population: pd.DataFrame, config: dict, *, lookbacks=LOOKBACKS,
                       batch_size: int | None = None, progress: bool = False) -> tuple[pd.DataFrame, dict]:
    event_rows = population[population.tp_r.eq(0)].copy()
    event_ids = set(event_rows.event_id.astype(str))
    metadata = _event_metadata(event_ids)
    engine_cfg_path = ROOT / config["population"]["parent_path_config"]
    engine_cfg = json.loads(engine_cfg_path.read_text(encoding="utf-8"))
    batch_size = int(batch_size or config["execution"]["event_batch_size"])
    results = []
    audits = {str(n): {"symbols": 0, "event_count": 0, "vectorbt": Counter(),
                       "channel": Counter(), "exit_reasons": Counter(),
                       "safe_path_bounds": Counter(), "symbols_detail": {}}
              for n in lookbacks}
    symbol_groups = list(event_rows.groupby("symbol", sort=True))
    for symbol_index, (symbol, frame) in enumerate(symbol_groups, start=1):
        was_top20 = _bool(metadata[str(frame.iloc[0].event_id)]["was_top20"])
        p12, p1, source_group = engine._choose_input(str(symbol), was_top20, engine_cfg)
        bars12 = engine._load_bars(p12, "12h")
        bars1 = engine._load_bars(p1, "1h")
        base_q, bar_ms, prepared = engine._prepare_symbol(bars12, bars1)
        if not len(base_q):
            raise ValueError(f"Missing frozen OHLC for eligible symbol {symbol}")
        symbol_detail = {"input_group": source_group, "bars12h": len(bars12),
                         "bars1h": len(bars1), "hourly_gaps": prepared["audit"]["hourly_gap_count"],
                         "stale_triggers": prepared["audit"]["stale_12h_trigger_count"]}
        for lookback in lookbacks:
            channel_q, ch_audit = _channel_q(bars12, base_q, bar_ms, int(lookback))
            executable = []
            priority_by_event = base_by_event(frame)
            for _, base in frame.iterrows():
                event = _build_event_dict(base, metadata[str(base.event_id)],
                                          channel_q, bar_ms)
                event["end_i"], upper_reason = engine._safe_path_end(
                    int(event["entry_i"]), int(event["side"]), channel_q, bar_ms)
                if upper_reason == "first_opposite_confirmed_12h_open":
                    upper_reason = "first_channel_breakout_open"
                event["path_upper_bound_reason"] = upper_reason
                executable.append(event)
            rows, vector = engine._simulate_symbol(executable, channel_q, bar_ms,
                                                   TP_RS, batch_size)
            for row in rows:
                reason = row["exit_reason"]
                if reason == "ema_flip_open":
                    # q[:,4] is disabled, so reason 4 can only represent our
                    # channel sentinel. This relabels provenance, not a fill.
                    row["exit_reason"] = "channel_breakout_open"
                    row["channel_confirm_ms"] = row["exit_bar_ms"]
                else:
                    row["channel_confirm_ms"] = None
                row["exit_model"] = f"prior_channel_{lookback}x12h"
                row["channel_lookback_12h"] = int(lookback)
                row["entry_order"] = float(priority_by_event.get(str(row["event_id"]), np.inf))
                row["source_input_group"] = source_group
            results.append(pd.DataFrame(rows))
            audit = audits[str(lookback)]
            audit["symbols"] += 1
            audit["event_count"] += len(executable)
            audit["vectorbt"].update(vector)
            for key in ("native_12h_long_breaks", "native_12h_short_breaks",
                        "channel_markers_written", "breaks_after_hourly_tail",
                        "noncontiguous_native_windows_skipped"):
                audit["channel"][key] += int(ch_audit[key])
            audit["channel"].setdefault("marker_fill_clock_counts", Counter()).update(
                ch_audit["marker_fill_clock_counts"])
            audit["exit_reasons"].update(Counter(r["exit_reason"] for r in rows))
            audit["safe_path_bounds"].update(Counter(e["path_upper_bound_reason"]
                                                    for e in executable))
            symbol_detail[str(lookback)] = {**ch_audit, "vectorbt": vector}
        for n in lookbacks:
            audits[str(n)]["symbols_detail"][str(symbol)] = symbol_detail
        if progress and (symbol_index % 20 == 0 or symbol_index == len(symbol_groups)):
            print(f"Generated channel paths for {symbol_index}/{len(symbol_groups)} symbols",
                  flush=True)
        del base_q, channel_q, bars12, bars1, prepared
        gc.collect()

    paths = pd.concat(results, ignore_index=True)
    if paths.duplicated(["exit_model", "event_id", "tp_r"]).any():
        raise AssertionError("Candidate path identity is not unique")
    for n in lookbacks:
        part = paths[paths.channel_lookback_12h.eq(n)]
        expected = set(event_ids)
        if not part.groupby("tp_r").event_id.apply(set).eq(pd.Series({0.0: expected, 2.0: expected})).all():
            raise AssertionError(f"{n}x12H candidate omitted an event/TP leg")
        if len(part) != len(event_ids) * len(TP_RS):
            raise AssertionError(f"Unexpected {n}x12H candidate path count")
        if not part.exit_phase.isin(["open", "intrabar", "close"]).all():
            raise AssertionError("Invalid VectorBT exit phase")
        tail = part[part.exit_reason.eq("series_tail_close")]
        for row in tail.itertuples():
            # _simulate_symbol asserts each tail fill equals the physical H1 tail.
            if row.exit_phase != "close":
                raise AssertionError("Native file-tail settlement was not a close")
    paths = paths.sort_values(["channel_lookback_12h", "source_row_number", "is_control", "tp_r"],
                              ascending=[True, True, True, True], kind="stable")
    return paths, audits


def base_by_event(frame: pd.DataFrame) -> dict[str, float]:
    if "entry_order" not in frame:
        return {}
    return dict(zip(frame.event_id.astype(str), frame.entry_order.astype(float)))


def _gate(row: dict, *, development: bool) -> dict:
    gates = {
        "win_pct_ge_30": row.get("win_pct") is not None and row.get("win_pct") >= 30.0,
        "equal_notional_payoff_ge_2_5": (row.get("payoff") is not None
                                         and row.get("payoff") >= 2.5),
        "cash_payoff_ge_2_5": (row.get("payoff_usdt") is not None
                               and row.get("payoff_usdt") >= 2.5),
        "conservative_dd_le_35": (row.get("dd_bound_pct") is not None
                                   and row.get("dd_bound_pct") <= 35.0),
    }
    if development:
        gates["minimum_100_closed_positions"] = row.get("trades", 0) >= 100
    return {"checks": gates, "pass": all(gates.values())}


def _run_accounts(paths: pd.DataFrame, config: dict) -> tuple[pd.DataFrame, dict]:
    baseline_path = THIRDS_EXP / "account_metrics.csv"
    baseline = pd.read_csv(baseline_path)
    baseline = baseline[(baseline.venue.eq("OKX")) & baseline.scenario.eq("actual")].copy()
    rows = []
    gate_records = {"frozen_ema_baseline": {}}
    for r in baseline.to_dict("records"):
        gate = _gate(r, development=(r["period"] == "dev"))
        rows.append({"variant": "frozen_ema_baseline", "channel_lookback_12h": 0,
                     "pool": "actual", **r, "gate": gate["pass"],
                     "gate_checks": json.dumps(gate["checks"], sort_keys=True)})
        gate_records["frozen_ema_baseline"][r["period"]] = gate

    checks = {}
    for lookback in LOOKBACKS:
        variant = f"prior_channel_{lookback}x12h"
        part = paths[paths.channel_lookback_12h.eq(lookback)].copy()
        real = part[~part.is_control]
        controls = part[part.is_control & part.parent_id.isin(real.parent_id)]
        paired = real[real.parent_id.isin(controls.parent_id)]
        if len(real.event_id.unique()) != EXPECTED_REAL or len(controls.event_id.unique()) != EXPECTED_CONTROL:
            raise ValueError(f"{variant}: eligible identities changed before funding")
        metrics, trades, equity = capacity.run(real, SLOTS, FRACTION, 0.0)
        for period, values in metrics.items():
            gate = _gate(values, development=(period == "dev"))
            rows.append({"variant": variant, "channel_lookback_12h": lookback,
                         "pool": "actual", "period": period, **values,
                         "gate": gate["pass"], "gate_checks": json.dumps(gate["checks"], sort_keys=True)})
            gate_records.setdefault(variant, {})[period] = gate
        if not np.isclose(1000.0 + trades.cash_pnl.sum(), equity.nav.iloc[-1],
                          rtol=1e-8, atol=.01):
            raise ValueError(f"{variant}: funded cash does not reconcile to final NAV")
        trades.to_csv(EXP / f"funded_{lookback}.csv.gz", index=False, compression="gzip")
        equity.to_csv(EXP / f"hourly_{lookback}.csv.gz", index_label="hour_end_utc")
        checks[variant] = {"funded_positions": len(trades), "periods": metrics,
                           "dev_gate": gate_records[variant]["dev"],
                           "all_gate": gate_records[variant]["all"]}
        if gate_records[variant]["dev"]["pass"]:
            for label, pool, slip in (("paired_signals", paired, 0.0),
                                      ("matched_control", controls, 0.0),
                                      ("stress_5bp", real, 0.0005)):
                if pool.empty:
                    raise ValueError(f"{variant} {label} unexpectedly empty")
                measured, check_trades, check_equity = capacity.run(pool, SLOTS, FRACTION, slip)
                for period, values in measured.items():
                    rows.append({"variant": variant, "channel_lookback_12h": lookback,
                                 "pool": label, "period": period, **values,
                                 "gate": _gate(values, development=(period == "dev"))["pass"],
                                 "gate_checks": json.dumps(_gate(values, development=(period == "dev"))["checks"],
                                                            sort_keys=True)})
                checks[variant][label] = {"periods": measured,
                                          "funded_positions": len(check_trades),
                                          "final_nav": float(check_equity.nav.iloc[-1]),
                                          "slippage_per_market_fill": slip}
                del check_trades, check_equity
                gc.collect()
        del trades, equity
        gc.collect()

    table = pd.DataFrame(rows)
    winners = []
    for lookback in LOOKBACKS:
        variant = f"prior_channel_{lookback}x12h"
        if (gate_records[variant]["dev"]["pass"]
                and gate_records[variant]["all"]["pass"]):
            dev_multiple = next(x["multiple"] for x in rows if x.get("variant") == variant
                                and x.get("pool") == "actual" and x.get("period") == "dev")
            winners.append((dev_multiple, variant))
    pick = max(winners)[1] if winners else "frozen_ema_baseline"
    summary = {
        "baseline_source": str(baseline_path.relative_to(ROOT)),
        "baseline_funded_positions": EXPECTED_FUNDED_BASELINE,
        "baseline_all_final_usdt": EXPECTED_BASELINE_FINAL,
        "fraction_at_2r": FRACTION,
        "slots": SLOTS,
        "initial_cash_usdt": INITIAL_CASH,
        "gate_results": gate_records,
        "paired_and_stress_checks": checks,
        "dev_and_all_period_winners": [name for _, name in winners],
        "pick": pick,
    }
    return table, summary


def _synthetic_check() -> dict:
    clock = np.arange(3, dtype=np.int64) * engine.STEP_12H
    prior_low_break = pd.DataFrame({
        "open": [7, 8, 4], "high": [10, 11, 8],
        "low": [5, 6, 0], "close": [7, 8, 4.5],
    }, index=clock)
    long2, _, skipped = channel_break_flags(prior_low_break, 2)
    if skipped or not long2[2]:
        raise AssertionError("Current low leaked into the prior-N channel")

    long_equal = prior_low_break.copy()
    long_equal.loc[clock[2], "close"] = 5.0
    equal_long, _, _ = channel_break_flags(long_equal, 2)
    if equal_long[2]:
        raise AssertionError("Long equality must not count as a strict break")

    current_high_break = pd.DataFrame({
        "open": [7, 8, 12], "high": [10, 11, 50],
        "low": [5, 6, 4], "close": [7, 8, 12],
    }, index=clock)
    _, short2, skipped = channel_break_flags(current_high_break, 2)
    if skipped or not short2[2]:
        raise AssertionError("Current high leaked into the prior-N channel")

    short_equal = current_high_break.copy()
    short_equal.loc[clock[2], "close"] = 11.0
    _, equal_short, _ = channel_break_flags(short_equal, 2)
    if equal_short[2]:
        raise AssertionError("Short equality must not count as a strict break")
    return {"strict_long_equality": True, "strict_short_equality": True,
            "current_low_excluded": True, "current_high_excluded": True}


def _mini_events(population: pd.DataFrame, symbols: int = 12) -> pd.DataFrame:
    """Deterministic in-memory smoke roster spanning the input universe."""
    real = population[(population.tp_r.eq(0)) & ~population.is_control].copy()
    by_symbol = {str(s): f.copy() for s, f in real.groupby("symbol", sort=True)}
    ordered = list(by_symbol)
    if not ordered:
        raise ValueError("No eligible real event available for smoke")
    take_symbols = ordered[::max(1, len(ordered)//symbols)][:symbols]
    selected = []
    for symbol in take_symbols:
        frame = by_symbol[symbol].sort_values(["entry_ms", "side"], kind="stable")
        for side in (-1, 1):
            candidates = frame[frame.side.eq(side)]
            if not candidates.empty:
                selected.append(candidates.iloc[len(candidates)//2])
        if not any(str(x.event_id) in {str(y.event_id) for y in selected} for x in frame.itertuples()):
            selected.append(frame.iloc[len(frame)//2])
    parents = {str(r.parent_id) for r in selected}
    controls = population[(population.tp_r.eq(0)) & population.is_control
                          & population.parent_id.isin(parents)]
    chosen_ids = {str(r.event_id) for r in selected} | set(controls.event_id.astype(str))
    return population[population.event_id.astype(str).isin(chosen_ids)].copy()


def mini_sample(symbol_count: int | None = None) -> dict:
    cfg = _read_config()
    started = time.perf_counter()
    fixture = _synthetic_check()
    population_start = time.perf_counter()
    population = _check_population(cfg)
    population_seconds = time.perf_counter() - population_start
    sample = _mini_events(population, symbols=int(symbol_count or cfg["execution"]["mini_symbols"]))
    event_count = int(sample[sample.tp_r.eq(0)].event_id.nunique())
    symbol_count = int(sample.symbol.nunique())
    build_start = time.perf_counter()
    paths, audits = _channel_path_rows(sample, cfg, progress=True)
    build_seconds = time.perf_counter() - build_start
    if paths.duplicated(["exit_model", "event_id", "tp_r"]).any():
        raise AssertionError("Smoke path identity repeated")
    if paths.exit_reason.eq("ema_flip_open").any():
        raise AssertionError("Native EMA exit leaked into channel candidate")
    if not paths[paths.exit_reason.eq("channel_breakout_open")].exit_phase.eq("open").all():
        raise AssertionError("Channel exit did not execute at an H1 open")
    if not paths[paths.exit_reason.eq("take_profit") | paths.exit_reason.eq("take_profit_open")].exit_fee_rate.eq(
            engine.MAKER).all():
        raise AssertionError("Native 2R limit lost maker fee class")
    sample_10 = paths[paths.channel_lookback_12h.eq(10)]
    account_smoke = {}
    for label, pool in (("actual", sample_10[~sample_10.is_control]),
                        ("matched_control", sample_10[sample_10.is_control])):
        if pool.empty:
            continue
        metrics, trades, equity = capacity.run(pool, SLOTS, FRACTION, 0.0)
        if trades.empty or not np.isclose(1000.0 + trades.cash_pnl.sum(), equity.nav.iloc[-1],
                                          rtol=1e-8, atol=.01):
            raise AssertionError(f"In-memory {label} shared-cash account did not reconcile")
        account_smoke[label] = {"funded_positions": len(trades),
                                "all_final_usdt": metrics["all"]["final_usdt"],
                                "all_dd_bound_pct": metrics["all"]["dd_bound_pct"],
                                "cash_reconciles": True}
        del metrics, trades, equity
        gc.collect()
    base_full_symbols = int(population.symbol.nunique())
    seconds_per_sample_symbol = build_seconds / max(1, symbol_count)
    estimated_path_seconds = seconds_per_sample_symbol * base_full_symbols
    report = {
        "mode": "memory_only_smoke",
        "synthetic_checks": fixture,
        "eligible_population_events": {
            "real": EXPECTED_REAL, "matched_controls": EXPECTED_CONTROL,
            "rank_eligible_control_orphans_excluded": EXPECTED_ORPHAN_CONTROLS_REMOVED,
            "symbols": base_full_symbols,
        },
        "sample": {"symbols": symbol_count, "events": event_count,
                   "path_rows": len(paths), "lookbacks": list(LOOKBACKS)},
        "in_memory_account_smoke": account_smoke,
        "timing_seconds": {"rank_and_population_load": round(population_seconds, 3),
                            "sample_vectorbt_path_build": round(build_seconds, 3),
                            "estimated_full_path_build": round(estimated_path_seconds, 1),
                            "estimate_range_seconds": [round(estimated_path_seconds*.65, 1),
                                                       round(estimated_path_seconds*1.7, 1)],
                            "wall_total": round(time.perf_counter()-started, 3)},
        "no_output_files_written": True,
        "vectorbt": vbt.__version__,
        "sample_audit": {n: {"symbols": a["symbols"], "events": a["event_count"],
                             "vectorbt": dict(a["vectorbt"]),
                             "channel": dict(a["channel"]),
                             "exit_reasons": dict(a["exit_reasons"]),
                             "safe_path_bounds": dict(a["safe_path_bounds"])}
                         for n, a in audits.items()},
    }
    print(json.dumps(_json_safe(report), ensure_ascii=False, indent=2, allow_nan=False), flush=True)
    return report


def generate(config_path: Path = CONFIG) -> dict:
    cfg = _read_config(config_path)
    for relative in cfg["outputs"].values():
        if isinstance(relative, str):
            output = ROOT / relative
            if output.exists() and output.is_file():
                raise FileExistsError(f"Refusing to overwrite {output}")
    paths_path = ROOT / cfg["outputs"]["paths_csv_gz"]
    audit_path = ROOT / cfg["outputs"]["paths_audit_json"]
    metrics_path = ROOT / cfg["outputs"]["account_metrics_csv"]
    summary_path = ROOT / cfg["outputs"]["summary_json"]
    exp_dir = paths_path.parent
    exp_dir.mkdir(parents=True, exist_ok=True)

    population = _check_population(cfg)
    paths, audits = _channel_path_rows(population, cfg, progress=True)
    if paths.empty:
        raise ValueError("Channel path builder produced no rows")
    temporary = paths_path.with_suffix(paths_path.suffix + ".tmp")
    paths.to_csv(temporary, index=False, compression="gzip", float_format="%.12g")
    temporary.replace(paths_path)

    table, summary = _run_accounts(paths, cfg)
    table.to_csv(metrics_path, index=False)
    summary["path_audit"] = {n: {
        "symbols": a["symbols"], "events": a["event_count"],
        "vectorbt": dict(a["vectorbt"]), "channel": dict(a["channel"]),
        "exit_reasons": dict(a["exit_reasons"]),
        "safe_path_bounds": dict(a["safe_path_bounds"]),
    } for n, a in audits.items()}
    summary["paths_csv_gz"] = str(paths_path.relative_to(ROOT))
    summary["vectorbt_version"] = vbt.__version__
    _write_json(summary_path, summary)
    audit = {
        "experiment_id": cfg["experiment_id"],
        "builder": str(Path(__file__).relative_to(ROOT)),
        "vectorbt_version": vbt.__version__,
        "source_paths": str((wallet.EXP / "paths.csv.gz").relative_to(ROOT)),
        "baseline": str((THIRDS_EXP / "account_metrics.csv").relative_to(ROOT)),
        "population": {"actual_events": EXPECTED_REAL,
                       "paired_controls": EXPECTED_CONTROL,
                       "rank_eligible_control_orphans_excluded": EXPECTED_ORPHAN_CONTROLS_REMOVED,
                       "actual_and_control_target_legs": list(TP_RS),
                       "source_event_join": "source_row_number + event_id; parent_id is preserved",
                       "non_null_rank_filter": "reused thirds.okx_paths(); checked by repair.check_parent_sets",
                       "priority": "source prior_atr_pct indexed by 1-based source_row_number; lower first"},
        "construction": {
            "channel_window": "N preceding complete contiguous native UTC12H bars; exclude current bar high/low",
            "long_condition": "completed close < minimum prior-N low",
            "short_condition": "completed close > maximum prior-N high",
            "execution": "first observed H1 open at/after completed 12H close",
            "q_encoding": "copy frozen q; q[:,4]=NaN; q[:,5] = -1 on long breakout, +1 on short breakout; q[:,6] unchanged",
            "fill_engine": "frozen cloud_close_reward_paths._simulate_symbol -> VectorBT Portfolio.from_order_func -> unchanged _order_func",
            "entry_stop_target": "frozen exact H1-open entry and absolute stop; native resting 2R limit",
            "same_open_precedence": "gap, stale and stop remain ahead of channel; channel callback is ahead of TP open as in frozen order; entry-hour exit is guarded by the callback",
            "tail": "the engine still asserts series_tail_close is the physical last H1 bar and uses its close",
            "no_lookahead": True,
        },
        "account": {
            "slots": SLOTS, "initial_cash_usdt": INITIAL_CASH,
            "target_leg_fraction_at_2r": FRACTION,
            "priority": "low prior ATR; frozen CRC32 tie-break order",
            "market_fee_per_fill": engine.TAKER,
            "resting_2r_fee_per_fill": engine.MAKER,
            "stress_market_slippage_per_fill": .0005,
            "baseline_frozen": str((THIRDS_EXP / "account_metrics.csv").relative_to(ROOT)),
        },
        "counts": {n: {
            "symbols": a["symbols"], "event_count": a["event_count"],
            "vectorbt": dict(a["vectorbt"]), "channel": dict(a["channel"]),
            "exit_reasons": dict(a["exit_reasons"]), "safe_path_bounds": dict(a["safe_path_bounds"]),
        } for n, a in audits.items()},
        "path_schema": list(paths.columns),
    }
    _write_json(audit_path, audit)
    print(json.dumps({"experiment_id": cfg["experiment_id"],
                      "path_rows": len(paths), "account_rows": len(table),
                      "pick": summary["pick"]}, ensure_ascii=False, indent=2), flush=True)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--mini-sample", action="store_true",
                        help="Generate a deterministic small sample in memory only")
    parser.add_argument("--mini-symbols", type=int, default=None,
                        help="Restrict the in-memory smoke to this many symbols")
    args = parser.parse_args(argv)
    if args.mini_sample:
        mini_sample(args.mini_symbols)
        return
    config_path = args.config if args.config.is_absolute() else ROOT / args.config
    generate(config_path)


if __name__ == "__main__":
    main()
