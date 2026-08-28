#!/usr/bin/env python3
"""Send all verified 2026-08-27 full-context signal PNGs to Telegram."""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from scripts.render_15m_ma_launch_owner_yolo_20260827_fullcontext import (
    DEFAULT_RESULTS,
    EXPECTED_EVENTS,
    EXPERIMENT_ID,
    read_json,
    resolve_repo_path,
    sha256_file,
    utc,
)
from yoyo import notify


DEFAULT_RECEIPT = DEFAULT_RESULTS / "telegram_delivery_receipt.json"


class FullContextDeliveryError(RuntimeError):
    """Fail closed if delivery evidence is incomplete or has drifted."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_receipt(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_contract(results: Path) -> tuple[list[dict[str, str]], str]:
    render_path = results / "render_receipt.json"
    qa_path = results / "qa_receipt.json"
    manifest_path = results / "manifest.jsonl"
    render_receipt = read_json(render_path)
    qa_receipt = read_json(qa_path)
    if render_receipt.get("experiment_id") != EXPERIMENT_ID:
        raise FullContextDeliveryError("render receipt experiment identity drifted")
    if qa_receipt.get("experiment_id") != EXPERIMENT_ID or qa_receipt.get("passed") is not True:
        raise FullContextDeliveryError("independent QA is absent or failed")
    for key in (
        "events",
        "documents_with_exactly_one_box",
        "exact_event_identity_matches",
        "exact_pixel_rerenders",
        "exact_png_hash_matches",
        "exact_model_input_pixel_matches",
        "raw_box_projection_roundtrip_matches",
    ):
        if int(qa_receipt.get(key, -1)) != EXPECTED_EVENTS:
            raise FullContextDeliveryError(f"QA coverage drifted: {key}")
    if int(render_receipt.get("holdout_consumption_number_for_this_configuration", -1)) != 3:
        raise FullContextDeliveryError("holdout consumption identity drifted")
    for key in (
        "new_model_inference",
        "training_or_tuning",
        "active_or_frozen_changed",
        "promoted",
        "deployed",
        "orders_placed",
    ):
        if render_receipt.get(key) is not False:
            raise FullContextDeliveryError(f"unsafe render flag: {key}")
    if sha256_file(manifest_path) != str(render_receipt.get("manifest_sha256")):
        raise FullContextDeliveryError("manifest hash drifted")
    manifest = [
        json.loads(line)
        for line in manifest_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    if len(manifest) != EXPECTED_EVENTS:
        raise FullContextDeliveryError("manifest does not contain 43 events")

    documents: list[dict[str, str]] = []
    for row in manifest:
        order = int(row["event_order"])
        if order != len(documents) + 1:
            raise FullContextDeliveryError("manifest order drifted")
        path = resolve_repo_path(row["image_path"])
        if not path.is_file() or sha256_file(path) != str(row["image_sha256"]):
            raise FullContextDeliveryError(f"PNG identity drifted: {path}")
        direction = "多头" if int(row["class_id"]) == 0 else "空头"
        symbol = str(row["symbol"]).replace("_USDT_SWAP", "")
        detect_utc = utc(row["window_end_time"])
        detect_cst = detect_utc.tz_convert("Asia/Shanghai")
        after_note = "｜注意：该信号在 UTC 次日完成" if bool(row["after_board_midnight"]) else ""
        documents.append(
            {
                "id": f"signal_{order:02d}",
                "path": str(path),
                "sha256": str(row["image_sha256"]),
                "caption": (
                    f"昨日全景信号 {order:02d}/{EXPECTED_EVENTS}｜#{int(row['rank']):02d} {symbol}｜"
                    f"{direction}｜conf {float(row['confidence']):.3f}\n"
                    f"检测完成：{detect_utc:%m-%d %H:%M} UTC / {detect_cst:%m-%d %H:%M} 北京时间\n"
                    f"W{int(row['window_len'])}｜核心 {int(row['core_length_bars'])} 根｜"
                    f"确认 {int(row['confirmation_bars'])} 根{after_note}\n"
                    "上方是 110 根 15m 全景；右下是模型实际输入与同一个原始框。"
                ),
            }
        )
    contract_sha = hashlib.sha256(
        json.dumps(
            [(item["id"], item["sha256"]) for item in documents],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return documents, contract_sha


def deliver(
    *,
    results: Path = DEFAULT_RESULTS,
    receipt_path: Path = DEFAULT_RECEIPT,
    sleep_seconds: float = 1.1,
    send_text: Callable[[str], bool] = notify.send,
    send_document: Callable[[Path, str], bool] = notify.send_document,
) -> dict[str, Any]:
    documents, contract_sha = build_contract(results)
    if receipt_path.exists():
        receipt = read_json(receipt_path)
        if receipt.get("contract_sha256") != contract_sha:
            raise FullContextDeliveryError("existing Telegram receipt belongs to another contract")
        if receipt.get("delivery_complete") is True:
            raise FullContextDeliveryError("delivery already complete; refusing duplicate resend")
    else:
        receipt = {
            "experiment_id": EXPERIMENT_ID,
            "started_at_utc": utc_now(),
            "contract_sha256": contract_sha,
            "expected_documents": EXPECTED_EVENTS,
            "intro_sent": False,
            "document_actions": [],
            "finish_sent": False,
            "delivery_complete": False,
            "holdout_consumption_number_for_this_configuration": 3,
            "transport": "telegram_document_no_recompression",
            "training_or_tuning": False,
            "production_eligible": False,
        }
    if receipt["intro_sent"] is not True:
        intro = (
            "<b>2026-08-27 昨日全部信号｜高清全景版</b>\n"
            "共 43 个原始事件（37 多 / 6 空，19 个币），每张只有一个真实检测框。\n"
            "上方为该币 110 根连续 15m 全景；右下为模型当时实际看到的 W18–25 输入。"
            "虚线是信号真正检测完成的时刻。\n"
            "全部按 PNG 文件逐张发送，不走 Telegram 图片压缩。"
        )
        if not send_text(intro):
            raise FullContextDeliveryError("Telegram intro failed")
        receipt["intro_sent"] = True
        receipt["intro_sent_at_utc"] = utc_now()
        write_receipt(receipt_path, receipt)

    already_sent = {str(action["id"]) for action in receipt["document_actions"]}
    for item in documents:
        if item["id"] in already_sent:
            continue
        path = Path(item["path"])
        if not send_document(path, item["caption"]):
            receipt["last_error"] = f"document failed: {item['id']}"
            write_receipt(receipt_path, receipt)
            raise FullContextDeliveryError(receipt["last_error"])
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
        print(f"Telegram [{len(receipt['document_actions']):02d}/{EXPECTED_EVENTS}] {path.name}", flush=True)
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    if len(receipt["document_actions"]) != EXPECTED_EVENTS:
        raise FullContextDeliveryError("Telegram document ledger is incomplete")
    if receipt["finish_sent"] is not True:
        if not send_text(
            "<b>43 张昨日高清全景信号已全部发完。</b>\n"
            "其中 2 个事件归属 08-27 榜单，但检测在 UTC 08-28 00:30/00:45 才完成，图内已单独注明。"
        ):
            raise FullContextDeliveryError("Telegram completion message failed")
        receipt["finish_sent"] = True
        receipt["finish_sent_at_utc"] = utc_now()
    receipt["delivery_complete"] = True
    receipt["completed_at_utc"] = utc_now()
    write_receipt(receipt_path, receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    parser.add_argument("--sleep-seconds", type=float, default=1.1)
    args = parser.parse_args()
    receipt = deliver(
        results=args.results.resolve(),
        receipt_path=args.receipt.resolve(),
        sleep_seconds=args.sleep_seconds,
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
