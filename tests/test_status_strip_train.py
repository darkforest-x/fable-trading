"""status_strip train sidecar is read-only and must not touch the train process."""
from __future__ import annotations

from pathlib import Path

from src.webapp import status_strip as ss


def test_parse_results_csv_epoch(tmp_path: Path) -> None:
    csv = tmp_path / "results.csv"
    csv.write_text(
        "epoch,time,train/box_loss\n"
        "1,1.0,2.0\n"
        "24,10.0,1.9\n"
        "25,11.0,1.8\n",
        encoding="utf-8",
    )
    assert ss._parse_results_epoch(csv) == 25


def test_v13_train_payload_shape(monkeypatch, tmp_path: Path) -> None:
    results = tmp_path / "results.csv"
    results.write_text("epoch\n3\n", encoding="utf-8")
    log = tmp_path / "train.log"
    log.write_text("ok\n", encoding="utf-8")
    monkeypatch.setattr(ss, "PROJECT", tmp_path)
    monkeypatch.setattr(ss, "V13_RESULTS_CSV", results)
    monkeypatch.setattr(ss, "V13_TRAIN_LOG", log)
    monkeypatch.setattr(ss, "V13_STABLE_PT", tmp_path / "missing.pt")
    monkeypatch.setattr(ss, "V13_MIDRUN_PT", tmp_path / "missing_best.pt")
    out = ss._v13_train()
    assert out["name"] == "owner_v13_pad200"
    assert out["epoch"] == 3
    assert out["epochs_target"] == 40
    assert "alive" in out
    assert "progress" in out


def test_status_strip_includes_train_and_debug_links() -> None:
    payload = ss.status_strip_payload()
    assert "train" in payload
    assert "freshness" in payload
    assert payload["freshness"]["gate_min"] == 30
    links = payload.get("debug_links") or []
    assert any("hardneg" in (x.get("id") or "") for x in links)
