"""Causal ETH 15m replay adding one confirmed VIDYA-flip exit to V12.10.

The V12.10 entry gate and all incumbent exits are frozen. A position is closed
at the following 15m open when a completed 15m bar shows the latched Volumatic
VIDYA direction opposite to the position. The original active stop still wins
an opening gap, and intrabar stop checks precede the close-confirmed flip.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys

import numpy as np
import pandas as pd

from yoyo.evaluation import market_sync_shock as market
from yoyo.evaluation import spike_v128_recent as replay
from yoyo.evaluation import spike_v1_v8_be05 as fixed
from yoyo.evaluation.spike_v7_fast import _initial_position_fast


ROOT = Path.cwd()
HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
PARENT_DIR = ROOT / "experiments/active/exp-spike-v1210-volumatic-vidya-gate-20261012-v1"
PARENT_BUILD = PARENT_DIR / "build.py"
PARENT_RESULTS = PARENT_DIR / "results_v1"
OUT = HERE / "results_v1"
MINUTES = 15
TRADE_FIELDS = [
    "trade_key", "signal_i", "signal_bar_open", "signal_close", "entry_i", "entry_time", "side",
    "entry_price", "initial_stop", "initial_risk", "initial_risk_frac", "exit_i", "exit_time",
    "exit_price", "exit_reason", "gross_return", "net_return", "gross_r", "net_r", "censored",
]


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _load_parent_builder():
    spec = importlib.util.spec_from_file_location("parent_vidya_gate_build", PARENT_BUILD)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load parent builder: {PARENT_BUILD}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _finish(pos: dict, *, i: int, stamp: pd.Timestamp, price: float, reason: str,
            censored: bool = False, precision: str = "bar_open_or_intrabar_window") -> dict:
    if not censored:
        side, entry = int(pos["side"]), float(pos["entry_price"])
        gross = side * (float(price) / entry - 1.0)
        pos["qty_realized"], pos["qty_remaining"] = 1.0, 0.0
        pos["realized_gross_return"] = gross
        pos["realized_net_return"] = gross - fixed.base.ENTRY_COST - fixed.base.EXIT_COST
        pos["last_exit_price"] = float(price)
    pos["last_exit_i"], pos["last_exit_time"], pos["last_exit_reason"] = int(i), stamp, reason
    result = fixed.base._trade_row(pos, censored=censored, precision=precision)
    result["vidya_flip_scheduled"] = bool(reason == "vidya_flip_next_open")
    return result


def early_attempt(prepared, i: int, side: int, trend: dict[str, np.ndarray]) -> tuple[str, dict | None]:
    """Replay one next-open entry with the causal VIDYA-flip exit overlay."""
    if i + 1 >= len(prepared.frame):
        return "no_next_bar", None
    if prepared.gap[i + 1]:
        return "next_bar_is_gap", None
    row = _initial_position_fast(prepared.frame.index, prepared.open, prepared.high, prepared.low,
                                 prepared.close, prepared.atr, prepared.gap, i, side, prepared.spec)
    if row is None:
        return "risk_invalid", None

    frame, gap, raw_side, spec = prepared.frame, prepared.gap, prepared.raw_side, prepared.spec
    oa, ha, la, ca, aa = prepared.open, prepared.high, prepared.low, prepared.close, prepared.atr
    start = int(frame.index.get_loc(pd.Timestamp(row["entry_time"])))
    pos = {key: row[key] for key in (
        "signal_i", "signal_bar_open", "entry_i", "entry_time", "side", "entry_price",
        "initial_stop", "initial_risk", "initial_risk_frac",
    )}
    pos["frozen_index_offset"] = int(row["entry_i"]) - start
    pos = fixed.base._new_trade(pos, trade_id=f"{prepared.context.key}:vidya-flip:{i}",
                                cohort=prepared.cohort, policy="vidya_flip_exit", context=prepared.context)
    pos.update(protection=float(row["initial_stop"]), mfe_r=0.0, trail_armed=False,
               be_armed=False, be_trigger_count=0)
    pending_reverse = False
    pending_vidya_flip = False

    for j in range(start, len(frame)):
        stamp = frame.index[j]
        if gap[j]:
            result = _finish(pos, i=j, stamp=stamp, price=math.nan,
                             reason="data_gap_censored", censored=True, precision="unknown_gap")
            return "censored_gap", result

        protection = float(pos["protection"])
        if pending_reverse or pending_vidya_flip:
            open_stopped = oa[j] <= protection if side == 1 else oa[j] >= protection
            if open_stopped:
                reason = "trailing_stop_gap" if protection != float(pos["initial_stop"]) else "initial_stop_gap"
                result = _finish(pos, i=j, stamp=stamp, price=float(oa[j]), reason=reason)
            else:
                reason = "opposite_v6_next_open" if pending_reverse else "vidya_flip_next_open"
                result = _finish(pos, i=j, stamp=stamp, price=float(oa[j]), reason=reason)
            return "closed", replay._mfe_fields(result, prepared) | result

        stopped = la[j] <= protection if side == 1 else ha[j] >= protection
        if stopped:
            price = min(oa[j], protection) if side == 1 else max(oa[j], protection)
            reason = "trailing_stop" if protection != float(pos["initial_stop"]) else "initial_stop"
            if (oa[j] <= protection if side == 1 else oa[j] >= protection):
                reason += "_gap"
            result = _finish(pos, i=j, stamp=stamp, price=float(price), reason=reason)
            return "closed", replay._mfe_fields(result, prepared) | result

        fixed._close_updates(pos, high=float(ha[j]), low=float(la[j]), close=float(ca[j]), atr=float(aa[j]),
                             spec=spec, enable_be=False, events=[], context=prepared.context,
                             arm="ordinary", policy="vidya_flip_exit", i=j)
        if int(raw_side[j]) == -side:
            pending_reverse = True
        if bool(trend["ready"][j]) and bool(trend["up"][j]) != (side == 1):
            pending_vidya_flip = True

    last = len(frame) - 1
    result = _finish(pos, i=last, stamp=frame.index[last], price=float(ca[last]),
                     reason="boundary_mark", censored=True, precision="last_complete_close")
    return "censored_boundary", replay._mfe_fields(result, prepared) | result


def early_serial(prepared, candidates: list[dict], key: str, trend: dict[str, np.ndarray]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Re-run serial occupancy so early exits can release later accepted signals."""
    trades, statuses, flat_from, holder = [], [], -1, None
    for event in candidates:
        i, side = int(event["signal_i"]), int(event["side"])
        common = {**event, "arm": "ordinary"}
        if i < flat_from:
            if event["in_window"]:
                statuses.append({**common, "status": "skipped_in_position", "blocking_trade": holder})
            continue
        status, result = early_attempt(prepared, i, side, trend)
        if event["in_window"]:
            statuses.append({**common, "status": status, "blocking_trade": None})
        if result is None:
            continue
        trade_key = f"{key}:ordinary:{pd.Timestamp(event['signal_close']).isoformat()}"
        row = {**common, **{name: result.get(name) for name in replay.source.TRADE_KEEP},
               "status": status, "trade_key": trade_key,
               "trail_armed": bool(result.get("close_peak_r", 0.0) >= 2.0),
               "mfe_known_r": result.get("mfe_known_r"), "mfe_upper_r": result.get("mfe_upper_r"),
               "close_peak_r": result.get("close_peak_r"),
               "stop_bar_excursion_ambiguous": result.get("stop_bar_excursion_ambiguous"),
               "vidya_flip_scheduled": bool(result.get("vidya_flip_scheduled", False))}
        if event["in_window"]:
            trades.append(row)
        holder = trade_key
        flat_from = len(prepared.frame) + 1 if status == "censored_boundary" else int(result["exit_i"])
    return pd.DataFrame(trades), pd.DataFrame(statuses)


