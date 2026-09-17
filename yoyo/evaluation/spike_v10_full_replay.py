"""Serial replay of SPIKE V9 against the V10 trendline-break gate.

Source: the frozen V8/V9 pool (`exp-spike-v9-full-backtest-20260915-v1`) and the
owner's trendline indicator pasted 2026-09-18. Only the entry mask changes
between arms; exits, the 0.2% round-trip cost, the raw opposite-confirmation
reversal and the fixed-entry random controls are the published V9 ones.

Holdout: every stream is truncated so that no bar opening at or after
`HOLDOUT_START` is loaded at all. The 2026-05-04..2026-09-10 tail of the frozen
pool therefore stays unread by this configuration; crossing it needs a numbered
`docs/HOLDOUT_LEDGER.md` entry naming this experiment, and the runner refuses
without one. Truncation is verified, not assumed: the V9 arm is compared
trade-for-trade with the published V9 ledger over every trade that both closed
before the cut.

No fetching, no training, no tuning, no promotion, no execution integration.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import time

import numpy as np
import pandas as pd

from yoyo.contracts.holdout import HOLDOUT_START
from yoyo.evaluation import spike_exit_policy_study as base
from yoyo.evaluation import spike_v1_v8_be05 as engine
from yoyo.evaluation.spike_v1_v8_be05 import KEY
from yoyo.evaluation.spike_v8_six_filters import _committed
from yoyo.evaluation.spike_v10 import ARMS, VERSION, break_ages, gate_mask, gate_reason, signal_break_age
from yoyo.evaluation.spike_v9_full_replay import digest, prepare_v9, random_controls
from yoyo.evaluation.trendline_break import TrendlineParams

EXP = Path("experiments/active/exp-spike-v10-trendline-gate-20260918-v1")
CONFIG = EXP / "config.json"
TEST = Path("tests/evaluation/test_spike_v10_full_replay.py")
V9_LEDGER = Path("experiments/active/exp-spike-v9-full-backtest-20260915-v1/results/full_v1")
CUT = pd.Timestamp(HOLDOUT_START)
DEPENDENCIES = (Path(__file__), TEST, CONFIG, EXP / "PROJECT_PLAN.md",
                Path("yoyo/evaluation/spike_v10.py"), Path("yoyo/evaluation/trendline_break.py"),
                Path("yoyo/evaluation/spike_v9_full_replay.py"), Path("yoyo/evaluation/spike_v9.py"),
                Path(engine.__file__), Path(base.__file__))


def truncate(context: base.StreamContext, cut: pd.Timestamp) -> tuple[base.StreamContext, int, int]:
    """Drop every bar whose open is at or after `cut`, on every aligned frame."""
    index = context.cache["bars"].index
    keep = index + pd.Timedelta(minutes=context.minutes) <= cut
    cache = dict(context.cache)
    for name, value in context.cache.items():
        if isinstance(value, (pd.DataFrame, pd.Series)) and value.index.equals(index):
            cache[name] = value.loc[keep].copy()
    return replace(context, cache=cache), int(keep.sum()), int((~keep).sum())


def v9_parity(key: str, ours: pd.DataFrame, last_open: pd.Timestamp) -> dict[str, int]:
    """Every published V9 trade that exited on a kept bar must reappear here.

    This is what makes truncation auditable: if removing the tail had changed a
    single earlier decision, these two ledgers would disagree.

    The boundary is the last KEPT bar's open, not its close. Exits are stamped
    with the exit bar's open, so a published trade stamped at the cut instant
    exited on the first holdout bar -- it belongs to the tail this run does not
    have, and comparing against it would be comparing against a bar we refused
    to read.
    """
    folder = V9_LEDGER / "streams" / key
    receipt = json.loads((folder / "completion.json").read_text())
    name = "v9.trades.csv.gz"
    if receipt.get("status") != "complete" or digest(folder / name) != receipt["files"][name]:
        raise ValueError(f"published V9 ledger receipt mismatch: {key}")
    return compare_v9_ledger(key, pd.read_csv(folder / name), ours, last_open)


def compare_v9_ledger(key: str, published: pd.DataFrame, ours: pd.DataFrame,
                      last_open: pd.Timestamp) -> dict[str, float]:
    """Compare two ledgers over the trades that both exited on a kept bar."""
    for table in (published, ours):
        for column in ("entry_time", "exit_time"):
            table[column] = pd.to_datetime(table[column], utc=True, errors="coerce")
    settled = published.loc[published.exit_time.le(last_open) & ~published.censored.astype(bool)]
    left = settled[KEY].reset_index(drop=True)
    right = ours.loc[ours.exit_time.le(last_open) & ~ours.censored.astype(bool)][KEY].reset_index(drop=True)
    if len(left) != len(right):
        raise ValueError(f"truncated V9 arm has a different trade count than the published ledger: {key}")
    drift = 0.0
    # Compare values, never dtypes: a stream whose every trade opened after the
    # cut leaves both sides empty, and an empty CSV column and an empty replay
    # column legitimately carry different dtypes.
    for column in KEY:
        if column in ("entry_price", "exit_price", "initial_stop", "initial_risk", "net_return", "net_r"):
            # The published ledger is a CSV, so its floats are decimal
            # round-trips of the originals, not the originals.
            gap = np.abs(left[column].to_numpy(float) - right[column].to_numpy(float))
            scale = np.maximum(np.abs(left[column].to_numpy(float)), 1e-12)
            if np.any(gap / scale > 1e-9):
                raise ValueError(f"truncated V9 arm diverged from the published ledger at {column}: {key}")
            drift = max(drift, float((gap / scale).max()) if len(gap) else 0.0)
        elif left[column].tolist() != right[column].tolist():
            raise ValueError(f"truncated V9 arm diverged from the published ledger at {column}: {key}")
    return {"published_trades": int(len(published)), "compared_trades": int(len(left)),
            "published_after_cut": int(len(published) - len(settled)),
            "max_relative_float_drift": drift}


def replay_stream(context: base.StreamContext) -> tuple[dict[str, tuple[pd.DataFrame, pd.DataFrame]], pd.DataFrame, object]:
    """Run every pre-registered arm off one frozen set of price/state arrays."""
    prepared = engine.prepare_arm(context, arm="v8")
    treatment, decisions = prepare_v9(prepared)
    ages = break_ages(treatment.frame, treatment.gap, float(context.cache["tick"]))
    v9_allowed = treatment.allowed
    age_at_signal = signal_break_age(treatment.raw_side, ages)
    outputs = {}
    for arm, max_age in ARMS.items():
        mask = v9_allowed & gate_mask(treatment.raw_side, ages, max_age)
        trades, fills, _ = engine.replay_serial(context, arm="v8", enable_be=False,
                                                prepared=replace(treatment, allowed=mask))
        for table in (trades, fills):
            table["arm"] = arm
            table["strategy_version"] = VERSION if arm != "v9" else "published-v9-truncated"
            table["trade_id"] = table.trade_id.str.replace(":v8:baseline:", f":{arm}:baseline:", regex=False)
        outputs[arm] = (trades, fills)
    if len(decisions):
        local = decisions.local_i.to_numpy(int)
        decisions["trendline_break_age"] = age_at_signal[local]
        decisions["trendline_line_active"] = np.where(
            decisions.side.to_numpy(int) == 1, ages.long_line_active.to_numpy()[local],
            ages.short_line_active.to_numpy()[local])
        for arm, max_age in ARMS.items():
            if max_age is None:
                continue
            decisions[arm] = decisions.v9.to_numpy(bool) & (decisions.trendline_break_age.to_numpy() >= 0) \
                & (decisions.trendline_break_age.to_numpy() <= max_age)
        decisions["v10_reason"] = [gate_reason(int(a), ARMS["v10_a12"]) for a in decisions.trendline_break_age]
    else:
        for column in ("trendline_break_age", "trendline_line_active", "v10_reason", *[a for a in ARMS if a != "v9"]):
            decisions[column] = pd.Series(dtype="object")
    return outputs, decisions, treatment


def run_stream(args):
    key, output, identity = args
    output = Path(output)
    final = output / "streams" / key
    if (final / "completion.json").is_file():
        receipt = json.loads((final / "completion.json").read_text())
        if receipt.get("status") != "complete" or receipt.get("run_identity") != identity:
            raise ValueError(f"completion identity mismatch: {final}")
        return receipt
    started = time.perf_counter()
    staging = output / "streams" / f".{key}.staging"
    staging.mkdir()
    try:
        context = base.load_verified_stream(engine.RAW / "streams" / key)
        context, kept, dropped = truncate(context, CUT)
        if kept == 0:
            # A symbol that only listed inside the holdout window contributes
            # nothing here. It is recorded, not silently dropped.
            receipt = {"status": "complete", "key": key, "run_identity": identity, "skipped": "no_bar_before_cut",
                       "cache_sha256": context.receipt["cache_sha256"], "bars_kept": 0, "bars_dropped": dropped,
                       "files": {}, "summaries": [{"stream_key": key, **context.identity, "arm": arm, "events": 0,
                                                   "closed": 0, "censored": 0, "net_r": 0.0, "gross_r": 0.0,
                                                   "realized_ge10": 0, "admitted": 0} for arm in ARMS],
                       "wall_seconds": time.perf_counter() - started}
            (staging / "completion.json").write_text(json.dumps(receipt, indent=2) + "\n")
            staging.replace(final)
            return receipt
        last_open = context.cache["bars"].index[-1]
        last_close = last_open + pd.Timedelta(minutes=context.minutes)
        outputs, decisions, treatment = replay_stream(context)
        parity = v9_parity(key, outputs["v9"][0].copy(), last_open)
        combined = pd.concat([outputs[arm][0] for arm in ARMS], ignore_index=True)
        controls = random_controls(treatment, combined)
        summaries = []
        for arm, (trades, fills) in outputs.items():
            trades.to_csv(staging / f"{arm}.trades.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
            fills.to_csv(staging / f"{arm}.fills.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
            closed = trades.loc[~trades.censored.astype(bool)]
            summaries.append({"stream_key": key, **context.identity, "arm": arm, "events": len(trades),
                              "closed": len(closed), "censored": len(trades) - len(closed),
                              "net_r": float(closed.net_r.sum()), "gross_r": float(closed.gross_r.sum()),
                              "realized_ge10": int(closed.net_r.ge(10).sum()),
                              "admitted": int(decisions[arm].sum()) if arm != "v9" and len(decisions) else int(decisions.v9.sum()) if len(decisions) else 0})
        decisions.to_csv(staging / "decisions.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
        controls.to_csv(staging / "controls.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
        receipt = {"status": "complete", "key": key, "run_identity": identity,
                   "cache_sha256": context.receipt["cache_sha256"], "bars_kept": kept, "bars_dropped": dropped,
                   "last_close": str(last_close), "v9_parity": parity,
                   "files": {p.name: digest(p) for p in staging.iterdir() if p.is_file()},
                   "summaries": summaries, "wall_seconds": time.perf_counter() - started}
        (staging / "completion.json").write_text(json.dumps(receipt, indent=2) + "\n")
        staging.replace(final)
        return receipt
    except Exception as exc:
        (staging / "failure.json").write_text(json.dumps({"key": key, "type": type(exc).__name__, "error": str(exc)}, indent=2) + "\n")
        staging.replace(output / "failures" / f"{key}.{time.time_ns()}")
        raise


def run(output: Path, *, workers: int = 3, limit: int | None = None, allow_holdout: bool = False):
    if allow_holdout:
        raise ValueError("crossing 2026-05-04 needs a numbered HOLDOUT_LEDGER entry and a separate runner")
    if not _committed(DEPENDENCIES):
        raise ValueError("commit unchanged runner/tests/config/dependencies before any market replay")
    config = json.loads(CONFIG.read_text())
    if digest(engine.RAW / "manifest.json") != config["raw_manifest_sha256"]:
        raise ValueError("frozen pool manifest changed")
    folders = sorted(p.name for p in (engine.RAW / "streams").iterdir() if (p / "completion.json").is_file())
    if len(folders) != config["expected_streams"]:
        raise ValueError("source stream coverage changed")
    identity = {str(p): digest(p) for p in DEPENDENCIES}
    identity["raw_manifest"] = config["raw_manifest_sha256"]
    identity["cut"] = str(CUT)
    identity_hash = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    output.mkdir(parents=True, exist_ok=True)
    for directory in ("streams", "failures"):
        (output / directory).mkdir(exist_ok=True)
    ipath = output / "identity.json"
    if ipath.exists() and json.loads(ipath.read_text()) != identity:
        raise ValueError("run identity drift; choose a new output directory")
    ipath.write_text(json.dumps(identity, indent=2, sort_keys=True) + "\n")
    exposure = output / "evaluation_started.json"
    if not exposure.exists():
        exposure.write_text(json.dumps({"started_at_unix": time.time(), "holdout_consumption": 0,
                                        "cut_exclusive_bar_close": str(CUT), "arms": list(ARMS),
                                        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                                        "run_identity": identity_hash}, indent=2) + "\n")
    keys = folders[:limit] if limit is not None else folders
    summaries = []
    start = time.perf_counter()
    with ProcessPoolExecutor(max_workers=max(1, min(workers, 6))) as pool:
        tasks = {pool.submit(run_stream, (key, str(output), identity_hash)): key for key in keys}
        for number, future in enumerate(as_completed(tasks), 1):
            receipt = future.result()
            summaries.extend(receipt["summaries"])
            if number == 1 or number % 100 == 0 or number == len(keys):
                print(json.dumps({"completed": number, "target": len(keys), "last_key": receipt["key"],
                                  "elapsed_seconds": round(time.perf_counter() - start, 2)}), flush=True)
    frame = pd.DataFrame(summaries).sort_values(["stream_key", "arm"])
    frame.to_csv(output / "stream_summary.csv", index=False)
    (output / "manifest.json").write_text(json.dumps({
        "complete": limit is None, "streams": len(keys), "expected_streams": config["expected_streams"],
        "run_identity": identity_hash, "holdout_consumption": 0, "cut_exclusive_bar_close": str(CUT),
        "strategy_version": VERSION, "arms": list(ARMS), "trendline_params": vars(TrendlineParams()),
        "v9_parity": "every published V9 trade closing before the cut reproduced trade-for-trade",
        "controls": "same deterministic stream/side/month/ATR-bucket draw and seed as the published V9 run",
        "summary_sha256": digest(output / "stream_summary.csv")}, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    run(args.output, workers=args.workers, limit=args.limit)
