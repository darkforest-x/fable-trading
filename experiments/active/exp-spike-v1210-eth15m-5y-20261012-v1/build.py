"""Five-year OKX ETHUSDT.P 15m replay of frozen SPIKE V12.10 (owner request 2026-10-12).

V12.10 = V12.9 ordinary signals admitted only when the same-timeframe Volumatic VIDYA trend agrees
(long in the latched up state, short in the down state), evaluated at the signal bar's confirmed close.
Everything is imported from the committed parent study exp-spike-v1210-volumatic-vidya-gate-20261012-v1
(``_make_events``, ``volumatic_trend``, ``_condition``, ``_same_regime_controls``, ``_metric``) and from
its engine modules (spike_v128_recent facts_for / serial / matched_controls), so the rules cannot drift:
V12.9 15m facts with the completed 1h SMA60 direction gate, next 15m open fill, stop beyond the 5-bar
extreme by 0.2 ATR (at least 2 ATR), 4 ATR close trail armed at a 2R close, raw opposite-signal exit,
0.2% round-trip cost, one position at a time.

Data: OKX official 1m archives 2021-09..2026-09 plus a confirmed API tail (fetch.py receipts, hashes
checked), aggregated to complete 5m and 15m UTC buckets; incomplete buckets stay timestamp gaps and are
counted. Window 2021-10-12 to 2026-10-12 (signal close). Split 2024-02-01: before it is data no V12.10
ETH study has used; from it is the parent study's window. The split is also the control fold.

Checks, all fail closed:
- parity: closed trades whose signal closes inside config parity_window must equal the parent study's
  saved OKX 15m ledgers (gated and ungated) field by field;
- every closed trade's cost is exactly 0.2% and net R = net return / initial risk.
Cross-check (descriptive): the same code on Binance USD-M ETHUSDT 5m, same window to the series end.

Outputs: results_v1/ (ledgers, controls, statuses, summary.json) and results_v1/bars_15m.csv.gz
(15m OHLC, VIDYA line/state, six-MA rope, for the report's candle charts).

Usage:
  PYTHONPATH=. .venv/bin/python experiments/active/exp-spike-v1210-eth15m-5y-20261012-v1/build.py
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_lowtf_v1_v126 as low
from yoyo.evaluation import spike_v128_recent as replay

ROOT = Path.cwd()
HERE = Path(__file__).resolve().parent
CFG = json.loads((HERE / "config.json").read_text())
OUT = HERE / "results_v1"
START, SPLIT, END = (pd.Timestamp(CFG[k]) for k in ("start", "split", "end"))
ASSET, TICK, MIN = CFG["asset"], float(CFG["tick"]), int(CFG["timeframe_min"])
FIELDS = ("side", "entry_time", "entry_price", "initial_stop", "exit_time", "exit_price", "exit_reason", "net_r")

_spec = importlib.util.spec_from_file_location("v1210_gate_study", ROOT / CFG["parent_study"] / "build.py")
assert _spec is not None and _spec.loader is not None
v1210 = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = v1210
_spec.loader.exec_module(v1210)


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def okx_base5m() -> tuple[pd.DataFrame, dict]:
    root, sym = Path(CFG["data_dir"]), CFG["symbol"]
    archive = json.loads((root / f"archive_{sym}.json").read_text())
    api = json.loads((root / f"api_{sym}.json").read_text())
    a_path, t_path = root / Path(archive["output_path"]).name, Path(api["path"])
    if archive["status"] != "complete" or archive["months_missing"] or sha(a_path) != archive["output_sha256"]:
        raise SystemExit("OKX archive incomplete or changed")
    if sha(t_path) != api["sha256"] or pd.Timestamp(api["start"]) != pd.Timestamp(CFG["archive_max_exclusive"]) \
            or pd.Timestamp(api["end_exclusive"]) != END:
        raise SystemExit("OKX API tail changed or misaligned")
    base1m = low.load_base([a_path, t_path], END)
    base5m, partial = low.complete_bars(base1m, 5)
    return base5m, {"source": "okx", "archive": str(a_path), "archive_sha256": archive["output_sha256"],
                    "tail": str(t_path), "tail_sha256": api["sha256"], "tail_missing_minutes": api["missing_minutes"],
                    "rows_1m": len(base1m), "first_1m": str(base1m.index[0]), "last_1m": str(base1m.index[-1]),
                    "incomplete_5m_buckets": int(partial)}


def binance_base5m() -> tuple[pd.DataFrame, dict, pd.Timestamp]:
    path = Path(CFG["binance_crosscheck"]["series"])
    raw = pd.read_csv(path, usecols=["ts", "open", "high", "low", "close", "volume"]).drop_duplicates("ts").sort_values("ts")
    index = pd.DatetimeIndex(pd.to_datetime(raw.ts.astype("int64").to_numpy(), unit="ms", utc=True))
    base = pd.DataFrame(raw[["open", "high", "low", "close", "volume"]].to_numpy(float), index=index,
                        columns=["open", "high", "low", "close", "volume"])
    base = base.loc[base.index < END]
    end = min(END, (base.index[-1] + pd.Timedelta(minutes=5)).floor(f"{MIN}min"))
    return base, {"source": "binance", "series": str(path), "sha256": sha(path), "first_5m": str(base.index[0]),
                  "last_5m": str(base.index[-1])}, end


def run_case(base5m: pd.DataFrame, venue: str, symbol: str, end: pd.Timestamp) -> dict:
    bars, partial = replay.complete_bars(base5m, MIN)
    facts = replay.facts_for(bars, base5m, ASSET, TICK, MIN)
    frame = facts["frame"]
    identity = {"venue": venue, "symbol": symbol, "asset": ASSET, "timeframe_min": MIN}
    key = f"{venue}:{symbol}:{MIN}m"
    prepared = replay.source.prepared_arm(frame, facts["gap"], facts["side"], key, identity, MIN, TICK)
    events = v1210._make_events(facts, frame, identity, key, MIN, START, end)
    trend = v1210.volumatic_trend(frame)
    gated_events, counts = v1210._condition(events, trend)
    out: dict = {"frame": frame, "trend": trend, "facts": facts, "counts": counts, "incomplete_15m_buckets": int(partial),
                 "data_gap_bars": int(np.asarray(facts["gap"]).sum())}
    for variant, evs in (("baseline", events), ("vidya_gate", gated_events)):
        trades, statuses = replay.serial(prepared, evs, "ordinary", key)
        closed = [t for t in trades if not bool(t["censored"])]
        if closed:
            np.testing.assert_allclose([t["gross_return"] - t["net_return"] for t in closed], CFG["round_trip_cost"], atol=1e-12)
            np.testing.assert_allclose([t["net_r"] for t in closed], [t["net_return"] / t["initial_risk_frac"] for t in closed], atol=1e-10)
        for t in trades:
            t["variant"] = variant
            t["vidya_up_at_signal"] = bool(trend["up"][int(t["signal_i"])])
            t["vidya_ready_at_signal"] = bool(trend["ready"][int(t["signal_i"])])
        out[variant] = trades
        out[variant + "_statuses"] = statuses
    window = {"start": str(START), "split": str(SPLIT), "end": str(end)}
    out["baseline_controls"] = replay.matched_controls(
        prepared, out["baseline"], {**window, "vol_bins": CFG["vol_bins"], "control_seed": CFG["control_seed"]})
    gate_cfg = {"eth_okx": window, "altcoins_binance": window,
                "execution": {"volatility_bins": CFG["vol_bins"], "control_seed": CFG["control_seed"]}}
    out["vidya_gate_controls"] = v1210._same_regime_controls(prepared, out["vidya_gate"], gate_cfg, trend)
    out["end"] = end
    return out


def parity(mine: list[dict], parent_path: str, label: str) -> dict:
    """Closed trades inside the parity window must equal the parent's saved OKX 15m ledger."""
    lo, hi = (pd.Timestamp(x) for x in CFG["parity_window"])
    parent = pd.read_csv(parent_path)
    parent = parent.loc[(parent.venue == "okx") & (parent.timeframe_min == MIN) & ~parent.censored.astype(bool)].copy()
    ours = pd.DataFrame(mine)
    ours = ours.loc[~ours.censored.astype(bool)].copy()
    for df in (parent, ours):
        df["signal_close"] = pd.to_datetime(df.signal_close, utc=True)
    parent = parent.loc[(parent.signal_close >= lo) & (parent.signal_close < hi)].set_index("trade_key").sort_index()
    ours = ours.loc[(ours.signal_close >= lo) & (ours.signal_close < hi)].set_index("trade_key").sort_index()
    if set(parent.index) != set(ours.index):
        raise SystemExit(f"{label} parity failed: missing {sorted(set(parent.index) - set(ours.index))[:5]} "
                         f"extra {sorted(set(ours.index) - set(parent.index))[:5]}")
    a, b = parent[list(FIELDS)].copy(), ours[list(FIELDS)].copy()
    for col in ("entry_time", "exit_time"):
        a[col], b[col] = pd.to_datetime(a[col], utc=True), pd.to_datetime(b[col], utc=True)
    for col in ("entry_price", "initial_stop", "exit_price", "net_r"):
        np.testing.assert_allclose(a[col].to_numpy(float), b[col].to_numpy(float), rtol=1e-10, atol=1e-10, err_msg=f"{label} {col}")
    if not a[["side", "entry_time", "exit_time", "exit_reason"]].equals(b[["side", "entry_time", "exit_time", "exit_reason"]]):
        raise SystemExit(f"{label} parity failed on identity fields")
    return {"label": label, "window": CFG["parity_window"], "closed_trades_checked": int(len(a)), "exact": True}