def matched_controls(prepared, trades: pd.DataFrame, cfg: dict, trend: dict[str, np.ndarray]) -> pd.DataFrame:
    """Apply the same deterministic same-state control assignment and exit overlay."""
    if trades.empty:
        return pd.DataFrame()
    frame, minutes = prepared.frame, int(prepared.context.minutes)
    close_time = frame.index + pd.Timedelta(minutes=minutes)
    week = np.asarray(close_time.strftime("%G-W%V"))
    fold = np.where(close_time < pd.Timestamp(cfg["split"]), "earlier", "later")
    bins = np.searchsorted(np.asarray(cfg["volatility_bins"], float), prepared.atr / prepared.close, side="left")
    ready = frame.get("ready", pd.Series(False, index=frame.index)).fillna(False).to_numpy(bool)
    valid = (ready & trend["ready"] & np.isfinite(prepared.atr) & (prepared.atr > 0)
             & np.isfinite(prepared.close) & (prepared.close > 0) & ~prepared.gap
             & (close_time >= pd.Timestamp(cfg["start"])) & (close_time < pd.Timestamp(cfg["end"])))
    pools: dict[tuple, np.ndarray] = {}
    cache: dict[tuple[int, int], tuple[str, dict | None]] = {}
    rows = []
    for trade in trades.to_dict("records"):
        i, side = int(trade["signal_i"]), int(trade["side"])
        stratum = (side, str(week[i]), str(fold[i]), int(bins[i]))
        if stratum not in pools:
            regime = trend["up"] if side == 1 else ~trend["up"]
            pools[stratum] = np.flatnonzero(valid & regime & (week == stratum[1])
                                             & (fold == stratum[2]) & (bins == stratum[3]))
        choices = pools[stratum][pools[stratum] != i]
        chosen = None
        if len(choices):
            token = f"{cfg['control_seed']}|{trade['trade_key']}"
            chosen = int(choices[int(hashlib.sha256(token.encode()).hexdigest(), 16) % len(choices)])
        status, control = "empty_stratum", None
        if chosen is not None:
            cache_key = (chosen, side)
            if cache_key not in cache:
                cache[cache_key] = early_attempt(prepared, chosen, side, trend)
            status, control = cache[cache_key]
        matched = control is not None and status == "closed" and not bool(trade["censored"])
        rows.append({"trade_key": trade["trade_key"], "venue": "okx", "symbol": "ETH_USDT_SWAP",
                     "timeframe_min": minutes, "side": side, "utc_week": stratum[1], "fold": stratum[2],
                     "vol_bin": stratum[3], "vidya_up": bool(trend["up"][i]), "matched": bool(matched),
                     "reason": "matched" if matched else ("target_censored" if bool(trade["censored"]) else status),
                     "control_sig": chosen,
                     "control_signal_close": None if chosen is None else close_time[chosen],
                     "control_exit_time": None if control is None else control.get("exit_time"),
                     "control_exit_reason": None if control is None else control.get("exit_reason"),
                     "control_net_r": math.nan if not matched else float(control["net_r"]),
                     "control_net_return": math.nan if not matched else float(control["net_return"])})
    return pd.DataFrame(rows)


