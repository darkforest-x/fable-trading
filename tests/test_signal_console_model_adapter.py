"""Signal-console model boundary tests; no network and no heavy inference."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from yoyo.console import model_adapter
from yoyo.console.model_adapter import AdapterError, LatestResearchReplayAdapter
from yoyo.console.replay import (
    ADAPTER_ID,
    ReplayError,
    _chart_box_geometry,
    _replay_chart_payload,
    _selected_record,
    normalize_symbol,
    replay_image_output_path,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("eth", "ETH_USDT_SWAP"),
        ("SOL-USDT-SWAP", "SOL_USDT_SWAP"),
        ("link/usdt", "LINK_USDT_SWAP"),
    ],
)
def test_symbol_normalization_is_lenient_but_canonical(raw: str, expected: str) -> None:
    assert normalize_symbol(raw) == expected


def test_symbol_normalization_rejects_path_syntax() -> None:
    with pytest.raises(ReplayError, match="unsupported symbol"):
        normalize_symbol("../../models/owner_best.pt")


def test_side_selection_requires_exact_selected_feature_contract() -> None:
    selection = {
        "selections": {
            "long": {
                "selected_arm": "full_110",
                "feature_columns": ["alpha", "beta"],
                "arms": [
                    {
                        "arm": "full_110",
                        "feature_columns": ["alpha", "beta"],
                    }
                ],
            }
        }
    }
    assert _selected_record(selection, "long")["arm"] == "full_110"
    selection["selections"]["long"]["feature_columns"] = ["beta", "alpha"]
    with pytest.raises(ReplayError, match="feature list drifted"):
        _selected_record(selection, "long")


def test_replay_image_is_only_served_after_worker_materializes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FABLE_SIGNAL_CONSOLE_RUNTIME_DIR", str(tmp_path))
    with pytest.raises(ReplayError, match="not ready"):
        replay_image_output_path("ETH")
    expected = tmp_path / "ETH_USDT_SWAP.png"
    expected.write_bytes(b"png")
    assert replay_image_output_path("ETH") == expected


def test_unknown_adapter_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FABLE_SIGNAL_CONSOLE_ADAPTER", "arbitrary-import-path")
    with pytest.raises(AdapterError, match="unknown"):
        model_adapter.get_adapter()
    payload = model_adapter.adapter_status_payload()
    assert payload["ok"] is False


def test_subprocess_adapter_keeps_the_ui_contract_stable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker_result = {
        "adapter_id": ADAPTER_ID,
        "symbol": "ETH_USDT_SWAP",
        "detector": {"box_count": 1},
        "judgment": {"available": True, "passed": True},
    }
    completed = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout=json.dumps({"ok": True, "result": worker_result}),
        stderr="",
    )
    monkeypatch.setattr(model_adapter.subprocess, "run", lambda *args, **kwargs: completed)

    result = LatestResearchReplayAdapter().infer("eth")
    assert result["symbol"] == "ETH_USDT_SWAP"
    assert result["image_url"] == "/api/model/replay-image/ETH_USDT_SWAP"


def test_public_device_resolver_survives_the_legacy_star_forwarder() -> None:
    from src.judgment.yolo_candidates import resolve_predict_device as forwarded
    from yoyo.layers.l1_detection.candidates import resolve_predict_device as canonical

    assert forwarded is canonical


def test_replay_box_geometry_inverts_the_exact_render_transform() -> None:
    transform = SimpleNamespace(
        width=100,
        height=100,
        left=10,
        top=10,
        plot_w=80,
        plot_h=80,
        n_bars=5,
        price_min=0.0,
        price_max=100.0,
    )
    geometry = _chart_box_geometry({"xywhn": [0.5, 0.5, 0.4, 0.2]}, transform)

    assert geometry == pytest.approx(
        {
            "logical_from": 1.0,
            "logical_to": 3.0,
            "price_top": 62.5,
            "price_bottom": 37.5,
        }
    )


def test_replay_chart_payload_keeps_precomputed_six_ma_contract() -> None:
    frame = pd.DataFrame(
        {
            "open_time": pd.to_datetime(["2026-04-29T03:45:00Z", "2026-04-29T04:00:00Z"]),
            "open": [100.0, 101.0],
            "high": [102.0, 103.0],
            "low": [99.0, 100.0],
            "close": [101.0, 102.0],
            "volume": [10.0, 11.0],
            "sma20": [100.1, 100.2],
            "sma60": [100.3, 100.4],
            "sma120": [100.5, 100.6],
            "ema20": [100.7, 100.8],
            "ema60": [100.9, 101.0],
            "ema120": [101.1, 101.2],
        }
    )
    event = SimpleNamespace(
        window=frame,
        transform=SimpleNamespace(
            width=100,
            height=100,
            left=10,
            top=10,
            plot_w=80,
            plot_h=80,
            n_bars=2,
            price_min=95.0,
            price_max=105.0,
        ),
        row={"input_pixel_sha256": "abc123"},
    )
    payload = _replay_chart_payload(
        event,
        [{"xywhn": [0.5, 0.5, 0.2, 0.2], "confidence": 0.8, "class_id": 0, "class_name": "dense_long"}],
    )

    assert payload["window_bars"] == 2
    assert set(payload["moving_averages"]) == {
        "sma20", "sma60", "sma120", "ema20", "ema60", "ema120"
    }
    assert payload["moving_averages"]["sma120"][-1]["value"] == pytest.approx(100.6)
    assert payload["boxes"][0]["class_name"] == "dense_long"
    assert payload["input_pixel_sha256"] == "abc123"
