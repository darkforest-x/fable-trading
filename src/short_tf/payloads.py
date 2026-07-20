"""Dashboard payload for short_tf (1m/5m tip rules)."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from src.short_tf.config import LATEST_JSON, SIGNAL_LOG, STATUS_JSON, SYMBOLS, BARS


def short_tf_payload() -> dict:
    status = {}
    if STATUS_JSON.exists():
        try:
            status = json.loads(STATUS_JSON.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            status = {}
    latest = {}
    if LATEST_JSON.exists():
        try:
            latest = json.loads(LATEST_JSON.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            latest = {}
    signals: list[dict] = []
    n_total = 0
    if SIGNAL_LOG.exists():
        df = pd.read_csv(SIGNAL_LOG)
        n_total = len(df)
        if not df.empty:
            df = df.sort_values("signal_time", ascending=False).head(80)
            signals = df.to_dict(orient="records")
    return {
        "channel": "short_tf",
        "note": (
            "短周期支线：主流币 1m/5m · 规则 expanded 密集 · 只取 tip 近端 bar · "
            "独立日志 data/short_tf/ · 不写 forward_log · 默认不接 executor。"
            "解决 15m YOLO「中图打标 vs 右缘 tip」结构延迟的旁路探索。"
        ),
        "symbols": list(SYMBOLS),
        "bars": list(BARS),
        "status": status,
        "latest": latest,
        "n_log_total": n_total,
        "recent_signals": signals,
        "paths": {
            "signal_log": str(SIGNAL_LOG) if SIGNAL_LOG.exists() else None,
            "status": str(STATUS_JSON) if STATUS_JSON.exists() else None,
        },
    }
