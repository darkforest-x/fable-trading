#!/usr/bin/env python3
"""Deliver the verified prediction/training parity evidence to Telegram.

The sender validates the frozen audit, creates a deterministic lossless archive
containing all 43 paired PNGs, and sends every artifact as a Telegram document.
Credentials stay inside ``yoyo.notify`` and are never read or printed here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from scripts.verify_15m_ma_launch_owner_yolo_prediction_vs_training import verify
from yoyo import notify


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-15m-ma-launch-owner-yolo-20260827-training-parity-audit-v1"
RESULTS = ROOT / "experiments" / "active" / EXPERIMENT_ID / "results"
REPORT = (
    ROOT
    / "analysis"
    / "html"
    / "p1_15m_ma_launch_owner_yolo_prediction_training_parity_20260828.html"
)
ARCHIVE = RESULTS / "actual_prediction_vs_training_43_hq.zip"
RECEIPT = RESULTS / "telegram_delivery_receipt.json"


class DeliveryError(RuntimeError):
    """Fail closed if delivery inputs, identity, or progress drift."""


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


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def archive_members(results: Path) -> list[tuple[str, bytes]]:
    members: list[tuple[str, bytes]] = [
        (
            "README.txt",
            (
                "2026-08-27 原始预测框 vs 模型实际训练图\n\n"
                "自动结论：43 个事件中 #27 TAO LONG、#30 LIT LONG 符合训练标准；"
                "其余 41 个不符合。\n"
                "红框=原始预测；橙框=实际训练标签；青框=同横向核心的六均线包络诊断。\n"
                "本包只是自动证据，不需要 Owner 人工审核、打勾或改框。\n"
            ).encode("utf-8"),
        ),
        ("comparison_gallery.html", (results / "comparison_gallery.html").read_bytes()),
        ("event_semantic_audit.csv", (results / "event_semantic_audit.csv").read_bytes()),
        ("summary.json", (results / "summary.json").read_bytes()),
        (
            "training_vs_prediction_overview.png",
            (results / "training_vs_prediction_overview.png").read_bytes(),
        ),
        (
            "representative_comparisons.png",
            (results / "representative_comparisons.png").read_bytes(),
        ),
    ]
    comparisons = sorted((results / "comparisons").glob("*.png"))
    if len(comparisons) != 43:
        raise DeliveryError(f"expected 43 comparison PNGs, found {len(comparisons)}")
    members.extend((f"comparisons/{path.name}", path.read_bytes()) for path in comparisons)
    return members


def write_deterministic_archive(path: Path, members: list[tuple[str, bytes]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False
    )
    temporary = Path(handle.name)
    handle.close()
    try:
        with zipfile.ZipFile(temporary, "w") as archive:
            for name, content in members:
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, content, compresslevel=9)
        if path.exists():
            if sha256_file(path) != sha256_file(temporary):
                raise DeliveryError("existing lossless archive differs from deterministic rebuild")
            temporary.unlink()
        else:
            os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def artifact_contract(
    *, results: Path = RESULTS, report: Path = REPORT, archive: Path = ARCHIVE
) -> tuple[list[dict[str, str]], str]:
    qa = verify(results)
    if qa.get("passed") is not True:
        raise DeliveryError("independent audit QA failed")
    if int(qa.get("strict_training_spec_matches", -1)) != 2:
        raise DeliveryError("strict-match count drifted")
    if int(qa.get("out_of_training_spec", -1)) != 41:
        raise DeliveryError("out-of-training-spec count drifted")
    if int(qa.get("failing_events_with_alternative_core", -1)) != 0:
        raise DeliveryError("a failing event unexpectedly gained an alternative core")

    html_qa = read_json(results / "html_qa_receipt.json")
    if html_qa.get("passed") is not True or html_qa.get("manual_owner_review_required") is not False:
        raise DeliveryError("HTML QA or no-manual-review contract drifted")
    expected_report_sha = str(html_qa["report_html"]["sha256"])
    if not report.is_file() or sha256_file(report) != expected_report_sha:
        raise DeliveryError("self-contained HTML report identity drifted")

    write_deterministic_archive(archive, archive_members(results))
    with zipfile.ZipFile(archive) as zipped:
        names = zipped.namelist()
        if len(names) != 49 or len([name for name in names if name.startswith("comparisons/")]) != 43:
            raise DeliveryError("lossless archive membership drifted")
        if zipped.testzip() is not None:
            raise DeliveryError("lossless archive CRC failed")

    artifacts = [
        {
            "id": "overview",
            "path": str(results / "training_vs_prediction_overview.png"),
            "caption": (
                "训练正例 vs 08-27 原始预测｜自动语义对照总览\n"
                "43 个事件仅 #27 TAO、#30 LIT 符合完整训练标准。"
            ),
        },
        {
            "id": "representative_pairs",
            "path": str(results / "representative_comparisons.png"),
            "caption": (
                "实际模型输入 vs 实际训练正例｜代表性高清对照\n"
                "红=原始预测，橙=训练标签，青=六均线包络诊断。"
            ),
        },
        {
            "id": "full_43_archive",
            "path": str(archive),
            "caption": (
                "完整 43 张高清逐图对照 + 画廊 + CSV + summary（无损 ZIP）\n"
                "自动结论：2 个保留、41 个淘汰；无需人工审核。"
            ),
        },
        {
            "id": "html_report",
            "path": str(report),
            "caption": "检测框与实际训练图语义对照｜完整自包含 HTML 报告",
        },
    ]
    for item in artifacts:
        path = Path(item["path"])
        if not path.is_file() or path.stat().st_size == 0:
            raise DeliveryError(f"delivery artifact missing: {path}")
        item["sha256"] = sha256_file(path)
    contract_sha = hashlib.sha256(
        json.dumps(
            [(item["id"], item["sha256"]) for item in artifacts],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return artifacts, contract_sha


def deliver(
    *,
    results: Path = RESULTS,
    report: Path = REPORT,
    archive: Path = ARCHIVE,
    receipt_path: Path = RECEIPT,
    sleep_seconds: float = 1.1,
    send_text: Callable[[str], bool] = notify.send,
    send_document: Callable[[Path, str], bool] = notify.send_document,
) -> dict[str, Any]:
    artifacts, contract_sha = artifact_contract(results=results, report=report, archive=archive)
    if receipt_path.exists():
        receipt = read_json(receipt_path)
        if receipt.get("contract_sha256") != contract_sha:
            raise DeliveryError("existing Telegram receipt belongs to another artifact contract")
        if receipt.get("delivery_complete") is True:
            raise DeliveryError("delivery already complete; refusing duplicate send")
    else:
        receipt = {
            "experiment_id": EXPERIMENT_ID,
            "started_at_utc": utc_now(),
            "contract_sha256": contract_sha,
            "expected_documents": len(artifacts),
            "intro_sent": False,
            "document_actions": [],
            "finish_sent": False,
            "delivery_complete": False,
            "transport": "telegram_document_no_recompression",
            "manual_owner_review_required": False,
            "new_inference": False,
            "training_or_tuning": False,
            "production_eligible": False,
        }

    if receipt["intro_sent"] is not True:
        intro = (
            "<b>08-27 检测框 vs 模型实际训练图｜自动对照结果</b>\n"
            "43 个原始事件中，只有 #27 TAO LONG 与 #30 LIT LONG 保持完整训练语义；"
            "其余 41 个自动判为训练标准外。\n"
            "41 个失败输入均没有其他合格框位置，所以不是统一左右移动可以修复。\n"
            "以下全部按文件发送，不经图片压缩；不需要人工审核、打勾或改框。"
        )
        if not send_text(intro):
            raise DeliveryError("Telegram intro failed")
        receipt["intro_sent"] = True
        receipt["intro_sent_at_utc"] = utc_now()
        write_json(receipt_path, receipt)

    already_sent = {str(action["id"]) for action in receipt["document_actions"]}
    for item in artifacts:
        if item["id"] in already_sent:
            continue
        path = Path(item["path"])
        if not send_document(path, item["caption"]):
            receipt["last_error"] = f"document failed: {item['id']}"
            write_json(receipt_path, receipt)
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
        write_json(receipt_path, receipt)
        print(
            f"Telegram [{len(receipt['document_actions'])}/{len(artifacts)}] {path.name}",
            flush=True,
        )
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    if len(receipt["document_actions"]) != len(artifacts):
        raise DeliveryError("Telegram document ledger is incomplete")
    if receipt["finish_sent"] is not True:
        if not send_text(
            "<b>自动对照资料已全部发完。</b>\n"
            "总览 + 代表对照 + 43 张无损包 + HTML 报告，共 4 个文件。"
        ):
            raise DeliveryError("Telegram completion message failed")
        receipt["finish_sent"] = True
        receipt["finish_sent_at_utc"] = utc_now()
    receipt["delivery_complete"] = True
    receipt["completed_at_utc"] = utc_now()
    write_json(receipt_path, receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=RESULTS)
    parser.add_argument("--report", type=Path, default=REPORT)
    parser.add_argument("--archive", type=Path, default=ARCHIVE)
    parser.add_argument("--receipt", type=Path, default=RECEIPT)
    parser.add_argument("--sleep-seconds", type=float, default=1.1)
    args = parser.parse_args()
    payload = deliver(
        results=args.results.resolve(),
        report=args.report.resolve(),
        archive=args.archive.resolve(),
        receipt_path=args.receipt.resolve(),
        sleep_seconds=args.sleep_seconds,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
