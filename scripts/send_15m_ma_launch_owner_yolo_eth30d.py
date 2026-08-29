#!/usr/bin/env python3
"""Send every verified ETHUSDT.P 30-day episode chart to Telegram as a document."""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from scripts.scan_15m_ma_launch_owner_yolo_eth30d import (
    DEFAULT_RESULTS,
    read_json,
    resolve_repo_path,
    sha256_file,
    utc,
)
from yoyo import notify


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "analysis" / "html" / "p1_15m_ma_launch_owner_yolo_eth30d_20260828.html"
DEFAULT_RECEIPT = DEFAULT_RESULTS / "telegram_delivery_receipt.json"


class Eth30dDeliveryError(RuntimeError):
    """Fail closed when verified artifacts or Telegram delivery drift."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_receipt(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_contract(
    results: Path = DEFAULT_RESULTS,
    report: Path = DEFAULT_REPORT,
) -> tuple[dict[str, Any], list[dict[str, str]], str]:
    scan = read_json(results / "scan_receipt.json")
    qa = read_json(results / "qa_receipt.json")
    experiment_id = str(scan.get("experiment_id", ""))
    if not experiment_id or qa.get("experiment_id") != experiment_id:
        raise Eth30dDeliveryError("receipt experiment identity drifted")
    if qa.get("passed") is not True:
        raise Eth30dDeliveryError("independent QA is absent or failed")
    events = int(scan["overlap_episodes"])
    for key in (
        "events",
        "documents_with_exactly_one_box",
        "exact_pixel_rerenders",
        "exact_png_hash_matches",
        "exact_model_input_pixel_matches",
        "unique_model_input_hashes",
        "unique_chart_hashes",
    ):
        if int(qa.get(key, -1)) != events:
            raise Eth30dDeliveryError(f"QA coverage drifted: {key}")
    if int(qa.get("shifted_event_input_hash_matches", -1)) != 0:
        raise Eth30dDeliveryError("shifted event/input null did not remain zero")
    holdout_number = int(scan.get("holdout_consumption_number_for_this_configuration", -1))
    if holdout_number < 1 or int(
        qa.get("holdout_consumption_number_for_this_configuration", -1)
    ) != holdout_number:
        raise Eth30dDeliveryError("holdout consumption identity drifted")
    for key in (
        "training_or_tuning",
        "threshold_or_weight_changed",
        "active_or_frozen_changed",
        "promoted",
        "deployed",
        "orders_placed",
        "training_eligible",
        "production_eligible",
    ):
        if scan.get(key) is not False:
            raise Eth30dDeliveryError(f"unsafe scan flag: {key}")

    manifest_path = results / "manifest.jsonl"
    if sha256_file(manifest_path) != str(scan["manifest_sha256"]):
        raise Eth30dDeliveryError("manifest hash drifted")
    manifest = [
        json.loads(line)
        for line in manifest_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    if len(manifest) != events:
        raise Eth30dDeliveryError("manifest event count drifted")

    documents: list[dict[str, str]] = []
    overview = scan["overview"]
    documents.append(
        {
            "id": "overview",
            "path": str(resolve_repo_path(overview["path"])),
            "sha256": str(overview["sha256"]),
            "caption": (
                f"ETHUSDT.P 15m｜近 30 个完整 UTC 日｜总览\n"
                f"模型：{scan.get('detector_display_name', 'Owner YOLO')}\n"
                f"原始框 {int(scan['scan_totals'].get('raw_boxes', 0)):,}｜"
                f"结构合格 {int(scan['accepted_candidates']):,}｜"
                f"合并后连续 episode {events}"
            ),
        }
    )
    for row in manifest:
        order = int(row["event_order"])
        if order != len(documents):
            raise Eth30dDeliveryError("manifest order drifted")
        path = resolve_repo_path(row["image_path"])
        direction = "多头" if int(row["class_id"]) == 0 else "空头"
        detect_utc = utc(row["window_end_time"])
        detect_cst = detect_utc.tz_convert("Asia/Shanghai")
        documents.append(
            {
                "id": f"signal_{order:03d}",
                "path": str(path),
                "sha256": str(row["image_sha256"]),
                "caption": (
                    f"ETHUSDT.P 近一月 {order:03d}/{events:03d}｜{direction}｜conf {float(row['confidence']):.3f}\n"
                    f"检测完成：{detect_utc:%m-%d %H:%M} UTC / {detect_cst:%m-%d %H:%M} 北京时间\n"
                    f"W{int(row['window_len'])}｜核心 {int(row['core_length_bars'])} 根｜"
                    f"确认 {int(row['confirmation_bars'])} 根｜episode 内候选 {int(row['episode_candidate_count'])}\n"
                    "上方 128 根整体行情；右下是模型实际输入与同一个原始框。"
                ),
            }
        )
    archive = scan["archive"]
    documents.append(
        {
            "id": "all_charts_zip",
            "path": str(resolve_repo_path(archive["path"])),
            "sha256": str(archive["sha256"]),
            "caption": f"ETHUSDT.P 近一月全部 {events} 张高清信号图 + 原始候选/episode CSV（无损 ZIP）",
        }
    )
    documents.append(
        {
            "id": "html_report",
            "path": str(report.resolve()),
            "sha256": sha256_file(report.resolve()),
            "caption": f"ETHUSDT.P 近一月｜{scan.get('detector_display_name', 'Owner YOLO')}｜完整 HTML 报告",
        }
    )
    for item in documents:
        path = Path(item["path"])
        if not path.is_file() or path.stat().st_size == 0:
            raise Eth30dDeliveryError(f"artifact missing: {path}")
        if sha256_file(path) != item["sha256"]:
            raise Eth30dDeliveryError(f"artifact hash drifted: {path}")
    contract_sha = hashlib.sha256(
        json.dumps(
            [(item["id"], item["sha256"]) for item in documents],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return scan, documents, contract_sha


def deliver(
    *,
    results: Path = DEFAULT_RESULTS,
    report: Path = DEFAULT_REPORT,
    receipt_path: Path = DEFAULT_RECEIPT,
    sleep_seconds: float = 1.1,
    send_text: Callable[[str], bool] = notify.send,
    send_document: Callable[[Path, str], bool] = notify.send_document,
) -> dict[str, Any]:
    scan, documents, contract_sha = build_contract(results, report)
    events = int(scan["overlap_episodes"])
    if receipt_path.exists():
        receipt = read_json(receipt_path)
        if receipt.get("contract_sha256") != contract_sha:
            raise Eth30dDeliveryError("existing Telegram receipt belongs to another contract")
        if receipt.get("delivery_complete") is True:
            raise Eth30dDeliveryError("delivery already complete; refusing duplicate resend")
    else:
        receipt = {
            "experiment_id": str(scan["experiment_id"]),
            "started_at_utc": utc_now(),
            "contract_sha256": contract_sha,
            "expected_documents": len(documents),
            "expected_signal_charts": events,
            "intro_sent": False,
            "document_actions": [],
            "finish_sent": False,
            "delivery_complete": False,
            "holdout_consumption_number_for_this_configuration": int(
                scan["holdout_consumption_number_for_this_configuration"]
            ),
            "transport": "telegram_document_no_recompression",
            "manual_owner_review_required": False,
            "training_or_tuning": False,
            "production_eligible": False,
        }
    if receipt["intro_sent"] is not True:
        intro = (
            f"<b>ETHUSDT.P 15m｜近一个月 {scan.get('detector_display_name', 'Owner YOLO')} 扫描</b>\n"
            f"范围：2026-07-29 至 08-27，共 30 个完整 UTC 日。"
            f"重叠滑窗已按连续行情合并为 {events} 个 episode。\n"
            "每张只有一个原始 YOLO 框；上方看 128 根整体行情，右下看模型实际输入。"
            "全部按文件发送，不经过 Telegram 图片压缩。"
        )
        if not send_text(intro):
            raise Eth30dDeliveryError("Telegram intro failed")
        receipt["intro_sent"] = True
        receipt["intro_sent_at_utc"] = utc_now()
        write_receipt(receipt_path, receipt)

    sent = {str(action["id"]) for action in receipt["document_actions"]}
    for item in documents:
        if item["id"] in sent:
            continue
        path = Path(item["path"])
        if not send_document(path, item["caption"]):
            receipt["last_error"] = f"document failed: {item['id']}"
            write_receipt(receipt_path, receipt)
            raise Eth30dDeliveryError(receipt["last_error"])
        receipt["document_actions"].append(
            {
                "id": item["id"],
                "path": item["path"],
                "sha256": item["sha256"],
                "sent_at_utc": utc_now(),
            }
        )
        receipt.pop("last_error", None)
        write_receipt(receipt_path, receipt)
        print(
            f"Telegram [{len(receipt['document_actions']):03d}/{len(documents):03d}] {path.name}",
            flush=True,
        )
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    if len(receipt["document_actions"]) != len(documents):
        raise Eth30dDeliveryError("Telegram document ledger is incomplete")
    if receipt["finish_sent"] is not True:
        if not send_text(
            f"<b>ETHUSDT.P 近一月扫描已发完。</b>\n"
            f"模型：{scan.get('detector_display_name', 'Owner YOLO')}；"
            f"共 {events} 张逐 episode 高清图，另含总览、无损 ZIP 和 HTML。"
        ):
            raise Eth30dDeliveryError("Telegram completion message failed")
        receipt["finish_sent"] = True
        receipt["finish_sent_at_utc"] = utc_now()
    receipt["delivery_complete"] = True
    receipt["completed_at_utc"] = utc_now()
    write_receipt(receipt_path, receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    parser.add_argument("--sleep-seconds", type=float, default=1.1)
    args = parser.parse_args()
    payload = deliver(
        results=args.results.resolve(),
        report=args.report.resolve(),
        receipt_path=args.receipt.resolve(),
        sleep_seconds=args.sleep_seconds,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
