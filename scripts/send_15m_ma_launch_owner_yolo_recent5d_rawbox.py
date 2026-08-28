#!/usr/bin/env python3
"""Deliver the corrected five-day raw YOLO box review artifacts to Telegram.

The sender validates the scan and independent QA receipts before sending all
PNGs as documents, so Telegram does not recompress them.  It is resumable and
refuses to mix artifacts from another run.  Credentials remain inside
``yoyo.notify`` and are never read or printed here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from yoyo import notify


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-15m-ma-launch-owner-yolo-recent5d-rawbox-v2"
RESULTS = ROOT / "experiments" / "active" / EXPERIMENT_ID / "results"
REPORT = ROOT / "analysis" / "html" / "p1_15m_ma_launch_owner_yolo_recent5d_rawbox_repair_20260828.html"
RECEIPT = RESULTS / "telegram_delivery_receipt.json"


class DeliveryError(RuntimeError):
    """Fail-closed Telegram artifact delivery error."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def artifact_contract() -> tuple[dict[str, Any], dict[str, Any], list[dict[str, str]]]:
    scan_path = RESULTS / "scan_receipt.json"
    qa_path = RESULTS / "qa_receipt.json"
    scan, qa = read_json(scan_path), read_json(qa_path)
    if scan.get("experiment_id") != EXPERIMENT_ID or qa.get("experiment_id") != EXPERIMENT_ID:
        raise DeliveryError("receipt experiment identity drifted")
    if qa.get("passed") is not True:
        raise DeliveryError("independent QA did not pass")
    if int(scan.get("maximum_boxes_per_review_panel", -1)) != 1:
        raise DeliveryError("review panel box limit drifted")
    if int(qa.get("exact_model_input_rerenders", -1)) != 100:
        raise DeliveryError("model-input parity is incomplete")
    if int(qa.get("exact_raw_box_overlay_reproductions", -1)) != 100:
        raise DeliveryError("raw-box overlay parity is incomplete")
    if int(scan.get("holdout_consumption_number_for_this_configuration", -1)) != 2:
        raise DeliveryError("holdout-use identity drifted")
    for key in ("training_or_tuning", "active_or_frozen_changed", "promoted", "deployed", "orders_placed"):
        if scan.get(key) is not False:
            raise DeliveryError(f"unsafe scan flag: {key}")

    artifacts: list[dict[str, str]] = []
    overview = scan["overview"]
    artifacts.append(
        {
            "id": "overview",
            "path": str(ROOT / overview["path"]),
            "sha256": str(overview["sha256"]),
            "caption": "修正版总览｜最近 5 个完整 UTC 日 Top20｜每个币日最多 1 个原始 YOLO 框",
        }
    )
    for item in scan["daily_images"]:
        day = str(item["day"])[:10]
        artifacts.append(
            {
                "id": f"day_{day}",
                "path": str(ROOT / item["path"]),
                "sha256": str(item["sha256"]),
                "caption": (
                    f"{day} Top20 修正版｜{item['review_boxes']}/20 有框｜"
                    "每面板为实际 1280×742 W18–25 模型输入；红/绿框=原始 YOLO xywh"
                ),
            }
        )
    archive = scan["review_archive"]
    artifacts.append(
        {
            "id": "actual_inputs_and_overlays_zip",
            "path": str(ROOT / archive["path"]),
            "sha256": str(archive["sha256"]),
            "caption": "100 张模型实际输入 + 100 张原始框 overlay + manifest（无损 ZIP）",
        }
    )
    artifacts.append(
        {
            "id": "html_report",
            "path": str(REPORT),
            "sha256": sha256_file(REPORT),
            "caption": "最近五日 Top20 原始 YOLO 框修正版｜完整自包含 HTML 报告",
        }
    )
    for item in artifacts:
        path = Path(item["path"])
        if not path.is_file() or path.stat().st_size == 0:
            raise DeliveryError(f"artifact missing: {path}")
        if sha256_file(path) != item["sha256"]:
            raise DeliveryError(f"artifact hash drifted: {path}")
    return scan, qa, artifacts


