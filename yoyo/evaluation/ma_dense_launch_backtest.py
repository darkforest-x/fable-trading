"""Backtest the frozen dense-launch rules on BTC/ETH perpetuals, six timeframes.

Source: owner request 2026-09-23 ("用代码回测一下这套规则 拿 btcusdt.p ethusdt.p 近2年的数据").
Plan and config: ``experiments/active/exp-ma-dense-launch-backtest-20260923-v1/``.

Signals come from :mod:`yoyo.evaluation.ma_dense_launch_v1_reference` — the same
mirror the Pine port is tested against — in two arms: ``grade_a`` (hard gates,
nearest-reference similarity and the Grade-A quality score) and ``hard_only``
(hard gates alone, what Pine V1 shows). Outcomes use the frozen research rule:
next-open entry, stop at the core extreme, a 3R target, a twelve-hour cap,
stop first inside a bar and a fixed 0.2% round trip. Every trade is paired with
one random entry in the same symbol, timeframe, UTC month, causal ATR/close
bucket and direction, carrying the same risk fraction, barriers and cost.

Bars come from the frozen OKX 1m archive plus its confirmed API tail; higher
timeframes are complete UTC buckets of those rows. No fetching, training,
promotion or account interaction happens here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from yoyo.evaluation import ma_dense_launch_v1_reference as rules
from yoyo.evaluation.spike_v8_six_filters import _committed

EXP = Path("experiments/active/exp-ma-dense-launch-backtest-20260923-v1")
CONFIG = EXP / "config.json"
PLAN = EXP / "PROJECT_PLAN.md"
TEST = Path("tests/evaluation/test_ma_dense_launch_backtest.py")
ARMS = ("grade_a", "hard_only")


def digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_1m(paths: Sequence[Path], end: pd.Timestamp) -> pd.DataFrame:
    """Merge archive and API rows; conflicting duplicates fail closed."""
    cols = ["ts", "open", "high", "low", "close", "volume"]
    raw = pd.concat([pd.read_csv(p, usecols=cols) for p in paths], ignore_index=True)
    raw = raw[raw.ts + 60_000 <= end.value // 10**6]
    duplicated = raw[raw.ts.duplicated(keep=False)]
    if len(duplicated) and len(duplicated.drop_duplicates()) != duplicated.ts.nunique():
        raise ValueError("archive and API rows disagree")
    raw = raw.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    index = pd.DatetimeIndex(pd.to_datetime(raw.ts.to_numpy(), unit="ms", utc=True))
    return pd.DataFrame(raw[cols[1:]].to_numpy(float), index=index, columns=cols[1:])


def aggregate(base: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Complete UTC epoch buckets only; partial buckets become timestamp gaps."""
    raw = base[["open", "high", "low", "close", "volume"]]
    valid = (np.isfinite(raw).all(axis=1) & raw.low.gt(0) & raw.volume.ge(0)
             & raw.high.ge(raw[["open", "close", "low"]].max(axis=1))
             & raw.low.le(raw[["open", "close", "high"]].min(axis=1)))
    if minutes == 1:
        return raw.loc[valid].copy()
    groups = raw.resample(f"{minutes}min", origin="epoch", label="left", closed="left")
    bars = groups.agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    keep = groups.close.count().eq(minutes) & valid.astype(int).resample(
        f"{minutes}min", origin="epoch", label="left", closed="left").sum().eq(minutes)
    return bars.loc[keep]


def coarse_candidates(frame: pd.DataFrame, sign: float) -> np.ndarray:
    """Vectorised release gate: the five confirmation bars must already have moved."""
    close = frame.close.to_numpy(float)
    atr = frame["atr"].to_numpy(float)
    n = len(close)
    confirm = np.arange(18, n)
    end = confirm - 5
    anchor = end + 2
    with np.errstate(invalid="ignore", divide="ignore"):
        scale = atr[anchor]
        ok = np.isfinite(scale) & (scale > 0)
        p1 = sign * (close[end + 1] - close[end]) / scale
        p2 = sign * (close[end + 2] - close[end]) / scale
        p3 = sign * (close[end + 3] - close[end]) / scale
        p5 = sign * (close[end + 5] - close[end]) / scale
    keep = ok & (p1 >= 0.0) & (p2 >= 1.0) & (p3 >= 1.25) & (p5 >= 1.75)
    return confirm[np.nan_to_num(keep, nan=False).astype(bool)]


