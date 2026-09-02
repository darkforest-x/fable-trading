#!/usr/bin/env python3
"""Send the 18 verified standard-retail A-share charts to Telegram.

The sender is bound to the immutable output of
``filter_ashare_signals_for_standard_retail.py``.  It sends each PNG with
Telegram ``sendDocument`` so the original bytes are not recompressed, records
one receipt action after every successful response, and resumes without
re-sending already receipted documents.

This is delivery plumbing only.  It performs no market-data read, model
inference, threshold change, promotion, deployment, or order action.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from yoyo import notify


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-15m-ashare-grade-a-yolo-latest-20260902-v1"
DEFAULT_RESULTS = (
    ROOT
    / "experiments/active"
    / EXPERIMENT_ID
    / "results/standard_retail_mainboard"
)
DEFAULT_RECEIPT = DEFAULT_RESULTS / "telegram_delivery_receipt.json"
EXPECTED_SIGNALS_SHA256 = (
    "5a05e66df2abaa8583408216e6717a27f83e415fd68662826d01d1c7d786767c"
)
EXPECTED_SUMMARY_SHA256 = (
    "35509448bda8c86a8331e08e4322f01aa37e1fde972a811b53924ccbb0cfb802"
)
EXPECTED_VERIFICATION_SHA256 = (
    "0968e315e65eec408afffdf9964cc60f61c6d693916b1252f836b63aece1490c"
)
EXPECTED_ORIGINAL_RANKS = [2, 5, 6, 7, 8, 10, 11, 12, 13, 17, 19, 21, 23, 24, 25, 27, 29, 30]
EXPECTED_DOCUMENTS = len(EXPECTED_ORIGINAL_RANKS)
STANDARD_BOARDS = frozenset({"SH_MAIN", "SZ_MAIN"})


class AshareTelegramDeliveryError(RuntimeError):
    """Fail closed when delivery evidence or Telegram state drifts."""


def utc_now() -> str:
    """Return one auditable UTC timestamp."""

    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    """Return the byte identity of one local artifact."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    """Read one JSON object and fail on missing/non-object evidence."""

    if not path.is_file() or path.stat().st_size == 0:
        raise FileNotFoundError(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AshareTelegramDeliveryError(f"expected JSON object: {path}")
    return payload


def read_signals(path: Path) -> list[dict[str, str]]:
    """Read the frozen filtered ledger while preserving six-digit codes."""

    if not path.is_file() or path.stat().st_size == 0:
        raise FileNotFoundError(path)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _assert_frozen_identity(results: Path) -> None:
    expected = {
        "signals.csv": EXPECTED_SIGNALS_SHA256,
        "summary.json": EXPECTED_SUMMARY_SHA256,
        "verification.json": EXPECTED_VERIFICATION_SHA256,
    }
    for name, expected_sha in expected.items():
        path = results / name
        if not path.is_file() or sha256_file(path) != expected_sha:
            raise AshareTelegramDeliveryError(f"frozen delivery artifact drifted: {path}")


def build_contract(results: Path = DEFAULT_RESULTS) -> tuple[list[dict[str, Any]], str]:
    """Validate all 18 image identities and return the delivery contract."""

    results = results.resolve()
    _assert_frozen_identity(results)
    summary = read_json(results / "summary.json")
    verification = read_json(results / "verification.json")
    rows = read_signals(results / "signals.csv")

    if summary.get("protocol") != "ashare_standard_retail_mainboard_delivery_filter_v1":
        raise AshareTelegramDeliveryError("standard-retail summary protocol drifted")
    if int(summary.get("retained_events", -1)) != EXPECTED_DOCUMENTS:
        raise AshareTelegramDeliveryError("standard-retail retained count drifted")
    if int(summary.get("retained_long", -1)) != 1 or int(summary.get("retained_short", -1)) != 17:
        raise AshareTelegramDeliveryError("LONG/SHORT delivery composition drifted")
    if summary.get("model_inference") is not False or summary.get("network_reads") != 0:
        raise AshareTelegramDeliveryError("delivery-only parent safety flags drifted")
    if (
        summary.get("production_eligible") is not False
        or summary.get("tradability_proven") is not False
    ):
        raise AshareTelegramDeliveryError("retail filter must not claim production/tradability")
    if verification.get("passed") is not True:
        raise AshareTelegramDeliveryError("standard-retail verification is absent or failed")
    for key in ("retained_events", "chart_sha_checks"):
        if int(verification.get(key, -1)) != EXPECTED_DOCUMENTS:
            raise AshareTelegramDeliveryError(f"verification coverage drifted: {key}")
    if len(rows) != EXPECTED_DOCUMENTS:
        raise AshareTelegramDeliveryError("filtered signal ledger must contain 18 rows")
    ranks = [int(row["original_rank"]) for row in rows]
    if ranks != EXPECTED_ORIGINAL_RANKS:
        raise AshareTelegramDeliveryError("filtered signal order/identity drifted")

    documents: list[dict[str, Any]] = []
    for delivery_order, row in enumerate(rows, 1):
        if row.get("retail_eligible", "").strip().lower() != "true":
            raise AshareTelegramDeliveryError(f"ineligible row entered delivery: {row.get('code')}")
        if row.get("board") not in STANDARD_BOARDS:
            raise AshareTelegramDeliveryError(
                f"non-main-board row entered delivery: {row.get('code')}"
            )
        relative = Path(row["filtered_chart"])
        path = (results / relative).resolve()
        try:
            path.relative_to(results)
        except ValueError as exc:
            raise AshareTelegramDeliveryError(
                f"chart escapes results directory: {relative}"
            ) from exc
        if not path.is_file() or path.stat().st_size == 0:
            raise FileNotFoundError(path)
        if path.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
            raise AshareTelegramDeliveryError(f"chart is not PNG: {path}")
        actual_sha = sha256_file(path)
        if actual_sha != row["chart_sha256"]:
            raise AshareTelegramDeliveryError(f"chart hash drifted: {path}")

        direction = row["direction"].upper()
        if direction not in {"LONG", "SHORT"}:
            raise AshareTelegramDeliveryError(f"unknown direction: {direction}")
        window_end = datetime.fromisoformat(row["window_end_time"])
        if window_end.tzinfo is None:
            raise AshareTelegramDeliveryError("window_end_time must be timezone-aware")
        detect_cst = window_end.astimezone(ZoneInfo("Asia/Shanghai"))
        code = row["code"].zfill(6)
        name = html.escape(row["name"])
        direction_zh = "多头形态" if direction == "LONG" else "空头形态"
        caption = (
            f"A股 15m 检测原图 {delivery_order:02d}/{EXPECTED_DOCUMENTS:02d}｜"
            f"原榜 #{int(row['original_rank']):03d}\n"
            f"{code} {name}｜{direction_zh}｜conf {float(row['confidence']):.3f}\n"
            f"检测窗右端：{detect_cst:%Y-%m-%d %H:%M} 北京时间\n"
            "沪深主板权限过滤保留｜原始 PNG document，不压缩\n"
            "研究输出，不构成交易建议；SHORT 是形态类别，"
            "不等于可融券卖空。"
        )
        documents.append(
            {
                "id": f"signal_{delivery_order:02d}",
                "delivery_order": delivery_order,
                "original_rank": int(row["original_rank"]),
                "code": code,
                "name": row["name"],
                "direction": direction,
                "confidence": float(row["confidence"]),
                "path": str(path),
                "sha256": actual_sha,
                "caption": caption,
            }
        )

    contract_sha = hashlib.sha256(
        json.dumps(
            [(item["id"], item["original_rank"], item["sha256"]) for item in documents],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return documents, contract_sha


def write_receipt(path: Path, payload: dict[str, Any]) -> None:
    """Atomically persist a resumable delivery receipt."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def load_or_create_receipt(
    *, receipt_path: Path, contract_sha: str, documents: list[dict[str, Any]]
) -> dict[str, Any]:
    """Load a matching partial ledger or initialize a new one."""

    if receipt_path.exists():
        receipt = read_json(receipt_path)
        if receipt.get("experiment_id") != EXPERIMENT_ID:
            raise AshareTelegramDeliveryError("receipt belongs to another experiment")
        if receipt.get("contract_sha256") != contract_sha:
            raise AshareTelegramDeliveryError("receipt belongs to another image contract")
        if receipt.get("delivery_complete") is True:
            raise AshareTelegramDeliveryError(
                "delivery already complete; refusing duplicate resend"
            )
        return receipt
    return {
        "experiment_id": EXPERIMENT_ID,
        "delivery_scope": "standard_retail_mainboard_original_charts_18",
        "owner_authorization": "把检测原图发到 tg",
        "started_at_utc": utc_now(),
        "contract_sha256": contract_sha,
        "signals_sha256": EXPECTED_SIGNALS_SHA256,
        "expected_documents": len(documents),
        "expected_original_ranks": [item["original_rank"] for item in documents],
        "intro_sent": False,
        "document_actions": [],
        "finish_sent": False,
        "delivery_complete": False,
        "transport": "telegram_sendDocument_original_png_no_recompression",
        "credentials_read_or_echoed_by_entrypoint": False,
        "additional_holdout_consumption": False,
        "market_data_network_reads": 0,
        "model_inference": False,
        "threshold_or_weight_changed": False,
        "active_or_frozen_changed": False,
        "promoted": False,
        "deployed": False,
        "orders_placed": False,
        "production_eligible": False,
    }


