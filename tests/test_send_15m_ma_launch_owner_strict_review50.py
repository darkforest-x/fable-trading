import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

from scripts.send_15m_ma_launch_owner_strict_review50 import (
    DeliveryError,
    deliver_images,
    deliver_report,
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture_delivery(tmp_path: Path) -> dict[str, Path]:
    rows = []
    for order in range(1, 51):
        image = tmp_path / f"{order:02d}.png"
        Image.new("RGB", (1280, 742), color=(255, 255, 255)).save(image)
        active = order <= 20
        rows.append(
            {
                "source_order": order,
                "sample_id": f"sample-{order:02d}",
                "symbol": f"COIN{order}_USDT_SWAP",
                "direction": "LONG" if order <= 25 else "SHORT",
                "has_box_proposal": active,
                "core_start_offset": -7 if active else None,
                "core_end_offset": -3 if active else None,
                "core_to_model_right_gap_bars": 1 if active else None,
                "reason": "strict reject",
                "review_image_path": str(image),
                "review_image_sha256": digest(image),
                "status": "PASS" if active else "REJECT",
                "training_eligible": False,
                "production_eligible": False,
            }
        )
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )
    summary = tmp_path / "summary.json"
    summary.write_text(
        json.dumps(
            {
                "n_review_box_proposals": 20,
                "n_no_box_rows": 30,
                "training_started": False,
                "yolo_labels_written": 0,
                "training_eligible": False,
                "production_eligible": False,
            }
        ),
        encoding="utf-8",
    )
    visual_qa = tmp_path / "visual_qa.json"
    visual_qa.write_text(
        json.dumps({"pixel_dimensions": "1280x742", "yolo_label_files": 0}),
        encoding="utf-8",
    )
    report = tmp_path / "report.html"
    report.write_text("<html>ok</html>", encoding="utf-8")
    return {
        "manifest": manifest,
        "summary_path": summary,
        "visual_qa_path": visual_qa,
        "report": report,
        "receipt_path": tmp_path / "receipt.json",
    }


def test_sends_all_50_documents_then_report_with_hash_receipt(tmp_path: Path) -> None:
    files = fixture_delivery(tmp_path)
    texts = []
    documents = []
    receipt = deliver_images(
        manifest=files["manifest"],
        summary_path=files["summary_path"],
        visual_qa_path=files["visual_qa_path"],
        receipt_path=files["receipt_path"],
        sleep_seconds=0,
        send_text=lambda text: texts.append(text) or True,
        send_document=lambda path, caption: documents.append((path, caption)) or True,
    )
    assert len(texts) == 2
    assert len(documents) == 50
    assert receipt["images_complete"] is True
    assert [row["source_order"] for row in receipt["document_actions"]] == list(range(1, 51))
    report_docs = []
    final = deliver_report(
        report=files["report"],
        receipt_path=files["receipt_path"],
        send_document=lambda path, caption: report_docs.append((path, caption)) or True,
    )
    assert len(report_docs) == 1
    assert final["delivery_complete"] is True
    assert final["report_sha256"] == digest(files["report"])


def test_partial_failure_is_receipted_and_resume_skips_sent_rows(tmp_path: Path) -> None:
    files = fixture_delivery(tmp_path)
    attempts = []

    def fail_at_three(path: Path, caption: str) -> bool:
        attempts.append(path.name)
        return len(attempts) != 3

    with pytest.raises(DeliveryError, match="source order 03"):
        deliver_images(
            manifest=files["manifest"],
            summary_path=files["summary_path"],
            visual_qa_path=files["visual_qa_path"],
            receipt_path=files["receipt_path"],
            sleep_seconds=0,
            send_text=lambda _text: True,
            send_document=fail_at_three,
        )
    partial = json.loads(files["receipt_path"].read_text(encoding="utf-8"))
    assert [row["source_order"] for row in partial["document_actions"]] == [1, 2]
    resumed = []
    receipt = deliver_images(
        manifest=files["manifest"],
        summary_path=files["summary_path"],
        visual_qa_path=files["visual_qa_path"],
        receipt_path=files["receipt_path"],
        sleep_seconds=0,
        send_text=lambda _text: True,
        send_document=lambda path, caption: resumed.append(path.name) or True,
    )
    assert resumed[0] == "03.png"
    assert len(resumed) == 48
    assert receipt["images_complete"] is True


def test_report_refuses_before_all_images_complete(tmp_path: Path) -> None:
    files = fixture_delivery(tmp_path)
    files["receipt_path"].write_text(
        json.dumps({"images_complete": False, "document_actions": []}), encoding="utf-8"
    )
    with pytest.raises(DeliveryError, match="not complete"):
        deliver_report(
            report=files["report"],
            receipt_path=files["receipt_path"],
            send_document=lambda *_args: True,
        )
