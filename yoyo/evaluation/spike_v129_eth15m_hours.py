"""SPIKE V12.9 15m without Beijing-weekend and Beijing 12:00-16:00 entries (owner 2026-10-10).

Owner: "15m去掉周末和12-16点再回测一下". Frozen config:
``experiments/active/exp-spike-v129-eth15m-hours-20261010-v1/config.json``.

Single variable relative to the no-weekend arm of exp-spike-v129-eth15m-weekend-20261010-v1:
signals whose close falls in Beijing hours 12-15 are also not taken. Open positions keep
their exits; occupancy is re-replayed so freed signals can trade. Each arm's matched random
controls come from the bars that arm may enter (baseline: all; no-weekend: Beijing weekdays;
this arm: Beijing weekdays outside 12-16), otherwise the strata of
``spike_v128_recent.matched_controls``. Facts, exits and costs come unchanged from
``spike_v129_eth15m.ledger``; Binance engine parity is re-checked.

Honesty boundary: both filters were found in these same ledgers; OKX and Binance 2024-26 are
the same market period. In-sample only.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import subprocess
from typing import Callable

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v128_recent as p
from yoyo.evaluation import spike_v128_recent_report as rep
from yoyo.evaluation import spike_v129_eth15m as fifteen
from yoyo.evaluation import spike_v129_eth15m_weekend as weekend
from yoyo.evaluation import spike_v129_eth5m as five
from yoyo.evaluation.spike_v112_support_study import _local_transitive_python
from yoyo.evaluation.spike_v8_six_filters import _committed

EXP = Path("experiments/active/exp-spike-v129-eth15m-hours-20261010-v1")
CONFIG = EXP / "config.json"
TEST = Path("tests/evaluation/test_spike_v129_eth15m_hours.py")
BJ = "Asia/Shanghai"


def config() -> dict:
    cfg = json.loads(CONFIG.read_text())
    if cfg["experiment_id"] != EXP.name or cfg["blocked_hours_beijing"] != [12, 13, 14, 15]:
        raise ValueError("invalid frozen hours config")
    return cfg


def sources() -> dict[str, str]:
    paths = tuple(dict.fromkeys([CONFIG, Path(__file__), TEST, *_local_transitive_python((Path(__file__),))]))
    if not _committed(paths):
        raise ValueError("Commit every builder, test, config and imported source before construction")
    return {str(x): p.digest(x) for x in paths}


def blocked_hours(close_times, hours=(12, 13, 14, 15)) -> np.ndarray:
    return np.isin(pd.DatetimeIndex(close_times).tz_convert(BJ).hour, list(hours))


ARMS: dict[str, Callable[[object], np.ndarray]] = {
    "baseline": lambda t: np.zeros(len(pd.DatetimeIndex(t)), bool),
    "no_weekend": weekend.is_weekend,
    "no_weekend_1216": lambda t: weekend.is_weekend(t) | blocked_hours(t),
}


def allowed_controls(prepared, trades: list[dict], cfg: dict, blocked: Callable) -> pd.DataFrame:
    """``spike_v128_recent.matched_controls`` restricted to bars the arm may enter."""
    frame, minutes = prepared.frame, prepared.context.minutes
    close = frame.index + pd.Timedelta(minutes=minutes)
    week = np.asarray(close.strftime("%G-W%V"))
    fold = np.where(close < pd.Timestamp(cfg["split"]), "earlier", "later")
    bins = np.searchsorted(np.asarray(cfg["vol_bins"], float), prepared.atr / prepared.close, side="left")
    ready = frame.get("ready", pd.Series(False, index=frame.index)).fillna(False).to_numpy(bool)
    valid = (ready & np.isfinite(prepared.atr) & (prepared.atr > 0) & np.isfinite(prepared.close) & (prepared.close > 0)
             & ~prepared.gap & (close >= pd.Timestamp(cfg["start"])) & (close < pd.Timestamp(cfg["end"])) & ~blocked(close))
    pools, cache, rows = {}, {}, []
    for trade in trades:
        i, side = int(trade["signal_i"]), int(trade["side"])
        stratum = (side, week[i], fold[i], int(bins[i]))
        if stratum not in pools:
            pools[stratum] = np.flatnonzero(valid & (week == stratum[1]) & (fold == stratum[2]) & (bins == stratum[3]))
        choices = pools[stratum][pools[stratum] != i]
        chosen = None if not len(choices) else int(choices[int(hashlib.sha256(
            f"{cfg['control_seed']}|{trade['trade_key']}".encode()).hexdigest(), 16) % len(choices)])
        status, control = "empty_stratum", None
        if chosen is not None:
            status, control = cache.setdefault((chosen, side), p.attempt(prepared, chosen, side))
        matched = control is not None and status == "closed" and not bool(trade["censored"])
        rows.append({"trade_key": trade["trade_key"], "arm": "ordinary", "symbol": trade["symbol"], "side": side,
                     "matched": bool(matched), "reason": "target_censored" if bool(trade["censored"]) else ("matched" if matched else status),
                     "control_sig": chosen, "control_net_r": math.nan if not matched else control["net_r"],
                     "control_net_return": math.nan if not matched else control["net_return"]})
    return pd.DataFrame(rows)


def sample(base5m: pd.DataFrame, *, venue: str, symbol: str, asset: str, tick: float, start, split, end, cfg: dict) -> dict:
    identity = {"venue": venue, "symbol": symbol, "asset": asset, "timeframe_min": 15}
    key = f"{venue}:{symbol}:15m"
    _, events, _, _, prepared = fifteen.ledger(base5m, asset=asset, tick=tick, key=key, identity=identity,
                                               start=pd.Timestamp(start), end=pd.Timestamp(end))
    scfg = {"start": start, "split": split, "end": end, "vol_bins": cfg["vol_bins"], "control_seed": cfg["control_seed"],
            "stat_seed": cfg["stat_seed"]}
    out = {}
    for arm, blocked in ARMS.items():
        keep = [e for e in events if not blocked([e["signal_close"]])[0]]
        trades, statuses = p.serial(prepared, keep, arm, key)
        statuses += [{**e, "arm": arm, "status": "filtered", "blocking_trade": None} for e in events
                     if e["in_window"] and blocked([e["signal_close"]])[0]]
        table = pd.DataFrame(trades)
        table["censored"] = table.censored.astype(bool)
        closed = table.loc[~table.censored]
        np.testing.assert_allclose(closed.gross_return - closed.net_return, .002, atol=1e-12)
        table["arm"] = "ordinary"
        status = pd.DataFrame(statuses).assign(arm="ordinary")
        tables = {"trades": table, "controls": allowed_controls(prepared, trades, scfg, blocked), "statuses": status,
                  "candidates": pd.DataFrame([e for e in keep if e["in_window"]])}
        out[arm] = {"tables": tables, "summary": five.summarize(tables, scfg)}
    return out


def main() -> None:
    cfg = config()
    code = sources()
    fcfg = fifteen.config()
    parity = fifteen.parent_parity(fcfg)
    meta = p.source.symbol_meta()["ETHUSDT"]
    runs = {
        "okx": sample(fifteen.okx_base5m(fcfg), venue="okx", symbol=fcfg["symbol"], asset=fcfg["asset"], tick=float(fcfg["tick"]),
                      start=cfg["okx_start"], split=cfg["okx_split"], end=cfg["okx_end"], cfg=cfg),
        "binance": sample(fifteen.binance_base5m(fcfg), venue="binance_um", symbol="ETHUSDT", asset=meta["asset"],
                          tick=float(meta["tick"]), start=cfg["binance_start"], split=cfg["binance_split"], end=cfg["binance_end"], cfg=cfg),
    }
    comparison = []
    for venue, arms in runs.items():
        split = pd.Timestamp(cfg[f"{venue}_split"])
        for arm, res in arms.items():
            trades = rep.enrich(res["tables"]["trades"], split)
            closed = trades.loc[~trades.censored]
            for window, label, mask in (("full", "full", closed.signal_close >= pd.Timestamp(cfg[f"{venue}_start"])),
                                        ("earlier", "before_split", closed.period.eq("earlier")),
                                        ("last_12m", "after_split", closed.signal_close >= split)):
                comparison.append({"venue": venue, "arm": arm, "window": label,
                                   **weekend.row_of(res["summary"], window, closed.loc[mask])})
    out = EXP / "run_v1"
    variant = runs["okx"]["no_weekend_1216"]
    (out / "okx_variant").mkdir(parents=True, exist_ok=True)
    for name, frame in variant["tables"].items():
        frame.to_csv(out / "okx_variant" / f"{name}.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    variant["summary"]["yearly"].to_csv(out / "okx_variant" / "yearly.csv", index=False)
    variant["summary"]["monthly"].to_csv(out / "okx_variant" / "monthly.csv", index=False)
    p.dump(out / "okx_variant" / "receipt.json", five._clean({"summary": variant["summary"]["summary"], "parity": parity}))
    runs["binance"]["no_weekend_1216"]["tables"]["trades"].to_csv(out / "binance_variant_trades.csv.gz", index=False,
                                                                   compression={"method": "gzip", "mtime": 0})
    table = pd.DataFrame(comparison)
    table.to_csv(out / "comparison.csv", index=False)
    p.dump(out / "receipt.json", five._clean({"config_sha256": p.digest(CONFIG), "code": code, "parity": parity,
                                               "comparison": comparison,
                                               "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()}))
    pd.set_option("display.width", 240)
    print(table.round(3).to_string(index=False))


if __name__ == "__main__":
    main()
