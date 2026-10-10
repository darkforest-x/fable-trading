"""SPIKE V12.9 15m ordinary signals without Beijing-weekend entries (owner 2026-10-10).

Owner: "15m去掉周末再回测一下". Frozen config:
``experiments/active/exp-spike-v129-eth15m-weekend-20261010-v1/config.json``.

Single variable: entry admission. Ordinary V9 signals whose close falls on a Beijing (UTC+8)
Saturday or Sunday are not taken; positions already open keep their exits; occupancy is
replayed again so a weekday signal that a weekend trade used to block can now trade. The
baseline ledger, facts, exits and costs come unchanged from ``spike_v129_eth15m.ledger``
(engine parity with the manual-system Binance 15m ledger is re-checked here). Matched
random controls for the filtered arm are drawn from weekday bars only, otherwise the
same strata as ``spike_v128_recent.matched_controls``.

Honesty boundary: the weekend weakness was noticed in these same ledgers, so both samples
are in-sample for this hypothesis; only forward data can validate it.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v128_recent as p
from yoyo.evaluation import spike_v128_recent_report as rep
from yoyo.evaluation import spike_v129_eth15m as fifteen
from yoyo.evaluation import spike_v129_eth5m as five
from yoyo.evaluation.spike_v112_support_study import _local_transitive_python
from yoyo.evaluation.spike_v8_six_filters import _committed

EXP = Path("experiments/active/exp-spike-v129-eth15m-weekend-20261010-v1")
CONFIG = EXP / "config.json"
TEST = Path("tests/evaluation/test_spike_v129_eth15m_weekend.py")
BJ = "Asia/Shanghai"


def config() -> dict:
    cfg = json.loads(CONFIG.read_text())
    if cfg["experiment_id"] != EXP.name or float(cfg["round_trip_cost"]) != .002:
        raise ValueError("invalid frozen weekend config")
    return cfg


def sources() -> dict[str, str]:
    paths = tuple(dict.fromkeys([CONFIG, Path(__file__), TEST, *_local_transitive_python((Path(__file__),))]))
    if not _committed(paths):
        raise ValueError("Commit every builder, test, config and imported source before construction")
    return {str(x): p.digest(x) for x in paths}


def is_weekend(close_times) -> np.ndarray:
    """Beijing Saturday or Sunday of the signal close."""
    return np.asarray(pd.DatetimeIndex(close_times).tz_convert(BJ).dayofweek >= 5)


def weekday_controls(prepared, trades: list[dict], cfg: dict) -> pd.DataFrame:
    """``spike_v128_recent.matched_controls`` with the random bar restricted to Beijing weekdays."""
    frame, minutes = prepared.frame, prepared.context.minutes
    close = frame.index + pd.Timedelta(minutes=minutes)
    week = np.asarray(close.strftime("%G-W%V"))
    fold = np.where(close < pd.Timestamp(cfg["split"]), "earlier", "later")
    bins = np.searchsorted(np.asarray(cfg["vol_bins"], float), prepared.atr / prepared.close, side="left")
    ready = frame.get("ready", pd.Series(False, index=frame.index)).fillna(False).to_numpy(bool)
    valid = (ready & np.isfinite(prepared.atr) & (prepared.atr > 0) & np.isfinite(prepared.close) & (prepared.close > 0)
             & ~prepared.gap & (close >= pd.Timestamp(cfg["start"])) & (close < pd.Timestamp(cfg["end"])) & ~is_weekend(close))
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
        rows.append({"trade_key": trade["trade_key"], "arm": trade["arm"], "symbol": trade["symbol"], "side": side,
                     "utc_week": stratum[1], "fold": stratum[2], "vol_bin": stratum[3], "matched": bool(matched),
                     "reason": "target_censored" if bool(trade["censored"]) else ("matched" if matched else status),
                     "target_net_return": trade["net_return"], "control_sig": chosen,
                     "control_net_r": math.nan if not matched else control["net_r"],
                     "control_net_return": math.nan if not matched else control["net_return"]})
    return pd.DataFrame(rows)


def stop_streak(trades: pd.DataFrame) -> int:
    best = run = 0
    for reason in trades.sort_values("entry_time").exit_reason:
        run = run + 1 if reason == "initial_stop" else 0
        best = max(best, run)
    return best


def sample(base5m: pd.DataFrame, *, venue: str, symbol: str, asset: str, tick: float, start, split, end, cfg: dict) -> dict:
    identity = {"venue": venue, "symbol": symbol, "asset": asset, "timeframe_min": 15}
    key = f"{venue}:{symbol}:15m"
    facts, events, base_trades, base_status, prepared = fifteen.ledger(
        base5m, asset=asset, tick=tick, key=key, identity=identity, start=pd.Timestamp(start), end=pd.Timestamp(end))
    keep = [e for e in events if not is_weekend([e["signal_close"]])[0]]
    blocked = [{**e, "status": "weekend_blocked", "blocking_trade": None} for e in events
               if e["in_window"] and is_weekend([e["signal_close"]])[0]]
    trades, statuses = p.serial(prepared, keep, "no_weekend", key)
    scfg = {"start": start, "split": split, "end": end, "vol_bins": cfg["vol_bins"], "control_seed": cfg["control_seed"],
            "stat_seed": cfg["stat_seed"]}
    out = {}
    for arm, tr, st, controls in (("baseline", base_trades, base_status, p.matched_controls(prepared, base_trades, scfg)),
                                  ("no_weekend", trades, statuses + blocked, weekday_controls(prepared, trades, scfg))):
        table = pd.DataFrame(tr)
        table["censored"] = table.censored.astype(bool)
        closed = table.loc[~table.censored]
        np.testing.assert_allclose(closed.gross_return - closed.net_return, .002, atol=1e-12)
        tables = {"trades": table, "controls": controls, "statuses": pd.DataFrame(st),
                  "candidates": pd.DataFrame([e for e in (events if arm == "baseline" else keep) if e["in_window"]])}
        if arm == "no_weekend":
            tables["trades"]["arm"] = "ordinary"
            tables["controls"]["arm"] = "ordinary"
            tables["statuses"]["arm"] = "ordinary"
        out[arm] = {"tables": tables, "summary": five.summarize(tables, scfg)}
    return out


def row_of(summary: dict, window: str, closed: pd.DataFrame) -> dict:
    m = summary["summary"][window]["ordinary_all"]
    ex = m.get("random_excess") or {}
    return {"closed": m["closed"], "win_rate": m.get("win_rate"), "mean_net_r": m.get("mean_net_r"), "sum_net_r": m.get("sum_net_r"),
            "sum_without_top5": m.get("sum_net_r_without_top5"), "pf": m.get("pf_r"),
            "max_drawdown_r": m.get("event_ordered_drawdown_r_nonaccount"), "max_losses_in_row": m.get("max_consecutive_losses"),
            "max_stops_in_row": stop_streak(closed), "excess_bp": ex.get("mean_excess_bp"), "excess_p": ex.get("p_one_sided"),
            "long_sum_r": summary["summary"][window]["ordinary_long"].get("sum_net_r"),
            "short_sum_r": summary["summary"][window]["ordinary_short"].get("sum_net_r")}


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
        for arm, res in arms.items():
            trades = rep.enrich(res["tables"]["trades"], pd.Timestamp(cfg[f"{venue}_split"]))
            closed = trades.loc[~trades.censored]
            split = pd.Timestamp(cfg[f"{venue}_split"])
            for window, label, mask in (("full", "full", closed.signal_close >= pd.Timestamp(cfg[f"{venue}_start"])),
                                        ("earlier", "before_split", closed.period.eq("earlier")),
                                        ("last_12m", "after_split", closed.signal_close >= split)):
                comparison.append({"venue": venue, "arm": arm, "window": label, **row_of(res["summary"], window, closed.loc[mask])})
    out = EXP / "run_v1"
    (out / "okx_no_weekend").mkdir(parents=True, exist_ok=True)
    nw = runs["okx"]["no_weekend"]
    for name, frame in nw["tables"].items():
        frame.to_csv(out / "okx_no_weekend" / f"{name}.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    nw["summary"]["yearly"].to_csv(out / "okx_no_weekend" / "yearly.csv", index=False)
    nw["summary"]["monthly"].to_csv(out / "okx_no_weekend" / "monthly.csv", index=False)
    p.dump(out / "okx_no_weekend" / "receipt.json", five._clean({"summary": nw["summary"]["summary"], "parity": parity}))
    for venue in runs:
        runs[venue]["no_weekend"]["tables"]["trades"].to_csv(out / f"{venue}_no_weekend_trades.csv.gz", index=False,
                                                              compression={"method": "gzip", "mtime": 0})
    table = pd.DataFrame(comparison)
    table.to_csv(out / "comparison.csv", index=False)
    p.dump(out / "receipt.json", five._clean({"config_sha256": p.digest(CONFIG), "code": code, "parity": parity,
                                               "comparison": comparison,
                                               "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()}))
    print(table.round(3).to_string(index=False))


if __name__ == "__main__":
    main()
