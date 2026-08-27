#!/usr/bin/env python3
"""Deliver the strict 15m MA-launch Review50 to Telegram one PNG at a time.

The delivery is deliberately split into ``images`` and ``report`` phases so
the canonical HTML can record the completed image receipt before it is sent.
Every source identity is delivered as a Telegram document: 20 rows carry one
red proposal box and 30 rows carry an explicit no-box decision.  Credentials
remain behind :mod:`yoyo.notify` and are never read or printed here.
"""

from __future__ import annotations

import argparse
import hashlib
import html as html_lib
import json
import struct
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from yoyo import notify


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-15m-ma-launch-owner-strict-review50-v5"
RESULTS = ROOT / "experiments" / "active" / EXPERIMENT_ID / "results"
DEFAULT_MANIFEST = RESULTS / "review_manifest.jsonl"
DEFAULT_SUMMARY = RESULTS / "summary.json"
DEFAULT_VISUAL_QA = RESULTS / "visual_qa_receipt.json"
DEFAULT_REPORT = (
    ROOT / "analysis" / "html" / "p0_15m_ma_launch_owner_strict_review50_20260827.html"
)
DEFAULT_RECEIPT = RESULTS / "telegram_delivery_receipt.json"


class DeliveryError(RuntimeError):
    """Fail-closed Telegram delivery error."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def png_dimensions(path: Path) -> tuple[int, int]:
    """Read the PNG IHDR dimensions without decoding or rewriting pixels."""

    with path.open("rb") as handle:
        header = handle.read(24)
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise DeliveryError(f"not a valid PNG: {path}")
    return struct.unpack(">II", header[16:24])


def load_rows(manifest: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines() if line]
    rows.sort(key=lambda row: int(row["source_order"]))
    return rows


def validate_inputs(
    *, manifest: Path, summary_path: Path, visual_qa_path: Path
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    for path in (manifest, summary_path, visual_qa_path):
        if not path.is_file() or path.stat().st_size == 0:
            raise FileNotFoundError(path)
    rows = load_rows(manifest)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    visual_qa = json.loads(visual_qa_path.read_text(encoding="utf-8"))
    if len(rows) != 50 or [int(row["source_order"]) for row in rows] != list(range(1, 51)):
        raise DeliveryError("manifest must contain source orders 01..50 exactly once")
    if summary.get("n_review_box_proposals") != 20 or summary.get("n_no_box_rows") != 30:
        raise DeliveryError("strict 20-box / 30-no-box contract drifted")
    if summary.get("training_started") is not False or summary.get("yolo_labels_written") != 0:
        raise DeliveryError("training/label safety contract drifted")
    if summary.get("training_eligible") is not False or summary.get("production_eligible") is not False:
        raise DeliveryError("eligibility safety contract drifted")
    if visual_qa.get("pixel_dimensions") != "1280x742" or visual_qa.get("yolo_label_files") != 0:
        raise DeliveryError("visual QA contract drifted")
    for row in rows:
        image_path = ROOT / row["review_image_path"]
        if not image_path.is_file():
            raise FileNotFoundError(image_path)
        if sha256_file(image_path) != row["review_image_sha256"]:
            raise DeliveryError(f"review image hash drifted: {image_path}")
        if png_dimensions(image_path) != (1280, 742):
            raise DeliveryError(f"review image dimensions drifted: {image_path}")
        if row.get("training_eligible") is not False or row.get("production_eligible") is not False:
            raise DeliveryError(f"row eligibility drifted: {row['source_order']}")
    return rows, summary


def write_receipt(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_or_create_receipt(
    *, receipt_path: Path, manifest: Path, rows: list[dict[str, Any]]
) -> dict[str, Any]:
    if receipt_path.exists():
        payload = json.loads(receipt_path.read_text(encoding="utf-8"))
        if payload.get("experiment_id") != EXPERIMENT_ID:
            raise DeliveryError("receipt belongs to another experiment")
        if payload.get("manifest_sha256") != sha256_file(manifest):
            raise DeliveryError("receipt manifest identity drifted")
        return payload
    return {
        "experiment_id": EXPERIMENT_ID,
        "started_at_utc": utc_now(),
        "manifest_path": str(manifest),
        "manifest_sha256": sha256_file(manifest),
        "expected_source_orders": [int(row["source_order"]) for row in rows],
        "expected_documents": 50,
        "expected_box_documents": 20,
        "expected_no_box_documents": 30,
        "intro_sent": False,
        "document_actions": [],
        "images_complete": False,
        "finish_summary_sent": False,
        "report_sent": False,
        "delivery_complete": False,
        "credentials_read_or_echoed_by_entrypoint": False,
        "holdout_consumed": False,
        "training_started": False,
        "production_eligible": False,
    }


def caption_for(row: dict[str, Any]) -> str:
    source_order = int(row["source_order"])
    symbol = html_lib.escape(str(row["symbol"]))
    direction = html_lib.escape(str(row["direction"]))
    if row["has_box_proposal"]:
        core = f"t{int(row['core_start_offset']):+d}..t{int(row['core_end_offset']):+d}"
        decision = "保留提案：每图仅 1 个红框"
        timing = f"模型右端=core+{int(row['core_to_model_right_gap_bars'])} 根"
    else:
        core = "无框"
        decision = "严格淘汰：不硬凑正例"
        timing = html_lib.escape(str(row["reason"]))
    return (
        f"严格纠正版 v5 | 原编号 {source_order:02d}/50\n"
        f"{symbol} | {direction}\n"
        f"{decision} | {core}\n{timing}\n"
        "1280×742 原始 PNG document；未训练，待 Owner 复审"
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
    """Send all 50 decisions one-by-one, resuming safely after partial failure."""

    rows, _summary = validate_inputs(
        manifest=manifest, summary_path=summary_path, visual_qa_path=visual_qa_path
    )
    receipt = load_or_create_receipt(receipt_path=receipt_path, manifest=manifest, rows=rows)
    if receipt.get("delivery_complete"):
        raise DeliveryError("delivery already complete; refusing duplicate send")
    if not receipt.get("intro_sent"):
        intro = (
            "<b>15m 均线密集严格纠正版 v5</b>\n"
            "下面按原编号 01–50 逐张发送高清 PNG：20 张各 1 个红框，30 张明确无框淘汰。\n"
            "v4 中间版未发送；本轮没有标签、训练、holdout 或生产切换。"
        )
        if not send_text(intro):
            raise DeliveryError("Telegram intro message failed")
        receipt["intro_sent"] = True
        receipt["intro_sent_at_utc"] = utc_now()
        write_receipt(receipt_path, receipt)

    sent_orders = {int(action["source_order"]) for action in receipt["document_actions"]}
    for row in rows:
        source_order = int(row["source_order"])
        if source_order in sent_orders:
            continue
        image_path = ROOT / row["review_image_path"]
        if not send_document(image_path, caption_for(row)):
            receipt["last_error"] = f"document failed at source order {source_order:02d}"
            receipt["last_error_at_utc"] = utc_now()
            write_receipt(receipt_path, receipt)
            raise DeliveryError(receipt["last_error"])
        receipt["document_actions"].append(
            {
                "source_order": source_order,
                "sample_id": row["sample_id"],
                "symbol": row["symbol"],
                "direction": row["direction"],
                "has_box_proposal": bool(row["has_box_proposal"]),
                "status": row["status"],
                "path": str(image_path),
                "sha256": row["review_image_sha256"],
                "sent_at_utc": utc_now(),
            }
        )
        receipt.pop("last_error", None)
        receipt.pop("last_error_at_utc", None)
        write_receipt(receipt_path, receipt)
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    orders = [int(action["source_order"]) for action in receipt["document_actions"]]
    if orders != list(range(1, 51)):
        raise DeliveryError(f"incomplete document ledger: {orders}")
    receipt["images_complete"] = True
    receipt["images_completed_at_utc"] = utc_now()
    if not receipt.get("finish_summary_sent"):
        if not send_text(
            "<b>严格纠正版 v5 图片已发完：50/50</b>\n"
            "有框 20、无框 30；请按原编号回复 KEEP / ADJUST / REJECT。"
        ):
            write_receipt(receipt_path, receipt)
            raise DeliveryError("Telegram image completion message failed")
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
    """Send the final HTML only after all 50 image actions are receipted."""

    if not receipt_path.is_file():
        raise FileNotFoundError(receipt_path)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if not receipt.get("images_complete") or len(receipt.get("document_actions", [])) != 50:
        raise DeliveryError("50 image documents are not complete")
    if receipt.get("report_sent"):
        raise DeliveryError("report already sent; refusing duplicate send")
    if not report.is_file() or report.stat().st_size == 0:
        raise FileNotFoundError(report)
    if not send_document(
        report,
        "15m 均线密集严格纠正版 v5：50 张逐图裁决、20 个单框提案、30 个无框淘汰（HTML）",
    ):
        raise DeliveryError("Telegram HTML report failed")
    receipt["report_path"] = str(report)
    receipt["report_sha256"] = sha256_file(report)
    receipt["report_sent"] = True
    receipt["report_sent_at_utc"] = utc_now()
    receipt["delivery_complete"] = True
    receipt["completed_at_utc"] = utc_now()
    write_receipt(receipt_path, receipt)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("images", "report"))
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--visual-qa", type=Path, default=DEFAULT_VISUAL_QA)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    parser.add_argument("--sleep-seconds", type=float, default=1.1)
    args = parser.parse_args()
    if args.phase == "images":
        payload = deliver_images(
            manifest=args.manifest.resolve(),
            summary_path=args.summary.resolve(),
            visual_qa_path=args.visual_qa.resolve(),
            receipt_path=args.receipt.resolve(),
            sleep_seconds=args.sleep_seconds,
        )
    else:
        payload = deliver_report(
            report=args.report.resolve(), receipt_path=args.receipt.resolve()
        )
    print(
        json.dumps(
            {
                "images_sent": len(payload.get("document_actions", [])),
                "images_complete": payload.get("images_complete"),
                "report_sent": payload.get("report_sent"),
                "delivery_complete": payload.get("delivery_complete"),
                "receipt": str(args.receipt.resolve()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
