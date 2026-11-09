"""Trade first completed 12H EMA20/50 reversals on the common OKX BTC/ETH history.

Signals use only completed native 12H closes. The first post-confirmation H1
open is the market entry; a skipped close-outside test does not become a later
entry. V1.7's VectorBT path callback owns the intrabar cloud/stop exit, and the
existing shared-cash VectorBT account handles two equal position slots.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_reward_paths as engine
from yoyo.monitor.cloud_signal_plans import fixed_stop

ROOT = engine.ROOT
EXP = ROOT / "experiments/active/exp-cloud-close-major-cross-20261109-v1"
CONFIG = EXP / "config.json"
HOUR_MS = engine.HOUR_MS
STEP_12H = engine.STEP_12H
TAKER = engine.TAKER


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


def _load_common(config: dict) -> tuple[dict[str, dict], int, int, dict]:
    data_config = config["data"]
    source_root = ROOT / data_config["top20_inputs_root"]
    cutoff_ms = int(data_config["cutoff_ms"])
    loaded = {}
    for symbol in data_config["symbols"]:
        symbol_dir = source_root / symbol
        p12, p1 = symbol_dir / "bars12h.csv.gz", symbol_dir / "bars1h.csv.gz"
        receipt_path = symbol_dir / "receipt.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt.get("symbol") != symbol or int(receipt.get("through_ms", -1)) != cutoff_ms:
            raise ValueError(f"Frozen native source receipt mismatch for {symbol}")
        bars12 = engine._load_bars(p12, "12h")
        bars1 = engine._load_bars(p1, "1h")
        if bars12.empty or bars1.empty:
            raise ValueError(f"Empty common-history input for {symbol}")
        if int(bars12.index[-1]) + STEP_12H != cutoff_ms:
            raise ValueError(f"12H source does not end on the frozen common cutoff: {symbol}")
        if int(bars1.index[-1]) + HOUR_MS != cutoff_ms:
            raise ValueError(f"1H source does not end on the frozen common cutoff: {symbol}")
        loaded[symbol] = {
            "bars12_full": bars12,
            "bars1_full": bars1,
            "receipt": receipt,
            "bars12_path": p12,
            "bars1_path": p1,
            "receipt_path": receipt_path,
        }

    common_start_ms = max(int(item["bars12_full"].index[0]) for item in loaded.values())
    common_h1_start = max(int(item["bars1_full"].index[0]) for item in loaded.values())
    if common_start_ms != common_h1_start or common_start_ms % STEP_12H:
        raise ValueError("BTC/ETH native H1/H12 sources have no shared aligned origin")
    expected_h1 = np.arange(common_start_ms, cutoff_ms, HOUR_MS, dtype=np.int64)
    expected_h12 = np.arange(common_start_ms, cutoff_ms, STEP_12H, dtype=np.int64)
    input_audit = {}
    for symbol, item in loaded.items():
        bars12 = item["bars12_full"].loc[
            (item["bars12_full"].index >= common_start_ms)
            & (item["bars12_full"].index + STEP_12H <= cutoff_ms)
        ].copy()
        bars1 = item["bars1_full"].loc[
            (item["bars1_full"].index >= common_start_ms)
            & (item["bars1_full"].index < cutoff_ms)
        ].copy()
        if not np.array_equal(bars12.index.to_numpy(dtype=np.int64), expected_h12):
            raise ValueError(f"{symbol} common native 12H history has gaps or a mismatched edge")
        if not np.array_equal(bars1.index.to_numpy(dtype=np.int64), expected_h1):
            raise ValueError(f"{symbol} common native 1H history has gaps or a mismatched edge")
        item["bars12"] = bars12
        item["bars1"] = bars1
        input_audit[symbol] = {
            "native_12h_path": str(item["bars12_path"].relative_to(ROOT)),
            "native_1h_path": str(item["bars1_path"].relative_to(ROOT)),
            "source_receipt": str(item["receipt_path"].relative_to(ROOT)),
            "native_12h_rows": len(bars12),
            "native_1h_rows": len(bars1),
            "native_12h_first_open_ms": int(bars12.index[0]),
            "native_12h_last_open_ms": int(bars12.index[-1]),
            "native_1h_first_open_ms": int(bars1.index[0]),
            "native_1h_last_open_ms": int(bars1.index[-1]),
            "12h_source_counts": bars12.source.value_counts(dropna=False).to_dict()
                if "source" in bars12 else {"source_not_recorded": len(bars12)},
            "hourly_gaps": int(np.count_nonzero(np.diff(bars1.index.to_numpy()) != HOUR_MS)),
            "native_12h_gaps": int(np.count_nonzero(np.diff(bars12.index.to_numpy()) != STEP_12H)),
        }
    symbols = data_config["symbols"]
    if not loaded[symbols[0]]["bars12"].index.equals(loaded[symbols[1]]["bars12"].index):
        raise ValueError("The BTC/ETH common native 12H indexes differ")
    if not loaded[symbols[0]]["bars1"].index.equals(loaded[symbols[1]]["bars1"].index):
        raise ValueError("The BTC/ETH common native 1H indexes differ")
    return loaded, common_start_ms, cutoff_ms, input_audit


def _cross_rows(bars12: pd.DataFrame, features: dict, symbol: str) -> list[dict]:
    """Return every causal EMA direction reversal and its same-bar close test."""
    times = bars12.index.to_numpy(dtype=np.int64)
    closes = bars12.close.to_numpy(dtype=float)
    ema20 = np.asarray(features["ema20"], dtype=float)
    ema50 = np.asarray(features["ema50"], dtype=float)
    ready = np.asarray(features["ema_ready"], dtype=bool)
    rows = []
    last_nonzero_direction = 0
    previous_time = None
    flip_number = 0
    for i, stamp in enumerate(times):
        if previous_time is not None and int(stamp) != previous_time + STEP_12H:
            last_nonzero_direction = 0
        spread = ema20[i] - ema50[i]
        direction = int(np.sign(spread)) if np.isfinite(spread) else 0
        if direction and last_nonzero_direction in (-1, 1) and direction != last_nonzero_direction:
            flip_number += 1
            close = float(closes[i])
            outside = (close > ema20[i] and close > ema50[i]) if direction > 0 else (
                close < ema20[i] and close < ema50[i]
            )
            if not ready[i]:
                status = "flip_before_ema_warmup"
            elif outside:
                status = "eligible_flip_close_outside_cloud"
            else:
                status = "flip_close_not_strictly_outside_both_emas"
            rows.append({
                "event_id": f"ema_cross|{symbol}|{int(stamp)}",
                "parent_id": f"ema_cross|{symbol}|{int(stamp)}",
                "source_row_number": flip_number,
                "symbol": symbol,
                "side": direction,
                "key_open_ms": int(stamp),
                "confirmation_ms": int(stamp + STEP_12H),
                "week": engine._ny_week_window(int(stamp))["week"],
                "close": close,
                "ema20_confirmed": float(ema20[i]),
                "ema50_confirmed": float(ema50[i]),
                "prior_direction": int(last_nonzero_direction),
                "direction": direction,
                "ema_ready": bool(ready[i]),
                "close_outside_both": bool(outside),
                "flip_status": status,
                "entry_status": status,
                "entry_ms": None,
                "entry_price": None,
                "stop_price": None,
                "stop_pct": None,
                "risk_distance": None,
                "risk_fraction": None,
                "entry_i": None,
                "end_i": None,
                "source_path_12h": None,
                "source_path_1h": None,
                "path_upper_bound_reason": None,
            })
        if direction:
            last_nonzero_direction = direction
        previous_time = int(stamp)
    return rows


def _bind_entry(event: dict, q: np.ndarray, bar_ms: np.ndarray,
                source_path_12h: str, source_path_1h: str,
                cutoff_ms: int) -> dict:
    """Bind a confirmed flip to the exact next-H1 open and frozen EMA50 stop."""
    bound = dict(event)
    if bound["flip_status"] != "eligible_flip_close_outside_cloud":
        return bound
    confirmation = int(bound["confirmation_ms"])
    idx = int(np.searchsorted(bar_ms, confirmation))
    if idx >= len(bar_ms) or int(bar_ms[idx]) != confirmation or confirmation >= cutoff_ms:
        bound["entry_status"] = "no_exact_post_confirmation_h1_open"
        return bound
    entry = float(q[idx, 0])
    stop, stop_pct = fixed_stop(bound["symbol"], int(bound["side"]),
                                float(bound["ema50_confirmed"]))
    side = int(bound["side"])
    risk = (entry - float(stop)) * side if stop is not None else math.nan
    if not math.isfinite(entry) or entry <= 0:
        bound["entry_status"] = "invalid_post_confirmation_open"
        return bound
    if stop is None or not math.isfinite(risk) or risk <= 0:
        bound["entry_status"] = "slow_ema_fixed_stop_not_adverse_from_entry"
        bound["stop_price"] = float(stop) if stop is not None else None
        bound["stop_pct"] = float(stop_pct)
        return bound
    end_i, end_reason = engine._safe_path_end(idx, side, q, bar_ms)
    bound.update({
        "entry_status": "path_ready",
        "entry_ms": confirmation,
        "entry_price": entry,
        "stop_price": float(stop),
        "stop_pct": float(stop_pct),
        "risk_distance": float(risk),
        "risk_fraction": float(risk / entry),
        "entry_i": idx,
        "end_i": int(end_i),
        "source_path_12h": source_path_12h,
        "source_path_1h": source_path_1h,
        "path_upper_bound_reason": end_reason,
        "stop_basis": "fixed_stop(BTC/ETH, side, completed_signal_12h_EMA50)",
        "tp_r": 0.0,
        "is_control": False,
        "rank": None,
        "cohort": "BTC_ETH_common_native_OKX_history",
        "is_current": True,
        "was_top20": True,
        "bar_source": "frozen_native_OKX_12H_and_1H",
        "entry_fee_class": "taker",
        "entry_fee_rate": TAKER,
        "control_match_status": "not_applicable",
    })
    return bound


def _price_events(events: list[dict], q: np.ndarray, bar_ms: np.ndarray,
                  batch_size: int) -> tuple[list[dict], dict]:
    executable = [event for event in events if event["entry_status"] == "path_ready"]
    if not executable:
        return [], {"columns": 0, "orders": 0, "trades": 0}
    rows, audit = engine._simulate_symbol(executable, q, bar_ms, (0.0,), batch_size)
    return rows, audit


def _account(paths: pd.DataFrame, quote_loader, start_ms: int, end_ms: int,
             slip: float, slots: int) -> tuple[dict, pd.DataFrame, pd.DataFrame, dict, list[dict]]:
    executable = paths[paths.status.eq("executed")].copy()
    data = wallet.prepare(executable, slots=slots, start=start_ms, end=end_ms,
                          quote_loader=quote_loader)
    funded, equity = wallet.account(data, slip=slip, exposure=1.0)
    metrics = wallet.summarize(funded, equity)
    if not np.isclose(1000.0 + funded.cash_pnl.sum(), equity.nav.iloc[-1],
                      rtol=1e-8, atol=0.005):
        raise ValueError("Cloud-cross shared account does not reconcile cash to marked NAV")
    selected_ids = set(data["selected"].event_id)
    missed = executable[~executable.event_id.isin(selected_ids)].copy()
    missed_rows = []
    for row in missed.itertuples(index=False):
        previous_same = funded[(funded.symbol == row.symbol)
                               & (funded.entry_ms < row.entry_ms)
                               & (funded.exit_bar_ms >= row.entry_ms)]
        if not previous_same.empty:
            prior_exit = int(previous_same.sort_values("entry_ms").iloc[-1].exit_bar_ms)
            reason = ("same_open_exit_still_occupies_symbol_slot" if prior_exit == int(row.entry_ms)
                      else "same_symbol_position_still_open")
        else:
            reason = "both_slots_occupied"
        missed_rows.append({"event_id": row.event_id, "parent_id": row.parent_id,
                            "symbol": row.symbol, "side": int(row.side),
                            "entry_ms": int(row.entry_ms), "missed_reason": reason})
    metadata = {
        "input_path_events": len(executable),
        "admitted": len(data["selected"]),
        "funded": len(funded),
        "missed_entries": len(missed),
        "missed_reason_counts": pd.Series([row["missed_reason"] for row in missed_rows])
            .value_counts().to_dict(),
        "admission_rejections": data["skips"],
        "unquoted_position_hours": int(data["gap_marks"]),
        "positions_equal_weighted": True,
        "slots": slots,
        "fee_per_fill": TAKER,
        "slippage_per_market_fill": slip,
    }
    return metrics, funded, equity, metadata, missed_rows


def _benchmark_events(loaded: dict[str, dict], start_ms: int, cutoff_ms: int) -> pd.DataFrame:
    rows = []
    for source_row, symbol in enumerate(("BTC", "ETH"), start=1):
        bars1 = loaded[symbol]["bars1"]
        rows.append({
            "event_id": f"buy_hold|{symbol}|{start_ms}",
            "parent_id": f"buy_hold|{symbol}|{start_ms}",
            "source_row_number": source_row,
            "symbol": symbol,
            "week": "full_common_history",
            "side": 1,
            "tp_r": 0.0,
            "is_control": False,
            "entry_ms": start_ms,
            "exit_bar_ms": int(bars1.index[-1]),
            "entry_price": float(bars1.open.iloc[0]),
            "exit_price": float(bars1.close.iloc[-1]),
            "stop_price": np.nan,
            "exit_reason": "series_tail_close",
            "exit_fee_rate": TAKER,
            "exit_phase": "close",
            "status": "executed",
            "entry_status": "executed",
            "entry_fee_rate": TAKER,
            "same_bar_ambiguous": False,
            "gap_flag": False,
        })
    if int(loaded["BTC"]["bars1"].index[-1]) + HOUR_MS != cutoff_ms:
        raise ValueError("Benchmark does not exit at the exact common H1 close")
    return pd.DataFrame(rows)


def _metrics_by_scenario(paths: pd.DataFrame, quote_loader,
                         start_ms: int, cutoff_ms: int, config: dict) -> tuple[dict, dict]:
    slots = int(config["account"]["slots"])
    out = {}
    artifacts = {}
    variants = {
        "ema_cross_long_short": paths[paths.side.isin([1, -1])].copy(),
        "ema_cross_long_only": paths[paths.side.eq(1)].copy(),
        "buy_hold_btc_eth_50_50": _benchmark_events(
            config["_loaded_bars"], start_ms, cutoff_ms
        ),
    }
    for name, scenario_paths in variants.items():
        if name == "buy_hold_btc_eth_50_50":
            scenario_paths = scenario_paths.copy()
            # The explicit two-row roster is admitted through the same live wallet rule.
        for slip_name, slip in config["account"]["slippage_scenarios"].items():
            metrics, funded, equity, metadata, missed_rows = _account(
                scenario_paths, quote_loader, start_ms, cutoff_ms, float(slip), slots
            )
            key = f"{name}_{slip_name}"
            out[key] = {"metrics": metrics, "account": metadata,
                        "exit_reasons": funded.exit_reason.value_counts().to_dict()}
            artifacts[key] = {"funded": funded, "equity": equity,
                              "missed": missed_rows}
    return out, artifacts


def _mini_check() -> dict:
    """Exercise flip skip semantics, native VectorBT stop fills, and same-open busy rule."""
    bars12 = pd.DataFrame({"close": [98., 98., 103., 100., 98.],
                           "open": [98., 98., 100., 101., 100.],
                           "high": [99., 99., 104., 102., 101.],
                           "low": [97., 97., 99., 99., 97.]},
                          index=np.arange(5, dtype=np.int64) * STEP_12H)
    features = {
        "ema20": np.array([99., 99., 102., 101., 98.]),
        "ema50": np.array([100., 100., 100., 100., 100.]),
        "ema_ready": np.array([False, False, True, True, True]),
    }
    flips = _cross_rows(bars12, features, "BTC")
    if len(flips) != 2 or flips[0]["flip_status"] != "eligible_flip_close_outside_cloud":
        raise AssertionError("Mini did not retain the completed long cloud break")
    if flips[1]["flip_status"] != "flip_close_not_strictly_outside_both_emas":
        raise AssertionError("A close inside the cloud must skip only the current flip")

    bar_ms = np.arange(4, dtype=np.int64) * HOUR_MS + 3 * STEP_12H
    q = np.array([
        [103., 104., 102., 103., 90., 1., 0.],
        [103., 104., 98., 99., 90., 1., 0.],
        [99., 101., 98., 100., 90., 1., 0.],
        [100., 101., 99., 100., 90., 1., 0.],
    ], dtype=float)
    event = dict(flips[0])
    event.update({"confirmation_ms": int(bar_ms[0]), "entry_status": "eligible_flip_close_outside_cloud",
                  "symbol": "BTC", "side": 1, "ema50_confirmed": 100.})
    event = _bind_entry(event, q, bar_ms, "mini12", "mini1", int(bar_ms[-1] + HOUR_MS))
    if event["entry_status"] != "path_ready":
        raise AssertionError(f"Exact next-open mini entry failed: {event['entry_status']}")
    paths, _ = _price_events([event], q, bar_ms, 16)
    if len(paths) != 1 or paths[0]["exit_reason"] != "stop" or paths[0]["exit_phase"] != "intrabar":
        raise AssertionError("Existing VectorBT path callback did not fill the fixed stop")
    mini_bars = q[:, :4]
    mini_quotes = lambda symbol: (bar_ms, mini_bars)
    mini_metrics, mini_funded, mini_equity, mini_meta, mini_missed = _account(
        pd.DataFrame(paths), mini_quotes, int(bar_ms[0]), int(bar_ms[-1] + HOUR_MS), 0.0, 2
    )
    if len(mini_funded) != 1 or mini_missed or mini_meta["funded"] != 1:
        raise AssertionError("Mini path did not fund and close through the shared VectorBT account")
    if not np.isfinite(mini_metrics["final_usdt"]) or len(mini_equity) != len(bar_ms):
        raise AssertionError("Mini shared account did not produce finite hourly NAV")

    admission = pd.DataFrame([
        {"event_id": "old", "symbol": "BTC", "entry_ms": 0, "exit_bar_ms": HOUR_MS},
        {"event_id": "new-reverse", "symbol": "BTC", "entry_ms": HOUR_MS,
         "exit_bar_ms": 2 * HOUR_MS},
        {"event_id": "other-symbol", "symbol": "ETH", "entry_ms": HOUR_MS,
         "exit_bar_ms": 2 * HOUR_MS},
    ])
    selected, rejected = wallet.admit(admission, 2)
    if set(selected.event_id) != {"old", "other-symbol"}:
        raise AssertionError("Same-open reversal should remain blocked by the prior symbol slot")
    if rejected != {"same_contract": 1, "all_slots_busy": 0}:
        raise AssertionError(f"Unexpected same-open rejection count: {rejected}")
    return {"long_flip_signal": flips[0]["event_id"],
            "inside_cloud_flip_skipped": flips[1]["event_id"],
            "vectorbt_exit": paths[0]["exit_reason"],
            "vectorbt_exit_phase": paths[0]["exit_phase"],
            "shared_account_final_usdt": mini_metrics["final_usdt"],
            "shared_account_funded": mini_meta["funded"],
            "same_open_reverse_missed": rejected["same_contract"],
            "admission": "existing cloud_close_reward_account.admit"}


def run() -> dict:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    if config["experiment_id"] != EXP.name:
        raise ValueError("Unexpected experiment_id")
    if (EXP / config["outputs"]["summary_json"]).exists():
        raise FileExistsError("Refusing to overwrite completed major-cross experiment")
    if float(config["fees"]["taker_per_fill"]) != TAKER:
        raise ValueError("Frozen OKX Lv1 taker fee must remain 5bp per fill")
    loaded, start_ms, cutoff_ms, source_audit = _load_common(config)
    if start_ms != int(config["data"]["expected_common_start_ms"]):
        raise ValueError("Common native history start differs from preregistered boundary")
    if cutoff_ms != int(config["data"]["cutoff_ms"]):
        raise ValueError("Frozen source cutoff differs from config")

    events_by_symbol, flip_rows, source_features = {}, [], {}
    path_rows, path_audit = [], {}
    for symbol in config["data"]["symbols"]:
        item = loaded[symbol]
        bars12, bars1 = item["bars12"], item["bars1"]
        q, bar_ms, prepared = engine._prepare_symbol(bars12, bars1)
        features = prepared["features"]
        flips = _cross_rows(bars12, features, symbol)
        bound = [
            _bind_entry(event, q, bar_ms,
                        str(item["bars12_path"].relative_to(ROOT)),
                        str(item["bars1_path"].relative_to(ROOT)), cutoff_ms)
            for event in flips
        ]
        entries = [event for event in bound
                   if event["flip_status"] == "eligible_flip_close_outside_cloud"]
        priced, audit = _price_events(entries, q, bar_ms,
                                      int(config["execution"]["vectorbt_path_batch_size"]))
        events_by_symbol[symbol] = priced
        flip_rows.extend(bound)
        path_rows.extend(priced)
        source_features[symbol] = prepared["audit"]
        path_audit[symbol] = audit

    flips_frame = pd.DataFrame(flip_rows)
    paths = pd.DataFrame(path_rows)
    if paths.empty or not paths.status.eq("executed").any():
        raise ValueError("No executable, priced EMA cross paths were generated")
    if paths.event_id.duplicated().any():
        raise ValueError("One completed EMA flip must have exactly one zero-TP path")

    quotes = {symbol: (
        loaded[symbol]["bars1"].index.to_numpy(dtype=np.int64),
        loaded[symbol]["bars1"][["open", "high", "low", "close"]].to_numpy(dtype=float),
    ) for symbol in config["data"]["symbols"]}
    quote_loader = lambda symbol: quotes[symbol]
    metric_config = dict(config)
    metric_config["_loaded_bars"] = loaded
    account_rows, artifacts = _metrics_by_scenario(paths, quote_loader,
                                                    start_ms, cutoff_ms, metric_config)

    EXP.mkdir(parents=True, exist_ok=True)
    flips_frame.to_csv(EXP / config["outputs"]["signal_candidates_csv_gz"],
                       index=False, compression="gzip")
    paths.to_csv(EXP / config["outputs"]["native_paths_csv_gz"],
                 index=False, compression="gzip")
    metrics_rows = []
    for name, result in account_rows.items():
        metrics_rows.append({"scenario": name, **result["metrics"],
                             "funded_positions": result["account"]["funded"],
                             "missed_entries": result["account"]["missed_entries"],
                             "same_contract_rejections": result["account"]["admission_rejections"]["same_contract"],
                             "all_slots_busy_rejections": result["account"]["admission_rejections"]["all_slots_busy"]})
        artifacts[name]["funded"].to_csv(
            EXP / config["outputs"]["funded_files"][name], index=False, compression="gzip")
        artifacts[name]["equity"].to_csv(
            EXP / config["outputs"]["hourly_files"][name], index_label="hour_end_utc",
            compression="gzip")
        if artifacts[name]["missed"]:
            pd.DataFrame(artifacts[name]["missed"]).to_csv(
                EXP / config["outputs"]["missed_files"][name], index=False, compression="gzip")
        elif name in config["outputs"]["missed_files"]:
            pd.DataFrame(columns=["event_id", "parent_id", "symbol", "side", "entry_ms", "missed_reason"]).to_csv(
                EXP / config["outputs"]["missed_files"][name], index=False, compression="gzip")
    metrics_frame = pd.DataFrame(metrics_rows)
    metrics_frame.to_csv(EXP / config["outputs"]["account_metrics_csv"], index=False)
    signal_counts = {
        "total_direction_flips": len(flips_frame),
        "pre_warmup_flips_skipped": int(flips_frame.flip_status.eq("flip_before_ema_warmup").sum()),
        "ready_flips_not_closed_outside_both": int(
            flips_frame.flip_status.eq("flip_close_not_strictly_outside_both_emas").sum()),
        "ready_flips_closed_outside": int(
            flips_frame.flip_status.eq("eligible_flip_close_outside_cloud").sum()),
        "outside_flips_without_exact_entry": int(
            flips_frame.entry_status.eq("no_exact_post_confirmation_h1_open").sum()),
        "outside_flips_with_invalid_stop": int(
            flips_frame.entry_status.eq("slow_ema_fixed_stop_not_adverse_from_entry").sum()),
        "long_entries_candidate": int(paths[(paths.side == 1) & paths.status.eq("executed")].shape[0]),
        "short_entries_candidate": int(paths[(paths.side == -1) & paths.status.eq("executed")].shape[0]),
    }
    summary = {
        "experiment_id": EXP.name,
        "source": {
            "venue": "OKX USDT perpetual",
            "source_scope": "BTC and ETH only; native 12H and native 1H frozen top20 histories",
            "start_ms": start_ms,
            "start_utc": pd.Timestamp(start_ms, unit="ms", tz="UTC").isoformat(),
            "cutoff_ms_exclusive": cutoff_ms,
            "cutoff_utc_exclusive": pd.Timestamp(cutoff_ms, unit="ms", tz="UTC").isoformat(),
            "common_12h_rows_per_symbol": len(loaded["BTC"]["bars12"]),
            "common_1h_rows_per_symbol": len(loaded["BTC"]["bars1"]),
            "input_audit": source_audit,
            "indicator_path_audit": source_features,
        },
        "signal_rule": config["signal_rule"],
        "exit_rule": config["exit_rule"],
        "account": {
            "starting_usdt": float(config["account"]["starting_usdt"]),
            "slots": int(config["account"]["slots"]),
            "fees": config["fees"],
            "signal_counts": signal_counts,
            "price_path_audit": path_audit,
            "scenarios": account_rows,
        },
        "interpretation_limit": "The 50/50 BTC/ETH hold is a market benchmark, not a matched random-entry control. Funding is not included.",
        "outputs": config["outputs"],
    }
    _write_json(EXP / config["outputs"]["summary_json"], summary)
    print(json.dumps(_json_safe(summary), ensure_ascii=False, indent=2), flush=True)
    return summary


def main(argv=None) -> None:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--mini-check", action="store_true")
    group.add_argument("--run", action="store_true")
    args = parser.parse_args(argv)
    if args.mini_check:
        print(json.dumps(_json_safe(_mini_check()), ensure_ascii=False, indent=2), flush=True)
    else:
        run()


if __name__ == "__main__":
    main()
