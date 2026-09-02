import csv
import hashlib
import json
from pathlib import Path

import pytest

import scripts.send_15m_ashare_standard_retail_to_telegram as sender
from scripts.send_15m_ashare_standard_retail_to_telegram import (
    AshareTelegramDeliveryError,
    deliver,
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture_delivery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    rows = []
    ranks = sender.EXPECTED_ORIGINAL_RANKS
    for order, original_rank in enumerate(ranks, 1):
        chart = tmp_path / "charts" / f"{original_rank:03d}.png"
        chart.parent.mkdir(exist_ok=True)
        chart.write_bytes(b"\x89PNG\r\n\x1a\n" + f"chart-{order}".encode())
        direction = "LONG" if order == 11 else "SHORT"
        rows.append(
            {
                "original_rank": original_rank,
                "code": f"{order:06d}",
                "name": f"示例{order}",
                "window_end_time": "2026-09-02T03:15:00+00:00",
                "direction": direction,
                "confidence": 0.9 - order / 100,
                "chart_sha256": digest(chart),
                "board": "SH_MAIN" if order % 2 else "SZ_MAIN",
                "retail_eligible": True,
                "filtered_chart": f"charts/{chart.name}",
            }
        )
    signals = tmp_path / "signals.csv"
    with signals.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary = tmp_path / "summary.json"
    summary.write_text(
        json.dumps(
            {
                "protocol": "ashare_standard_retail_mainboard_delivery_filter_v1",
                "retained_events": 18,
                "retained_long": 1,
                "retained_short": 17,
                "model_inference": False,
                "network_reads": 0,
                "production_eligible": False,
                "tradability_proven": False,
            }
        ),
        encoding="utf-8",
    )
    verification = tmp_path / "verification.json"
    verification.write_text(
        json.dumps({"passed": True, "retained_events": 18, "chart_sha_checks": 18}),
        encoding="utf-8",
    )
    monkeypatch.setattr(sender, "EXPECTED_SIGNALS_SHA256", digest(signals))
    monkeypatch.setattr(sender, "EXPECTED_SUMMARY_SHA256", digest(summary))
    monkeypatch.setattr(sender, "EXPECTED_VERIFICATION_SHA256", digest(verification))
    return tmp_path, tmp_path / "receipt.json"


def test_sends_all_original_png_documents_and_writes_complete_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    results, receipt_path = fixture_delivery(tmp_path, monkeypatch)
    texts: list[str] = []
    documents: list[tuple[Path, str]] = []
    receipt = deliver(
        results=results,
        receipt_path=receipt_path,
        sleep_seconds=0,
        send_text=lambda value: texts.append(value) or True,
        send_document=lambda path, caption: documents.append((path, caption)) or True,
    )
    assert len(texts) == 2
    assert len(documents) == 18
    assert all(path.suffix == ".png" for path, _caption in documents)
    assert "不压缩" in documents[0][1]
    assert "不等于可融券卖空" in documents[0][1]
    assert receipt["delivery_complete"] is True
    assert receipt["transport"] == "telegram_sendDocument_original_png_no_recompression"
    assert [
        row["original_rank"] for row in receipt["document_actions"]
    ] == sender.EXPECTED_ORIGINAL_RANKS


def test_partial_failure_resumes_without_resending_receipted_documents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    results, receipt_path = fixture_delivery(tmp_path, monkeypatch)
    attempts: list[str] = []

    def fail_at_four(path: Path, _caption: str) -> bool:
        attempts.append(path.name)
        return len(attempts) != 4

    with pytest.raises(AshareTelegramDeliveryError, match="signal_04"):
        deliver(
            results=results,
            receipt_path=receipt_path,
            sleep_seconds=0,
            send_text=lambda _value: True,
            send_document=fail_at_four,
        )
    partial = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert [row["id"] for row in partial["document_actions"]] == [
        "signal_01",
        "signal_02",
        "signal_03",
    ]
    resumed: list[str] = []
    final = deliver(
        results=results,
        receipt_path=receipt_path,
        sleep_seconds=0,
        send_text=lambda _value: True,
        send_document=lambda path, _caption: resumed.append(path.name) or True,
    )
    assert resumed[0] == "007.png"
    assert len(resumed) == 15
    assert final["delivery_complete"] is True


def test_refuses_drifted_chart_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    results, _receipt_path = fixture_delivery(tmp_path, monkeypatch)
    (results / "charts" / "002.png").write_bytes(b"not-the-frozen-png")
    with pytest.raises(AshareTelegramDeliveryError, match="chart is not PNG|chart hash drifted"):
        sender.build_contract(results)


def test_complete_receipt_refuses_duplicate_delivery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    results, receipt_path = fixture_delivery(tmp_path, monkeypatch)
    kwargs = {
        "results": results,
        "receipt_path": receipt_path,
        "sleep_seconds": 0,
        "send_text": lambda _value: True,
        "send_document": lambda _path, _caption: True,
    }
    deliver(**kwargs)
    with pytest.raises(AshareTelegramDeliveryError, match="already complete"):
        deliver(**kwargs)
