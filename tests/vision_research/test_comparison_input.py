"""Strict packet construction for replay modality comparisons."""
from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from yoyo.vision_research.comparison_input import (
    COLUMNS,
    COMPARISON_VERSION,
    build_market_packet,
    packet_sha256,
)
from yoyo.vision_research.server import create_app
from yoyo.vision_research.source import chart_sha256


TIMEFRAME = "15m"
DURATION_MS = 15 * 60_000
CURSOR_MS = 1_750_032_000_000


def observation() -> dict:
    candles = []
    for index in range(120):
        close = 100.0 + index / 10
        row = {
            "t": CURSOR_MS - (120 - index) * DURATION_MS,
            "o": close,
            "h": close + 1,
            "l": close - 1,
            "c": close + 0.1,
            "sma20": close - 0.2,
            "ema20": close - 0.1,
            "sma60": close - 0.4,
            "ema60": close - 0.3,
            "sma120": close - 0.6,
            "ema120": close - 0.5,
        }
        candles.append(row)
    digest = chart_sha256(candles)
    chart = {
        "candles": candles,
        "chart_sha256": digest,
        "provenance": {
            "symbol": "ETH-USDT-SWAP",
            "timeframe": TIMEFRAME,
            "time_boundary": "historical_replay",
            "observed_at_ms": CURSOR_MS,
            "visible_end_ms": CURSOR_MS,
            "visible_start_ms": candles[0]["t"],
            "last_bar_closed": True,
            "bar_count": 120,
        },
    }
    return {
        "id": "observation-id",
        "symbol": "ETH-USDT-SWAP",
        "timeframe": TIMEFRAME,
        "cursor_ms": CURSOR_MS,
        "chart_sha256": digest,
        "chart": chart,
        # These fields must never be copied into the market packet.
        "human": {"current_state": "launching"},
        "case": {"outcome": {"net_r": 99}},
        "rows": [{"future": True}],
        "volume": [123],
    }


def test_packet_is_fixed_to_the_frozen_whitelist_and_same_closed_120_bar_range():
    source = observation()
    packet = build_market_packet(source)

    assert packet["version"] == COMPARISON_VERSION
    assert packet["symbol"] == source["symbol"]
    assert packet["timeframe"] == source["timeframe"]
    assert packet["cursor_ms"] == source["cursor_ms"]
    assert packet["visible_start_ms"] == source["chart"]["candles"][0]["t"]
    assert packet["visible_end_ms"] == source["cursor_ms"]
    assert packet["bar_count"] == 120
    assert packet["last_bar_closed"] is True
    assert packet["chart_sha256"] == source["chart_sha256"]
    assert packet["columns"] == list(COLUMNS)
    assert len(packet["rows"]) == 120
    assert all(len(row) == 11 for row in packet["rows"])
    assert packet["rows"][0][0] == source["chart"]["candles"][0]["t"]
    assert packet["rows"][-1][0] + DURATION_MS == source["cursor_ms"]
    assert "human" not in packet and "case" not in packet and "volume" not in packet
    assert packet_sha256(packet) == packet_sha256(copy.deepcopy(packet))


@pytest.mark.parametrize("mutate", [
    lambda obs: obs.update(chart_sha256="0" * 64),
    lambda obs: obs["chart"]["provenance"].update(time_boundary="live_observation"),
    lambda obs: obs["chart"]["provenance"].update(visible_end_ms=obs["cursor_ms"] - 1),
    lambda obs: obs["chart"]["provenance"].update(last_bar_closed=False),
    lambda obs: obs["chart"]["candles"].pop(),
    lambda obs: obs["chart"]["candles"][50].update(volume=123),
    lambda obs: obs["chart"]["candles"][20].update(c=float("nan")),
    lambda obs: obs["chart"]["candles"][20].update(c=True),
    lambda obs: obs["chart"]["candles"][61].update(t=obs["chart"]["candles"][61]["t"] + 60_000),
    lambda obs: obs["chart"]["candles"][-1].update(c=999.0),
])
def test_invalid_provenance_rows_or_hash_are_rejected(mutate):
    source = observation()
    mutate(source)
    with pytest.raises(ValueError):
        build_market_packet(source)


def test_packet_digest_rejects_fields_or_mutated_rows():
    packet = build_market_packet(observation())
    with pytest.raises(ValueError, match="unexpected or missing"):
        packet_sha256({**packet, "future_result": {"net_r": 99}})

    mutated = copy.deepcopy(packet)
    mutated["rows"][-1][5] += 0.1
    with pytest.raises(ValueError, match="chart hash"):
        packet_sha256(mutated)


class EmptySource:
    def list_signals(self):
        return {"items": [], "warning": ""}

    def status(self):
        return {"available": True, "count": 0}


class ReplayHistory:
    def __init__(self):
        self.rows = []
        for index in range(350):
            close = 100 + index / 10
            row = {"t": CURSOR_MS - 200 * DURATION_MS + index * DURATION_MS,
                   "o": close, "h": close + 1, "l": close - 1, "c": close + 0.1}
            row.update({f"{kind}{period}": close - period / 100
                        for kind in ("sma", "ema") for period in (20, 60, 120)})
            self.rows.append(row)

    def load(self, symbol, timeframe, start_ms):
        return {"rows": self.rows, "cursor_index": 200, "first_cursor_index": 119,
                "duration_ms": DURATION_MS, "source_label": "fixture", "source_receipt": {}}


class UnusedProvider:
    def __init__(self, api_key, model):
        self.model = model

    def close(self):
        pass


def test_real_replay_freeze_route_produces_the_expected_packet_contract(tmp_path, monkeypatch):
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.delenv("BIGMODEL_API_KEY", raising=False)
    app = create_app(tmp_path, source=EmptySource(), provider_factory=UnusedProvider,
                     replay_history=ReplayHistory())
    with TestClient(app, base_url="http://127.0.0.1") as client:
        created = client.post("/api/replay/sessions", json={
            "symbol": "ETH-USDT-SWAP", "timeframe": "15m", "start_ms": CURSOR_MS,
        })
        assert created.status_code == 200, created.text
        session = created.json()
        frozen = client.post(f"/api/replay/sessions/{session['id']}/freeze", json={
            "expected_cursor_ms": session["cursor_ms"], "criteria": "按当前冻结输入分类，不评估未来。",
        })
        assert frozen.status_code == 200, frozen.text
        packet = build_market_packet(frozen.json())

    assert len(frozen.json()["chart"]["candles"]) == 120
    assert all(set(row) == set(COLUMNS) for row in frozen.json()["chart"]["candles"])
    assert packet["chart_sha256"] == frozen.json()["chart_sha256"]
    assert packet["chart_sha256"] == chart_sha256(frozen.json()["chart"]["candles"])
