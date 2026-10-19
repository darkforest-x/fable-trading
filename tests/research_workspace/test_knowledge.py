"""Behavioral tests for the learning, knowledge and content REST contract."""
from __future__ import annotations

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
import pytest

from yoyo.research_workspace.api import same_origin
from yoyo.research_workspace.knowledge_api import install
from yoyo.research_workspace.store import WorkspaceStore


HEADERS = {"origin": "http://testserver", "sec-fetch-site": "same-origin"}
BASE = "/api/research"


class Catalog:
    def experiments(self):
        return [{"id": "exp-fixture-v1"}, {"experiment_id": "exp-fixture-v2"}]


@pytest.fixture
def workspace(tmp_path):
    runtime = tmp_path / "runtime"
    store = WorkspaceStore(runtime)
    app = FastAPI()
    api = APIRouter(prefix=BASE, dependencies=[Depends(same_origin)])
    install(api, app, store, Catalog())
    app.include_router(api)
    with TestClient(app) as client:
        yield client, runtime, store


def post(client, path, payload, code=201):
    response = client.post(BASE + path, json=payload, headers=HEADERS)
    assert response.status_code == code, response.text
    return response


def put(client, path, payload, code=200):
    response = client.put(BASE + path, json=payload, headers=HEADERS)
    assert response.status_code == code, response.text
    return response


def knowledge_payload(**overrides):
    payload = {
        "title": "Liquidity changes around the open",
        "topic": "market",
        "kind": "hypothesis",
        "status": "organized",
        "evidence_status": "unverified",
        "summary": "A concise working summary.",
        "body": "This is a bounded research hypothesis.",
        "source_urls": ["https://example.com/paper"],
        "evidence": "",
        "limitations": "Not tested against matched controls.",
        "experiment_ids": ["exp-fixture-v1"],
        "notion_url": "https://app.notion.com/p/3d98856479af80d9ad43f622dd7c3efd",
    }
    payload.update(overrides)
    return payload


def content_payload(**overrides):
    payload = {
        "title": "A research note for readers",
        "audience": "Independent traders",
        "angle": "Separate observation from evidence.",
        "body": "Master draft text.",
        "knowledge_refs": [],
        "variants": [],
    }
    payload.update(overrides)
    return payload


def test_hub_exposes_topics_platform_guidance_and_empty_counts(workspace):
    client, _, _ = workspace
    result = client.get(BASE + "/knowledge-hub").json()
    assert [item["id"] for item in result["topics"]] == [
        "market", "strategy", "risk", "data", "models", "methodology"
    ]
    assert [item["name"] for item in result["topics"]] == [
        "市场机制", "形态与策略", "风险与执行", "数据工程", "模型方法", "研究验证"
    ]
    assert {item["id"] for item in result["platforms"]} == {
        "zhihu", "wechat", "xiaohongshu", "x", "bilibili", "douyin", "youtube", "telegram", "custom"
    }
    assert all(item["guidance"] and item["format"] for item in result["platforms"])
    assert result["notion"] == {
        "hub": "https://app.notion.com/p/3d98856479af80d9ad43f622dd7c3efd",
        "resources": "https://app.notion.com/p/b4da71cf12cf4b58a00b85b8dce976d5",
        "notes": "https://app.notion.com/p/20dcfabc21bc4c59abec37afe89a2320",
    }
    assert result["counts"]["learning"] == 0
    assert result["counts"]["knowledge"] == 0
    assert result["counts"]["content"] == 0