def isolated_fixed_entry_pairs(prepared, original: pd.DataFrame, trend: dict[str, np.ndarray]) -> pd.DataFrame:
    """Replay the original entries one-for-one to isolate the per-trade exit effect."""
    rows = []
    closed = original.loc[~original.censored.astype(bool)]
    for old in closed.to_dict("records"):
        status, new = early_attempt(prepared, int(old["signal_i"]), int(old["side"]), trend)
        if new is None:
            continue
        rows.append({"trade_key": old["trade_key"], "signal_i": int(old["signal_i"]),
                     "signal_close": old["signal_close"], "side": int(old["side"]),
                     "old_exit_time": old["exit_time"], "new_exit_time": new["exit_time"],
                     "old_exit_reason": old["exit_reason"], "new_exit_reason": new["exit_reason"],
                     "old_net_r": float(old["net_r"]), "new_net_r": float(new["net_r"]),
                     "delta_net_r": float(new["net_r"]) - float(old["net_r"]),
                     "new_status": status, "vidya_flip_exit": new["exit_reason"] == "vidya_flip_next_open",
                     "loss_reduced": bool(float(old["net_r"]) < 0 and float(new["net_r"]) > float(old["net_r"]) + 1e-12),
                     "loss_to_nonloss": bool(float(old["net_r"]) < 0 and float(new["net_r"]) >= 0),
                     "winner_harmed": bool(float(old["net_r"]) > 0 and float(new["net_r"]) < float(old["net_r"]) - 1e-12)})
    return pd.DataFrame(rows)


