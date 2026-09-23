"""Case selection must preserve outcomes, source identity and blind chronology."""
import copy
import hashlib
import json

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from yoyo.vision_research.replay_cases import (
    BUCKETS, PUBLIC_KEYS, V9, V9_PATH, V9_RAW, FrozenCaseHistory,
    HistoricalCaseCatalog, SourceError, in_bucket, normalize,
)
from yoyo.vision_research.server import create_app
from test_replay import (
    DURATION, START, EmptySource, History, Provider, analyze, freeze, judge, move,
)


def row(net_r=7):
    return dict(timeframe_min=15, available_at=pd.Timestamp(START + 201 * DURATION, unit="ms", tz="UTC").isoformat(),
                signal_bar_open=pd.Timestamp(START + 200 * DURATION, unit="ms", tz="UTC").isoformat(),
                event_key="test:200:1", venue="binance", symbol="ETHUSDT", side=1, valid_entry=True,
                censored=False, net_r=net_r, gross_r=net_r + .1, mfe_r=15, entry_time="2025-06-01T12:00:00Z",
                exit_time="2025-06-02T12:00:00Z", exit_reason="trailing_stop")


class Cases:
    def __init__(self):
        self.item = normalize(row(), V9, "a" * 64, "binance_15m_test")

    def get(self, identity):
        assert identity == self.item["id"]
        return copy.deepcopy(self.item)

    def list(self, dataset, bucket, symbol, timeframe, offset, limit):
        if bucket not in BUCKETS or offset < 0 or limit not in range(1, 101):
            raise SourceError("bad_query", "invalid query")
        return {"items": [{k: self.item[k] for k in PUBLIC_KEYS}], "counts": {"all": 1}, "total": 1}

    def load_history(self, case):
        return History().load(case["symbol"], case["timeframe"], case["signal_close_ms"])


@pytest.fixture
def cases_client(tmp_path):
    Provider.calls, Provider.failure, Provider.entered, Provider.release = [], False, None, None
    cases = Cases()
    app = create_app(tmp_path, source=EmptySource(), provider_factory=Provider, replay_history=History(), replay_cases=cases)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        client.post("/api/config", json={"api_key": "offline-replay-case-fixture-key"})
        yield client, cases, app


def create_case(client, cases, bucket="all", mode="blind"):
    r = client.post(f'/api/replay/cases/{cases.item["id"]}/sessions',
                    json={"mode": mode, "selection_bucket": bucket})
    assert r.status_code == 200, r.text
    return r.json()


def test_blind_case_omits_future_results_and_unlocks_only_after_judgment(cases_client):
    client, cases, _ = cases_client
    listing = client.get('/api/replay/cases').json()
    assert "net_r" not in json.dumps(listing) and "source_row" not in json.dumps(listing)
    s = create_case(client, cases)
    assert s["cursor_ms"] == cases.item["signal_close_ms"]
    assert s["case"]["outcome"] is None and not s["case"]["outcome_known"]
    obs = freeze(client, s)
    assert "net_r" not in json.dumps(obs) and "net_r" not in client.get(f'/api/replay/observations/{obs["id"]}/export').text
    reveal = f'/api/replay/sessions/{s["id"]}/outcome'
    assert client.post(reveal, json={}).status_code == 409
    assert judge(client, obs).status_code == 200
    image_hash = obs["image_sha256"]
    exposed = client.post(reveal, json={}).json()
    assert exposed["case"]["outcome"]["net_r"] == 7
    assert exposed["case"]["outcome"]["mfe_r"] == 15
    assert client.post(reveal, json={}).json() == exposed
    reread = client.get(f'/api/replay/observations/{obs["id"]}').json()
    assert reread["image_sha256"] == image_hash
    assert reread["independence"] == "before_ai_and_later_bars_in_this_session"
    assert reread["case"]["outcome"]["net_r"] == 7
    assert reread["case"]["outcome_revealed_at"]
    assert reread["case"]["ledger_sha256"] == "a" * 64
    assert reread["case"]["original_event_key"] == cases.item["native_key"]


def test_bucket_exposure_survives_switch_to_all_and_new_session(cases_client):
    client, cases, _ = cases_client
    assert client.get('/api/replay/cases', params={"bucket": "ge5"}).status_code == 200
    s = create_case(client, cases)
    assert s["mode"] == "free" and s["case"]["outcome_known"]
    assert s["case"]["outcome"] is None
    obs = freeze(client, s)
    assert obs["independence"] == "outcome_known_before_judgment"
    judged = judge(client, obs).json()
    assert judged["independence"] == "outcome_known_before_judgment"


def test_known_outcome_never_enters_model_input(cases_client):
    client, cases, _ = cases_client
    s = create_case(client, cases, bucket="ge5")
    assert client.post(f'/api/replay/sessions/{s["id"]}/outcome', json={}).status_code == 200
    obs = freeze(client, s)
    response = analyze(client, obs)
    assert response.status_code == 200 and response.json()["status"] == "completed"
    kwargs = Provider.calls[0]
    assert "net_r" not in str(kwargs["context"]) and "mfe_r" not in str(kwargs["context"])
    assert kwargs["criteria"] == obs["criteria"]
    assert set(kwargs) == {"image", "references", "criteria", "context"}
    assert kwargs["image"].sha256 == obs["image_sha256"]
    assert response.json()["training_eligible"] is False


