"""Offline provider switching: a saved key never follows a model to another host."""
import pytest
from fastapi.testclient import TestClient

from yoyo.vision_research.server import create_app
from test_app import EmptySource, image_url
from test_replay import History, freeze, session

ZKEY = "offline-zhipu-secret-key-0001"
QKEY = "sk-offline-qwen-beijing-key-0002"
SKEY = "sk-offline-qwen-singapore-key-0003"


class Client:
    instances = []

    def __init__(self, api_key, model, region="default"):
        self.key, self.model, self.region = api_key, model, region
        self.closed = False
        self.trace_callback = None
        self.instances.append(self)

    def close(self):
        self.closed = True

    def check_connection(self):
        if self.trace_callback:
            self.trace_callback({"request": {"body_text": "offline fixture"}, "response": {}, "http_status": 200})
        return {"ok": True, "model": self.model, "message": "offline"}

    def analyze(self, **kwargs):
        self.check_connection()
        return {"model": self.model, "decision": {"verdict": "uncertain", "side": "unknown",
                "current_state": "unclear", "assessment_scope": "current_right_edge", "summary": "offline",
                "evidence": [], "risks": [], "box_2d": None}, "usage": {}, "response_id": "offline-id"}

    analyze_comparison = analyze


@pytest.fixture
def app(tmp_path, monkeypatch):
    for key in ("ZHIPU_API_KEY", "BIGMODEL_API_KEY", "ZHIPU_MODEL", "DASHSCOPE_API_KEY", "DASHSCOPE_REGION", "QWEN_MODEL"):
        monkeypatch.delenv(key, raising=False)
    Client.instances = []
    return create_app(tmp_path, source=EmptySource(), provider_factory=Client,
                      qwen_factory=Client, replay_history=History())


def save(client, **payload):
    response = client.post("/api/config", json=payload)
    assert response.status_code == 200, response.text
    assert all(key not in response.text for key in (ZKEY, QKEY, SKEY))
    return response.json()


def test_keys_are_isolated_by_provider_and_region_and_survive_restart(app, tmp_path):
    with TestClient(app, base_url="http://127.0.0.1") as c:
        save(c, api_key=ZKEY)
        state = save(c, provider="qwen")
        assert state["model"] == "qwen3-vl-plus"
        assert state["api_key_configured"] is False
        assert c.post("/api/connection-test", json={}).status_code == 503
        assert not Client.instances
        save(c, api_key=QKEY)
        result = c.post("/api/connection-test", json={}).json()
        assert result["provider"] == "qwen" and result["provider_region"] == "beijing"
        assert Client.instances[-1].key == QKEY
        assert Client.instances[-1].closed
        assert save(c, region="singapore")["api_key_configured"] is False
        save(c, api_key=SKEY)
        c.post("/api/connection-test", json={})
        assert Client.instances[-1].key == SKEY
        assert save(c, region="beijing")["api_key_configured"] is True
        c.post("/api/connection-test", json={})
        assert Client.instances[-1].key == QKEY
        assert save(c, provider="zhipu")["api_key_configured"] is True
        c.post("/api/connection-test", json={})
        assert Client.instances[-1].key == ZKEY
    with TestClient(create_app(tmp_path, source=EmptySource(), provider_factory=Client, qwen_factory=Client),
                    base_url="http://127.0.0.1") as c:
        assert c.get("/api/status").json()["provider"] == "zhipu"
        save(c, provider="qwen", region="beijing")
        c.post("/api/connection-test", json={})
        assert Client.instances[-1].key == QKEY
    settings = tmp_path / "private/settings.json"
    assert settings.stat().st_mode & 0o777 == 0o600
    for path in tmp_path.rglob("*"):
        if path.is_file() and path != settings:
            assert all(key.encode() not in path.read_bytes() for key in (ZKEY, QKEY, SKEY))


def test_wrong_model_or_region_does_not_replace_config_and_runs_record_qwen(app):
    with TestClient(app, base_url="http://127.0.0.1") as c:
        save(c, provider="qwen", api_key=QKEY)
        for payload in ({"provider": "zhipu", "model": "qwen3-vl-plus"},
                        {"model": "qwen3-vl-max"}, {"region": "arbitrary-host"},
                        {"provider": "qwen", "region": "default"}):
            assert c.post("/api/config", json=payload).status_code == 400
        assert c.get("/api/status").json()["provider"] == "qwen"
        assert c.post("/api/analyze", json={"image_data_url": image_url(), "model": "glm-5.3-flash"}).status_code == 409
        assert not Client.instances
        run = c.post("/api/analyze", json={"image_data_url": image_url()}).json()
        assert run["status"] == "completed"
        assert run["provider"] == "qwen" and run["provider_region"] == "beijing"
        assert "qwen" in run["prompt_version"]
        exchange = c.get(f'/api/exchanges/{run["api_exchange_id"]}').json()
        assert exchange["provider"] == "qwen" and exchange["provider_region"] == "beijing"