def paired_effect(pairs: pd.DataFrame, parent, seed: int) -> dict:
    if pairs.empty:
        return {"n": 0}
    x = pairs.loc[np.isfinite(pairs.delta_net_r)].copy()
    months = pd.to_datetime(x.signal_close, utc=True).dt.strftime("%Y-%m").to_numpy(str)
    delta = x.delta_net_r.to_numpy(float)
    return {"n": int(len(x)), "mean_delta_net_r": float(delta.mean()), "sum_delta_net_r": float(delta.sum()),
            "median_delta_net_r": float(np.median(delta)), "month_blocks": int(pd.Series(months).nunique()),
            "month_block_sign_flip_p_two_sided": float(market.month_block_sign_flip(
                delta, months, seed, parent.BOOTSTRAPS)),
            "month_block_bootstrap_ci95": parent._block_ci(delta, months, seed + 1),
            "flip_exit_count": int(x.vidya_flip_exit.sum()), "losses_reduced": int(x.loss_reduced.sum()),
            "losses_to_nonloss": int(x.loss_to_nonloss.sum()), "baseline_winners_harmed": int(x.winner_harmed.sum()),
            "positive_delta_count": int((delta > 0).sum()), "negative_delta_count": int((delta < 0).sum()),
            "zero_delta_count": int(np.isclose(delta, 0.0, atol=1e-12).sum())}


def _assert_gate_parity(actual: pd.DataFrame, expected: pd.DataFrame) -> dict:
    fields = ["trade_key", "signal_i", "side", "entry_time", "entry_price", "initial_stop", "exit_time",
              "exit_price", "exit_reason", "gross_return", "net_return", "gross_r", "net_r", "censored"]
    left, right = expected.loc[:, fields].copy(), actual.loc[:, fields].copy()
    left = left.sort_values("trade_key").reset_index(drop=True)
    right = right.sort_values("trade_key").reset_index(drop=True)
    for col in ("entry_time", "exit_time"):
        left[col], right[col] = pd.to_datetime(left[col], utc=True), pd.to_datetime(right[col], utc=True)
    pd.testing.assert_frame_equal(left, right, check_dtype=False, check_exact=False, rtol=1e-10, atol=1e-10)
    return {"closed_gate_trades_checked": int(len(left)), "exact": True}


