"""Replay input arms must share one frozen market and never retry paid attempts."""
import copy
import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from yoyo.vision_research.server import create_app
from yoyo.vision_research.store import ResearchStore
from test_replay import EmptySource, History, Provider, freeze, judge, move, session


class ComparisonProvider(Provider):
    def analyze_comparison(self, **kwargs):
        return self.analyze(**kwargs)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.delenv("BIGMODEL_API_KEY", raising=False)
    ComparisonProvider.calls = []
    ComparisonProvider.entered = ComparisonProvider.release = None
    ComparisonProvider.failure = False
    app = create_app(tmp_path, source=EmptySource(), provider_factory=ComparisonProvider, replay_history=History())
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.post("/api/config", json={"api_key": "offline-comparison-fixture-key"}).status_code == 200
        yield client


def prepare(client, obs):
    return client.post(f'/api/replay/observations/{obs["id"]}/comparison', json={})


def analyze(client, obs, mode):
    return client.post(f'/api/replay/observations/{obs["id"]}/comparison/{mode}/analyze', json={})


def get(client, obs):
    return client.get(f'/api/replay/observations/{obs["id"]}').json()


def test_prepare_is_free_idempotent_and_export_has_exact_frozen_rows(client):
    obs = freeze(client, session(client))
    assert obs["comparison"] is None
    response = prepare(client, obs)
    assert response.status_code == 200, response.text
    group = response.json()["comparison"]
    packet = group["market_packet"]
    assert packet["bar_count"] == 120
    assert packet["rows"] == [[row[k] for k in packet["columns"]] for row in obs["chart"]["candles"]]
    assert group["input_window"]["end_ms"] == obs["cursor_ms"]
    assert group["image_sha256"] == obs["image_sha256"]
    assert group["criteria_sha256"] == hashlib.sha256(obs["criteria"].encode()).hexdigest()
    assert group["reference_policy"] == "none"
    assert sorted(group["recommended_order"]) == ["hybrid", "text", "vision"]
    assert not group["execution_order"] and group["agreement"] is None
    assert all(a["status"] == "pending" for a in group["arms"])
    assert prepare(client, obs).json()["comparison"] == group
    export = client.get(f'/api/replay/observations/{obs["id"]}/comparison/export')
    assert "attachment" in export.headers["content-disposition"]
    assert export.json()["comparison"] == group
    assert not ComparisonProvider.calls
    assert judge(client, obs).status_code == 200  # Preparing data is not AI exposure.


def test_three_arms_share_snapshot_model_rules_and_never_other_replies(client):
    s = session(client, "blind")
    obs = freeze(client, s)
    prepare(client, obs)
    assert analyze(client, obs, "vision").status_code == 409
    assert judge(client, obs).status_code == 200
    original_human = get(client, obs)["human"]
    client.post("/api/config", json={"model": "glm-4.6v-flash"})
    moved = move(client, s, 5).json()
    client.post(f'/api/replay/observations/{obs["id"]}/followup',
                json={"expected_cursor_ms": moved["cursor_ms"], "note": "future outcome must stay hidden"})
    for mode in ("text", "hybrid", "vision"):
        result = analyze(client, obs, mode)
        assert result.status_code == 200, result.text
        arm = next(a for a in result.json()["comparison"]["arms"] if a["mode"] == mode)
        assert arm["status"] == "completed"
        assert analyze(client, obs, mode).json()["comparison"]["arms"] == result.json()["comparison"]["arms"]
    value = get(client, obs)
    group = value["comparison"]
    assert group["agreement"] is True and group["completed_count"] == 3
    assert group["execution_order"] == ["text", "hybrid", "vision"]
    assert value["run_id"] is None and value["run"] is None and value["comparison_requested"] is True
    assert value["human"] == original_human
    assert value["independence"] == "before_ai_and_later_bars_in_this_session"
    assert len(ComparisonProvider.calls) == 3
    for call, mode in zip(ComparisonProvider.calls, group["execution_order"]):
        assert set(call) == {"input_mode", "image", "market_packet", "criteria", "context"}
        assert call["input_mode"] == mode
        assert call["market_packet"] == group["market_packet"]
        assert call["criteria"] == obs["criteria"]
        assert call["context"] == group["context"]
        assert "future outcome" not in str(call) and "independent" not in str(call)
        assert (call["image"] is None) == (mode == "text")
        if call["image"]:
            assert call["image"].data == client.get(obs["image_url"]).content
    assert all(a["run"]["model"] == obs["model"] and a["run"]["references"] == [] for a in group["arms"])
    assert group["returned_models_consistent"] is True


def test_unknown_unprepared_and_extra_parameters_do_not_call_provider(client):
    obs = freeze(client, session(client))
    assert analyze(client, obs, "text").status_code == 409
    assert analyze(client, obs, "unknown").status_code == 409
    assert client.get(f'/api/replay/observations/{obs["id"]}/comparison/export').status_code == 409
    assert client.post(f'/api/replay/observations/{obs["id"]}/comparison', json={"future": 1}).status_code == 400
    assert not ComparisonProvider.calls


