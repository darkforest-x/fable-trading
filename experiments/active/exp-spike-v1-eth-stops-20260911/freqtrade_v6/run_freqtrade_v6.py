"""Run bounded Freqtrade 2026.8 V6 ETH bridge backtests and preserve receipts.

Only offline bars written by write_frozen_ohlcv.py are used.  Each invocation has
one pair, max_open_trades=1, one leverage, 10bp entry plus 10bp exit fee, and
zero funding placeholder.  The output separates V6 bridge construction from
Freqtrade's independent slot rejection, fee accounting, and stop execution.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pandas as pd

EXP = Path(__file__).resolve().parent
FT = Path("/Users/zhangzc/.local/share/crypto-toolkit/venvs/freqtrade/bin/freqtrade")
RESULTS = EXP / "results"
PLAN = EXP / "plans"
CONFIG = EXP / "config_futures.json"
STRATEGY_PATH = EXP / "strategies"
TIMEFRAMES = (30, 60, 240)
TF_NAME = {30: "30m", 60: "1h", 240: "4h"}
FULL = "20240910-20260910"
DEVELOPMENT = "20240910-20250910"
LATER = "20250910-20260910"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _result_zip(before: set[Path]) -> Path:
    after = set(RESULTS.glob("backtest-result-*.zip"))
    new = after - before
    if len(new) != 1:
        raise RuntimeError(f"Expected one Freqtrade result, got {sorted(str(x) for x in new)}")
    return next(iter(new))


def _read_result(path: Path) -> tuple[dict, pd.DataFrame]:
    with zipfile.ZipFile(path) as archive:
        report_name = next(name for name in archive.namelist() if name.endswith(".json") and "_config" not in name)
        report = json.loads(archive.read(report_name))
    strategy = report["strategy"]["FrozenV6Bridge"]
    return strategy, pd.DataFrame(strategy["trades"])


def _input_count(universe: str, minutes: int, variant: str, timerange: str) -> int:
    source = pd.read_csv(PLAN / f"signals_{universe}_{minutes}m_{variant}.csv", parse_dates=["entry_time"])
    begin = pd.Timestamp(f"{timerange[:4]}-{timerange[4:6]}-{timerange[6:8]}T00:00:00Z")
    end = pd.Timestamp(f"{timerange[9:13]}-{timerange[13:15]}-{timerange[15:17]}T00:00:00Z")
    return int(source.entry_time.between(begin, end, inclusive="left").sum())


def _check_trade_provenance(trades: pd.DataFrame, universe: str, minutes: int, variant: str) -> dict[str, int]:
    """Each actual framework entry must match a causal signal and stop plan row."""
    signals = pd.read_csv(PLAN / f"signals_{universe}_{minutes}m_{variant}.csv", parse_dates=["entry_time"])
    stops = pd.read_csv(PLAN / f"stops_{universe}_{minutes}m_{variant}.csv", parse_dates=["entry_time", "current_time"])
    signal_keys = {(pd.Timestamp(r.entry_time), int(r.side)) for r in signals.itertuples()}
    stop_keys = {(pd.Timestamp(r.entry_time), pd.Timestamp(r.current_time), int(r.side)): float(r.active_stop) for r in stops.itertuples()}
    missing_entry = missing_stop = beyond_tick = 0
    for row in trades.itertuples():
        entry = pd.Timestamp(row.open_date)
        side = -1 if bool(row.is_short) else 1
        if (entry, side) not in signal_keys:
            missing_entry += 1
        close = pd.Timestamp(row.close_date)
        expected = stop_keys.get((entry, close, side))
        if row.exit_reason in {"stop_loss", "trailing_stop_loss"}:
            if expected is None:
                missing_stop += 1
            elif abs(float(row.close_rate) - expected) > 0.0100001:
                # The local Freqtrade price formatter may round one tick outward.
                # Larger differences are retained for review (for example a gap fill).
                beyond_tick += 1
    if missing_entry or missing_stop:
        raise AssertionError(f"Framework trade lacks causal bridge input: entries={missing_entry}, stops={missing_stop}")
    return {"entry_input_missing": missing_entry, "stop_input_missing": missing_stop, "stop_close_price_beyond_one_tick": beyond_tick}


def run(name: str, universe: str, minutes: int, variant: str, timerange: str) -> dict[str, object]:
    before = set(RESULTS.glob("backtest-result-*.zip"))
    env = os.environ.copy()
    env.update({"V6_FT_PLAN_DIR": str(PLAN), "V6_FT_UNIVERSE": universe, "V6_FT_VARIANT": variant,
                "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1", "NUMEXPR_MAX_THREADS": "1"})
    command = [str(FT), "backtesting", "--config", str(CONFIG), "--datadir", str(EXP / "data"),
               "--strategy", "FrozenV6Bridge", "--strategy-path", str(STRATEGY_PATH),
               "--timeframe", TF_NAME[minutes], "--timerange", timerange, "--fee", "0.001",
               "--export", "trades", "--backtest-directory", str(RESULTS), "--cache", "none"]
    completed = subprocess.run(command, cwd=EXP, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=True)
    log = RESULTS / f"{name}.log"
    log.write_text(completed.stdout, encoding="utf-8")
    zipped = _result_zip(before)
    report, trades = _read_result(zipped)
    copied = RESULTS / f"{name}.zip"
    shutil.move(zipped, copied)
    meta = zipped.with_suffix(".meta.json")
    if meta.exists():
        shutil.move(meta, RESULTS / f"{name}.meta.json")
    ledger = RESULTS / f"{name}_trades.csv.gz"
    trades.to_csv(ledger, index=False, compression={"method": "gzip", "mtime": 0})
    bridge = _check_trade_provenance(trades, universe, minutes, variant)
    candidate_count = _input_count(universe, minutes, variant, timerange)
    returned = {
        "name": name, "universe": universe, "timeframe_min": minutes, "variant": variant, "timerange": timerange,
        "bridge_candidates": candidate_count, "framework_trades": int(report["total_trades"]),
        "not_opened_due_to_single_active_or_boundary": candidate_count - int(report["total_trades"]),
        "framework_rejected_signals_field": int(report["rejected_signals"]),
        "long_trades": int(report["trade_count_long"]), "short_trades": int(report["trade_count_short"]),
        "profit_total_fraction": float(report["profit_total"]), "profit_total_abs": float(report["profit_total_abs"]),
        "profit_factor": float(report["profit_factor"]), "winrate": float(report["winrate"]),
        "max_relative_drawdown": float(report["max_relative_drawdown"]), "left_open": int(len(report["left_open_trades"])),
        "trade_ledger": ledger.name, "result_zip": copied.name, "log": log.name, "bridge_provenance": bridge,
    }
    return returned


def main() -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    runs: list[dict[str, object]] = []
    # Fixed V6 baseline across all requested periods and two direction policies.
    for universe in ("both", "long_only"):
        for minutes in TIMEFRAMES:
            runs.append(run(f"full_{universe}_{minutes}m_baseline", universe, minutes, "baseline", FULL))
    # Development-only single-axis stop probes on the highest-frequency both-side execution.
    for variant in ("initial_1.5", "initial_2.0", "initial_2.5", "trail_3.0", "trail_4.0", "trail_5.0"):
        runs.append(run(f"development_both_30m_{variant}", "both", 30, variant, DEVELOPMENT))
    table = pd.DataFrame(runs)
    # Deterministic axis selection: only development PnL; ties retain frozen baseline.
    axis = {}
    for name, candidates in {"initial": ["initial_1.5", "initial_2.0", "initial_2.5"], "trail": ["trail_3.0", "trail_4.0", "trail_5.0"]}.items():
        portion = table[table.variant.isin(candidates)].sort_values(["profit_total_abs", "variant"], ascending=[False, True])
        axis[name] = str(portion.iloc[0].variant)
        runs.append(run(f"later_both_30m_{axis[name]}", "both", 30, axis[name], LATER))
    table = pd.DataFrame(runs)
    table.to_csv(RESULTS / "freqtrade_run_summary.csv", index=False)
    receipt = {
        "schema": "spike-v6-freqtrade-execution-v1", "freqtrade": "2026.8", "runs": runs,
        "development_axis_selection": axis, "fee": "0.001 per side / 0.002 round trip",
        "funding": "zero placeholder only; no actual funding history", "leverage": 1,
        "mechanics": "isolated futures executor solely to exercise both directions; no liquidation tiers or funding economics",
        "commands_sha256": hashlib.sha256("\n".join(str(x["name"]) for x in runs).encode()).hexdigest(),
    }
    (RESULTS / "freqtrade_execution_receipt.json").write_text(json.dumps(receipt, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
