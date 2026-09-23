"""Behavioral contracts for causal replay, independent labels and one AI attempt."""
import copy
import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from yoyo.vision_research.server import create_app

DURATION = 900_000
START = 1_750_032_000_000 // DURATION * DURATION


class EmptySource:
    def list_signals(self):
        return {"items": [], "warning": ""}

    def status(self):
        return {"available": True, "count": 0}


class History:
    def __init__(self):
        self.rows = []
        for i in range(350):
            c = 100 + i / 10
            row = {"t": START + i * DURATION, "o": c, "h": c + 1, "l": c - 1, "c": c + .1}
            row.update({f"{kind}{n}": c - n / 100 for kind in ("sma", "ema") for n in (20, 60, 120)})
            self.rows.append(row)

    def catalog(self):
        return {"items": [{"symbol": "ETH-USDT-SWAP", "timeframe": "15m", "source_label": "test OHLC"}], "warning": ""}

    def coverage(self, symbol, timeframe):
        from yoyo.vision_research.replay_data import SourceError
        if symbol != "ETH-USDT-SWAP":
            raise SourceError("history_missing", "没有找到该交易对和周期的本地 OKX 历史数据")
        return {"first_close_ms": START + DURATION, "last_close_ms": START + 350 * DURATION,
                "segments": [{"first_close_ms": START + DURATION,
                              "first_replay_close_ms": START + 239 * DURATION,
                              "last_close_ms": START + 350 * DURATION}], "source_label": "test OHLC"}

    def load(self, symbol, timeframe, start_ms):
        return {"rows": copy.deepcopy(self.rows), "cursor_index": 200, "first_cursor_index": 119,
                "duration_ms": DURATION, "source_label": "test OHLC", "source_receipt": {"private_path": "/private/test"}}


class Provider:
    calls = []
    entered = None
    release = None
    failure = False

    def __init__(self, api_key, model):
        self.model = model

    def close(self):
        pass

    def analyze(self, **kwargs):
        self.calls.append(kwargs)
        if self.entered:
            self.entered.set()
            assert self.release.wait(5)
        if self.failure:
            raise RuntimeError("offline simulated failure")
        return {"decision": {"verdict": "match", "side": "long", "current_state": "launching",
                             "assessment_scope": "current_right_edge", "summary": "当前启动", "evidence": [], "risks": [], "box_2d": None},
                "model": self.model, "usage": {"total_tokens": 10}, "latency_ms": 1}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.delenv("BIGMODEL_API_KEY", raising=False)
    Provider.calls, Provider.entered, Provider.release, Provider.failure = [], None, None, False
    app = create_app(tmp_path, source=EmptySource(), provider_factory=Provider, replay_history=History())
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.post("/api/config", json={"api_key": "offline-replay-fixture-key-000"}).status_code == 200
        yield client


def session(client, mode="free"):
    response = client.post("/api/replay/sessions", json={"symbol": "ETH-USDT-SWAP", "timeframe": "15m", "mode": mode,
                                                       "start_ms": START + 201 * DURATION})
    assert response.status_code == 200, response.text
    return response.json()


def freeze(client, s, **kwargs):
    response = client.post(f'/api/replay/sessions/{s["id"]}/freeze', json={"expected_cursor_ms": s["cursor_ms"], **kwargs})
    assert response.status_code == 200, response.text
    return response.json()


def judge(client, obs):
    return client.post(f'/api/replay/observations/{obs["id"]}/judgment',
                       json={"current_state": "launching", "side": "long", "note": "independent"})


def move(client, s, steps):
    return client.post(f'/api/replay/sessions/{s["id"]}/move', json={"expected_cursor_ms": s["cursor_ms"], "steps": steps})


def analyze(client, obs):
    return client.post(f'/api/replay/observations/{obs["id"]}/analyze', json={})


def test_only_disclosed_prefix_and_public_metadata_reach_client(client):
    s = session(client)
    assert len(s["chart"]["candles"]) == 120
    assert s["chart"]["candles"][-1]["t"] + DURATION == s["cursor_ms"]
    assert "rows" not in s and "source_receipt" not in s and "/private" not in str(s)
    assert len(client.get("/api/replay/sessions").json()["items"]) == 1
    listing = client.get("/api/replay/sessions").json()["items"][0]
    assert "chart" not in listing
    assert s["chart"]["provenance"]["time_boundary"] == "historical_replay"


def test_coverage_error_is_readable_and_export_preserves_original_input(client):
    coverage = client.get('/api/replay/coverage', params={"symbol": "ETH-USDT-SWAP", "timeframe": "15m"})
    assert coverage.status_code == 200
    assert coverage.json()["segments"][0]["first_replay_close_ms"] == START + 239 * DURATION
    missing = client.get('/api/replay/coverage', params={"symbol": "missing", "timeframe": "15m"})
    assert missing.status_code == 409 and "本地" in missing.json()["detail"]
    obs = freeze(client, session(client))
    exported = client.get(f'/api/replay/observations/{obs["id"]}/export')
    assert exported.json()["image_sha256"] == obs["image_sha256"]
    assert exported.json()["chart"] == obs["chart"]
    assert "attachment" in exported.headers["content-disposition"]


