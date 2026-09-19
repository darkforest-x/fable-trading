"""Diagnose the owner's existing 50-chart sample without changing trade rules.

Source: owner asks whether most sampled SPIKE V11.2 directions were correct.
Inputs are the original sample key map, Chinese trade book, box_any ledger, and
the same local Binance 5m archive. OHLC after entry is used ONLY as a descriptive
outcome label, never as a feature or signal. Fixed 12/24/48 chart-bar horizons
are reported together; 48 matches the original chart's EXTEND constant. These
are not competing exit strategies or an optimization grid. Existing exits and
0.2% cost remain untouched. Original MFE excludes the exit bar; an additional
first-touch label retains same-bar ambiguity. No fetching or production writes.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v10_4_study as study
from yoyo.evaluation import spike_v11_study as v11
from yoyo.evaluation.spike_burst_replay import features

EXP = Path("experiments/active/exp-spike-v11-box-joint-20260918-v1")
BOOK = EXP / "statistics/trade_book/逐笔明细_突破spike.csv"
KEYS = EXP / "statistics/trade_book/sample_keys.json"
LEDGER = EXP / "statistics/run_v1/trades.csv.gz"
DEFAULT_OUT = Path("experiments/active/exp-spike-v112-sample-direction-20260919-v1")


def first_touch(bars: pd.DataFrame, entry: float, risk: float) -> str:
    """First +/-1R touch in this fixed window; OHLC cannot order two touches."""
    for row in bars.itertuples():
        if row.open >= entry + risk:
            return "up_first"
        if row.open <= entry - risk:
            return "down_first"
        up, down = row.high >= entry + risk, row.low <= entry - risk
        if up and down:
            return "same_bar_unknown"
        if up:
            return "up_first"
        if down:
            return "down_first"
    return "neither"


def path_metrics(frame: pd.DataFrame, start: int, end: int, entry: float,
                 stop: float, prefix: str, minutes: int) -> dict:
    """Post-entry outcomes from OHLC/ATR, fixed windows and pre-exit closes."""
    risk = entry - stop
    assert risk > 0 and start <= end
    assert np.isclose(frame.open.iloc[start], entry, rtol=1e-10)
    # Exclude the exit bar: the old engine exits before close/MFE updates.
    held = frame.iloc[start:end]
    out = {f"{prefix}_max_close_r_before_exit": float((held.close.max() - entry) / risk),
           f"{prefix}_held_bars": end - start,
           f"{prefix}_mfe_rebuilt": max(0., float((held.high.max() - entry) / risk))}
    close_r = (held.close - entry) / risk
    armed = (close_r >= 2.).cummax()
    candidates = ((held.close - 4. * held.atr - entry) / risk).loc[armed]
    out[f"{prefix}_best_raw_trail_candidate_r"] = float(candidates.max())
    for n in (12, 24, 48):
        window = frame.iloc[start:start + n]
        complete = (len(window) == n and
                    window.index[-1] - window.index[0] == pd.Timedelta(minutes=(n - 1) * minutes))
        out[f"{prefix}_h{n}_complete"] = complete
        if not complete:
            continue
        out[f"{prefix}_h{n}_close_r"] = float((window.close.iloc[-1] - entry) / risk)
        out[f"{prefix}_h{n}_close_return"] = float(window.close.iloc[-1] / entry - 1)
        out[f"{prefix}_h{n}_max_r"] = float((window.high.max() - entry) / risk)
        out[f"{prefix}_h{n}_min_r"] = float((window.low.min() - entry) / risk)
        out[f"{prefix}_h{n}_first_touch"] = first_touch(window, entry, risk)
    # Starts strictly after the exit bar to avoid inventing intrabar ordering.
    after = frame.iloc[end + 1:end + 49]
    out[f"{prefix}_post_exit48_complete"] = (len(after) == 48 and
        after.index[-1] - after.index[0] == pd.Timedelta(minutes=47 * minutes))
    if out[f"{prefix}_post_exit48_complete"]:
        out[f"{prefix}_post_exit48_max_r"] = float((after.high.max() - entry) / risk)
        out[f"{prefix}_post_exit48_min_r"] = float((after.low.min() - entry) / risk)
    return out


def work(args: tuple) -> list[dict]:
    symbol, path, pairs = args
    earliest = study.START - pd.Timedelta(minutes=study.WARMUP_BARS * max(study.TIMEFRAMES.values()))
    base = study.load_5m(Path(path), earliest)
    cache, rows = {}, []
    for record, old in pairs:
        tf = record["timeframe"]
        minutes = study.TIMEFRAMES[tf]
        if tf not in cache:
            cache[tf] = features(v11.bars_for(base, minutes))
        frame = cache[tf]
        i, e, x, s = (int(record[k]) for k in ("signal_i", "entry_i", "exit_i", "box_entry_i"))
        assert frame.index[i] == pd.Timestamp(record["signal_bar_open"])
        assert frame.index[e] == pd.Timestamp(record["entry_time"])
        assert frame.index[x] == pd.Timestamp(record["exit_time"])
        v9_exit = pd.Timestamp(old["V9出场(北京)"]).tz_localize("Asia/Shanghai").tz_convert("UTC")
        vx = int(frame.index.get_loc(v9_exit))
        row = {"figure": int(old["图号"]), "symbol": symbol, "timeframe": tf,
               "period": record["period"], "bars_after_v9": int(record["bars_after_v9"]),
               "source": old["突破来源"], "joint_reason": record["exit_reason"],
               "v9_net_r": old["V9净R"], "joint_net_r": old["联合净R"],
               "v9_mfe": old["V9最大浮盈R"], "joint_mfe": old["联合最大浮盈R"],
               "entry_premium_pct": (old["联合进场价"] / old["V9进场价"] - 1) * 100,
               "joint_stop_above_v9_stop": old["联合止损"] > old["V9止损"],
               "matched": bool(record["matched"]), "control_net_r": record["control_net_r"]}
        for prefix, start, end, entry, stop in (
            ("v9", s + 1, vx, old["V9进场价"], old["V9止损"]),
            ("joint", e, x, old["联合进场价"], old["联合止损"]),
        ):
            row.update(path_metrics(frame, start, end, float(entry), float(stop), prefix, minutes))
            assert np.isclose(row[f"{prefix}_mfe_rebuilt"], row[f"{prefix}_mfe"], atol=1e-8)
        rows.append(row)
    return rows


def main(out: Path, workers: int) -> None:
    if (out / "paths.csv").exists():
        raise FileExistsError("Keep the previous diagnostic intact; use a new output path.")
    keys = json.loads(KEYS.read_text())["sample"]
    ledger = pd.read_csv(LEDGER).set_index("trade_key")
    book = pd.read_csv(BOOK)
    sample = book[book["图号"].notna()].set_index("图号")
    assert len(keys) == len(sample) == 50
    grouped = {}
    for key, number in keys.items():
        record, old = ledger.loc[key].to_dict(), sample.loc[number].to_dict() | {"图号": number}
        assert record["arm"] == "box_any" and record["symbol"] == old["币种"]
        assert record["timeframe"] == old["周期"] and record["status"] == "closed"
        grouped.setdefault(record["symbol"], []).append((record, old))
    files = study.series_files()
    jobs = [(sym, str(files[sym]), pairs) for sym, pairs in grouped.items()]
    rows = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for result in pool.map(work, jobs):
            rows.extend(result)
    result = pd.DataFrame(rows).sort_values("figure")
    assert len(result) == 50 and result.figure.nunique() == 50
    out.mkdir(parents=True, exist_ok=True)
    result.to_csv(out / "paths.csv", index=False)
    inputs = [BOOK, KEYS, LEDGER, Path(__file__)] + [Path(j[1]) for j in jobs]
    receipt = {"source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
               "rows": 50, "symbols": len(jobs), "mfe_checks": 100,
               "training_eligible": False, "production_eligible": False,
               "horizons_are_outcomes_not_strategy_parameters": True,
               "inputs": [{"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in inputs]}
    (out / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({k: v for k, v in receipt.items() if k != "inputs"}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    main(args.out, args.workers)