def test_learning_versions_conflict_and_survive_store_reopen(workspace):
    client, runtime, store = workspace
    assert client.get(BASE + "/learning").json() == {"items": []}
    created = post(client, "/learning", {
        "title": "Review the failed entry hypothesis",
        "topic": "strategy",
        "question": "Which condition failed first?",
        "goal": "Find a falsifiable next question.",
        "stage": "learning",
        "source_urls": ["https://example.com/source"],
        "notes": "The first replay was inconclusive.",
        "retrieval_notes": "Search by symbol and date.",
        "next_review": "2026-10-01",
        "knowledge_ids": [],
    }).json()
    assert created["id"].startswith("learning-")
    assert created["revision"] == 1

    update = {
        "expected_revision": 1,
        "title": created["title"],
        "topic": created["topic"],
        "question": "Which condition failed first?",
        "goal": created["goal"],
        "stage": "review",
        "source_urls": created["source_urls"],
        "notes": "Added the matched-control question.",
        "retrieval_notes": created["retrieval_notes"],
        "next_review": "",
        "knowledge_ids": [],
    }
    updated = put(client, f"/learning/{created['id']}", update).json()
    assert updated["revision"] == 2
    assert put(client, f"/learning/{created['id']}", update, 409)
    assert client.get(BASE + "/learning/learning-0000000000000000").status_code == 404
    assert client.get(BASE + "/learning/not-an-id").status_code == 404

    reopened = WorkspaceStore(runtime)
    rows = reopened.notes("learning")
    assert rows[created["id"]]["revision"] == 2
    assert rows[created["id"]]["notes"] == "Added the matched-control question."
    detail = client.get(BASE + f"/learning/{created['id']}").json()
    assert [row["revision"] for row in detail["history"]] == [2, 1]
    assert store.jobs() == []


def test_knowledge_requires_evidence_and_limitations_for_conclusions(workspace):
    client, _, _ = workspace
    missing_both = post(client, "/knowledge", knowledge_payload(evidence_status="supported"), 422)
    assert "证据和适用限制" in missing_both.text
    missing_limit = post(client, "/knowledge", knowledge_payload(
        evidence_status="refuted", evidence="A preregistered check failed.", limitations=""), 422)
    assert "证据和适用限制" in missing_limit.text

    saved = post(client, "/knowledge", knowledge_payload(
        evidence_status="supported", evidence="Three registered checks agree.",
        limitations="Only the stated time range was checked." )).json()
    assert saved["evidence_status"] == "supported"
    assert saved["revision"] == 1
    assert saved["experiment_ids"] == ["exp-fixture-v1"]
    assert post(client, "/knowledge", knowledge_payload(experiment_ids=["not-registered"]), 400)
    assert post(client, "/knowledge", knowledge_payload(
        notion_url="https://evil.example/notion"), 422)
    assert post(client, "/knowledge", knowledge_payload(
        source_urls=["javascript:alert(1)"]), 422)
    assert post(client, "/knowledge", knowledge_payload(
        source_urls=["https://user:pass@example.com/secret"]), 422)
    assert post(client, "/knowledge", dict(knowledge_payload(), privileged=True), 422)


def test_content_freezes_requested_knowledge_revision_and_keeps_evidence_state(workspace):
    client, _, _ = workspace
    source = post(client, "/knowledge", knowledge_payload(
        title="Original title", evidence_status="refuted",
        evidence="The preregistered check did not reproduce the effect.",
        limitations="The test covered one market regime." )).json()
    payload = content_payload(
        knowledge_refs=[{"id": source["id"], "revision": 1}],
        variants=[{"platform": "x", "title": "Short thread", "body": "One claim and its evidence.",
                   "status": "draft"}],
    )
    content = post(client, "/content", payload).json()
    snapshot = content["knowledge_snapshots"][0]
    assert snapshot == {
        "id": source["id"],
        "revision": 1,
        "title": "Original title",
        "summary": "A concise working summary.",
        "body": "This is a bounded research hypothesis.",
        "evidence_status": "refuted",
        "evidence": "The preregistered check did not reproduce the effect.",
        "limitations": "The test covered one market regime.",
        "source_urls": ["https://example.com/paper"],
    }

    source_update = knowledge_payload(
        expected_revision=1, title="Revised title", evidence_status="testing",
        body="A later knowledge revision.", evidence="A later follow-up is underway.")
    assert put(client, f"/knowledge/{source['id']}", source_update).json()["revision"] == 2
    detail = client.get(BASE + f"/content/{content['id']}").json()
    assert detail["knowledge_snapshots"] == [snapshot]
    assert detail["history"][0]["knowledge_snapshots"] == [snapshot]
    assert detail["history"][0]["knowledge_snapshots"][0]["evidence_status"] == "refuted"


