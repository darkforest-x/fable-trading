"""SPIKE V10.4 joint-signal long backtest on 5m / 15m / 30m / 1h / 4h / 1d.

Source: owner request 2026-09-18 「跑一下回测吧 只做多 只做突破➕spike信号的
5min 15min 30min 1h 4h 1d 周期都跑一下 币种数据你都有的 不用新拉数据」, on the
Pine "SPIKE V10.4 · 突破+spike" (`pine/spike_burst_v10_4_owner.pine`).

Data: the local Binance USD-M official monthly 5m archive
(`data/kline_preholdout_binance_um5m/series`, 638 perpetuals, ends
2026-04-30 23:55 UTC, zero holdout rows materialized by its fetcher). Every
timeframe is built from the same 5m rows by UTC-epoch buckets, so one symbol
list and one clock serve all six timeframes. Nothing is fetched.

Signals: V9 comes from the published engine (`features`, `v6_signals`,
`v7_diagnostics`, the V8 distance gate, the V9 bundle), unchanged. V10.4 joint
events come from `spike_v10_4.joint_events`. Only joint events open trades;
the `v9_long` arm (every V9 long, same engine) is a reference, not a variant.

Execution: the published V9 fixed-entry exit (`replay_fixed_entry`): entry at the
next bar's open, stop = min(5-bar low - 0.2 ATR, close - 2 ATR), 4 ATR close
trail once close reaches 2R, a raw V9 short confirmation exits at the next
open, 0.2% round trip, gaps censor. One position per stream: a signal is taken
only if the stream is flat at that signal's close.

Controls: for every trade, one deterministic random long entry from the same
stream, calendar month, fold and ATR/close bucket, scored by the same exit.

Holdout: nothing at or after 2026-05-04 exists in the source; the loader
asserts it. No training, tuning, promotion or execution integration.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
import math
from pathlib import Path
import subprocess
import time

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v1_v8_be05 as fixed
from yoyo.evaluation.spike_burst_replay import features
from yoyo.evaluation.spike_v6_wvf_study import ExecutionSpec, _data_gap
from yoyo.evaluation.spike_v7_fast import _initial_position_fast, _legacy_v4, v6_signals, v7_diagnostics
from yoyo.evaluation.spike_v8_six_filters import _committed
from yoyo.evaluation.spike_v8_lowtf_study import V8_DISTANCE_ATR
from yoyo.evaluation.spike_v9 import MAX_VOLUME_RATIO
from yoyo.evaluation.spike_v10_4 import VERSION, V104Params, joint_events, reference_long_exits

ROOT = Path(__file__).resolve().parents[2]
EXP = Path("experiments/active/exp-spike-v10-4-joint-multitf-20260918-v1")
CONFIG = EXP / "config.json"
SERIES = ROOT / "data/kline_preholdout_binance_um5m/series"
EXCHANGE_INFO = ROOT / "data/kline_preholdout_binance_um5m/exchange_info.json"
TIMEFRAMES = {"5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}
START = pd.Timestamp("2024-09-10T00:00:00Z")
SPLIT = pd.Timestamp("2025-09-10T00:00:00Z")
DATA_END = pd.Timestamp("2026-05-01T00:00:00Z")
HOLDOUT_START = pd.Timestamp("2026-05-04T00:00:00Z")
WARMUP_BARS = 1500
VOL_BINS = np.array([.005, .01, .02, .05, .1])
CONTROL_SEED = 91918
ARMS = ("joint", "v9_long")
TEST = Path("tests/evaluation/test_spike_v10_4.py")
DEPENDENCIES = (Path(__file__), Path("yoyo/evaluation/spike_v10_4.py"), TEST, CONFIG, EXP / "PROJECT_PLAN.md",
                Path("yoyo/evaluation/pine/spike_burst_v10_4_owner.pine"),
                Path("yoyo/evaluation/trendline_break.py"), Path("yoyo/evaluation/spike_v7_fast.py"),
                Path("yoyo/evaluation/spike_burst_replay.py"), Path("yoyo/evaluation/spike_v1_v8_be05.py"),
                Path("yoyo/evaluation/spike_v9.py"))


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def symbol_meta() -> dict[str, dict]:
    """Base asset and current tick from the archived exchangeInfo snapshot."""
    info = json.loads(EXCHANGE_INFO.read_text())
    meta = {}
    for item in info["symbols"]:
        tick = next((float(f["tickSize"]) for f in item.get("filters", [])
                     if f.get("filterType") == "PRICE_FILTER"), math.nan)
        meta[item["symbol"]] = {"asset": str(item.get("baseAsset", "")).upper(), "tick": tick}
    return meta


def series_files() -> dict[str, Path]:
    out = {}
    for path in sorted(SERIES.glob("binance_um_*_5m_*.csv")):
        symbol = path.name[len("binance_um_"):].rsplit("_5m_", 1)[0]
        out[symbol] = path
    return out


def load_5m(path: Path, since: pd.Timestamp) -> pd.DataFrame:
    """Closed 5m bars opening at or after `since`; refuses any holdout row."""
    raw = pd.read_csv(path, usecols=["ts", "open", "high", "low", "close", "volume"])
    raw = raw.loc[raw.ts >= since.value // 10**6]
    index = pd.DatetimeIndex(pd.to_datetime(raw.ts.to_numpy(), unit="ms", utc=True))
    frame = pd.DataFrame(raw[["open", "high", "low", "close", "volume"]].to_numpy(float),
                         index=index, columns=["open", "high", "low", "close", "volume"])
    frame = frame[~frame.index.duplicated(keep="first")].sort_index()
    if len(frame) and frame.index[-1] + pd.Timedelta(minutes=5) > HOLDOUT_START:
        raise ValueError(f"holdout row present in {path.name}")
    return frame


def aggregate(bars: pd.DataFrame, minutes: int) -> tuple[pd.DataFrame, int]:
    """UTC-epoch buckets (4h at 00/04/.., 1d at 00:00); empty buckets stay missing.

    Returns the bars and how many buckets were built from fewer 5m rows than
    the bucket holds (exchange maintenance or archive holes).
    """
    if minutes == 5:
        return bars, 0
    rule = f"{minutes}min"
    grouped = bars.resample(rule, origin="epoch", label="left", closed="left")
    out = grouped.agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    count = grouped.close.count()
    keep = count > 0
    partial = int((count[keep] < minutes // 5).sum())
    return out.loc[keep], partial


def v9_facts(bars: pd.DataFrame, minutes: int, asset: str, tick: float) -> dict:
    """Everything V10.4 needs from V9, computed by the published engine."""
    frame = features(bars)
    frame.attrs["minutes"] = minutes
    gap = _data_gap(frame, minutes)
    raw = v6_signals(frame, minutes)
    bb = v7_diagnostics(frame, data_gap=gap)
    long = raw.long_signal.fillna(False).to_numpy(bool)
    short = raw.short_signal.fillna(False).to_numpy(bool)
    if (long & short).any():
        raise ValueError("ambiguous raw side")
    side = np.where(long, 1, np.where(short, -1, 0))
    close, atr = frame.close.to_numpy(float), frame.atr.to_numpy(float)
    rope_high, rope_low = frame.ropeHigh.to_numpy(float), frame.ropeLow.to_numpy(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        safe_atr = np.where(atr > 0, atr, np.nan)
        edge = np.where(side == 1, rope_high, rope_low)
        distance = side * (close - edge) / safe_atr
        long_distance = (close - rope_high) / safe_atr
    bb_ok = (bb.v7_ready.fillna(False).to_numpy(bool) & bb.prior_squeeze_run3.fillna(False).to_numpy(bool))
    v8 = (side != 0) & bb_ok & (distance <= V8_DISTANCE_ATR)
    rv = frame.rv.to_numpy(float)
    close_time = frame.index + pd.Timedelta(minutes=minutes)
    with np.errstate(invalid="ignore"):
        bundle = (asset not in ("", "USDC")) & np.isfinite(rv) & (rv >= 0) & (rv <= MAX_VOLUME_RATIO)
    bundle &= np.asarray(close_time.dayofweek != 6)
    v9 = v8 & bundle
    legacy = _legacy_v4(frame, minutes, 1)
    valid_parent = (legacy.legacy_confirmed & legacy.legacy_parent_high.notna() & legacy.legacy_parent_low.notna()
                    & legacy.legacy_parent_low.le(legacy.legacy_parent_high))
    parent_high = legacy.legacy_parent_high.where(valid_parent).ffill().to_numpy(float)
    parent_low = legacy.legacy_parent_low.where(valid_parent).ffill().to_numpy(float)
    ready = frame.ready.fillna(False).to_numpy(bool)
    md, sb = frame.md.to_numpy(float), frame.sb.to_numpy(float)
    md_prev = np.r_[np.nan, md[:-1]]
    with np.errstate(invalid="ignore"):
        momentum = np.isfinite(md_prev) & np.isfinite(sb) & (md > sb) & (md > md_prev)
        long_alive = ready & (atr > 0) & np.isfinite(rope_high) & (close > rope_high) & (side != -1)
        current_gate = bb_ok & bundle & (long_distance <= V8_DISTANCE_ATR)
    gap_a = gap.to_numpy(bool)
    can_run = ~gap_a & np.isfinite(atr) & (atr > 0)
    signal_side = np.where(v9, side, 0)
    ref_exit = reference_long_exits(frame.high.to_numpy(float), frame.low.to_numpy(float), close, atr,
                                    ready=ready, gap=gap_a, raw_side=side, signal_side=signal_side, tick=tick)
    return {"frame": frame, "gap": gap_a, "side": side, "v8": v8, "v9": v9, "v9_long": v9 & (side == 1),
            "parent_high": parent_high, "parent_low": parent_low, "momentum": momentum,
            "long_alive": long_alive, "current_gate": current_gate, "can_run": can_run,
            "ref_long_exit": ref_exit, "ready": ready}


def prepared_arm(frame: pd.DataFrame, gap: np.ndarray, side: np.ndarray, key: str, identity: dict,
                 minutes: int, tick: float) -> fixed.PreparedArm:
    """Wrap one stream for the published fixed-entry exit engine (BE off)."""
    context = fixed.base.StreamContext(Path("."), key, {}, {"bars": frame, "tick": tick}, pd.DataFrame(),
                                       minutes, identity)
    arrays = [frame[k].to_numpy(float) for k in ("open", "high", "low", "close", "atr")]
    return fixed.PreparedArm(context, "v8", "v7_both", frame, gap, np.zeros(len(frame), bool), side,
                             *arrays, {}, ExecutionSpec(tick=tick))


def evaluate(prepared: fixed.PreparedArm, i: int) -> dict | None:
    """One long entry signalled at bar i's close, filled at i+1's open."""
    if i + 1 >= len(prepared.frame) or prepared.gap[i + 1]:
        return None
    row = _initial_position_fast(prepared.frame.index, prepared.open, prepared.high, prepared.low,
                                 prepared.close, prepared.atr, prepared.gap, i, 1, prepared.spec)
    if row is None:
        return None
    return fixed.replay_fixed_entry(prepared.context, pd.Series(row), arm="v8", enable_be=False, prepared=prepared)


def in_window(index: pd.DatetimeIndex, minutes: int) -> np.ndarray:
    close_time = index + pd.Timedelta(minutes=minutes)
    return np.asarray((close_time >= START) & (close_time < DATA_END))


def serial_trades(prepared: fixed.PreparedArm, candidates: np.ndarray) -> tuple[list[dict], dict]:
    """Take a candidate only when flat at its close; count what was skipped and why."""
    trades, counts = [], {"candidates": int(len(candidates)), "skipped_in_position": 0, "invalid_initial": 0}
    flat_from = -1
    for i in candidates.tolist():
        if i < flat_from:
            counts["skipped_in_position"] += 1
            continue
        result = evaluate(prepared, i)
        if result is None:
            counts["invalid_initial"] += 1
            continue
        trades.append(result)
        if result["censored"] and result["exit_reason"] == "boundary_mark":
            flat_from = len(prepared.frame) + 1
        else:
            flat_from = int(result["exit_i"])
    return trades, counts


def controls(prepared: fixed.PreparedArm, trades: pd.DataFrame, minutes: int, ready: np.ndarray) -> pd.DataFrame:
    """One fixed-seed random long per trade: same stream, month, fold and ATR/close bin."""
    frame = prepared.frame
    with np.errstate(invalid="ignore", divide="ignore"):
        vol = np.searchsorted(VOL_BINS, prepared.atr / prepared.close, side="left")
    month = np.asarray(frame.index.strftime("%Y-%m"))
    close_time = frame.index + pd.Timedelta(minutes=minutes)
    fold = np.where(np.asarray(close_time < SPLIT), "earlier", "later")
    eligible = (ready & np.isfinite(prepared.atr) & (prepared.atr > 0) & np.isfinite(prepared.close)
                & (prepared.close > 0) & in_window(frame.index, minutes))
    pools: dict = {}
    rows = []
    for t in trades.itertuples(index=False):
        i = int(t.signal_i)
        key = (month[i], int(vol[i]), fold[i])
        if key not in pools:
            pools[key] = np.flatnonzero(eligible & (month == key[0]) & (vol == key[1]) & (fold == key[2]))
        choices = pools[key][pools[key] != i]
        chosen, result, reason = None, None, "empty_stratum"
        if len(choices):
            value = int(hashlib.sha256(f"{CONTROL_SEED}|{t.trade_key}".encode()).hexdigest(), 16)
            chosen = int(choices[value % len(choices)])
            result = evaluate(prepared, chosen)
            reason = "invalid_initial" if result is None else "censored" if result["censored"] else "matched"
        matched = reason == "matched" and not bool(t.censored)
        rows.append({"trade_key": t.trade_key, "arm": t.arm, "matched": matched,
                     "reason": "target_censored" if t.censored else reason,
                     "control_signal_i": chosen,
                     "control_signal_bar_open": None if chosen is None else frame.index[chosen],
                     "control_net_r": result["net_r"] if matched else math.nan,
                     "control_net_return": result["net_return"] if matched else math.nan,
                     "control_exit_time": None if result is None else result["exit_time"],
                     "month": key[0], "vol_bin": key[1], "fold": key[2]})
    return pd.DataFrame(rows)


TRADE_KEEP = ["signal_i", "signal_bar_open", "entry_i", "entry_time", "entry_price", "initial_stop",
              "initial_risk", "initial_risk_frac", "exit_i", "exit_time", "exit_price", "exit_reason",
              "gross_return", "net_return", "gross_r", "net_r", "censored", "mfe_r"]


def run_stream(symbol: str, timeframe: str, bars: pd.DataFrame, meta: dict, params: V104Params) -> dict:
    """All arms and diagnostics for one symbol x timeframe."""
    minutes = TIMEFRAMES[timeframe]
    tick, asset = float(meta["tick"]), meta["asset"]
    key = f"binance_um:{symbol}:{timeframe}"
    facts = v9_facts(bars, minutes, asset, tick)
    frame = facts["frame"]
    joint = joint_events(frame.open, frame.high, frame.low, frame.close, frame.atr,
                         can_run=facts["can_run"], confirmed_long=facts["v9_long"],
                         parent_high=facts["parent_high"], parent_low=facts["parent_low"],
                         raw_side=facts["side"], long_alive=facts["long_alive"], momentum=facts["momentum"],
                         current_gate=facts["current_gate"], ref_long_exit=facts["ref_long_exit"],
                         tick=tick, params=params)
    window = in_window(frame.index, minutes)
    identity = {"venue": "binance_um", "symbol": symbol, "asset": asset, "timeframe": timeframe,
                "timeframe_min": minutes}
    prepared = prepared_arm(frame, facts["gap"], facts["side"], key, identity, minutes, tick)
    masks = {"joint": joint.joint_event & window, "v9_long": facts["v9_long"] & window}
    tables, counts = [], {}
    joint_by_i = {j["i"]: j for j in joint.joints}
    for arm in ARMS:
        rows, counts[arm] = serial_trades(prepared, np.flatnonzero(masks[arm]))
        table = pd.DataFrame(rows, columns=[*TRADE_KEEP]) if rows else pd.DataFrame(columns=TRADE_KEEP)
        table = table[TRADE_KEEP].copy()
        table["arm"] = arm
        table["stream_key"] = key
        for k, v in identity.items():
            table[k] = v
        table["trade_key"] = key + ":" + arm + ":" + table.signal_i.astype(int).astype(str)
        if arm == "joint":
            table["joint_order"] = [joint_by_i[int(i)]["order"] for i in table.signal_i]
            table["line_source"] = [joint_by_i[int(i)]["source"] for i in table.signal_i]
            table["spike_age"] = [int(i) - joint_by_i[int(i)]["spike_i"] for i in table.signal_i]
            table["break_age"] = [int(i) - joint_by_i[int(i)]["break_i"] for i in table.signal_i]
            table["also_v9_long"] = [bool(facts["v9_long"][int(i)]) for i in table.signal_i]
        tables.append(table)
    trades = pd.concat(tables, ignore_index=True)
    trades["censored"] = trades.censored.astype(bool)
    matched = controls(prepared, trades, minutes, facts["ready"]) if len(trades) else pd.DataFrame()
    joints = pd.DataFrame(joint.joints)
    if len(joints):
        joints["signal_bar_open"] = frame.index[joints.i.to_numpy(int)]
        joints["in_window"] = window[joints.i.to_numpy(int)]
        joints["stream_key"] = key
        joints["timeframe"] = timeframe
    refusals = pd.Series([r["reason"] for r in joint.refusals if window[r["i"]]], dtype=object).value_counts()
    summary = {"stream_key": key, "symbol": symbol, "timeframe": timeframe, "asset": asset, "tick": tick,
               "bars": len(frame), "bars_in_window": int(window.sum()),
               "first_bar": str(frame.index[0]), "last_bar": str(frame.index[-1]),
               "gaps": int(facts["gap"].sum()), "ready_in_window": int((facts["ready"] & window).sum()),
               "v9_long_in_window": int(masks["v9_long"].sum()),
               "v9_any_in_window": int((facts["v9"] & window).sum()),
               "breaks_in_window": int((joint.break_event & window).sum()),
               "lines_born_in_window": int((joint.born_event & window).sum()),
               "joint_in_window": int(masks["joint"].sum()), "pivot_ties": joint.pivot_ties,
               "store_codes": {str(k): v for k, v in joint.store_codes.items()},
               "pair_refusals": {str(k): int(v) for k, v in refusals.items()},
               "serial": counts}
    return {"trades": trades, "controls": matched, "joints": joints, "summary": summary}


def run_symbol(args) -> dict:
    """Load one 5m file once and run all six timeframes; write, then mark complete."""
    symbol, path, meta, output, identity_hash = args
    final = Path(output) / "streams" / symbol
    if (final / "completion.json").is_file():
        receipt = json.loads((final / "completion.json").read_text())
        if receipt.get("run_identity") != identity_hash:
            raise ValueError(f"completion identity mismatch: {final}")
        return receipt
    started = time.perf_counter()
    staging = Path(output) / "streams" / f".{symbol}.staging"
    if staging.exists():
        for p in staging.iterdir():
            p.unlink()
    else:
        staging.mkdir(parents=True)
    params = V104Params()
    earliest = START - pd.Timedelta(minutes=WARMUP_BARS * max(TIMEFRAMES.values()))
    base = load_5m(Path(path), earliest)
    summaries, failures = [], []
    parts = {"trades": [], "controls": [], "joints": []}
    for timeframe, minutes in TIMEFRAMES.items():
        since = START - pd.Timedelta(minutes=WARMUP_BARS * minutes)
        bars, partial = aggregate(base.loc[base.index >= since.floor(f"{minutes}min")], minutes)
        if len(bars) == 0 or not (in_window(bars.index, minutes)).any():
            summaries.append({"stream_key": f"binance_um:{symbol}:{timeframe}", "symbol": symbol,
                              "timeframe": timeframe, "skipped": "no_bar_in_window"})
            continue
        try:
            out = run_stream(symbol, timeframe, bars, meta, params)
        except ValueError as exc:
            failures.append({"timeframe": timeframe, "error": str(exc)})
            summaries.append({"stream_key": f"binance_um:{symbol}:{timeframe}", "symbol": symbol,
                              "timeframe": timeframe, "skipped": f"error: {exc}"})
            continue
        out["summary"]["partial_buckets"] = partial
        summaries.append(out["summary"])
        for name in parts:
            if len(out[name]):
                parts[name].append(out[name])
    for name, frames in parts.items():
        if frames:
            pd.concat(frames, ignore_index=True).to_csv(staging / f"{name}.csv.gz", index=False,
                                                        compression={"method": "gzip", "mtime": 0})
    receipt = {"status": "complete", "symbol": symbol, "run_identity": identity_hash,
               "source": str(path), "source_bytes": Path(path).stat().st_size,
               "summaries": summaries, "failures": failures,
               "files": {p.name: digest(p) for p in staging.iterdir() if p.is_file()},
               "wall_seconds": round(time.perf_counter() - started, 2)}
    (staging / "completion.json").write_text(json.dumps(receipt, indent=2, default=str) + "\n")
    staging.replace(final)
    return receipt


def run(output: Path, *, workers: int = 8, limit: int | None = None, symbols: list[str] | None = None,
        allow_uncommitted: bool = False) -> None:
    if not allow_uncommitted and not _committed(DEPENDENCIES):
        raise ValueError("commit runner, port, tests, plan and config before any market replay")
    config = json.loads(CONFIG.read_text())
    if config["strategy_version"] != VERSION or config["timeframes"] != list(TIMEFRAMES):
        raise ValueError("config and module disagree")
    files = series_files()
    if len(files) != config["expected_symbols"]:
        raise ValueError(f"source coverage changed: {len(files)} files")
    meta = symbol_meta()
    keys = symbols or sorted(files)
    keys = keys[:limit] if limit is not None else keys
    identity = {str(p): digest(p) for p in DEPENDENCIES}
    identity["params"] = json.dumps(vars(V104Params()), sort_keys=True)
    identity_hash = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    output.mkdir(parents=True, exist_ok=True)
    (output / "streams").mkdir(exist_ok=True)
    ipath = output / "identity.json"
    if ipath.exists() and json.loads(ipath.read_text()) != identity:
        raise ValueError("run identity drift; choose a new output directory")
    ipath.write_text(json.dumps(identity, indent=2, sort_keys=True) + "\n")
    started_path = output / "evaluation_started.json"
    if not started_path.exists():
        started_path.write_text(json.dumps({
            "started_at_unix": time.time(), "holdout_consumption": 0,
            "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
            "run_identity": identity_hash, "symbols": len(keys)}, indent=2) + "\n")
    start = time.perf_counter()
    receipts = []
    with ProcessPoolExecutor(max_workers=max(1, workers)) as pool:
        tasks = {pool.submit(run_symbol, (s, str(files[s]), meta.get(s, {"asset": "", "tick": math.nan}),
                                          str(output), identity_hash)): s for s in keys}
        for number, future in enumerate(as_completed(tasks), 1):
            receipt = future.result()
            receipts.append(receipt)
            if number == 1 or number % 25 == 0 or number == len(keys):
                print(json.dumps({"completed": number, "target": len(keys), "last": receipt["symbol"],
                                  "wall": receipt.get("wall_seconds"),
                                  "elapsed_seconds": round(time.perf_counter() - start, 1)}), flush=True)
    rows = [s for r in receipts for s in r["summaries"]]
    pd.DataFrame(rows).to_csv(output / "stream_summary.csv", index=False)
    (output / "manifest.json").write_text(json.dumps({
        "complete": limit is None and symbols is None, "symbols": len(keys),
        "expected_symbols": config["expected_symbols"], "run_identity": identity_hash,
        "holdout_consumption": 0, "strategy_version": VERSION, "params": vars(V104Params()),
        "window": [str(START), str(DATA_END)], "split": str(SPLIT),
        "failures": {r["symbol"]: r["failures"] for r in receipts if r["failures"]},
        "summary_sha256": digest(output / "stream_summary.csv")}, indent=2, default=str) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--symbols", nargs="*")
    parser.add_argument("--allow-uncommitted", action="store_true",
                        help="smoke runs only; results from such runs are not reportable")
    args = parser.parse_args()
    run(args.output, workers=args.workers, limit=args.limit, symbols=args.symbols,
        allow_uncommitted=args.allow_uncommitted)
