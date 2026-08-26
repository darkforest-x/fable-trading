#!/usr/bin/env python3
"""Send the completed 15m t-3 HTML and rendered prediction sheet to Telegram.

The Owner requested the browser-ready report in the configured TG group.  This
entrypoint never reads or prints credentials; ``yoyo.notify`` owns that boundary.
It requires the final training and preview receipts, sends one preview photo and
one HTML document, then writes a local hash-bound delivery receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from yoyo import notify


ROOT = Path(__file__).resolve().parents[1]
RESULTS = (
    ROOT
    / "experiments"
    / "active"
    / "exp-15m-ma-launch-t3-yolo10000-v1"
    / "results"
)
DEFAULT_HTML = ROOT / "analysis" / "html" / "p1_15m_ma_launch_t3_yolo10000_20260826.html"
DEFAULT_PREVIEW = RESULTS / "validation_preview.png"
DEFAULT_TRAINING = RESULTS / "training_receipt.json"
DEFAULT_PREVIEW_RECEIPT = RESULTS / "validation_preview_receipt.json"
DEFAULT_RECEIPT = RESULTS / "telegram_delivery_receipt.json"


class DeliveryError(RuntimeError):
    """Fail-closed Telegram delivery error."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def deliver(
    *,
    html: Path,
    preview: Path,
    training_receipt: Path,
    preview_receipt: Path,
    receipt: Path,
) -> dict[str, Any]:
    """Validate final identities, send both artifacts and record success."""

    if receipt.exists():
        raise FileExistsError(f"refusing to overwrite Telegram receipt: {receipt}")
    for path in (html, preview, training_receipt, preview_receipt):
        if not path.is_file() or path.stat().st_size == 0:
            raise FileNotFoundError(path)
    training = json.loads(training_receipt.read_text(encoding="utf-8"))
    preview_meta = json.loads(preview_receipt.read_text(encoding="utf-8"))
    if training.get("production_eligible") is not False or training.get("holdout_consumed") is not False:
        raise DeliveryError("training receipt safety flags drifted")
    if sha256_file(preview) != preview_meta.get("preview_sha256"):
        raise DeliveryError("validation preview hash differs from its receipt")
    best_epoch = training["best"]
    best = training["best_model_final_validation"]
    caption = (
        "15m 六均线 t-3 弱标签 YOLO（研究用，未上线）\n"
        f"best epoch {best_epoch['epoch']} | mAP50 {best['map50']:.4f} | "
        f"mAP50-95 {best['map50_95']:.4f}\n"
        "图中黄框=GT，绿/红框=模型预测。"
    )
    photo_sent = bool(notify.send_photo(preview, caption))
    document_sent = bool(
        notify.send_document(
            html,
            "15m 六均线 t-3：10000 候选、36812 图数据集、3060 训练完整 HTML 报告",
        )
    )
    if not photo_sent or not document_sent:
        raise DeliveryError(
            f"Telegram delivery incomplete: photo={photo_sent} document={document_sent}"
        )
    payload = {
        "experiment_id": "exp-15m-ma-launch-t3-yolo10000-v1",
        "sent_at_utc": datetime.now(timezone.utc).isoformat(),
        "photo_sent": photo_sent,
        "document_sent": document_sent,
        "preview_path": str(preview),
        "preview_sha256": sha256_file(preview),
        "html_path": str(html),
        "html_sha256": sha256_file(html),
        "caption_best_epoch": int(best_epoch["epoch"]),
        "caption_map50": float(best["map50"]),
        "caption_map50_95": float(best["map50_95"]),
        "credentials_read_or_echoed_by_entrypoint": False,
        "holdout_consumed": False,
        "production_eligible": False,
    }
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--html", type=Path, default=DEFAULT_HTML)
    parser.add_argument("--preview", type=Path, default=DEFAULT_PREVIEW)
    parser.add_argument("--training-receipt", type=Path, default=DEFAULT_TRAINING)
    parser.add_argument("--preview-receipt", type=Path, default=DEFAULT_PREVIEW_RECEIPT)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    args = parser.parse_args()
    payload = deliver(
        html=args.html.resolve(),
        preview=args.preview.resolve(),
        training_receipt=args.training_receipt.resolve(),
        preview_receipt=args.preview_receipt.resolve(),
        receipt=args.receipt.resolve(),
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
