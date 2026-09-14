"""Fixed V9/V8 serial replay on the owner-authorized frozen two-year pool.

Source: receipt-bound V8 full_v3 and V9 ef4de009d0; authorization is recorded in
the experiment's PROJECT_PLAN. Gates use current RV/base metadata and scheduled
signal close only. Initial-stop diagnostics use close/ATR and [t-4,t] highs/lows.
Random entries match stream, side, month and fixed contemporaneous ATR/close
bins; their outcomes use the same parent fixed-entry exit engine and 20bp cost.
No indicator rebuild, fetching, tuning, notification or execution integration.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import subprocess
import time

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_exit_policy_study as base
from yoyo.evaluation import spike_v1_v8_be05 as engine
from yoyo.evaluation.spike_burst_replay import risk_reference
from yoyo.evaluation.spike_v8_six_filters import SOURCE, assert_baseline_parity, _committed
from yoyo.evaluation.spike_v9 import VERSION, entry_decision

EXP = Path("experiments/active/exp-spike-v9-full-backtest-20260915-v1")
CONFIG = EXP / "config.json"
TEST = Path("tests/evaluation/test_spike_v9_full_replay.py")
VOL_BINS = np.array([.005, .01, .02, .05, .1])
CONTROL_SEED = 91509
DEPENDENCIES = (Path(__file__), TEST, CONFIG, EXP / "PROJECT_PLAN.md", EXP / "authorization.json",
                Path("yoyo/evaluation/spike_v9.py"), Path(engine.__file__), Path(base.__file__),
                Path("yoyo/evaluation/spike_v7_fast.py"), Path("yoyo/evaluation/spike_v8_replay.py"),
                Path("yoyo/evaluation/spike_burst_replay.py"))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalized_gap(prepared):
    """Recognize a missing candle only at the later candle's arrival."""
    gap = prepared.frame.index.to_series().diff().ne(pd.Timedelta(minutes=prepared.context.minutes)).to_numpy()
    if len(gap):
        gap[0] = False
    return prepared.gap | gap


def prepare_v9(prepared):
    """Compute diagnostics only for V8 candidates, preserving raw exit events."""
    frame, context = prepared.frame, prepared.context
    if frame.index.tz is None or not frame.index.is_unique or not frame.index.is_monotonic_increasing:
        raise ValueError("invalid input clock")
    allowed = np.zeros(len(frame), dtype=bool)
    gap = normalized_gap(prepared)
    delta = pd.Timedelta(minutes=context.minutes)
    rows = []
    for i in np.flatnonzero(prepared.allowed):
        stamp, side = frame.index[i], int(prepared.raw_side[i])
        original_i = prepared.ordinal.get(stamp)
        if original_i is None:
            raise ValueError("candidate missing frozen ordinal")
        rv = frame.rv.iloc[i] if "rv" in frame else math.nan
        decision = entry_decision(context.identity.get("asset"), rv, stamp + delta)
        allowed[i] = bool(decision["v9_bundle_allowed"])
        entry, atr = float(prepared.close[i]), float(prepared.atr[i])
        stop = risk = fraction = cost_r = math.nan
        if i >= 4 and not gap[i-3:i+1].any():
            extreme = np.min(prepared.low[i-4:i+1]) if side == 1 else np.max(prepared.high[i-4:i+1])
            ref = risk_reference(side, entry, float(extreme), atr, tick=prepared.spec.tick)
            if ref.valid:
                stop, risk = ref.stop, ref.risk
                fraction, cost_r = risk / entry, .002 / (risk / entry)
        rows.append({"stream_key": context.key, **context.identity, "local_i": int(i),
                     "signal_i": int(original_i), "signal_bar_open": stamp, "side": side,
                     **decision, "v8": True, "v9": bool(allowed[i]), "strategy_version": VERSION,
                     "asset_type": context.identity.get("asset_type") or "unknown",
                     "signal_atr_pct": atr / entry if entry > 0 else math.nan,
                     "reference_price": entry, "reference_initial_stop": stop,
                     "reference_initial_risk": risk, "reference_risk_fraction": fraction,
                     "reference_cost_r": cost_r, "risk_basis": "confirmation_close_reference_not_fill"})
    columns = ["stream_key", "venue", "symbol", "asset", "timeframe_min", "local_i", "signal_i",
               "signal_bar_open", "side", "base_asset", "volume_ratio", "scheduled_open_utc",
               "v9_bundle_allowed", "v9_bundle_reasons", "v8", "v9", "strategy_version", "asset_type",
               "signal_atr_pct", "reference_price", "reference_initial_stop", "reference_initial_risk",
               "reference_risk_fraction", "reference_cost_r", "risk_basis"]
    return replace(prepared, allowed=allowed, gap=gap), pd.DataFrame(rows, columns=columns)