def test_replay_freezes_provider_region_and_rejects_cross_region_reuse(app):
    with TestClient(app, base_url="http://127.0.0.1") as c:
        save(c, provider="qwen", api_key=QKEY)
        s = session(c)
        obs = freeze(c, s)
        assert obs["provider"] == "qwen" and obs["provider_region"] == "beijing"
        assert "qwen" in obs["prompt_version"]
        save(c, region="singapore", api_key=SKEY)
        different = freeze(c, s)
        assert different["id"] != obs["id"]
        assert c.post(f'/api/replay/observations/{obs["id"]}/analyze', json={}).status_code == 409
        assert c.get(f'/api/replay/observations/{obs["id"]}').json()["run_id"] is None
        save(c, region="beijing")
        assert c.post(f'/api/replay/observations/{obs["id"]}/analyze', json={}).status_code == 200
        updated = c.get(f'/api/replay/observations/{obs["id"]}').json()
        run = c.get(f'/api/runs/{updated["run_id"]}').json()
        assert run["provider"] == "qwen"
        assert run["provider_region"] == "beijing"


def test_environment_key_stays_in_its_declared_region(app, tmp_path, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", SKEY)
    monkeypatch.setenv("DASHSCOPE_REGION", "singapore")
    monkeypatch.setenv("ZHIPU_API_KEY", ZKEY)
    configured = create_app(tmp_path / "environment", source=EmptySource(),
                            provider_factory=Client, qwen_factory=Client)
    with TestClient(configured, base_url="http://127.0.0.1") as c:
        status = c.get("/api/status").json()
        assert (status["provider"], status["region"], status["credential_source"]) == ("qwen", "singapore", "environment")
        c.post("/api/connection-test", json={})
        assert Client.instances[-1].key == SKEY
        assert save(c, region="beijing")["api_key_configured"] is False
        count = len(Client.instances)
        assert c.post("/api/connection-test", json={}).status_code == 503
        assert len(Client.instances) == count
        save(c, provider="zhipu")
        c.post("/api/connection-test", json={})
        assert Client.instances[-1].key == ZKEY


@pytest.mark.parametrize("provider,region,model,key_env,env_model,key", [
    ("zhipu", "default", "glm-4.6v-flash", "ZHIPU_API_KEY", "ZHIPU_MODEL", ZKEY),
    ("qwen", "beijing", "qwen-vl-max", "DASHSCOPE_API_KEY", "QWEN_MODEL", QKEY),
])
def test_saved_empty_key_uses_same_profile_environment_key_and_preserves_model(
        app, tmp_path, monkeypatch, provider, region, model, key_env, env_model, key):
    with TestClient(app, base_url="http://127.0.0.1") as c:
        assert save(c, provider=provider, region=region, model=model)["api_key_configured"] is False
    monkeypatch.setenv(key_env, key)
    monkeypatch.setenv(env_model, "glm-5.3-flash" if provider == "zhipu" else "qwen3-vl-plus")
    configured = create_app(tmp_path, source=EmptySource(), provider_factory=Client, qwen_factory=Client)
    with TestClient(configured, base_url="http://127.0.0.1") as c:
        status = c.get("/api/status").json()
        assert status["api_key_configured"] and status["credential_source"] == "environment"
        assert status["model"] == model
        c.post("/api/connection-test", json={})
        assert Client.instances[-1].key == key


def test_comparison_keeps_frozen_provider_and_region_without_spending_on_mismatch(app):
    from test_comparison import analyze, prepare
    with TestClient(app, base_url="http://127.0.0.1") as c:
        save(c, provider="qwen", api_key=QKEY)
        obs = freeze(c, session(c))
        group = prepare(c, obs).json()["comparison"]
        assert group["provider"] == "qwen" and group["provider_region"] == "beijing"
        save(c, region="singapore", api_key=SKEY)
        assert analyze(c, obs, "text").status_code == 409
        group = c.get(f'/api/replay/observations/{obs["id"]}').json()["comparison"]
        assert not group["execution_order"]
        assert Client.instances[-1].closed
        save(c, region="beijing")
        result = analyze(c, obs, "text")
        assert result.status_code == 200, result.text
        arm = next(item for item in result.json()["comparison"]["arms"] if item["mode"] == "text")
        assert arm["status"] == "completed"
        assert arm["run"]["provider"] == "qwen" and arm["run"]["provider_region"] == "beijing"
