"""Dashboard payload for the ETH micro channel."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from src.eth_micro.config import BACKTEST_JSON, SIGNAL_LOG, STATUS_JSON, SYMBOL


def eth_micro_payload() -> dict:
    backtest = {}
    if BACKTEST_JSON.exists():
        backtest = json.loads(BACKTEST_JSON.read_text(encoding="utf-8"))
    status = {}
    if STATUS_JSON.exists():
        status = json.loads(STATUS_JSON.read_text(encoding="utf-8"))
    signals: list[dict] = []
    if SIGNAL_LOG.exists():
        df = pd.read_csv(SIGNAL_LOG)
        if not df.empty:
            df = df.sort_values("signal_time", ascending=False).head(50)
            signals = df.to_dict(orient="records")
    results = backtest.get("results") or []
    table = []
    for r in results:
        if r.get("status") != "ok":
            table.append(
                {
                    "bar": r.get("bar"),
                    "status": r.get("status"),
                    "n_candidates": r.get("n_candidates", 0),
                    "n_val": r.get("n_val"),
                    "val_auc": None,
                    "top_net_0p2": None,
                    "accept_n": None,
                    "accept_pf": None,
                    "accept_net_cap": None,
                    "full_n": None,
                    "full_pf": None,
                }
            )
            continue
        a = (r.get("portfolio") or {}).get("accept", {}).get("0.003") or {}
        f = (r.get("portfolio") or {}).get("full") or {}
        table.append(
            {
                "bar": r["bar"],
                "status": "ok",
                "n_candidates": r.get("n_candidates"),
                "n_val": r.get("n_val"),
                "val_auc": r.get("val_auc"),
                "perm_p": r.get("perm_p"),
                "top_net_0p2": r.get("top_net_0p2"),
                "threshold": r.get("threshold_val_q90"),
                "accept_n": a.get("n_trades"),
                "accept_pf": a.get("profit_factor"),
                "accept_net_cap": a.get("net_return_on_capital"),
                "accept_win_rate": a.get("win_rate"),
                "full_n": f.get("n_trades"),
                "full_pf": f.get("profit_factor"),
                "full_net_cap": f.get("net_return_on_capital"),
            }
        )
    return {
        "symbol": SYMBOL,
        "channel": "eth_micro",
        "note": "ETH-only 1/2/3/5m 通道；与 15m YOLO 主线隔离。发现级 val-only 回测 + 实时规则扫描。",
        "generated_at": backtest.get("generated_at"),
        "best_bar_by_top_net": backtest.get("best_bar_by_top_net"),
        "coverage": backtest.get("coverage") or [],
        "backtest_table": table,
        "monitor": status,
        "recent_signals": signals,
        "paths": {
            "backtest": str(BACKTEST_JSON.as_posix()) if BACKTEST_JSON.exists() else None,
            "signal_log": str(SIGNAL_LOG.as_posix()) if SIGNAL_LOG.exists() else None,
        },
    }