def bars_table(case: dict) -> pd.DataFrame:
    """15m candles plus the series the report draws: VIDYA trend line (na on flip bars) and the six-MA rope."""
    f, tr = case["frame"], case["trend"]
    up = tr["up"]
    line = np.where(up, tr["lower"], tr["upper"]).astype(float)
    flip = np.zeros(len(up), bool)
    flip[1:] = up[1:] != up[:-1]
    line[flip | ~tr["ready"]] = np.nan
    return pd.DataFrame({"ts": f.index.asi8 // 1_000_000, "open": f.open.to_numpy(), "high": f.high.to_numpy(),
                         "low": f.low.to_numpy(), "close": f.close.to_numpy(), "vidya_line": line, "vidya_up": up,
                         "rope_high": f.ropeHigh.to_numpy(float), "rope_low": f.ropeLow.to_numpy(float),
                         "gap": np.asarray(case["facts"]["gap"], bool)})


def main():
    t0 = time.time()
    OUT.mkdir(exist_ok=True)
    seed = int(CFG["stat_seed"])
    base5m, okx_info = okx_base5m()
    print("okx data", okx_info, flush=True)
    okx = run_case(base5m, "okx", CFG["symbol"], END)
    print("okx case", okx["counts"], "trades", len(okx["baseline"]), len(okx["vidya_gate"]), f"{time.time() - t0:.0f}s", flush=True)
    checks = [parity(okx["vidya_gate"], CFG["parent_trades"]["vidya_gate"], "V12.10 gated"),
              parity(okx["baseline"], CFG["parent_trades"]["baseline"], "V12.9 baseline")]
    print("parity", checks, flush=True)
    bbase, binfo, bend = binance_base5m()
    bnc = run_case(bbase, "binance", CFG["binance_crosscheck"]["symbol"], bend)
    print("binance case", bnc["counts"], "trades", len(bnc["baseline"]), len(bnc["vidya_gate"]), f"{time.time() - t0:.0f}s", flush=True)

    summary: dict = {"generated_at": pd.Timestamp.now(tz="UTC").isoformat(), "git_head": subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip(), "config": CFG, "parity": checks,
        "data": {"okx": okx_info, "binance": binfo}, "cases": {}}
    for name, case in (("okx", okx), ("binance", bnc)):
        entry: dict = {"end": str(case["end"]), "gate_counts": case["counts"], "incomplete_15m_buckets": case["incomplete_15m_buckets"],
                       "data_gap_bars": case["data_gap_bars"], "first_bar": str(case["frame"].index[0]), "last_bar": str(case["frame"].index[-1])}
        for variant in ("vidya_gate", "baseline"):
            trades, controls = pd.DataFrame(case[variant]), case[variant + "_controls"]
            entry[variant] = v1210._metric(trades, controls, START, SPLIT, case["end"], seed)
            entry[variant]["open_at_end"] = int(trades.censored.astype(bool).sum()) if len(trades) else 0
            entry[variant]["skipped_in_position"] = sum(s["status"] == "skipped_in_position" for s in case[variant + "_statuses"])
            trades.to_csv(OUT / f"{name}_{variant}_trades.csv.gz", index=False)
            controls.to_csv(OUT / f"{name}_{variant}_controls.csv.gz", index=False)
        summary["cases"][name] = entry
        f = entry["vidya_gate"]["periods"]["full"]
        print(name, "V12.10", {k: f.get(k) for k in ("closed", "win_rate", "mean_net_r", "sum_net_r", "max_drawdown_r", "excess_mean_r", "excess_month_block_p")}, flush=True)
    bars_table(okx).to_csv(OUT / "bars_15m.csv.gz", index=False)
    (OUT / "summary.json").write_text(json.dumps(v1210.clean(summary), ensure_ascii=False, indent=1, default=str))
    print("wrote", OUT, f"{time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