def run() -> None:
    cfg = json.loads(CONFIG_PATH.read_text())
    if cfg["experiment_id"] != HERE.name or float(cfg["execution"]["round_trip_cost"]) != 0.002:
        raise ValueError("experiment identity or cost contract changed")
    parent = _load_parent_builder()
    parent_cfg = parent.load_config()
    start, split, end = (pd.Timestamp(cfg["scope"][key]) for key in ("start", "split", "end"))
    base1m, data_info = parent._eth_base(parent_cfg)
    base5m, partial5m = parent.low.complete_bars(base1m, 5)
    bars15m, partial15m = replay.complete_bars(base5m, MINUTES)
    if partial5m or partial15m:
        raise ValueError(f"partial bars: 5m={partial5m}, 15m={partial15m}")
    facts = replay.facts_for(bars15m, base5m, parent_cfg["eth_okx"]["asset"],
                             float(parent_cfg["eth_okx"]["tick"]), MINUTES)
    frame = facts["frame"]
    if not frame.index.is_unique or not frame.index.is_monotonic_increasing:
        raise ValueError("15m frame index is not unique and monotonic")
    identity = {"venue": "okx", "symbol": "ETH_USDT_SWAP", "asset": "ETH", "timeframe_min": MINUTES}
    key = "okx:ETH_USDT_SWAP:15m"
    prepared = replay.source.prepared_arm(frame, facts["gap"], facts["side"], key,
                                          identity, MINUTES, 0.01)
    events = parent._make_events(facts, frame, identity, key, MINUTES, start, end)
    trend = parent.volumatic_trend(frame)
    gate_events, gate_counts = parent._condition(events, trend)

    # Baseline and parent gate must reappear exactly before applying the overlay.
    incumbent_v129, _ = replay.serial(prepared, events, "ordinary", key)
    v129_path = ROOT / parent_cfg["eth_okx"]["baseline_experiments"]["15"]
    v129_expected = pd.read_csv(v129_path)
    v129_parity = parent._assert_parent_parity(pd.DataFrame(incumbent_v129), v129_expected, "OKX ETH 15m")
    incumbent_gate, _ = replay.serial(prepared, gate_events, "ordinary", key)
    parent_gate_path = PARENT_RESULTS / "vidya_gate_trades.csv.gz"
    saved_gate = pd.read_csv(parent_gate_path, parse_dates=["entry_time", "exit_time", "signal_close"])
    saved_gate = saved_gate.loc[(saved_gate.symbol == "ETH_USDT_SWAP") & (saved_gate.timeframe_min == MINUTES)]
    incumbent_gate_df = pd.DataFrame(incumbent_gate)
    parity = _assert_gate_parity(incumbent_gate_df, saved_gate)

    early_trades, early_statuses = early_serial(prepared, gate_events, key, trend)
    if early_trades.trade_key.duplicated().any():
        raise ValueError("early-exit serial ledger has duplicate trade keys")
    if len(early_trades):
        closed = early_trades.loc[~early_trades.censored.astype(bool)]
        np.testing.assert_allclose(closed.gross_return - closed.net_return, 0.002, atol=1e-12)
        np.testing.assert_allclose(closed.net_r, closed.net_return / closed.initial_risk_frac, rtol=1e-10, atol=1e-10)

    control_cfg = {
        "start": str(start), "split": str(split), "end": str(end),
        "volatility_bins": parent_cfg["execution"]["volatility_bins"],
        "control_seed": int(parent_cfg["execution"]["control_seed"]),
    }
    early_controls = matched_controls(prepared, early_trades, control_cfg, trend)
    incumbent_controls = pd.read_csv(PARENT_RESULTS / "vidya_gate_controls.csv.gz")
    incumbent_controls = incumbent_controls.loc[(incumbent_controls.symbol == "ETH_USDT_SWAP")
                                                & (incumbent_controls.timeframe_min == MINUTES)]
    incumbent_metrics = parent._metric(incumbent_gate_df, incumbent_controls, start, split, end,
                                       int(parent_cfg["execution"]["stat_seed"]) + 1)
    early_metrics = parent._metric(early_trades, early_controls, start, split, end,
                                   int(parent_cfg["execution"]["stat_seed"]) + 1)
    fixed_pairs = isolated_fixed_entry_pairs(prepared, incumbent_gate_df, trend)
    paired = paired_effect(fixed_pairs, parent, int(parent_cfg["execution"]["stat_seed"]) + 21)

    raw_input_rows = sum(len(pd.read_csv(path, usecols=["ts"])) for path in map(Path, data_info["inputs"]))
    source = {
        "study_builder": digest(Path(__file__).resolve()),
        "parent_builder": digest(PARENT_BUILD),
        "parent_config": digest(PARENT_DIR / "config.json"),
        "parent_run_receipt": digest(PARENT_RESULTS / "receipt.json"),
        "parent_gate_ledger": digest(parent_gate_path),
        "parent_control_ledger": digest(PARENT_RESULTS / "vidya_gate_controls.csv.gz"),
        "v129_eth15_ledger": digest(v129_path),
        "raw_inputs": data_info["inputs"],
    }
    diagnostics = {
        "raw_1m_rows_before_identical_duplicate_collapse": int(raw_input_rows),
        "merged_1m_rows": int(len(base1m)),
        "identical_boundary_or_file_overlap_rows_collapsed": int(raw_input_rows - len(base1m)),
        "bars_5m": int(len(base5m)), "bars_15m": int(len(frame)),
        "partial_5m_buckets": int(partial5m), "partial_15m_buckets": int(partial15m),
        "15m_data_gap_bars": int(np.asarray(facts["gap"], dtype=bool).sum()),
        "first_15m_open": str(frame.index[0]), "last_15m_open": str(frame.index[-1]),
        "first_15m_close": str(frame.index[0] + pd.Timedelta(minutes=MINUTES)),
        "last_15m_close": str(frame.index[-1] + pd.Timedelta(minutes=MINUTES)),
        "data_receipt": data_info["receipt"], "raw_1m_source_first": data_info["first"],
        "raw_1m_source_last": data_info["last"], "raw_1m_rows": int(data_info["rows_1m"]),
    }
    summary = {
        "experiment_id": cfg["experiment_id"], "parent_experiment_id": cfg["parent_experiment_id"],
        "variant": "V12.10 VIDYA entry gate + confirmed VIDYA direction-flip exit at next 15m open",
        "baseline_variant": "same V12.10 VIDYA entry gate + original V12.9 exit engine",
        "scope": cfg["scope"], "exit_rule": cfg["single_variable"],
        "data_quality": diagnostics,
        "replay_validation": {"v129_saved_baseline_parity": v129_parity,
                               "parent_v1210_gate_parity": parity,
                               "gate_candidates": gate_counts,
                               "cost_contract": "closed trades are checked at exactly 0.2% round trip"},
        "original_gate_metrics": incumbent_metrics,
        "vidya_flip_exit_metrics": early_metrics,
        "same_entry_isolated_exit_effect": paired,
        "early_exit_reason_counts": {str(k): int(v) for k, v in early_trades.exit_reason.value_counts(dropna=False).items()},
        "early_exit_trade_flow": {"opened_in_window": int(len(early_trades)),
                                  "closed": int((~early_trades.censored.astype(bool)).sum()),
                                  "censored": int(early_trades.censored.astype(bool).sum()),
                                  "skipped_in_position": int((early_statuses.status == "skipped_in_position").sum()),
                                  "matched_controls": int(early_controls.matched.astype(bool).sum())},
        "limitations": [
            "The direction-flip exit is confirmed at a 15m close and filled at the next bar open; it does not act within the flip bar.",
            "An opening gap through the already-active initial/trailing stop keeps stop-gap priority.",
            "The fixed-entry paired comparison isolates the per-trade exit effect but does not include trades admitted later by freed serial occupancy; the full serial ledger includes those entries.",
            "Costs remain the frozen 0.2% round-trip assumption; dynamic spread, funding, and slippage are not modeled.",
            "Research only; not a promotion or live-execution result."
        ],
        "training_eligible": False, "production_eligible": False,
    }

    OUT.mkdir(parents=True, exist_ok=True)
    trades_path, controls_path = OUT / "vidya_flip_trades.csv.gz", OUT / "vidya_flip_controls.csv.gz"
    pairs_path = OUT / "same_entry_exit_pairs.csv.gz"
    early_trades.to_csv(trades_path, index=False, compression={"method": "gzip", "mtime": 0})
    early_controls.to_csv(controls_path, index=False, compression={"method": "gzip", "mtime": 0})
    fixed_pairs.to_csv(pairs_path, index=False, compression={"method": "gzip", "mtime": 0})
    summary_path = OUT / "summary.json"
    summary_path.write_text(json.dumps(parent.clean(summary), ensure_ascii=False, indent=2, sort_keys=True,
                                      allow_nan=False) + "\n")
    receipt = {"experiment_id": cfg["experiment_id"], "source_commit": __import__("subprocess").check_output(
                  ["git", "rev-parse", "HEAD"], text=True).strip(),
              "config_sha256": digest(CONFIG_PATH), "source_sha256": source,
              "parent_source_commit": json.loads((PARENT_RESULTS / "receipt.json").read_text())["source_commit"],
              "inputs": source["raw_inputs"],
              "artifacts_sha256": {path.name: digest(path) for path in
                                   (trades_path, controls_path, pairs_path, summary_path)}}
    receipt_path = OUT / "receipt.json"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"incumbent": incumbent_metrics, "vidya_flip_exit": early_metrics,
                      "same_entry_isolated_exit_effect": paired,
                      "exit_counts": summary["early_exit_reason_counts"],
                      "data_quality": diagnostics, "replay_validation": summary["replay_validation"]},
                     ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    run()