def test_comparison_exposure_survives_new_prompt_and_keeps_followup_usable(client):
    s = session(client)
    obs = freeze(client, s)
    prepare(client, obs)
    assert analyze(client, obs, "text").status_code == 200
    assert judge(client, obs).status_code == 409
    other = freeze(client, s, criteria="New rule at the same already disclosed decision point")
    assert judge(client, other).status_code == 409
    summaries = client.get(f'/api/replay/sessions/{s["id"]}').json()["observations"]
    assert next(item for item in summaries if item["id"] == obs["id"])["comparison_requested"] is True
    assert next(item for item in summaries if item["id"] == other["id"])["comparison_requested"] is False
    newer = move(client, s, 1).json()
    response = client.post(f'/api/replay/observations/{obs["id"]}/followup',
                           json={"expected_cursor_ms": newer["cursor_ms"], "note": "later"})
    assert response.status_code == 200 and len(response.json()["followups"]) == 1


@pytest.mark.parametrize("field", ["market_packet", "context", "model", "criteria", "criteria_sha256", "image_url", "protocol_version"])
def test_changed_frozen_contract_is_rejected_without_spend(client, field):
    obs = freeze(client, session(client))
    prepare(client, obs)
    comparison = client.app.state.replay.comparisons
    group = comparison._get(obs["id"])
    if field == "market_packet":
        group[field]["rows"][-1][4] += 1
    elif field == "context":
        group[field]["future_profit"] = "10R"
    else:
        group[field] = "tampered"
    comparison._save(group)
    response = analyze(client, obs, "text")
    assert response.status_code == 409, response.text
    assert not ComparisonProvider.calls
    assert not get(client, obs)["comparison_requested"]


def test_even_text_requires_original_paired_image(client):
    obs = freeze(client, session(client))
    prepare(client, obs)
    client.app.state.replay.store.image_path(obs["image_sha256"] + ".png").unlink()
    assert analyze(client, obs, "text").status_code == 409
    assert not ComparisonProvider.calls


def test_duplicate_and_busy_arms_never_issue_duplicate_requests(client):
    obs = freeze(client, session(client))
    prepare(client, obs)
    ComparisonProvider.entered, ComparisonProvider.release = threading.Event(), threading.Event()
    with ThreadPoolExecutor(max_workers=2) as pool:
        running = pool.submit(analyze, client, obs, "text")
        try:
            assert ComparisonProvider.entered.wait(3)
            duplicate = analyze(client, obs, "text")
            assert duplicate.status_code == 200
            assert duplicate.json()["comparison"]["arms"][1]["status"] == "running"
            other = analyze(client, obs, "vision")
            assert other.status_code == 409
            assert get(client, obs)["comparison"]["arms"][0]["status"] == "pending"
        finally:
            ComparisonProvider.release.set()
        assert running.result().status_code == 200
    assert len(ComparisonProvider.calls) == 1


def test_failure_and_restart_are_durable_not_no_match_or_retry(client):
    obs = freeze(client, session(client))
    prepare(client, obs)
    ComparisonProvider.failure = True
    failed = analyze(client, obs, "hybrid").json()["comparison"]
    arm = failed["arms"][2]
    assert arm["status"] == "failed" and arm["run"]["decision"] is None
    assert failed["agreement"] is None and failed["completed_count"] == 0
    assert analyze(client, obs, "hybrid").status_code == 200
    assert len(ComparisonProvider.calls) == 1
    store = client.app.state.replay.store
    run = store.get(arm["run_id"])
    run.update(status="running", error=None)
    store.save(run)
    ResearchStore(store.root)  # Recovery after a request whose outcome was lost.
    assert get(client, obs)["comparison"]["arms"][2]["status"] == "interrupted"
    assert analyze(client, obs, "hybrid").status_code == 200
    assert len(ComparisonProvider.calls) == 1


def test_original_analysis_is_independent_and_input_hash_does_not_follow_future(client):
    s = session(client)
    obs = freeze(client, s)
    ordinary = client.post(f'/api/replay/observations/{obs["id"]}/analyze', json={}).json()
    prepared = prepare(client, obs).json()["comparison"]
    client.app.state.replay.history.rows[-1]["c"] *= 100
    result = analyze(client, obs, "text").json()
    assert result["run_id"] == ordinary["run_id"] and result["run"] == ordinary["run"]
    assert result["comparison"]["data_sha256"] == prepared["data_sha256"]
    assert result["comparison"]["market_packet"] == prepared["market_packet"]
    call = ComparisonProvider.calls[-1]
    assert ordinary["run"]["decision"]["summary"] not in str(call["context"])


def test_disagreement_and_returned_model_difference_are_explicit(client):
    obs = freeze(client, session(client))
    prepare(client, obs)
    for mode in ("vision", "text", "hybrid"):
        assert analyze(client, obs, mode).status_code == 200
    store = client.app.state.replay.store
    run = copy.deepcopy(get(client, obs)["comparison"]["arms"][1]["run"])
    run.update(model="provider-different-model")
    run["decision"].update(verdict="no_match", current_state="no_setup")
    store.save(run)
    group = get(client, obs)["comparison"]
    assert group["agreement"] is False and group["returned_models_consistent"] is False
