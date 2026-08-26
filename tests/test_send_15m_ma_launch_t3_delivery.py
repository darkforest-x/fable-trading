import hashlib
import json
from pathlib import Path

import pytest

from scripts.send_15m_ma_launch_t3_delivery import DeliveryError, deliver


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture_files(tmp_path: Path) -> dict[str, Path]:
    html = tmp_path / "report.html"
    preview = tmp_path / "preview.png"
    training = tmp_path / "training.json"
    preview_receipt = tmp_path / "preview.json"
    html.write_text("<html>ok</html>", encoding="utf-8")
    preview.write_bytes(b"png")
    training.write_text(
        json.dumps(
            {
                "production_eligible": False,
                "holdout_consumed": False,
                "best": {"epoch": 7, "map50": 0.8, "map50_95": 0.6},
                "best_model_final_validation": {"map50": 0.81, "map50_95": 0.61},
            }
        ),
        encoding="utf-8",
    )
    preview_receipt.write_text(
        json.dumps({"preview_sha256": digest(preview)}), encoding="utf-8"
    )
    return {
        "html": html,
        "preview": preview,
        "training_receipt": training,
        "preview_receipt": preview_receipt,
        "receipt": tmp_path / "delivery.json",
    }


def test_delivery_sends_photo_and_html_then_records_hashes(tmp_path: Path, monkeypatch) -> None:
    files = fixture_files(tmp_path)
    calls = []
    monkeypatch.setattr(
        "scripts.send_15m_ma_launch_t3_delivery.notify.send_photo",
        lambda path, caption: calls.append(("photo", path, caption)) or True,
    )
    monkeypatch.setattr(
        "scripts.send_15m_ma_launch_t3_delivery.notify.send_document",
        lambda path, caption: calls.append(("document", path, caption)) or True,
    )
    payload = deliver(**files)
    assert [call[0] for call in calls] == ["photo", "document"]
    assert payload["photo_sent"] and payload["document_sent"]
    assert payload["html_sha256"] == digest(files["html"])
    assert files["receipt"].is_file()


def test_delivery_fails_when_one_telegram_action_fails(tmp_path: Path, monkeypatch) -> None:
    files = fixture_files(tmp_path)
    monkeypatch.setattr(
        "scripts.send_15m_ma_launch_t3_delivery.notify.send_photo",
        lambda *args: True,
    )
    monkeypatch.setattr(
        "scripts.send_15m_ma_launch_t3_delivery.notify.send_document",
        lambda *args: False,
    )
    with pytest.raises(DeliveryError, match="delivery incomplete"):
        deliver(**files)
    assert not files["receipt"].exists()