def deliver(
    *,
    results: Path = DEFAULT_RESULTS,
    receipt_path: Path = DEFAULT_RECEIPT,
    sleep_seconds: float = 1.1,
    send_text: Callable[[str], bool] = notify.send,
    send_document: Callable[[Path, str], bool] = notify.send_document,
) -> dict[str, Any]:
    """Deliver the 18 original PNGs and resume after a partial failure."""

    documents, contract_sha = build_contract(results)
    receipt = load_or_create_receipt(
        receipt_path=receipt_path, contract_sha=contract_sha, documents=documents
    )

    if receipt.get("intro_sent") is not True:
        intro = (
            "<b>A股 15m 检测原图｜普通沪深主板账户版</b>\n"
            "原始 31 个模型命中经板块权限过滤后保留 18 个："
            "1 个 LONG、17 个 SHORT。\n"
            "下面逐张按原始 PNG 文件发送，不经过 Telegram 图片压缩。"
            "这只是板块权限过滤，不代表个股此刻一定可交易。"
        )
        if not send_text(intro):
            raise AshareTelegramDeliveryError("Telegram intro message failed")
        receipt["intro_sent"] = True
        receipt["intro_sent_at_utc"] = utc_now()
        write_receipt(receipt_path, receipt)

    sent_ids = {str(action["id"]) for action in receipt.get("document_actions", [])}
    for item in documents:
        if item["id"] in sent_ids:
            continue
        path = Path(item["path"])
        if not send_document(path, item["caption"]):
            receipt["last_error"] = f"document failed: {item['id']}"
            receipt["last_error_at_utc"] = utc_now()
            write_receipt(receipt_path, receipt)
            raise AshareTelegramDeliveryError(receipt["last_error"])
        receipt["document_actions"].append(
            {
                key: item[key]
                for key in (
                    "id",
                    "delivery_order",
                    "original_rank",
                    "code",
                    "name",
                    "direction",
                    "confidence",
                    "path",
                    "sha256",
                )
            }
            | {"sent_at_utc": utc_now()}
        )
        receipt.pop("last_error", None)
        receipt.pop("last_error_at_utc", None)
        write_receipt(receipt_path, receipt)
        print(
            f"Telegram [{len(receipt['document_actions']):02d}/{len(documents):02d}] "
            f"{item['code']} {item['name']} {path.name}",
            flush=True,
        )
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    actions = receipt.get("document_actions", [])
    if [action["id"] for action in actions] != [item["id"] for item in documents]:
        raise AshareTelegramDeliveryError("Telegram document ledger is incomplete or out of order")
    if receipt.get("finish_sent") is not True:
        if not send_text(
            "<b>A股检测原图已发完：18/18</b>\n"
            "其中 LONG 1、SHORT 17；SHORT 仅为模型形态类别，"
            "不等于普通账户可做空。"
        ):
            write_receipt(receipt_path, receipt)
            raise AshareTelegramDeliveryError("Telegram completion message failed")
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
    parser.add_argument(
        "--send",
        action="store_true",
        help="perform the externally visible Telegram delivery; otherwise validate only",
    )
    args = parser.parse_args()
    results = args.results.resolve()
    receipt = args.receipt.resolve()
    if args.send:
        payload = deliver(
            results=results,
            receipt_path=receipt,
            sleep_seconds=args.sleep_seconds,
        )
        output = {
            "delivery_complete": payload["delivery_complete"],
            "documents_sent": len(payload["document_actions"]),
            "receipt": str(receipt),
            "contract_sha256": payload["contract_sha256"],
        }
    else:
        documents, contract_sha = build_contract(results)
        output = {
            "validated_only": True,
            "documents": len(documents),
            "original_ranks": [item["original_rank"] for item in documents],
            "contract_sha256": contract_sha,
            "would_write_receipt": str(receipt),
        }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