def test_content_rejects_bad_refs_urls_and_duplicate_platforms(workspace):
    client, _, _ = workspace
    missing = content_payload(knowledge_refs=[{"id": "knowledge-0000000000000000", "revision": 1}])
    assert post(client, "/content", missing, 400)

    source = post(client, "/knowledge", knowledge_payload()).json()
    absent_revision = content_payload(knowledge_refs=[{"id": source["id"], "revision": 9}])
    assert post(client, "/content", absent_revision, 409)
    duplicate = content_payload(variants=[
        {"platform": "wechat"}, {"platform": "wechat", "title": "Second"}
    ])
    assert post(client, "/content", duplicate, 422)
    duplicate_refs = content_payload(knowledge_refs=[
        {"id": source["id"], "revision": 1}, {"id": source["id"], "revision": 1}
    ])
    assert post(client, "/content", duplicate_refs, 422)
    bad_url = content_payload(variants=[{
        "platform": "custom", "status": "published", "body": "Done",
        "published_url": "https://user:secret@example.com/post",
        "published_at": "2026-09-24T10:00:00+08:00",
    }])
    assert post(client, "/content", bad_url, 422)


def test_manual_ready_published_receipts_and_markdown_export(workspace):
    client, _, _ = workspace
    assert post(client, "/content", content_payload(variants=[{
        "platform": "youtube", "status": "ready", "body": ""
    }]), 422)
    assert post(client, "/content", content_payload(variants=[{
        "platform": "youtube", "status": "published", "body": "Published body"
    }]), 422)
    assert post(client, "/content", content_payload(variants=[{
        "platform": "youtube", "status": "published", "body": "Published body",
        "published_url": "https://youtube.com/watch?v=abc", "published_at": "2026-09-24T10:00:00"
    }]), 422)

    source = post(client, "/knowledge", knowledge_payload(
        evidence_status="supported", evidence="A fixed check supports the mechanism.",
        limitations="The conclusion is limited to this sample." )).json()
    published = post(client, "/content", content_payload(
        knowledge_refs=[{"id": source["id"], "revision": 1}],
        variants=[{
            "platform": "youtube", "title": "The evidence, with limits", "body": "Manually published body.",
            "status": "published", "published_url": "https://youtube.com/watch?v=abc",
            "published_at": "2026-09-24T10:00:00+08:00",
        }, {"platform": "telegram", "status": "ready", "body": "Ready for manual review."}],
    )).json()
    assert published["variants"][0]["status"] == "published"
    assert published["variants"][1]["status"] == "ready"
    export = client.get(BASE + f"/content/{published['id']}/export")
    assert export.status_code == 200
    assert export.headers["content-type"].startswith("text/markdown")
    assert export.headers["content-disposition"] == f'attachment; filename="spike-{published["id"]}.md"'
    markdown = export.text
    assert "## 内容母稿" in markdown and "Master draft text." in markdown
    assert "### YouTube" in markdown and "### Telegram" in markdown
    assert "来源知识快照" in markdown
    assert "证据状态（保留来源原值）：有支持证据" in markdown
    assert "A fixed check supports the mechanism." in markdown
    assert "The conclusion is limited to this sample." in markdown
    assert "https://example.com/paper" in markdown
    assert "https://youtube.com/watch?v=abc" in markdown
    assert "2026-09-24T10:00:00+08:00" in markdown
    assert "导出不会发布内容" in markdown
    assert client.post(BASE + f"/content/{published['id']}/publish", headers=HEADERS).status_code == 404