def serial_pair(context):
    """Replay both fixed masks with one frozen set of price/state arrays."""
    prepared = engine.prepare_arm(context, arm="v8")
    treatment, decisions = prepare_v9(prepared)
    outputs = {}
    for arm, value in (("v8", prepared), ("v9", treatment)):
        trades, fills, _ = engine.replay_serial(context, arm="v8", enable_be=False, prepared=value)
        for table in (trades, fills):
            table["arm"] = arm
            table["strategy_version"] = VERSION if arm == "v9" else "frozen-v8-full-v3"
            table["trade_id"] = table.trade_id.str.replace(":v8:baseline:", f":{arm}:baseline:", regex=False)
        outputs[arm] = (trades, fills)
    return outputs, decisions, treatment


def random_controls(prepared, targets: pd.DataFrame) -> pd.DataFrame:
    """One fixed-seed random entry per event, never resampling failed outcomes.

    Eligibility uses original ready bars and current fixed ATR/price bins.
    Future OHLC is only used after the deterministic draw to evaluate its path.
    Missing/unresolved paths remain unmatched rather than drawing a replacement.
    Each chosen single-event path has the original raw reversal and stop rules.
    """
    frame, context = prepared.frame, prepared.context
    delta = pd.Timedelta(minutes=context.minutes)
    fraction = prepared.atr / prepared.close
    bins = np.searchsorted(VOL_BINS, fraction, side="left")
    months = frame.index.strftime("%Y-%m")
    ready = frame.get("ready", pd.Series(False, index=frame.index)).fillna(False).to_numpy(bool)
    finite = np.isfinite(frame[["open", "high", "low", "close", "atr"]].to_numpy(float)).all(axis=1)
    eligible = ready & finite & (prepared.close > 0) & (prepared.atr > 0)
    scheduled = frame.index + delta
    folds = np.where(scheduled < base.SPLIT, "earlier", "later")
    eligible &= (scheduled >= base.START) & (scheduled < base.END)
    pools = {}
    for month, bucket, fold in sorted(set(zip(months[eligible], bins[eligible], folds[eligible]))):
        pools[(month, int(bucket), fold)] = np.flatnonzero(eligible & (months == month) & (bins == bucket) & (folds == fold))
    paths = {}
    rows = []
    for target in targets.itertuples(index=False):
        stamp, side = pd.Timestamp(target.signal_bar_open), int(target.side)
        i = int(frame.index.get_loc(stamp))
        item = {"stream_key": context.key, **context.identity, "arm": target.arm,
                "signal_i": int(target.signal_i), "signal_bar_open": stamp,
                "entry_time": target.entry_time, "exit_time": target.exit_time,
                "side": side, "matched": False, "reason": "no_exact_match",
                "target_net_r": target.net_r, "target_net_return": target.net_return,
                "target_censored": bool(target.censored), "control_net_r": math.nan,
                "control_net_return": math.nan, "control_signal_time": pd.NaT,
                "control_entry_time": pd.NaT, "control_exit_time": pd.NaT,
                "control_censored": True, "month": months[i], "vol_bin": int(bins[i])}
        options = pools.get((months[i], int(bins[i]), folds[i]), np.array([], dtype=int))
        options = options[options != i]
        if len(options):
            choice = int(hashlib.sha256(f"{CONTROL_SEED}|{context.key}|{stamp.isoformat()}|{side}".encode()).hexdigest(), 16) % len(options)
            chosen = int(options[choice])
            item["control_signal_time"] = frame.index[chosen]
            key = (chosen, side)
            if key not in paths:
                initial = base._initial_position_fast(frame.index, prepared.open, prepared.high, prepared.low,
                                                      prepared.close, prepared.atr, prepared.gap, chosen, side, prepared.spec)
                if initial is not None and base.START <= initial["entry_time"] < base.END:
                    initial["initial_risk_frac"] = initial["initial_risk"] / initial["entry_price"]
                    paths[key] = engine.replay_fixed_entry(context, pd.Series(initial), arm="v8", enable_be=False, prepared=prepared)
                else:
                    paths[key] = None
            control = paths[key]
            if control is None:
                item["reason"] = "initial_position_unavailable"
            else:
                item.update(control_entry_time=control["entry_time"], control_exit_time=control["exit_time"],
                            control_censored=bool(control["censored"]), control_net_r=control["net_r"],
                            control_net_return=control["net_return"])
                item["matched"] = not bool(target.censored) and not bool(control["censored"])
                item["reason"] = "matched" if item["matched"] else "censored_pair"
        rows.append(item)
    columns = ["stream_key", "venue", "symbol", "asset", "timeframe_min", "arm", "signal_i", "signal_bar_open",
               "entry_time", "exit_time", "side", "matched", "reason", "target_net_r", "target_net_return",
               "target_censored", "control_net_r", "control_net_return", "control_signal_time", "control_entry_time",
               "control_exit_time", "control_censored", "month", "vol_bin"]
    return pd.DataFrame(rows, columns=columns)


