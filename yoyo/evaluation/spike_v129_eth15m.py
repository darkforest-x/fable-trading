"""SPIKE V12.9 frozen-rule replay on OKX ETHUSDT.P (ETH-USDT-SWAP) 15m, ordinary signals.

Source: owner request 2026-10-10 ("v12.9 15min ethusdt.p详细回测呢"), after the 5m run
(exp-spike-v129-eth5m-20261010-v1). Frozen config:
``experiments/active/exp-spike-v129-eth15m-20261010-v1/config.json``.

Decision basis: on 15m V12.9 is V12.8 = V12.6 entries/exits; the trail stays 4ATR (only
1h uses 2ATR) and the completed 1h SMA60 direction gate applies. The committed 15m port
``spike_v126_htf_recheck.pine_facts`` (via ``spike_v128_recent.facts_for``) and the
side-aware next-open exit ``spike_v128_recent.serial`` are reused unchanged, exactly as
exp-spike-manual-system-20260925-v1 did for its 15m streams. Ordinary both-side signals
only, one position at a time, warmed from the first archive bar.

Parity: the same ``ledger`` function run on the Binance ETH 5m series must reproduce every
closed ordinary baseline trade of the manual-system ETHUSDT_15m ledger. Causality: chart
facts use bars through the signal close; the 1h SMA60 uses 1h buckets closed at or before
the chart bar open. Matched controls: same side x UTC week x fold x ATR/close bucket.
No training, tuning, Pine, monitor or account change.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_lowtf_v1_v126 as low
from yoyo.evaluation import spike_v128_recent as p
from yoyo.evaluation import spike_v129_eth5m as five
from yoyo.evaluation.spike_v112_support_study import _local_transitive_python
from yoyo.evaluation.spike_v8_six_filters import _committed

EXP = Path("experiments/active/exp-spike-v129-eth15m-20261010-v1")
CONFIG = EXP / "config.json"
TEST = Path("tests/evaluation/test_spike_v129_eth15m.py")
MINUTES = 15
PARITY_FIELDS = ["side", "entry_time", "entry_price", "initial_stop", "exit_time", "exit_price", "exit_reason", "net_r"]


def config() -> dict:
    cfg = json.loads(CONFIG.read_text())
    if cfg["experiment_id"] != EXP.name or cfg["timeframe_min"] != MINUTES or float(cfg["round_trip_cost"]) != .002:
        raise ValueError("invalid frozen V12.9 ETH 15m config")
    return cfg


def sources() -> dict[str, str]:
    paths = tuple(dict.fromkeys([CONFIG, Path(__file__), TEST, five.PINE_V129, p.PINE, p.PINE_V128,
                                 *_local_transitive_python((Path(__file__),))]))
    if not _committed(paths):
        raise ValueError("Commit every builder, test, config and imported source before construction")
    return {str(x): p.digest(x) for x in paths}


def okx_base5m(cfg: dict) -> pd.DataFrame:
    data = json.loads(Path(cfg["data_receipt"]).read_text())
    if any(p.digest(Path(k)) != v for k, v in data["inputs"].items()):
        raise ValueError("registered OKX inputs changed")
    base1m = low.load_base([Path(x) for x in data["inputs"]], pd.Timestamp(cfg["end"]))
    base5m, partial = low.complete_bars(base1m, 5)
    if partial:
        raise ValueError("partial 5m buckets in OKX data")
    return base5m


def binance_base5m(cfg: dict) -> pd.DataFrame:
    if p.digest(Path(cfg["binance_data"])) != cfg["binance_data_sha256"]:
        raise ValueError("Binance series changed")
    raw = pd.read_csv(cfg["binance_data"])
    return raw.set_index(pd.to_datetime(raw.ts, unit="ms", utc=True))[["open", "high", "low", "close", "volume"]]


def ledger(base5m: pd.DataFrame, *, asset: str, tick: float, key: str, identity: dict,
           start: pd.Timestamp, end: pd.Timestamp) -> tuple[dict, list[dict], list[dict], list[dict], object]:
    """Ordinary V9 events on 15m, serial trades (window only) and statuses; warm from the first bar."""
    bars, partial = p.complete_bars(base5m, MINUTES)
    if partial:
        raise ValueError("partial 15m buckets")
    facts = p.facts_for(bars, base5m, asset, tick, MINUTES)
    frame = facts["frame"]
    prepared = p.source.prepared_arm(frame, facts["gap"], facts["side"], key, identity, MINUTES, tick)
    events = []
    for i in np.flatnonzero(facts["v9"]):
        close_time = frame.index[i] + pd.Timedelta(minutes=MINUTES)
        if close_time >= end:
            continue
        events.append({**identity, "arm": "ordinary", "signal_i": int(i), "side": int(facts["side"][i]),
                       "signal_bar_open": frame.index[i], "signal_close": close_time, "in_window": bool(close_time >= start),
                       "signal_price": float(frame.close.iloc[i]),
                       "signal_atr_pct": float(frame.atr.iloc[i] / frame.close.iloc[i]),
                       "h1_sma60": float(facts["h1"].sma_60.iloc[i])})
    trades, statuses = p.serial(prepared, events, "ordinary", key)
    return facts, events, trades, statuses, prepared


def parent_parity(cfg: dict) -> dict:
    """The Binance run of this code path must equal the registered manual-system 15m ledger."""
    meta = p.source.symbol_meta()["ETHUSDT"]
    identity = {"venue": "binance_um", "symbol": "ETHUSDT", "asset": meta["asset"], "timeframe_min": MINUTES}
    _, _, trades, _, _ = ledger(binance_base5m(cfg), asset=meta["asset"], tick=float(meta["tick"]), key="ETHUSDT_15",
                                identity=identity, start=pd.Timestamp("2021-09-25T00:00:00Z"), end=pd.Timestamp("2026-09-25T00:00:00Z"))
    mine = pd.DataFrame(trades)
    mine = mine.loc[~mine.censored.astype(bool)].set_index("signal_i")
    parent = pd.read_csv(cfg["parent_trades"])
    parent = parent.loc[(parent.policy == "baseline") & (parent.arm == "ordinary") & ~parent.censored.astype(bool)].set_index("signal_i")
    if set(parent.index) != set(mine.index):
        raise ValueError(f"Binance 15m parity: missing {sorted(set(parent.index) - set(mine.index))[:5]} "
                         f"extra {sorted(set(mine.index) - set(parent.index))[:5]}")
    left, right = parent[PARITY_FIELDS].sort_index(), mine.loc[parent.index.sort_values(), PARITY_FIELDS]
    for col in ("entry_time", "exit_time"):
        left[col], right[col] = pd.to_datetime(left[col], utc=True), pd.to_datetime(right[col], utc=True)
    pd.testing.assert_frame_equal(left, right, check_dtype=False, check_exact=False, rtol=1e-10, atol=1e-10)
    return {"binance_parent_closed_trades_checked": len(left), "exact": True}


def main() -> None:
    argparse.ArgumentParser(description="SPIKE V12.9 OKX ETH 15m replay").parse_args()
    cfg = config()
    code = sources()
    parity = parent_parity(cfg)
    identity = {"venue": "okx", "symbol": cfg["symbol"], "asset": cfg["asset"], "timeframe_min": MINUTES}
    facts, events, trades, statuses, prepared = ledger(okx_base5m(cfg), asset=cfg["asset"], tick=float(cfg["tick"]),
                                                       key=f"okx:{cfg['symbol']}:15m", identity=identity,
                                                       start=pd.Timestamp(cfg["start"]), end=pd.Timestamp(cfg["end"]))
    table = pd.DataFrame(trades)
    table["censored"] = table.censored.astype(bool)
    closed = table.loc[~table.censored]
    np.testing.assert_allclose(closed.gross_return - closed.net_return, .002, atol=1e-12)
    np.testing.assert_allclose(closed.net_r, closed.net_return / closed.initial_risk_frac, atol=1e-10)
    controls = p.matched_controls(prepared, trades, cfg)
    tables = {"trades": table, "controls": controls, "statuses": pd.DataFrame(statuses),
              "candidates": pd.DataFrame([e for e in events if e["in_window"]])}
    out = EXP / "run_v1"
    out.mkdir(parents=True, exist_ok=True)
    for name, frame in tables.items():
        frame.to_csv(out / f"{name}.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    result = five.summarize(tables, cfg)
    result["yearly"].to_csv(out / "yearly.csv", index=False)
    result["monthly"].to_csv(out / "monthly.csv", index=False)
    frame = facts["frame"]
    receipt = {"run": "v1", "config_sha256": p.digest(CONFIG), "code": code, "parity": parity,
               "info": {"rows_15m": len(frame), "first": str(frame.index[0]), "last": str(frame.index[-1]),
                        "gaps": int(facts["gap"].sum()), "native_pine_parity": False},
               "summary": result["summary"],
               "files": {f"{n}.csv.gz": p.digest(out / f"{n}.csv.gz") for n in tables} | {
                   f: p.digest(out / f) for f in ("yearly.csv", "monthly.csv")},
               "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()}
    p.dump(out / "receipt.json", five._clean(receipt))
    s = result["summary"]["full"]["ordinary_all"]
    print(json.dumps(five._clean({"parity": parity, "closed": s["closed"], "win_rate": s["win_rate"],
                                  "mean_net_r": s["mean_net_r"], "sum_net_r": s["sum_net_r"]}), ensure_ascii=False))


if __name__ == "__main__":
    main()
