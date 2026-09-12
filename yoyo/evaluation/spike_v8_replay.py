"""Full serial replay for the development-selected SPIKE V8 admission gate.

V8 changes only new-entry permission: a frozen V7 confirmation is admitted
when its signal-close price is no farther than three current ATR beyond the
directional edge of the six-MA rope.  The raw V6 opposite confirmation feed is
left untouched so a filtered opposite entry can still close an existing trade.

Inputs are closed OHLC/ATR and frozen V7 state.  No future bar participates in
the gate.  Future OHLC is consumed only by the unchanged execution/account and
evaluation engines.
"""
from __future__ import annotations

from dataclasses import replace
import argparse
import json
from pathlib import Path
import time

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_exit_accounts import marked_account_grid, period_summary
from yoyo.evaluation.spike_exit_policy_study import END, SPLIT, START, load_verified_stream, replay_policy, sha256
from yoyo.evaluation.spike_v7_episode_study import sample_controls, trade_metrics, verify_baseline


EXP = Path("experiments/active/exp-spike-v8-noise-filter-20260913-v1")
ARMS = ("v7", "v8")


def v8_admissions(context, threshold: float = 3.0) -> pd.DataFrame:
    """Return aligned V7/V8 masks using the current closed bar only."""
    bars = context.cache["bars"]
    raw = context.cache["signals"]
    bb = context.cache["bb"]
    if not bars.index.equals(raw.index) or not bars.index.equals(bb.index):
        raise ValueError("V8 inputs must share one clock")
    long = raw.long_signal.fillna(False).astype(bool)
    short = raw.short_signal.fillna(False).astype(bool)
    if (long & short).any():
        raise ValueError("ambiguous V6 side")
    side = pd.Series(np.where(long, 1, np.where(short, -1, 0)), index=bars.index, dtype=int)
    baseline = (long | short) & bb.v7_ready.fillna(False).astype(bool) & bb.prior_squeeze_run3.fillna(False).astype(bool)
    atr = pd.to_numeric(bars.atr, errors="coerce")
    edge = pd.Series(np.where(side.eq(1), bars.ropeHigh, bars.ropeLow), index=bars.index)
    distance = side * (pd.to_numeric(bars.close, errors="coerce") - edge) / atr.where(atr.gt(0))
    v8 = baseline & distance.le(float(threshold)).fillna(False)
    reason = np.where(~baseline, "not_v7", np.where(v8, "within_3atr", "overheated_gt_3atr"))
    return pd.DataFrame({"v7": baseline, "v8": v8, "side": side,
                         "rope_distance_atr": distance, "reason": reason}, index=bars.index)


def replay_mask(context, mask: pd.Series):
    """Inject an admission mask while retaining raw signals for exits."""
    cache = dict(context.cache)
    cache["bb"] = context.cache["bb"].copy()
    cache["bb"]["prior_squeeze_run3"] = pd.Series(mask, index=cache["bb"].index).fillna(False).astype(bool)
    return replay_policy(replace(context, cache=cache), cohort="v7_both", policy="baseline")


def _period(stamps: pd.Series | pd.DatetimeIndex) -> np.ndarray:
    return np.where(pd.to_datetime(stamps, utc=True) < SPLIT, "development", "validation")


