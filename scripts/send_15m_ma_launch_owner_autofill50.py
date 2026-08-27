#!/usr/bin/env python3
"""QA and deliver the fully boxed 15m MA-launch example pack to Telegram."""

from __future__ import annotations

import argparse
import hashlib
import html as html_lib
import json
import struct
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

from yoyo import notify


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-15m-ma-launch-owner-autofill50-v7"
RESULTS = ROOT / "experiments" / "active" / EXPERIMENT_ID / "results"
DEFAULT_MANIFEST = RESULTS / "review_manifest.jsonl"
DEFAULT_SUMMARY = RESULTS / "summary.json"
DEFAULT_VISUAL_QA = RESULTS / "visual_qa_receipt.json"
DEFAULT_REPORT = ROOT / "analysis" / "html" / "p0_15m_ma_launch_owner_autofill50_20260827.html"
DEFAULT_RECEIPT = RESULTS / "telegram_delivery_receipt.json"
RED_BGR = np.asarray((45, 45, 232), dtype=np.uint8)


class DeliveryError(RuntimeError):
    """Fail-closed QA or Telegram delivery error."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def png_dimensions(path: Path) -> tuple[int, int]:
    with path.open("rb") as handle:
        header = handle.read(24)
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise DeliveryError(f"not a valid PNG: {path}")
    return struct.unpack(">II", header[16:24])


def load_rows(manifest: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines() if line]
    rows.sort(key=lambda row: int(row["source_order"]))
    return rows


def validate_contract(manifest: Path, summary_path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = load_rows(manifest)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if len(rows) != 50 or [int(row["source_order"]) for row in rows] != list(range(1, 51)):
        raise DeliveryError("manifest must contain source orders 01..50")
    if summary.get("n_delivered") != 50 or summary.get("direction_counts") != {"LONG": 25, "SHORT": 25}:
        raise DeliveryError("50-row direction contract drifted")
    if summary.get("boxes_per_image_min") != 1 or summary.get("boxes_per_image_max") != 1:
        raise DeliveryError("one-box-per-image contract drifted")
    if summary.get("manual_owner_review_workflow_created") is not False:
        raise DeliveryError("manual Owner workflow must remain disabled")
    if summary.get("training_started") is not False or summary.get("yolo_labels_written") != 0:
        raise DeliveryError("training/label safety contract drifted")
    if summary.get("holdout_ohlcv_rows_materialized") != 0:
        raise DeliveryError("holdout safety contract drifted")
    for row in rows:
        image_path = ROOT / str(row["image_path"])
        if not image_path.is_file() or sha256_file(image_path) != row["image_sha256"]:
            raise DeliveryError(f"image missing or hash drifted: {image_path}")
        if png_dimensions(image_path) != (1280, 742):
            raise DeliveryError(f"image dimensions drifted: {image_path}")
        if row.get("boxes_per_image") != 1 or int(row.get("core_bars", 0)) not in {4, 5}:
            raise DeliveryError(f"box geometry contract drifted: {row['source_order']}")
        if row.get("yolo_label_path") is not None:
            raise DeliveryError(f"unexpected label path: {row['source_order']}")
        if row.get("training_eligible") is not False or row.get("production_eligible") is not False:
            raise DeliveryError(f"eligibility drifted: {row['source_order']}")
    return rows, summary


def run_visual_qa(
    manifest: Path = DEFAULT_MANIFEST,
    summary_path: Path = DEFAULT_SUMMARY,
    visual_qa_path: Path = DEFAULT_VISUAL_QA,
) -> dict[str, Any]:
    """Decode every actual delivery PNG and prove that one red box is present."""

    rows, summary = validate_contract(manifest, summary_path)
    red_counts = []
    box_heights = []
    for row in rows:
        image_path = ROOT / str(row["image_path"])
        image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if image is None or image.shape != (742, 1280, 3):
            raise DeliveryError(f"OpenCV decode drifted: {image_path}")
        red_count = int(np.all(image == RED_BGR, axis=2).sum())
        if red_count <= 0:
            raise DeliveryError(f"red box pixels absent: {image_path}")
        red_counts.append(red_count)
        box_heights.append(float(row["box"]["source_height_px"]))
    label_files = list(RESULTS.rglob("*.txt"))
    if label_files:
        raise DeliveryError(f"unexpected YOLO-like text labels: {len(label_files)}")
    payload = {
        "experiment_id": EXPERIMENT_ID,
        "created_at_utc": utc_now(),
        "manifest_sha256": sha256_file(manifest),
        "n_images": len(rows),
        "pixel_dimensions": "1280x742",
        "red_box_images": len(red_counts),
        "red_pixel_count_min": min(red_counts),
        "red_pixel_count_max": max(red_counts),
        "boxes_per_image_min": 1,
        "boxes_per_image_max": 1,
        "core_bars_counts": dict(Counter(str(row["core_bars"]) for row in rows)),
        "box_source_height_px_min": min(box_heights),
        "box_source_height_px_max": max(box_heights),
        "yolo_label_files": 0,
        "holdout_ohlcv_rows_materialized": summary["holdout_ohlcv_rows_materialized"],
        "manual_owner_review_workflow_created": False,
        "training_started": False,
        "passed": True,
    }
    visual_qa_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def write_receipt(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def caption_for(row: dict[str, Any]) -> str:
    order = int(row["source_order"])
    symbol = html_lib.escape(str(row["symbol"]))
    direction = html_lib.escape(str(row["direction"]))
    return (
        f"严格自动补齐 v7 | {order:02d}/50\n"
        f"{symbol} | {direction}\n"
        f"单一红框 | {int(row['core_bars'])} 根 K | "
        f"t{int(row['core_start_offset']):+d}..t{int(row['core_end_offset']):+d}\n"
        "1280×742 原始 PNG document；内部筛选已完成，无需人工裁决；未打标签、未训练"
    )


def deliver_images(
    *,
    manifest: Path = DEFAULT_MANIFEST,
    summary_path: Path = DEFAULT_SUMMARY,
    visual_qa_path: Path = DEFAULT_VISUAL_QA,
    receipt_path: Path = DEFAULT_RECEIPT,
    sleep_seconds: float = 1.1,
    send_text: Callable[[str], bool] = notify.send,
    send_document: Callable[[Path, str], bool] = notify.send_document,
) -> dict[str, Any]:
    rows, _summary = validate_contract(manifest, summary_path)
    qa = json.loads(visual_qa_path.read_text(encoding="utf-8"))
    if qa.get("passed") is not True or qa.get("manifest_sha256") != sha256_file(manifest):
        raise DeliveryError("visual QA receipt missing or stale")
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt.get("manifest_sha256") != sha256_file(manifest):
            raise DeliveryError("Telegram receipt belongs to another manifest")
    else:
        receipt = {
            "experiment_id": EXPERIMENT_ID,
            "started_at_utc": utc_now(),
            "manifest_sha256": sha256_file(manifest),
            "expected_documents": 50,
            "intro_sent": False,
            "document_actions": [],
            "images_complete": False,
            "finish_summary_sent": False,
            "report_sent": False,
            "delivery_complete": False,
            "holdout_consumed": False,
            "training_started": False,
        }
    if receipt.get("delivery_complete"):
        raise DeliveryError("delivery already complete; refusing duplicate send")
    if not receipt.get("intro_sent"):
        intro = (
            "<b>15m 均线密集严格自动补齐 v7</b>\n"
            "上一批 20 张有框 + 30 张无框的人工审核口径作废。\n"
            "本批内部淘汰并补足后，50 张全部有且只有 1 个红框，下面逐张发送高清原 PNG；"
            "不需要你回复 KEEP / ADJUST / REJECT。"
        )
        if not send_text(intro):
            raise DeliveryError("Telegram intro failed")
        receipt["intro_sent"] = True
        receipt["intro_sent_at_utc"] = utc_now()
        write_receipt(receipt_path, receipt)
    sent_orders = {int(action["source_order"]) for action in receipt["document_actions"]}
    for row in rows:
        order = int(row["source_order"])
        if order in sent_orders:
            continue
        image_path = ROOT / str(row["image_path"])
        if not send_document(image_path, caption_for(row)):
            receipt["last_error"] = f"document failed at {order:02d}"
            write_receipt(receipt_path, receipt)
            raise DeliveryError(receipt["last_error"])
        receipt["document_actions"].append(
            {
                "source_order": order,
                "sample_id": row["sample_id"],
                "symbol": row["symbol"],
                "direction": row["direction"],
                "path": str(image_path),
                "sha256": row["image_sha256"],
                "sent_at_utc": utc_now(),
            }
        )
        receipt.pop("last_error", None)
        write_receipt(receipt_path, receipt)
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)
    orders = [int(action["source_order"]) for action in receipt["document_actions"]]
    if orders != list(range(1, 51)):
        raise DeliveryError("Telegram document ledger is incomplete")
    receipt["images_complete"] = True
    receipt["images_completed_at_utc"] = utc_now()
    if not receipt.get("finish_summary_sent"):
        if not send_text(
            "<b>严格自动补齐 v7 已发完：50/50</b>\n"
            "50 张全部单框；无无框图、无人工审核任务、无 YOLO 标签、无训练。"
        ):
            raise DeliveryError("Telegram completion message failed")
        receipt["finish_summary_sent"] = True
        receipt["finish_summary_sent_at_utc"] = utc_now()
    write_receipt(receipt_path, receipt)
    return receipt


def deliver_report(
    *,
    report: Path = DEFAULT_REPORT,
    receipt_path: Path = DEFAULT_RECEIPT,
    send_document: Callable[[Path, str], bool] = notify.send_document,
) -> dict[str, Any]:
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if not receipt.get("images_complete") or len(receipt.get("document_actions", [])) != 50:
        raise DeliveryError("50 image documents are not complete")
    if receipt.get("report_sent"):
        raise DeliveryError("report already sent; refusing duplicate send")
    if not send_document(report, "15m 均线密集严格自动补齐 v7：50 张全部单框（HTML）"):
        raise DeliveryError("Telegram HTML report failed")
    receipt["report_path"] = str(report)
    receipt["report_sha256"] = sha256_file(report)
    receipt["report_sent"] = True
    receipt["delivery_complete"] = True
    receipt["completed_at_utc"] = utc_now()
    write_receipt(receipt_path, receipt)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("qa", "images", "report"))
    parser.add_argument("--sleep-seconds", type=float, default=1.1)
    args = parser.parse_args()
    if args.phase == "qa":
        payload = run_visual_qa()
    elif args.phase == "images":
        payload = deliver_images(sleep_seconds=args.sleep_seconds)
    else:
        payload = deliver_report()
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
