import hashlib
import json
from pathlib import Path

import cv2
import numpy as np

import scripts.send_15m_ma_launch_owner_autofill50 as delivery


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture_pack(tmp_path: Path, monkeypatch) -> dict[str, Path]:
    monkeypatch.setattr(delivery, "ROOT", tmp_path)
    monkeypatch.setattr(delivery, "RESULTS", tmp_path)
    rows = []
    for order in range(1, 51):
        image_path = tmp_path / f"{order:02d}.png"
        image = np.full((742, 1280, 3), 255, dtype=np.uint8)
        cv2.rectangle(image, (600, 300), (800, 380), (45, 45, 232), 4)
        cv2.imwrite(str(image_path), image)
        rows.append(
            {
                "source_order": order,
                "sample_id": f"sample-{order:02d}",
                "symbol": f"COIN{order}_USDT_SWAP",
                "direction": "LONG" if order <= 25 else "SHORT",
                "core_start_offset": -6,
                "core_end_offset": -2,
                "core_bars": 5,
                "box": {"source_height_px": 80.0},
                "boxes_per_image": 1,
                "image_path": image_path.name,
                "image_sha256": digest(image_path),
                "yolo_label_path": None,
                "training_eligible": False,
                "production_eligible": False,
            }
        )
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    summary = tmp_path / "summary.json"
    summary.write_text(
        json.dumps(
            {
                "n_delivered": 50,
                "direction_counts": {"LONG": 25, "SHORT": 25},
                "boxes_per_image_min": 1,
                "boxes_per_image_max": 1,
                "manual_owner_review_workflow_created": False,
                "training_started": False,
                "yolo_labels_written": 0,
                "holdout_ohlcv_rows_materialized": 0,
            }
        ),
        encoding="utf-8",
    )
    return {
        "manifest": manifest,
        "summary": summary,
        "qa": tmp_path / "qa.json",
        "receipt": tmp_path / "tg.json",
        "report": tmp_path / "report.html",
    }


def test_qa_and_delivery_are_fully_boxed_without_owner_workflow(tmp_path: Path, monkeypatch) -> None:
    files = fixture_pack(tmp_path, monkeypatch)
    qa = delivery.run_visual_qa(files["manifest"], files["summary"], files["qa"])
    assert qa["red_box_images"] == 50
    texts, documents = [], []
    receipt = delivery.deliver_images(
        manifest=files["manifest"],
        summary_path=files["summary"],
        visual_qa_path=files["qa"],
        receipt_path=files["receipt"],
        sleep_seconds=0,
        send_text=lambda value: texts.append(value) or True,
        send_document=lambda path, caption: documents.append((path, caption)) or True,
    )
    assert len(documents) == 50
    assert len(texts) == 2
    assert receipt["images_complete"] is True
    assert "无需人工裁决" in documents[0][1]
