"""Lightweight status strip for the dashboard header / overview.

Surfaces owner-detector promotion, forward decision progress, and scout
freshness without pulling full ops hubs. Safe when artifacts are missing.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from src.webapp.dashboard_cache import relative_path

PROJECT = Path(__file__).resolve().parents[2]
OWNER_BEST_JSON = PROJECT / "models" / "owner_best.json"
OWNER_BEST_PT = PROJECT / "models" / "owner_best.pt"
FORWARD_LOG_PATH = PROJECT / "data" / "forward_log.csv"
FORWARD_DECISION_TRADES = 100
SCOUT_HTML = PROJECT / "src" / "webapp" / "static" / "scout.html"
SCOUT_DIR = PROJECT / "src" / "webapp" / "static" / "scout"


def status_strip_payload() -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "owner_detector": _owner_detector(),
        "forward": _forward_progress(),
        "scout": _scout_status(),
        "links": {
            "scout": "/scout.html",
            "label_studio_hint": "http://127.0.0.1:8081",
        },
    }


def _owner_detector() -> dict:
    out: dict = {
        "exists": OWNER_BEST_JSON.exists() or OWNER_BEST_PT.exists(),
        "json_path": relative_path(OWNER_BEST_JSON) if OWNER_BEST_JSON.exists() else None,
        "weights_path": relative_path(OWNER_BEST_PT) if OWNER_BEST_PT.exists() else None,
        "source_run": None,
        "frozen_eval_f1": None,
        "precision": None,
        "recall": None,
        "eval_set": None,
        "note": None,
    }
    if not OWNER_BEST_JSON.exists():
        out["note"] = "models/owner_best.json 不存在"
        return out
    try:
        data = json.loads(OWNER_BEST_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        out["note"] = f"读取失败: {exc}"
        return out
    metrics = data.get("metrics") or {}
    out["source_run"] = data.get("source_run")
    out["frozen_eval_f1"] = _num(data.get("frozen_eval_f1") or metrics.get("f1"))
    out["precision"] = _num(metrics.get("p"))
    out["recall"] = _num(metrics.get("r"))
    out["eval_set"] = data.get("eval_set")
    out["mtime"] = datetime.fromtimestamp(
        OWNER_BEST_JSON.stat().st_mtime, tz=timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    return out


def _forward_progress() -> dict:
    out = {
        "exists": FORWARD_LOG_PATH.exists(),
        "path": relative_path(FORWARD_LOG_PATH),
        "decision_trades": 0,
        "decision_target": FORWARD_DECISION_TRADES,
        "progress": 0.0,
        "decision_remaining": FORWARD_DECISION_TRADES,
        "closed_rows": 0,
        "total_rows": 0,
    }
    if not FORWARD_LOG_PATH.exists():
        return out
    try:
        import pandas as pd

        frame = pd.read_csv(FORWARD_LOG_PATH)
    except Exception:  # noqa: BLE001 — status strip must never crash the page
        return out
    if frame.empty:
        return out
    out["total_rows"] = int(len(frame))
    closed = frame
    if "status" in frame.columns:
        closed = frame[frame["status"] == "closed"]
    out["closed_rows"] = int(len(closed))
    decision = closed
    if "maker_filled" in closed.columns:
        decision = closed[closed["maker_filled"].fillna(False).astype(bool)]
    n = int(len(decision))
    out["decision_trades"] = n
    out["decision_remaining"] = max(FORWARD_DECISION_TRADES - n, 0)
    out["progress"] = round(min(n / FORWARD_DECISION_TRADES, 1.0), 4)
    return out


def _scout_status() -> dict:
    pngs = sorted(SCOUT_DIR.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True) if SCOUT_DIR.exists() else []
    latest = pngs[0] if pngs else None
    return {
        "exists": SCOUT_HTML.exists(),
        "n_frames": len(pngs),
        "latest_symbol": latest.stem if latest else None,
        "latest_mtime": datetime.fromtimestamp(latest.stat().st_mtime, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        if latest
        else None,
        "href": "/scout.html",
    }


def _num(x):
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None
