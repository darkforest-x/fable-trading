"""View-only mutations, exact filtering and Owner-work preservation in memory."""
from copy import deepcopy
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from yoyo.datasets import label_studio_review_views as views


@pytest.fixture
def server(tmp_path, monkeypatch):
    manifest, tasks_file, imported, queue_file, output = (tmp_path/name for name in ("manifest.jsonl", "tasks.json", "import.json", "queue.json", "receipt.json"))
    manifest.write_text("frozen source manifest fixture")
    monkeypatch.setattr(views, "MANIFEST_SHA", views.digest(manifest))
    tasks = [{"data": {"review_id": f"r{i}", "protocol_id": views.SOURCE_PROTOCOL, "image": f"/image{i}.png"}} for i in range(5)]
    tasks_file.write_text(json.dumps(tasks))
    monkeypatch.setattr(views, "TASKS_SHA", views.digest(tasks_file))
    mapping = {f"r{i}": 100+i for i in range(5)}
    imported.write_text(json.dumps({"project_id": 77, "protocol_id": views.SOURCE_PROTOCOL, "task_ids": mapping}))
    queue = {"schema_version": 1, "protocol_id": views.PROTOCOL, "source_manifest_sha256": views.MANIFEST_SHA,
        "views": [{"key": "priority", "title": "先审：重点复核", "review_ids": ["r3", "r0"]},
                  {"key": "empty", "title": "当前无命中", "review_ids": []}]}
    rows = [{"id": mapping[t["data"]["review_id"]], "data": deepcopy(t["data"])} for t in tasks]
    state = SimpleNamespace(queue=queue, queue_file=queue_file, output=output, calls=[], rows=rows,
        automatic_predictions=False, bad_filter=False, extra_after_create=False,
        annotations={100: [{"id": 51, "result": "owner answer"}]}, drafts={100: [{"id": 52}]},
        predictions={i: [{"id": i+500}] for i in mapping.values()},
        stored=[{"id": 44, "project": 77, "order": 0, "data": {"title": "Default", "filters": {"conjunction": "and", "items": []}}}])
    state.write = lambda: queue_file.write_text(json.dumps(state.queue, ensure_ascii=False))
    state.write()

    def api(_session, method, path, payload=None):
        state.calls.append((method, path, deepcopy(payload)))
        parsed, query = urlsplit(path), parse_qs(urlsplit(path).query)
        if method == "GET" and parsed.path == "/api/projects/77":
            return {"id": 77, "evaluate_predictions_automatically": state.automatic_predictions}
        if method == "GET" and parsed.path == "/api/dm/views":
            assert query["project"] == ["77"]
            return deepcopy(state.stored)
        if method == "POST" and path == "/api/dm/views/":
            assert payload["project"] == 77
            view = {"id": 44+len(state.stored), **deepcopy(payload)}
            state.stored.append(view)
            return deepcopy(view)
        if method == "GET" and parsed.path.startswith("/api/dm/views/"):
            return deepcopy(next(v for v in state.stored if v["id"] == int(parsed.path.rstrip("/").rsplit("/", 1)[1])))
        if method == "GET" and parsed.path == "/api/tasks":
            assert query["project"] == ["77"] and query["fields"] == ["task_only"]
            assert query["include"][0] in ("id", "id,data")
            rows = deepcopy(state.rows)
            if "view" in query:
                data = next(v["data"] for v in state.stored if v["id"] == int(query["view"][0]))
            elif "query" in query:
                data = json.loads(query["query"][0])
            else:
                data = None
            if data is not None:
                f = data["filters"]
                assert f["conjunction"] == "and" and len(f["items"]) == 1
                item = f["items"][0]
                assert (item["filter"], item["operator"], item["type"]) == ("filter:tasks:id", "in_list", "Number")
                ids = set(item["value"])
                rows = [r for r in rows if r["id"] in ids]
                if state.bad_filter or (state.extra_after_create and "view" in query):
                    rows.append({"id": 999, "data": {}})
            if query["include"] == ["id"]:
                rows = [{"id": r["id"]} for r in rows]
            page, size = int(query["page"][0]), int(query["page_size"][0])
            return {"total": len(rows), "tasks": rows[(page-1)*size:page*size]}
        raise AssertionError(f"Forbidden API endpoint: {method} {path}")

    monkeypatch.setattr(views.base, "api", api)
    monkeypatch.setattr(views.base, "session", lambda: object())
    monkeypatch.setattr(views, "source_identity", lambda: {"source_commit": "fixture", "code_sha256": {}})
    state.run = lambda: views.create_review_views(queue_file, output, import_receipt=imported, tasks_file=tasks_file, source_manifest=manifest)
    return state


def test_views_are_exact_idempotent_and_preserve_all_owner_work(server):
    untouched = deepcopy((server.rows, server.annotations, server.drafts, server.predictions, server.stored[0]))
    first, second = server.run(), server.run()
    assert (first["created"], first["reused"], second["created"], second["reused"]) == (2, 0, 0, 2)
    assert first["views"][0]["task_ids"] == [100, 103] and first["views"][1]["task_ids"] == []
    assert (server.rows, server.annotations, server.drafts, server.predictions, server.stored[0]) == untouched
    writes = [(method, path) for method, path, _ in server.calls if method != "GET"]
    assert writes == [("POST", "/api/dm/views/")]*2
    assert first["task_data_identity_before"] == first["task_data_identity_after"]


@pytest.mark.parametrize("kind", ["protocol", "manifest", "duplicate_key", "duplicate_review", "unknown_review"])
def test_bad_queue_is_rejected_before_any_api(server, kind):
    if kind == "protocol": server.queue["protocol_id"] = "wrong"
    elif kind == "manifest": server.queue["source_manifest_sha256"] = "0"*64
    elif kind == "duplicate_key": server.queue["views"][1]["key"] = "priority"
    elif kind == "duplicate_review": server.queue["views"][0]["review_ids"] = ["r0", "r0"]
    elif kind == "unknown_review": server.queue["views"][0]["review_ids"] = ["foreign"]
    server.write()
    with pytest.raises(ValueError): server.run()
    assert not server.calls and not server.output.exists()


def test_live_task_data_drift_is_rejected_before_view_creation(server):
    server.rows[0]["data"]["image"] = "/changed.png"
    with pytest.raises(ValueError, match="Live task identity"): server.run()
    assert not any(method != "GET" for method, _, _ in server.calls)


def test_existing_view_drift_blocks_all_new_views(server):
    server.run()
    server.stored[2]["data"]["filters"]["items"][0]["value"] = [104]
    server.queue["views"].insert(0, {"key": "new", "title": "新视图", "review_ids": ["r2"]})
    server.write()
    before = len(server.calls)
    with pytest.raises(ValueError, match="drifted"): server.run()
    assert not any(method != "GET" for method, _, _ in server.calls[before:])


@pytest.mark.parametrize("phase", ["before", "after"])
def test_server_filter_must_match_exactly_before_success(server, phase):
    server.bad_filter, server.extra_after_create = phase == "before", phase == "after"
    with pytest.raises(ValueError, match="filtered task IDs"): server.run()
    assert not server.output.exists()
    if phase == "before": assert not any(method != "GET" for method, _, _ in server.calls)


def test_automatic_prediction_setting_blocks_task_reads(server):
    server.automatic_predictions = True
    with pytest.raises(ValueError, match="automatic predictions"): server.run()
    assert len(server.calls) == 1 and server.calls[0][1] == "/api/projects/77"