def test_stale_moves_gap_boundary_and_blind_reverse_fail(client):
    s = session(client, "blind")
    assert move(client, s, -1).status_code == 409
    newer = move(client, s, 1).json()
    assert newer["cursor_ms"] == s["cursor_ms"] + DURATION
    assert move(client, s, 1).status_code == 409
    assert client.post(f'/api/replay/sessions/{s["id"]}/move', json={"expected_cursor_ms": newer["cursor_ms"], "steps": 200}).status_code == 400
    assert client.post(f'/api/replay/sessions/{s["id"]}/move', json={"expected_cursor_ms": newer["cursor_ms"], "target_ms": s["last_cursor_ms"] + DURATION}).status_code == 409


def test_freeze_dedup_exact_model_pixels_and_independent_label_gate(client):
    s = session(client, "blind")
    obs = freeze(client, s)
    assert freeze(client, s)["id"] == obs["id"]
    assert analyze(client, obs).status_code == 409 and not Provider.calls
    assert judge(client, obs).status_code == 200
    assert judge(client, obs).status_code == 200
    conflict = client.post(f'/api/replay/observations/{obs["id"]}/judgment',
                           json={"current_state": "no_setup", "side": "unknown"})
    assert conflict.status_code == 409
    result = analyze(client, obs).json()
    assert result["status"] == "completed"
    original = client.get(obs["image_url"]).content
    assert original == Provider.calls[0]["image"].data
    assert hashlib.sha256(original).hexdigest() == obs["image_sha256"]
    assert Provider.calls[0]["context"]["time_boundary"] == "historical_replay"
    assert analyze(client, obs).json()["run_id"] == result["run_id"]
    assert len(Provider.calls) == 1
    assert result["training_eligible"] is False and result["production_eligible"] is False


def test_future_exposure_cannot_be_relabelled_independent(client):
    s = session(client)
    obs = freeze(client, s)
    forward = move(client, s, 5).json()
    backward = move(client, forward, -5).json()
    assert judge(client, obs).status_code == 409
    assert backward["seen_until_ms"] == forward["cursor_ms"]
    newobs = freeze(client, backward, criteria="仅根据当前图中可见内容判断新的形态版本。")
    assert newobs["independence"] == "future_seen_in_session"
    assert judge(client, newobs).status_code == 409


def test_new_prompt_at_same_cursor_does_not_erase_ai_exposure(client):
    s = session(client)
    obs = freeze(client, s)
    assert analyze(client, obs).status_code == 200
    nextobs = freeze(client, s, criteria="另一版本规则，但这张图片已经见过 AI 判断。")
    assert nextobs["id"] != obs["id"]
    assert judge(client, nextobs).status_code == 409


def test_followup_is_append_only_and_never_changes_original_ai_input(client):
    s = session(client, "blind")
    obs = freeze(client, s)
    assert judge(client, obs).status_code == 200
    newer = move(client, s, 5).json()
    payload = {"expected_cursor_ms": newer["cursor_ms"], "note": "five bars later"}
    endpoint = f'/api/replay/observations/{obs["id"]}/followup'
    followed = client.post(endpoint, json=payload).json()
    assert followed["image_sha256"] == obs["image_sha256"]
    assert followed["chart"] == obs["chart"]
    assert followed["followups"][0]["bars_elapsed"] == 5
    assert followed["followups"][0]["meaning"] == "descriptive_price_change_not_trade_pnl"
    assert len(client.post(endpoint, json=payload).json()["followups"]) == 1
    assert analyze(client, obs).status_code == 200
    assert Provider.calls[0]["image"].sha256 == obs["image_sha256"]
    assert "five bars later" not in str(Provider.calls[0]["context"])


def test_mutating_future_does_not_change_snapshot_or_image(client):
    s = session(client)
    first = freeze(client, s)
    for row in client.app.state.replay.history.rows[201:]:
        for field in ("o", "h", "l", "c", "sma20", "sma60", "sma120", "ema20", "ema60", "ema120"):
            row[field] *= 999
    second = freeze(client, session(client))
    assert first["chart_sha256"] == second["chart_sha256"]
    assert first["image_sha256"] == second["image_sha256"]


def test_restore_and_failed_attempt_never_silently_retries(client, tmp_path):
    s = session(client)
    obs = freeze(client, s)
    Provider.failure = True
    assert analyze(client, obs).json()["status"] == "failed"
    with TestClient(create_app(tmp_path, source=EmptySource(), provider_factory=Provider, replay_history=History()),
                    base_url="http://127.0.0.1") as restored:
        assert restored.get(f'/api/replay/sessions/{s["id"]}').json()["chart"] == s["chart"]
        assert analyze(restored, obs).json()["status"] == "failed"
    assert len(Provider.calls) == 1


def test_concurrent_duplicate_analyze_claims_one_request(client):
    obs = freeze(client, session(client))
    Provider.entered, Provider.release = threading.Event(), threading.Event()
    with ThreadPoolExecutor(2) as executor:
        first = executor.submit(analyze, client, obs)
        assert Provider.entered.wait(3)
        try:
            second = analyze(client, obs)
            assert second.status_code == 200 and second.json()["status"] == "running"
        finally:
            Provider.release.set()
        assert first.result(timeout=5).json()["status"] == "completed"
    assert len(Provider.calls) == 1


def test_bad_body_and_unknown_session_are_explicit(client):
    assert client.get("/api/replay/sessions/unknown").status_code == 404
    assert client.post("/api/replay/sessions", json={"symbol": "x", "timeframe": "15m", "start_ms": True}).status_code == 400
    s = session(client)
    assert client.post(f'/api/replay/sessions/{s["id"]}/freeze', json={"expected_cursor_ms": s["cursor_ms"], "reference_revision": 99}).status_code == 409
