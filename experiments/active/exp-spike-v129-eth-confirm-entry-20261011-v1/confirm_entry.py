"""Wait-for-profit confirmation entry on SPIKE V12.9 ETH 5m/15m ordinary trades.

Owner 2026-10-11: "5min级别出信号了 我先不开仓 等那个点确认了我再开". The winner anatomy
showed that trades already closing in profit a few bars after entry win far more often,
but that split ignores the worse price a confirmed entry pays. This replays the recorded
V12.9 trades with a delayed entry and keeps everything else anchored to the original
signal (learning: delayed-entry-must-preserve-anchor-clock-and-stop):

* bars counted from the original entry bar (k = 1 is the entry bar); at each close k <= K
  the signal is skipped if the original stop printed in that bar or the original trade
  already exited, and confirmed if side * (close - original entry) >= m * original risk;
* a confirmed signal enters at the next open, with the original absolute stop, and leaves at
  the original recorded exit (same trail anchored to the original entry, same opposite
  exit); if that exit is at or before the new entry the signal is skipped;
* new risk = side * (entry - stop); net R uses that risk and 0.2% round-trip cost.

Candidates K in {3, 6, 12} x m in {0, 0.5, 1}; selection on OKX signals before 2025-10-10,
checks on OKX from 2025-10-10 and Binance ETHUSDT before 2024-02-01 (config.json).

Run: PYTHONPATH=. .venv/bin/python experiments/active/exp-spike-v129-eth-confirm-entry-20261011-v1/confirm_entry.py
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_lowtf_v1_v126 as low
from yoyo.evaluation import spike_v128_recent as p
from yoyo.evaluation import spike_v129_eth5m as s5
from yoyo.evaluation import spike_v129_eth15m as s15

EXP = Path("experiments/active/exp-spike-v129-eth-confirm-entry-20261011-v1")
CONFIG = EXP / "config.json"
OUT = EXP / "results_v1"
OKX = {5: Path("experiments/active/exp-spike-v129-eth5m-20261010-v1/run_v1/trades.csv.gz"),
       15: Path("experiments/active/exp-spike-v129-eth15m-20261010-v1/run_v1/trades.csv.gz")}
BINANCE = {5: Path("experiments/active/exp-spike-manual-system-20260925-v1/run_v1/streams/ETHUSDT_5m/trades.csv.gz"),
           15: Path("experiments/active/exp-spike-manual-system-20260925-v1/run_v1/streams/ETHUSDT_15m/trades.csv.gz")}
BINANCE_5M = Path("data/research/spike_manual_system_20260925/ETHUSDT.csv.gz")
BINANCE_END = pd.Timestamp("2024-02-01T00:00:00Z")
SPLIT = pd.Timestamp("2025-10-10T00:00:00Z")
COST = 0.002


def okx_frame(minutes: int) -> pd.DataFrame:
    if minutes == 5:
        cfg = s5.config()
        data = json.loads((Path(cfg["data_dir"]) / "receipt.json").read_text())
        base = low.load_base([Path(x) for x in data["inputs"]], pd.Timestamp(cfg["end"]))
        bars, _ = low.complete_bars(base, 5)
        return low.pine_facts(bars, bars, cfg["asset"], float(cfg["tick"]), 5)["frame"]
    cfg = s15.config()
    base5m = s15.okx_base5m(cfg)
    bars, _ = p.complete_bars(base5m, 15)
    return p.facts_for(bars, base5m, cfg["asset"], float(cfg["tick"]), 15)["frame"]


def binance_frame(minutes: int) -> pd.DataFrame:
    raw = pd.read_csv(BINANCE_5M)
    base = raw.set_index(pd.to_datetime(raw.ts, unit="ms", utc=True))[["open", "high", "low", "close", "volume"]]
    bars, _ = p.complete_bars(base, minutes)
    meta = p.source.symbol_meta()["ETHUSDT"]
    if minutes == 5:
        return low.pine_facts(bars, base, meta["asset"], float(meta["tick"]), 5)["frame"]
    return p.facts_for(bars, base, meta["asset"], float(meta["tick"]), 15)["frame"]


def trades(minutes: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    okx = pd.read_csv(OKX[minutes])
    okx = okx.loc[(okx.arm == "ordinary") & ~okx.censored.astype(bool)].reset_index(drop=True)
    okx["sample"] = np.where(pd.to_datetime(okx.signal_close, utc=True) < SPLIT, "selection", "check_okx")
    bn = pd.read_csv(BINANCE[minutes])
    bn = bn.loc[(bn.policy == "baseline") & (bn.arm == "ordinary") & ~bn.censored.astype(bool)
                & (pd.to_datetime(bn.signal_close, utc=True) < BINANCE_END)].reset_index(drop=True)
    bn["sample"] = "check_binance"
    return okx, bn


def confirm(t: pd.DataFrame, f: pd.DataFrame, K: int, m: float) -> pd.DataFrame:
    """Delayed-entry replay of every trade row; skipped rows have entered = False."""
    O, H, L, C = (f[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    if not np.allclose(C[t.signal_i.to_numpy(int)], t.signal_price.to_numpy(float)):
        raise ValueError("ledger signal_i does not index this frame")
    rows = []
    for r in t.itertuples():
        sd, a, x = int(r.side), int(r.entry_i), int(r.exit_i)
        e0, stop, exit_px = float(r.entry_price), float(r.initial_stop), float(r.exit_price)
        if not np.isclose(O[a], e0):
            raise ValueError("entry_i does not index the entry open")
        risk0 = sd * (e0 - stop)
        got = {"entered": False, "confirm_bar": np.nan, "entry": np.nan, "net_r": np.nan, "net_ret": np.nan, "gross_r": np.nan}
        for k in range(K):
            j = a + k
            if j >= x or j + 1 >= len(O):
                break
            touched = (L[j] <= stop) if sd == 1 else (H[j] >= stop)
            if touched:
                break
            if sd * (C[j] - e0) >= m * risk0:
                e1 = O[j + 1]
                risk1 = sd * (e1 - stop)
                same_bar_open_exit = (x == j + 1) and r.exit_reason == "opposite_v6_next_open"
                if risk1 <= 0 or x < j + 1 or same_bar_open_exit:
                    break
                gross = sd * (exit_px / e1 - 1.0)
                got = {"entered": True, "confirm_bar": k + 1, "entry": e1,
                       "gross_r": gross / (risk1 / e1), "net_r": (gross - COST) / (risk1 / e1), "net_ret": gross - COST}
                break
        rows.append(got)
    return pd.DataFrame(rows, index=t.index)


def stats(net_r: pd.Series, net_ret: pd.Series, n_signals: int) -> dict:
    r = net_r.dropna()
    return {"signals": n_signals, "entries": len(r), "kept": len(r) / n_signals if n_signals else np.nan,
            "win_rate": float((r > 0).mean()) if len(r) else np.nan, "mean_net_r": float(r.mean()) if len(r) else np.nan,
            "total_net_r": float(r.sum()), "mean_net_bp": float(net_ret.dropna().mean() * 1e4) if len(r) else np.nan,
            "ge5r": int((r >= 5).sum()), "worst_r": float(r.min()) if len(r) else np.nan}


def main() -> None:
    cfg = json.loads(CONFIG.read_text())
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "--", str(Path(__file__)), str(CONFIG)], capture_output=True, text=True).stdout.strip()
    if dirty:
        raise ValueError("commit the script and config before generating results")
    rows, ledgers = [], []
    for minutes in (5, 15):
        okx, bn = trades(minutes)
        for t, f in ((okx, okx_frame(minutes)), (bn, binance_frame(minutes))):
            for sample, g in t.groupby("sample"):
                rows.append({"minutes": minutes, "sample": sample, "candidate": "immediate",
                             **stats(g.net_r, g.net_return, len(g))})
            for K in cfg["candidates"]["K_bars"]:
                for m in cfg["candidates"]["m_r"]:
                    c = confirm(t, f, K, m)
                    both = pd.concat([t[["sample", "trade_key", "side", "signal_close", "net_r"]].rename(columns={"net_r": "immediate_net_r"}), c], axis=1)
                    both["minutes"], both["candidate"] = minutes, f"K{K}_m{m:g}"
                    ledgers.append(both)
                    for sample, g in both.groupby("sample"):
                        rows.append({"minutes": minutes, "sample": sample, "candidate": f"K{K}_m{m:g}",
                                     **stats(g.net_r, g.net_ret, len(g))})
    table = pd.DataFrame(rows)
    picks = []
    for minutes, g in table.groupby("minutes"):
        sel = g[(g["sample"] == "selection") & (g.candidate != "immediate") & (g.entries >= 30)]
        best = sel.sort_values("mean_net_r", ascending=False).iloc[0].candidate
        base = g[g.candidate == "immediate"].set_index("sample").mean_net_r
        pick = g[g.candidate == best].set_index("sample").mean_net_r
        passed = all(pick[s] > base[s] and pick[s] > 0 for s in ("check_okx", "check_binance"))
        picks.append({"minutes": int(minutes), "selected": best, "passed": bool(passed),
                      **{f"{s}_pick": float(pick[s]) for s in pick.index}, **{f"{s}_immediate": float(base[s]) for s in base.index}})
    OUT.mkdir(parents=True, exist_ok=True)
    table.to_csv(OUT / "summary.csv", index=False)
    pd.concat(ledgers, ignore_index=True).to_csv(OUT / "trades.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    (OUT / "receipt.json").write_text(json.dumps({"experiment_id": cfg["experiment_id"], "source_commit": head, "picks": picks,
                                                  "generated_at": pd.Timestamp.now(tz="UTC").isoformat()}, indent=1))
    pd.set_option("display.width", 250)
    print(table.round(3).to_string(index=False))
    print(json.dumps(picks, indent=1))


if __name__ == "__main__":
    main()