def outcome(frame: pd.DataFrame, entry_i: int, sign: float, entry: float, stop: float,
            target: float, horizon: int, cost: float) -> dict[str, Any] | None:
    """First passage with the stop taking priority inside a bar; close out at the cap."""
    o, h, l, c = (frame[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    last = min(entry_i + horizon - 1, len(frame) - 1)
    if last <= entry_i:
        return None
    for i in range(entry_i, last + 1):
        if (sign > 0 and l[i] <= stop) or (sign < 0 and h[i] >= stop):
            price = min(o[i], stop) if sign > 0 else max(o[i], stop)
            return {"exit_i": i, "exit_price": price, "result": "SL"}
        if (sign > 0 and h[i] >= target) or (sign < 0 and l[i] <= target):
            return {"exit_i": i, "exit_price": target, "result": "TP"}
    return {"exit_i": last, "exit_price": c[last], "result": "TIMEOUT"}


def _trade(frame: pd.DataFrame, confirm_i: int, sign: float, stop: float, horizon: int,
           cost: float, r_mult: float) -> dict[str, Any] | None:
    entry_i = confirm_i + 1
    if entry_i >= len(frame):
        return None
    entry = float(frame.open.iloc[entry_i])
    risk = sign * (entry - stop)
    if not np.isfinite(risk) or risk <= 0:
        return None
    target = entry + sign * r_mult * risk
    resolved = outcome(frame, entry_i, sign, entry, stop, target, horizon, cost)
    if resolved is None:
        return None
    gross = sign * (resolved["exit_price"] / entry - 1.0)
    risk_frac = risk / entry
    return {"entry_i": entry_i, "entry_time": frame.index[entry_i], "entry": entry, "stop": stop,
            "target": target, "risk_frac": risk_frac, "exit_time": frame.index[resolved["exit_i"]],
            "result": resolved["result"], "gross_bp": gross * 1e4, "net_bp": (gross - cost) * 1e4,
            "gross_r": gross / risk_frac, "net_r": (gross - cost) / risk_frac}


def scan_stream(frame: pd.DataFrame, symbol: str, minutes: int, pack: Mapping[str, Any],
                cfg: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Every signal of both arms for one symbol and timeframe, after 4h de-duplication."""
    start, end = pd.Timestamp(cfg["window_start"]), pd.Timestamp(cfg["window_end"])
    horizon = int(round(float(cfg["horizon_hours"]) * 60 / minutes))
    cost, r_mult = float(cfg["round_trip_cost"]), float(cfg["r_multiple"])
    close_time = frame.index + pd.Timedelta(minutes=minutes)
    rows: list[dict[str, Any]] = []
    for direction, sign in (("LONG", 1.0), ("SHORT", -1.0)):
        best: dict[int, dict[str, Any]] = {}
        for confirm_i in coarse_candidates(frame, sign):
            stamp = close_time[confirm_i]
            if not (start <= stamp < end):
                continue
            for core_bars in (4, 5):
                decision = rules.evaluate_full(frame, int(confirm_i), direction, core_bars, pack)
                if decision is None or not decision["hard_gates"]:
                    continue
                previous = best.get(int(confirm_i))
                if previous is None or decision["stage1_distance"] < previous["stage1_distance"]:
                    best[int(confirm_i)] = {**decision, "core_bars": core_bars, "confirm_i": int(confirm_i)}
        for confirm_i in sorted(best):
            decision = best[confirm_i]
            stop = decision["core_low"] if sign > 0 else decision["core_high"]
            trade = _trade(frame, confirm_i, sign, stop, horizon, cost, r_mult)
            if trade is None:
                continue
            rows.append({"symbol": symbol, "timeframe_min": minutes, "direction": direction,
                         "confirm_i": confirm_i, "signal_close": close_time[confirm_i],
                         "core_end_time": frame.index[confirm_i - 5], "core_bars": decision["core_bars"],
                         "quality_score": decision["quality_score"], "stage1_distance": decision["stage1_distance"],
                         "grade_a": bool(decision["grade_a"]), **trade})
    return _deduplicate(rows, int(cfg["dedupe_minutes"]))


def _deduplicate(rows: list[dict[str, Any]], minutes: int) -> list[dict[str, Any]]:
    """Quality-first NMS inside a fixed window, per direction — the research convention."""
    kept: list[dict[str, Any]] = []
    for direction in ("LONG", "SHORT"):
        pool = sorted([r for r in rows if r["direction"] == direction],
                      key=lambda r: (-float(r["quality_score"]), r["core_end_time"]))
        chosen: list[dict[str, Any]] = []
        for row in pool:
            if all(abs((row["core_end_time"] - other["core_end_time"]).total_seconds()) > minutes * 60
                   for other in chosen):
                chosen.append(row)
        kept.extend(chosen)
    return sorted(kept, key=lambda r: r["signal_close"])


def controls(frame: pd.DataFrame, trades: Sequence[Mapping[str, Any]], minutes: int,
             cfg: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One random entry per trade: same month, causal volatility bucket and direction."""
    start, end = pd.Timestamp(cfg["window_start"]), pd.Timestamp(cfg["window_end"])
    horizon = int(round(float(cfg["horizon_hours"]) * 60 / minutes))
    cost, r_mult = float(cfg["round_trip_cost"]), float(cfg["r_multiple"])
    close_time = frame.index + pd.Timedelta(minutes=minutes)
    atr = frame["atr"].to_numpy(float)
    close = frame.close.to_numpy(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        bins = np.searchsorted(np.asarray(cfg["vol_bins"], float), atr / close, side="left")
    month = np.asarray(close_time.strftime("%Y-%m"))
    eligible = np.isfinite(atr) & (atr > 0) & (close_time >= start) & (close_time < end)
    pools: dict[tuple[str, int], np.ndarray] = {}
    rows = []
    for trade in trades:
        i = int(trade["confirm_i"])
        key = (month[i], int(bins[i]))
        if key not in pools:
            pools[key] = np.flatnonzero(eligible & (month == key[0]) & (bins == key[1]))
        options = pools[key][pools[key] != i]
        record = {"trade_key": trade["trade_key"], "matched": False, "control_net_r": np.nan,
                  "control_net_bp": np.nan, "reason": "empty_stratum"}
        if len(options):
            token = int(hashlib.sha256(f"{cfg['control_seed']}|{trade['trade_key']}".encode()).hexdigest(), 16)
            j = int(options[token % len(options)])
            sign = 1.0 if trade["direction"] == "LONG" else -1.0
            if j + 1 < len(frame):
                entry = float(frame.open.iloc[j + 1])
                stop = entry - sign * entry * float(trade["risk_frac"])
                control = _trade(frame, j, sign, stop, horizon, cost, r_mult)
                if control is not None:
                    record = {"trade_key": trade["trade_key"], "matched": True,
                              "control_net_r": control["net_r"], "control_net_bp": control["net_bp"],
                              "reason": "matched", "control_time": control["entry_time"]}
        rows.append(record)
    return rows


def run(output: Path) -> None:
    cfg = json.loads(CONFIG.read_text())
    code = (Path(__file__), Path("yoyo/evaluation/ma_dense_launch_v1_reference.py"), CONFIG, PLAN, TEST)
    if not _committed(code):
        raise ValueError("commit scanner, rules mirror, plan, config and tests before the backtest")
    pack = json.loads(Path(cfg["reference_pack"]).read_text())
    decimals = int(pack["decimals"])
    for key in ("features", "sequences"):
        pack["stage1"][key] = np.round(np.asarray(pack["stage1"][key], float), decimals).tolist()
    for key in ("anchors", "bad", "family"):
        pack["stage2"][key] = np.round(np.asarray(pack["stage2"][key], float), decimals).tolist()
    output.mkdir(parents=True, exist_ok=False)
    trades, control_rows, coverage = [], [], []
    for symbol, meta in cfg["symbols"].items():
        paths = [Path(p) for p in meta["paths"]]
        base = load_1m(paths, pd.Timestamp(cfg["window_end"]))
        for minutes in cfg["timeframes_min"]:
            bars = aggregate(base, int(minutes))
            frame = rules.add_features(bars)
            rows = scan_stream(frame, symbol, int(minutes), pack, cfg)
            for row in rows:
                row["trade_key"] = f"{symbol}:{minutes}m:{row['direction']}:{row['signal_close'].isoformat()}"
            control_rows.extend(controls(frame, rows, int(minutes), cfg))
            trades.extend(rows)
            coverage.append({"symbol": symbol, "timeframe_min": int(minutes), "bars": len(frame),
                             "signals": len(rows), "grade_a": sum(r["grade_a"] for r in rows)})
            print(json.dumps(coverage[-1]), flush=True)
    frame_trades = pd.DataFrame(trades)
    frame_trades.to_csv(output / "trades.csv", index=False)
    pd.DataFrame(control_rows).to_csv(output / "controls.csv", index=False)
    pd.DataFrame(coverage).to_csv(output / "coverage.csv", index=False)
    receipt = {"config": cfg, "code": {str(p): digest(p) for p in code},
               "inputs": {s: [digest(Path(p)) for p in m["paths"]] for s, m in cfg["symbols"].items()},
               "reference_pack_sha256": digest(Path(cfg["reference_pack"])),
               "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
               "trades": len(trades), "grade_a": int(frame_trades.grade_a.sum()) if len(frame_trades) else 0}
    (output / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2, default=str) + "\n")
    print(json.dumps({"trades": len(trades), "grade_a": receipt["grade_a"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args().output)
