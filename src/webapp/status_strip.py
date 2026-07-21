"""Lightweight status strip for the dashboard header / overview.

Surfaces owner-detector promotion, judgment ACTIVE (threshold + dataset),
and forward decision progress. Safe when artifacts missing.

Visual scout (scout.html) was retired — multi-TF radar is dashboard #radar.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from src.webapp.dashboard_cache import relative_path
from src.webapp.forward_payloads import FRESH_DETECT_MIN
from src.webapp.model_hub import read_active_pointer

PROJECT = Path(__file__).resolve().parents[2]
OWNER_BEST_JSON = PROJECT / "models" / "owner_best.json"
OWNER_BEST_PT = PROJECT / "models" / "owner_best.pt"
FORWARD_LOG_PATH = PROJECT / "data" / "forward_log.csv"
FORWARD_DECISION_TRADES = 100


def status_strip_payload() -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "owner_detector": _owner_detector(),
        "judgment_active": _judgment_active(),
        "forward": _forward_progress(),
        "links": {
            "scout_mtf": "/#radar",
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


def _judgment_active() -> dict:
    """models/ACTIVE pointer + frozen JSON meta (threshold / dataset)."""
    ptr = read_active_pointer()
    out: dict = {
        "exists": bool(ptr.get("exists") and ptr.get("artifact_id")),
        "artifact_id": ptr.get("artifact_id"),
        "pointer_path": ptr.get("path"),
        "threshold_val_q90": None,
        "dataset_path": None,
        "dataset_name": None,
        "objective": None,
        "config": None,
        "created_at": None,
        "note": None,
    }
    if not out["exists"]:
        out["note"] = "models/ACTIVE 未设置"
        return out
    aid = str(ptr.get("artifact_id") or "")
    meta_path = PROJECT / "models" / f"{aid}.json"
    if not meta_path.is_file():
        # pointer may be .txt path; try sibling .json
        raw = str(ptr.get("raw") or "")
        cand = PROJECT / raw
        if cand.suffix == ".txt":
            meta_path = cand.with_suffix(".json")
        elif cand.suffix == ".json":
            meta_path = cand
    if not meta_path.is_file():
        out["note"] = f"找不到 {aid}.json"
        return out
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        out["note"] = f"读取失败: {exc}"
        return out
    ds = meta.get("dataset_path")
    out["threshold_val_q90"] = _num(meta.get("threshold_val_q90"))
    out["dataset_path"] = ds
    out["dataset_name"] = Path(str(ds)).name if ds else None
    out["objective"] = meta.get("objective")
    out["config"] = meta.get("config")
    out["created_at"] = meta.get("created_at")
    out["meta_path"] = relative_path(meta_path)
    return out


def _forward_progress() -> dict:
    """Decision counter must match /api/forward: closed + maker_filled + lag≤30m.

    See docs/learnings/status-strip-decision-counter-skips-freshness-gate.md —
    counting maker-filled closed without the freshness gate made the strip
    show 15/100 while the forward tab correctly showed 0/100.
    """
    out = {
        "exists": FORWARD_LOG_PATH.exists(),
        "path": relative_path(FORWARD_LOG_PATH),
        "decision_trades": 0,
        "decision_target": FORWARD_DECISION_TRADES,
        "progress": 0.0,
        "decision_remaining": FORWARD_DECISION_TRADES,
        "closed_rows": 0,
        "total_rows": 0,
        "hindsight_excluded": 0,
        "fresh_detect_min": FRESH_DETECT_MIN,
        "open_rows": 0,
    }
    if not FORWARD_LOG_PATH.exists():
        out["stall_reason"] = "forward_log 不存在"
        return out
    try:
        import pandas as pd

        frame = pd.read_csv(FORWARD_LOG_PATH)
    except Exception:  # noqa: BLE001 — status strip must never crash the page
        out["stall_reason"] = "forward_log 读取失败"
        return out
    if frame.empty:
        out["stall_reason"] = "forward_log 为空：前向扫描未跑或日志被清空"
        return out
    out["total_rows"] = int(len(frame))
    closed = frame
    open_rows = frame
    if "status" in frame.columns:
        closed = frame[frame["status"] == "closed"]
        open_rows = frame[frame["status"] == "open"]
    out["closed_rows"] = int(len(closed))
    out["open_rows"] = int(len(open_rows))
    decision = closed
    if "maker_filled" in closed.columns:
        decision = closed[closed["maker_filled"].fillna(False).astype(bool)]
    hindsight_n = 0
    # Same freshness gate as forward_payloads (tip-tradable only).
    if "detected_at" in decision.columns and "signal_time" in decision.columns and not decision.empty:
        det = pd.to_datetime(decision["detected_at"], errors="coerce", utc=True)
        sig = pd.to_datetime(decision["signal_time"], errors="coerce", utc=True)
        lag_min = (det - sig).dt.total_seconds() / 60.0
        hindsight_n = int(((lag_min > FRESH_DETECT_MIN) | lag_min.isna()).sum())
        decision = decision[lag_min <= FRESH_DETECT_MIN]
    out["hindsight_excluded"] = hindsight_n
    n = int(len(decision))
    out["decision_trades"] = n
    out["decision_remaining"] = max(FORWARD_DECISION_TRADES - n, 0)
    out["progress"] = round(min(n / FORWARD_DECISION_TRADES, 1.0), 4)
    if n == 0 and out["total_rows"] == 0:
        out["stall_reason"] = "forward_log 为空：前向扫描未跑或日志被清空"
    elif n == 0 and hindsight_n > 0:
        out["stall_reason"] = (
            f"{hindsight_n} 笔 closed 但延迟>{int(FRESH_DETECT_MIN)}min（事后），不进裁决"
        )
    elif n == 0 and out["open_rows"] > 0:
        out["stall_reason"] = (
            f"有 {out['open_rows']} 笔 open，等待关闭（闸门只计新鲜 maker closed）"
        )
    elif n == 0 and out["closed_rows"] > 0:
        out["stall_reason"] = "有 closed 行但未过 maker+新鲜度门"
    return out


def _num(x):
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None
