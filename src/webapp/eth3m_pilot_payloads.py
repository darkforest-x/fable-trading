"""Dashboard payload for the ETH 3m short-start pilot (last two days of work).

Read-only over analysis artifacts. Does not touch forward_log, ACTIVE, or
executor. Replaces the generic short_tf (1m/5m multi-symbol) view content on
the #shorttf page.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.webapp.dashboard_cache import OUTPUT_DIR, relative_path

PROJECT = Path(__file__).resolve().parents[2]
V1_BT = OUTPUT_DIR / "eth3m_short_pilot_v1_backtest"
V1_SUMMARY = V1_BT / "summary.json"
V1_REPORT = PROJECT / "analysis" / "p_eth_3m_short_pilot_v1.md"
V1_BT_REPORT = PROJECT / "analysis" / "p_eth_3m_short_pilot_v1_backtest.md"
V1_HTML = V1_BT / "eth3m_short_pilot_v1_backtest_mobile.html"

V2_DIR = OUTPUT_DIR / "eth3m_short_pilot_v2_cls_diag_20260730"
V2_SUMMARY = V2_DIR / "summary.json"
V2_REPORT = PROJECT / "analysis" / "p_eth3m_short_pilot_v2_cls_diag_20260730.md"
V2_DATASET = PROJECT / "analysis" / "p_eth_3m_short_pilot_v2_dataset.md"


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _pct(x: float | None, digits: int = 2) -> str | None:
    if x is None:
        return None
    try:
        return f"{100 * float(x):+.{digits}f}%"
    except (TypeError, ValueError):
        return None


def eth3m_pilot_payload() -> dict[str, Any]:
    """Build the #shorttf page body for ETH 3m pilot work."""
    v1 = _load_json(V1_SUMMARY)
    v2 = _load_json(V2_SUMMARY)
    oos = (v1.get("replay") or {}).get("strict_oos") or {}
    gap = (v1.get("replay") or {}).get("gap_replay") or {}
    v2_val = ((v2.get("metrics") or {}).get("val") or {})
    v2_train = ((v2.get("metrics") or {}).get("train") or {})
    gates = v2.get("gates") or {}
    baseline = v2.get("baseline_first_below_all") or {}

    v1_verdict = "FAIL"
    if oos:
        # continuous fire rate ~1.0 is the kill criterion from the report
        fire_rate = float(oos.get("raw_fire_rate") or 0)
        if fire_rate >= 0.5:
            v1_verdict = "FAIL · 连续开火（非稀疏事件）"
        elif float(oos.get("net_profit_factor_at_20bp") or 0) >= 1.3:
            v1_verdict = "PASS?"
        else:
            v1_verdict = "FAIL · 经济性未过"

    v2_verdict = "FAIL · val 静态门" if not gates.get("passed", False) else "PASS"
    if v2.get("status") == "failed_gates":
        v2_verdict = "FAIL · val 静态第一门（TP=0）"

    return {
        "channel": "eth3m_short_pilot",
        "title": "ETH 3m 做空 pilot",
        "subtitle": "两日工作台 · 与 15m 主线隔离 · 不写 forward_log · 未 promote",
        "note": (
            "本页展示 2026-07-29/30 ETH 3 分钟 short-start pilot 结论。"
            "v1 检测器在严格 OOS 上几乎每 bar 开火；v2 tip 分类在 val 上全判 no_start。"
            "两轮均未进 smoke/promote/ACTIVE。"
        ),
        "v1": {
            "available": bool(v1),
            "name": "v1 检测 pilot（YOLO detect cold）",
            "verdict": v1_verdict if v1 else "无产物",
            "generated_at": v1.get("generated_at"),
            "model": v1.get("model"),
            "data": v1.get("data"),
            "protocol": v1.get("protocol") or {},
            "holdout_touched": v1.get("holdout_touched"),
            "training_images": v1.get("training_images"),
            "strict_oos": {
                "window": f"{oos.get('start', '—')} → {oos.get('end', '—')}",
                "eligible_bars": oos.get("eligible_bars"),
                "raw_fires": oos.get("raw_fires"),
                "raw_fire_rate": oos.get("raw_fire_rate"),
                "raw_fire_rate_pct": _pct(oos.get("raw_fire_rate"), 1) if oos.get("raw_fire_rate") is not None else None,
                "dedup_signals": oos.get("dedup_signals"),
                "net_mean_20bp": oos.get("net_mean_at_20bp"),
                "net_mean_20bp_pct": _pct(oos.get("net_mean_at_20bp"), 2),
                "net_win_rate": oos.get("net_win_rate_at_20bp"),
                "net_win_rate_pct": _pct(oos.get("net_win_rate_at_20bp"), 1),
                "net_pf": oos.get("net_profit_factor_at_20bp"),
                "paired_excess_mean": oos.get("paired_excess_mean"),
                "paired_excess_t": oos.get("paired_excess_t"),
                "block_signflip_p": oos.get("block_signflip_p_one_sided"),
            },
            "gap_replay": {
                "dedup_signals": gap.get("dedup_signals"),
                "raw_fire_rate": gap.get("raw_fire_rate"),
                "net_mean_20bp_pct": _pct(gap.get("net_mean_at_20bp"), 2),
                "net_pf": gap.get("net_profit_factor_at_20bp"),
            },
            "links": {
                "summary.json": (
                    "/debug-artifacts/eth3m_short_pilot_v1_backtest/summary.json"
                    if V1_SUMMARY.is_file()
                    else None
                ),
                "回测HTML": (
                    f"/debug-artifacts/eth3m_short_pilot_v1_backtest/{V1_HTML.name}"
                    if V1_HTML.is_file()
                    else None
                ),
                # md under analysis/ is not HTTP-mounted; path for owner copy only
                "report_md_path": relative_path(V1_REPORT) if V1_REPORT.is_file() else None,
                "backtest_md_path": relative_path(V1_BT_REPORT) if V1_BT_REPORT.is_file() else None,
            },
        },
        "v2": {
            "available": bool(v2),
            "name": "v2 tip 图像分类诊断（yolo11n-cls）",
            "verdict": v2_verdict if v2 else "无产物",
            "status": v2.get("status"),
            "experiment_id": v2.get("experiment_id"),
            "threshold_policy": v2.get("threshold_policy"),
            "weights_sha256": (v2.get("weights_sha256") or "")[:16] + "…" if v2.get("weights_sha256") else None,
            "training": v2.get("training") or {},
            "val": {
                "n": v2_val.get("n"),
                "tp": v2_val.get("tp"),
                "fp": v2_val.get("fp"),
                "tn": v2_val.get("tn"),
                "fn": v2_val.get("fn"),
                "precision": v2_val.get("precision"),
                "recall": v2_val.get("recall"),
                "balanced_accuracy": v2_val.get("balanced_accuracy"),
                "accuracy": v2_val.get("accuracy"),
                "roc_auc": v2_val.get("roc_auc"),
            },
            "train": {
                "n": v2_train.get("n"),
                "tp": v2_train.get("tp"),
                "fp": v2_train.get("fp"),
                "tn": v2_train.get("tn"),
                "fn": v2_train.get("fn"),
                "balanced_accuracy": v2_train.get("balanced_accuracy"),
            },
            "gates": {
                "passed": gates.get("passed"),
                "tp_min": gates.get("tp_min"),
                "fp_max": gates.get("fp_max"),
                "actual_tp": gates.get("actual_tp"),
                "actual_fp": gates.get("actual_fp"),
                "policy": gates.get("policy"),
            },
            "baseline_first_below": {
                "tp": baseline.get("tp"),
                "fp": baseline.get("fp"),
                "tn": baseline.get("tn"),
                "fn": baseline.get("fn"),
                "name": baseline.get("name"),
            },
            "links": {
                "summary.json": (
                    "/debug-artifacts/eth3m_short_pilot_v2_cls_diag_20260730/summary.json"
                    if V2_SUMMARY.is_file()
                    else None
                ),
                "evidence.md": (
                    "/debug-artifacts/eth3m_short_pilot_v2_cls_diag_20260730/evidence.md"
                    if (V2_DIR / "evidence.md").is_file()
                    else None
                ),
                "report_md_path": relative_path(V2_REPORT) if V2_REPORT.is_file() else None,
                "dataset_md_path": relative_path(V2_DATASET) if V2_DATASET.is_file() else None,
            },
        },
        "discipline": [
            "不写 forward_log / 不接 executor / 不 promote / 不切 ACTIVE",
            "holdout（≥2026-05-04）未进入训练或本轮裁决",
            "v1/v2 失败后 fail-fast：无 smoke、无人工复核续训门槛下调",
            "主线 15m 管道与本页无关",
        ],
    }