def run_one(context, threshold: float = 3.0) -> dict[str, pd.DataFrame]:
    gates = v8_admissions(context, threshold=threshold)
    close_time = gates.index + pd.Timedelta(minutes=context.minutes)
    in_window = (close_time >= START) & (close_time < END)
    gates["signal_bar_open"] = gates.index
    gates["period"] = np.where(close_time < SPLIT, "development", "validation")
    trades_all: list[pd.DataFrame] = []
    accounts: list[dict[str, object]] = []
    events: list[dict[str, object]] = []
    for arm in ARMS:
        trades, fills, _ = replay_mask(context, gates[arm])
        if arm == "v7":
            verify_baseline(context, trades)
        trades["arm"] = arm
        trades["period"] = _period(trades.entry_time)
        times, nav, _ = marked_account_grid(
            context.cache["bars"], trades, fills, settings=((0.01, 1.0),), minutes=context.minutes,
        )
        for row in period_summary(pd.DataFrame({"time": times, "equity": nav[:, 0]})):
            accounts.append({"arm": arm, **row})
        for period in ("development", "validation"):
            part = trades.loc[trades.period.eq(period)]
            signal_mask = in_window & gates.period.eq(period) & gates[arm]
            events.append({"arm": arm, "period": period, **trade_metrics(part),
                           "signals": int(signal_mask.sum())})
        trades_all.append(trades)
    trades = pd.concat(trades_all, ignore_index=True)
    baseline = trades.loc[trades.arm.eq("v7") & ~trades.censored.astype(bool)].copy()
    v8 = trades.loc[trades.arm.eq("v8")]
    exact = {(str(stamp), int(side)) for stamp, side in zip(v8.signal_bar_open, v8.side)}
    retention = baseline[["signal_bar_open", "entry_time", "side", "net_r", "mfe_r", "net_return", "period"]].copy()
    retention["exact_retained"] = [(str(stamp), int(side)) in exact for stamp, side in zip(retention.signal_bar_open, retention.side)]
    reasons = gates.reason.to_dict()
    retention["reason"] = [reasons[pd.Timestamp(stamp)] for stamp in retention.signal_bar_open]
    signals = gates.loc[in_window & gates.v7].reset_index(drop=True)
    for table in (trades, retention, signals):
        table["stream_key"] = context.key
        for key, value in context.identity.items():
            table[key] = value
    account_frame = pd.DataFrame(accounts)
    event_frame = pd.DataFrame(events)
    for table in (account_frame, event_frame):
        table["stream_key"] = context.key
        for key, value in context.identity.items():
            table[key] = value
    controls = sample_controls(context, trades)
    return {"accounts": account_frame, "events": event_frame, "trades": trades,
            "retention": retention, "signals": signals, "controls": controls}


def run(output: Path, *, limit: int | None = None) -> None:
    config = json.loads((EXP / "config.json").read_text())
    selected = json.loads((EXP / "selected_rule.json").read_text())
    raw = Path(config["raw"])
    if sha256(raw / "manifest.json") != config["raw_manifest_sha256"]:
        raise ValueError("frozen V7 manifest changed")
    discovery_manifest = EXP / "discovery_v1/manifest.json"
    if sha256(discovery_manifest) != selected["discovery_manifest_sha256"]:
        raise ValueError("selected rule does not identify the frozen discovery")
    source_paths = [Path(__file__), Path(__file__).with_name("spike_v8_noise_study.py"),
                    Path(__file__).with_name("spike_v7_episode_study.py"),
                    Path(__file__).with_name("spike_exit_policy_study.py"),
                    Path(__file__).with_name("spike_exit_accounts.py"),
                    EXP / "config.json", EXP / "selected_rule.json", EXP / "PROJECT_PLAN.md"]
    identity = {str(path): sha256(path) for path in source_paths}
    output.mkdir(parents=True, exist_ok=True)
    (output / "streams").mkdir(exist_ok=True)
    identity_path = output / "identity.json"
    if identity_path.exists() and json.loads(identity_path.read_text()) != identity:
        raise ValueError("replay identity changed; use a new output directory")
    identity_path.write_text(json.dumps(identity, indent=2))
    folders = sorted(path for path in (raw / "streams").iterdir() if (path / "completion.json").exists())
    if len(folders) != int(config["expected_streams"]):
        raise ValueError("unexpected frozen stream count")
    folders = folders if limit is None else folders[:limit]
    began = time.monotonic()
    for number, folder in enumerate(folders, 1):
        receipt = output / "streams" / f"{folder.name}.json"
        if receipt.exists():
            saved = json.loads(receipt.read_text())
            if any(sha256(output / "streams" / name) != digest for name, digest in saved["files"].items()):
                raise ValueError("existing V8 stream output changed")
            continue
        context = load_verified_stream(folder)
        tables = run_one(context, threshold=float(selected["threshold"]))
        files: dict[str, str] = {}
        for kind, table in tables.items():
            path = output / "streams" / f"{folder.name}.{kind}.csv.gz"
            table.to_csv(path, index=False, compression={"method": "gzip", "compresslevel": 1, "mtime": 0})
            files[path.name] = sha256(path)
        receipt.write_text(json.dumps({"files": files, "cache_sha256": context.receipt["cache_sha256"],
                                       "v7_baseline_parity": True}, indent=2))
        if number % 100 == 0 or number == len(folders):
            print(json.dumps({"replay_streams": number, "scope": len(folders),
                              "seconds": round(time.monotonic() - began, 1)}), flush=True)
    (output / "manifest.json").write_text(json.dumps({
        "complete": limit is None,
        "streams": len(folders),
        "expected": int(config["expected_streams"]),
        "identity_sha256": sha256(identity_path),
        "source_manifest_sha256": config["raw_manifest_sha256"],
        "selected_rule_sha256": sha256(EXP / "selected_rule.json"),
        "generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
    }, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    run(args.output, limit=args.limit)