def check_completed(folder: Path, identity: str):
    path = folder / "completion.json"
    if not path.exists():
        return None
    receipt = json.loads(path.read_text())
    if receipt.get("status") != "complete" or receipt.get("run_identity") != identity:
        raise ValueError(f"completion identity mismatch: {folder}")
    for name, expected in receipt["files"].items():
        if digest(folder / name) != expected:
            raise ValueError(f"output hash drift: {folder / name}")
    return receipt


def run_stream(args):
    key, output, identity = args
    output = Path(output)
    final = output / "streams" / key
    old = check_completed(final, identity)
    if old:
        return old
    started = time.perf_counter()
    staging = output / "streams" / f".{key}.staging"
    staging.mkdir()  # Stale staging is evidence; never silently replace it.
    try:
        source = SOURCE / "streams" / key
        archived = json.loads((source / "completion.json").read_text())
        name = "v8.serial_baseline.csv.gz"
        if archived.get("status") != "complete" or digest(source / name) != archived["files"][name]:
            raise ValueError(f"baseline source receipt mismatch: {key}")
        context = base.load_verified_stream(engine.RAW / "streams" / key)
        outputs, decisions, treatment = serial_pair(context)
        assert_baseline_parity(outputs["v8"][0], pd.read_csv(source / name))
        combined = pd.concat([outputs[a][0] for a in ("v8", "v9")], ignore_index=True)
        controls = random_controls(treatment, combined)
        summaries = []
        for arm, (trades, fills) in outputs.items():
            trades.to_csv(staging / f"{arm}.trades.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
            fills.to_csv(staging / f"{arm}.fills.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
            closed = trades.loc[~trades.censored.astype(bool)]
            summaries.append({"stream_key": key, **context.identity, "arm": arm, "events": len(trades),
                              "closed": len(closed), "censored": len(trades)-len(closed),
                              "net_r": float(closed.net_r.sum()), "realized_ge10": int(closed.net_r.ge(10).sum()),
                              "candidates": len(decisions) if arm == "v8" else int(decisions.v9.sum())})
        decisions.to_csv(staging / "decisions.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
        controls.to_csv(staging / "controls.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
        receipt = {"status": "complete", "key": key, "run_identity": identity,
                   "source_completion_sha256": digest(source / "completion.json"),
                   "raw_completion_sha256": digest(context.path / "completion.json"),
                   "cache_sha256": context.receipt["cache_sha256"], "baseline_parity": True,
                   "files": {p.name: digest(p) for p in staging.iterdir() if p.is_file()},
                   "summaries": summaries, "wall_seconds": time.perf_counter()-started}
        (staging / "completion.json").write_text(json.dumps(receipt, indent=2)+"\n")
        staging.replace(final)
        return receipt
    except Exception as exc:
        (staging / "failure.json").write_text(json.dumps({"key": key, "type": type(exc).__name__, "error": str(exc)}, indent=2)+"\n")
        staging.replace(output / "failures" / f"{key}.{time.time_ns()}")
        raise


def run(output: Path, *, workers: int = 3, limit: int | None = None):
    if not _committed(DEPENDENCIES):
        raise ValueError("commit unchanged runner/tests/config/dependencies before any market replay")
    config = json.loads(CONFIG.read_text())
    if digest(SOURCE / "manifest.json") != config["baseline_manifest_sha256"] or digest(engine.RAW / "manifest.json") != config["raw_manifest_sha256"]:
        raise ValueError("frozen pool manifest changed")
    folders = sorted(p.name for p in (engine.RAW / "streams").iterdir() if (p / "completion.json").is_file())
    if len(folders) != config["expected_streams"]:
        raise ValueError("source stream coverage changed")
    identity = {str(p): digest(p) for p in DEPENDENCIES}
    identity["baseline_manifest"] = config["baseline_manifest_sha256"]
    identity["raw_manifest"] = config["raw_manifest_sha256"]
    identity_hash = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    output.mkdir(parents=True, exist_ok=True)
    for directory in ("streams", "failures"):
        (output / directory).mkdir(exist_ok=True)
    ipath = output / "identity.json"
    if ipath.exists() and json.loads(ipath.read_text()) != identity:
        raise ValueError("run identity drift; choose a new output directory")
    ipath.write_text(json.dumps(identity, indent=2, sort_keys=True)+"\n")
    exposure = output / "evaluation_started.json"
    if not exposure.exists():
        exposure.write_text(json.dumps({"started_at_unix": time.time(), "v9_holdout_consumption": 1,
                                       "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                                       "history": "owner-authorized already-exposed full history", "run_identity": identity_hash}, indent=2)+"\n")
    keys = folders[:limit] if limit is not None else folders
    summaries = []
    start = time.perf_counter()
    with ProcessPoolExecutor(max_workers=max(1, min(workers, 4))) as pool:
        tasks = {pool.submit(run_stream, (key, str(output), identity_hash)): key for key in keys}
        for number, future in enumerate(as_completed(tasks), 1):
            receipt = future.result()
            summaries.extend(receipt["summaries"])
            if number == 1 or number % 25 == 0 or number == len(keys):
                print(json.dumps({"completed": number, "target": len(keys), "last_key": receipt["key"],
                                  "elapsed_seconds": round(time.perf_counter()-start, 2)}), flush=True)
    pd.DataFrame(summaries).sort_values(["stream_key", "arm"]).to_csv(output / "stream_summary.csv", index=False)
    (output / "manifest.json").write_text(json.dumps({"complete": limit is None, "streams": len(keys),
        "expected_streams": config["expected_streams"], "run_identity": identity_hash, "v9_holdout_consumption": 1,
        "strategy_version": VERSION, "arms": ["v8", "v9"], "all_baseline_parity": True,
        "controls": "one deterministic same-stream/side/month/fixed ATR-fraction bucket draw per actual event; same exits/cost; unresolved draws kept",
        "summary_sha256": digest(output / "stream_summary.csv")}, indent=2)+"\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    run(args.output, workers=args.workers, limit=args.limit)