def test_later_bars_prevent_restarting_same_case_as_fresh_blind(cases_client):
    client, cases, _ = cases_client
    s = create_case(client, cases)
    assert move(client, s, 1).status_code == 200
    assert create_case(client, cases)["mode"] == "free"


def test_exposure_after_freeze_and_restart_cannot_become_blind(cases_client):
    from yoyo.vision_research.replay import CaseSession, ReplayResearch
    client, cases, app = cases_client
    s = create_case(client, cases)
    obs = freeze(client, s)
    client.get('/api/replay/cases', params={"bucket": "ge5"})
    assert judge(client, obs).json()["independence"] == "outcome_known_before_judgment"
    restarted = ReplayResearch(app.state.replay.store, History(), cases)
    assert restarted.create_case(cases.item["id"], CaseSession())["mode"] == "free"


def test_same_market_event_shares_exposure_across_family_but_not_exchange():
    original = normalize(row(), V9, "sha", "stream")
    from yoyo.vision_research.replay_cases import V128
    raw = dict(row(), venue="binance_um", signal_close=row()["available_at"],
               trade_key="different-study", arm="joint")
    other = normalize(raw, V128, "other-sha", "other-stream")
    assert original["id"] != other["id"]
    assert original["exposure_key"] == other["exposure_key"]
    raw["venue"] = "okx"
    assert normalize(raw, V128, "sha", "stream")["exposure_key"] != other["exposure_key"]


def test_selection_and_paging_validation(cases_client):
    client, cases, _ = cases_client
    bad = client.post(f'/api/replay/cases/{cases.item["id"]}/sessions', json={"selection_bucket": "loss"})
    assert bad.status_code == 409
    for params in ({"bucket": "wrong"}, {"offset": -1}, {"limit": 10000}):
        assert client.get('/api/replay/cases', params=params).status_code == 409


def test_cumulative_realized_r_buckets_do_not_use_mfe_or_censored_r():
    case = normalize(row(-1), V9, "sha", "stream")
    assert in_bucket(case, "loss") and not in_bucket(case, "ge3")
    for r, bucket in ((3, "ge3"), (5, "ge5"), (10.01, "gt10")):
        assert in_bucket(normalize(row(r), V9, "sha", "stream"), bucket)
    assert not in_bucket(normalize(row(10), V9, "sha", "stream"), "gt10")
    raw = row(100); raw["censored"] = True
    case = normalize(raw, V9, "sha", "stream")
    assert case["outcome"]["net_r"] is None and in_bucket(case, "open")
    assert not in_bucket(case, "ge3")
    raw = row(float('nan'))
    with pytest.raises(SourceError):
        normalize(raw, V9, "sha", "stream")


def test_frozen_ohlc_adaptor_is_causal_and_uses_signal_close(tmp_path):
    index = pd.date_range('2025-01-01', periods=400, freq='15min', tz='UTC')
    frame = pd.DataFrame({'open': 100., 'high': 102., 'low': 99., 'close': 101.}, index=index)
    cutoff = int(index[300].value // 1_000_000) + DURATION
    path = tmp_path / 'source.csv'
    before = FrozenCaseHistory(frame, 15, path, 'a' * 64).load('ETHUSDT', '15m', cutoff)
    changed = frame.copy(); changed.iloc[301:] *= 10
    after = FrozenCaseHistory(changed, 15, path, 'b' * 64).load('ETHUSDT', '15m', cutoff)
    i = before['cursor_index']
    assert before['rows'][:i + 1] == after['rows'][:i + 1]
    assert before['rows'][i]['t'] + DURATION == cutoff


def test_catalog_rejects_altered_ledger_and_source_before_loading_pickle(tmp_path):
    folder = tmp_path / V9_PATH
    folder.mkdir(parents=True)
    data = pd.DataFrame([dict(row(), stream_key='test')]).to_csv(index=False).encode()
    import gzip
    raw = gzip.compress(data)
    (folder / 'candidates.csv.gz').write_bytes(raw)
    manifest = {'complete': True, 'candidate_sha256': hashlib.sha256(raw).hexdigest(), 'candidates': 1,
                'stream_completion_sha256': {}}
    (folder / 'manifest.json').write_text(json.dumps(manifest))
    c = HistoricalCaseCatalog(tmp_path)
    case = c._load(V9)[0]
    assert case['outcome']['net_r'] == 7
    (folder / 'candidates.csv.gz').write_bytes(raw + b'changed')
    with pytest.raises(SourceError):
        HistoricalCaseCatalog(tmp_path)._load(V9)
    with pytest.raises(SourceError):
        c.load_history(case)
