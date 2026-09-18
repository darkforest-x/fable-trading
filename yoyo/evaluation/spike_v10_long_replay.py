"""Serial replay of the second SPIKE V10 definition on the frozen V9 pool.

Source: the frozen V8/V9 pool (`exp-spike-v9-full-backtest-20260915-v1`), the
owner's trendline indicator, and the owner's 2026-09-18 restatement of V10
(long only, V9 long confirmation on the break bar or at most 7 bars after it).
Only the entry mask changes between arms. Exits, the 0.2% round-trip cost, the
raw opposite-confirmation reversal and the fixed-entry random controls are the
published V9 ones.

Holdout: every stream is truncated so that no bar opening at or after
`HOLDOUT_START` is loaded at all, using the same `truncate` and the same
trade-for-trade V9 parity check as the withdrawn V10 run. Crossing the cut
needs a numbered `docs/HOLDOUT_LEDGER.md` entry and a separate runner.

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

from yoyo.evaluation import spike_exit_policy_study as base
from yoyo.evaluation import spike_v1_v8_be05 as engine
from yoyo.evaluation.spike_v8_six_filters import _committed
from yoyo.evaluation.spike_v9_full_replay import digest, prepare_v9, random_controls
from yoyo.evaluation.spike_v10_full_replay import CUT, truncate, v9_parity
from yoyo.evaluation.spike_v10_long import ARMS, MAX_BREAK_AGE, VERSION, arm_masks, gate_reason, long_break_ages
from yoyo.evaluation.trendline_break import TrendlineParams

EXP = Path("experiments/active/exp-spike-v10-long-break7-20260918-v1")
CONFIG = EXP / "config.json"
TEST = Path("tests/evaluation/test_spike_v10_long.py")
DEPENDENCIES = (Path(__file__), TEST, CONFIG, EXP / "PROJECT_PLAN.md",
                Path("yoyo/evaluation/spike_v10_long.py"), Path("yoyo/evaluation/trendline_break.py"),
                Path("yoyo/evaluation/spike_v10_full_replay.py"),
                Path("yoyo/evaluation/spike_v9_full_replay.py"), Path("yoyo/evaluation/spike_v9.py"),
                Path(engine.__file__), Path(base.__file__))


def replay_stream(context: base.StreamContext) -> tuple[dict[str, tuple[pd.DataFrame, pd.DataFrame]], pd.DataFrame, object]:
    """Run every pre-registered arm off one frozen set of price/state arrays."""
    prepared = engine.prepare_arm(context, arm="v8")
    treatment, decisions = prepare_v9(prepared)
    ages = long_break_ages(treatment.frame, treatment.gap, float(context.cache["tick"]))
    age = ages.long_break_age.to_numpy(np.int64)
    masks = arm_masks(treatment.allowed, treatment.raw_side, age)
    outputs = {}
    for arm in ARMS:
        trades, fills, _ = engine.replay_serial(context, arm="v8", enable_be=False,
                                                prepared=replace(treatment, allowed=masks[arm]))
        for table in (trades, fills):
            table["arm"] = arm
            table["strategy_version"] = VERSION if arm != "v9" else "published-v9-truncated"
            table["trade_id"] = table.trade_id.str.replace(":v8:baseline:", f":{arm}:baseline:", regex=False)
        outputs[arm] = (trades, fills)
    if len(decisions):
        local = decisions.local_i.to_numpy(int)
        decisions["trendline_break_age"] = age[local]
        decisions["trendline_line_active"] = ages.long_line_active.to_numpy()[local]
        for arm in ARMS[1:]:
            decisions[arm] = masks[arm][local]
        decisions["v10_reason"] = [gate_reason(int(s), bool(v), int(a)) for s, v, a
                                   in zip(decisions.side, decisions.v9, decisions.trendline_break_age)]
    else:
        for column in ("trendline_break_age", "trendline_line_active", "v10_reason", *ARMS[1:]):
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
            admitted = int(decisions[arm].astype(bool).sum()) if len(decisions) else 0
            summaries.append({"stream_key": key, **context.identity, "arm": arm, "events": len(trades),
                              "closed": len(closed), "censored": len(trades) - len(closed),
                              "net_r": float(closed.net_r.sum()), "gross_r": float(closed.gross_r.sum()),
                              "realized_ge10": int(closed.net_r.ge(10).sum()), "admitted": admitted})
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


def run_identity(config: dict) -> tuple[dict, str]:
    identity = {str(p): digest(p) for p in DEPENDENCIES}
    identity["raw_manifest"] = config["raw_manifest_sha256"]
    identity["cut"] = str(CUT)
    return identity, hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def run(output: Path, *, workers: int = 3, limit: int | None = None, allow_holdout: bool = False):
    if allow_holdout:
        raise ValueError("crossing 2026-05-04 needs a numbered HOLDOUT_LEDGER entry and a separate runner")
    if not _committed(DEPENDENCIES):
        raise ValueError("commit unchanged runner/tests/config/dependencies before any market replay")
    config = json.loads(CONFIG.read_text())
    if config["arms"] != list(ARMS) or config["max_break_age"] != MAX_BREAK_AGE:
        raise ValueError("config and module disagree on the pre-registered arms")
    if digest(engine.RAW / "manifest.json") != config["raw_manifest_sha256"]:
        raise ValueError("frozen pool manifest changed")
    folders = sorted(p.name for p in (engine.RAW / "streams").iterdir() if (p / "completion.json").is_file())
    if len(folders) != config["expected_streams"]:
        raise ValueError("source stream coverage changed")
    identity, identity_hash = run_identity(config)
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
                                        "max_break_age": MAX_BREAK_AGE,
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
        "strategy_version": VERSION, "arms": list(ARMS), "max_break_age": MAX_BREAK_AGE,
        "trendline_params": vars(TrendlineParams()),
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