def write_receipt(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def deliver(
    *,
    receipt_path: Path = RECEIPT,
    sleep_seconds: float = 1.1,
    send_text: Callable[[str], bool] = notify.send,
    send_document: Callable[[Path, str], bool] = notify.send_document,
) -> dict[str, Any]:
    scan, qa, artifacts = artifact_contract()
    contract_sha = hashlib.sha256(
        json.dumps([(item["id"], item["sha256"]) for item in artifacts], separators=(",", ":")).encode()
    ).hexdigest()
    if receipt_path.exists():
        receipt = read_json(receipt_path)
        if receipt.get("contract_sha256") != contract_sha:
            raise DeliveryError("existing Telegram receipt belongs to another artifact contract")
        if receipt.get("delivery_complete"):
            raise DeliveryError("delivery already complete; refusing duplicate send")
    else:
        receipt = {
            "experiment_id": EXPERIMENT_ID,
            "started_at_utc": utc_now(),
            "contract_sha256": contract_sha,
            "scan_receipt_sha256": sha256_file(RESULTS / "scan_receipt.json"),
            "qa_receipt_sha256": sha256_file(RESULTS / "qa_receipt.json"),
            "expected_documents": len(artifacts),
            "intro_sent": False,
            "document_actions": [],
            "finish_sent": False,
            "delivery_complete": False,
            "holdout_consumption_number_for_this_configuration": 2,
            "training_or_tuning": False,
            "production_eligible": False,
        }
    if not receipt["intro_sent"]:
        intro = (
            "<b>最近五日 Top20 原始框修正版</b>\n"
            "旧版多框日图不再作为模型框位置证据。修正版保留模型原始 cx/cy/w/h，"
            "每个币日只展示最早连续 episode，最多 1 框；97 张单框、3 张无框。\n"
            "下面全部按文件发送，不经 Telegram 图片压缩。"
        )
        if not send_text(intro):
            raise DeliveryError("Telegram intro failed")
        receipt["intro_sent"] = True
        receipt["intro_sent_at_utc"] = utc_now()
        write_receipt(receipt_path, receipt)
    sent = {str(action["id"]) for action in receipt["document_actions"]}
    for item in artifacts:
        if item["id"] in sent:
            continue
        path = Path(item["path"])
        if not send_document(path, item["caption"]):
            receipt["last_error"] = f"document failed: {item['id']}"
            write_receipt(receipt_path, receipt)
            raise DeliveryError(receipt["last_error"])
        receipt["document_actions"].append(
            {
                "id": item["id"],
                "path": str(path),
                "sha256": item["sha256"],
                "sent_at_utc": utc_now(),
            }
        )
        receipt.pop("last_error", None)
        write_receipt(receipt_path, receipt)
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)
    if len(receipt["document_actions"]) != len(artifacts):
        raise DeliveryError("Telegram document ledger is incomplete")
    if not receipt["finish_sent"]:
        if not send_text(
            "<b>修正版已发完</b>：总览 + 5 张每日高清图 + 无损 ZIP + HTML。\n"
            "239 个旧扫描事件完全不变；这次修的是框证据和多框展示，不代表模型已解决过度检出。"
        ):
            raise DeliveryError("Telegram completion message failed")
        receipt["finish_sent"] = True
        receipt["finish_sent_at_utc"] = utc_now()
    receipt["delivery_complete"] = True
    receipt["completed_at_utc"] = utc_now()
    receipt["review_panels"] = int(scan["review_panels"])
    receipt["one_raw_box_panels"] = int(qa["one_raw_box_panels"])
    receipt["zero_box_panels"] = int(qa["zero_box_panels"])
    write_receipt(receipt_path, receipt)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, default=RECEIPT)
    parser.add_argument("--sleep-seconds", type=float, default=1.1)
    args = parser.parse_args()
    payload = deliver(receipt_path=args.receipt.resolve(), sleep_seconds=args.sleep_seconds)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
