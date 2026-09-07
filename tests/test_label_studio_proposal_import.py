"""Proposal import contracts; all Label Studio calls are replaced in memory."""
from contextlib import nullcontext
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from yoyo.datasets import label_studio_import as base
from yoyo.datasets import label_studio_proposal_import as importer


PROTOCOL = "owner_geometry_refinement_v1"


@pytest.fixture
def server(tmp_path, monkeypatch):
    root, data_root = tmp_path / "reports", tmp_path / "datasets" / "pack"
    (root / "label_studio").mkdir(parents=True)
    data_root.mkdir(parents=True)
    (root / "label_studio" / "pack").symlink_to(data_root, target_is_directory=True)
    state = SimpleNamespace(root=root, data_root=data_root, tasks=[], rows=[], stores=[],
                            calls=[], probes=[], human={}, corrupt_http=False,
                            projects=[{"id": 1, "title": "unrelated", "label_config": "<View />"}])
    for index in range(5):
        data = {"review_id": f"r{index}", "protocol_id": PROTOCOL}
        for field in ("image", "reference_image", "future_image"):
            path = data_root / field / f"{index}.png"
            path.parent.mkdir(exist_ok=True)
            blob = f"{field}-{index}".encode()
            path.write_bytes(blob)
            data[field] = f"/data/local-files/?d=label_studio/pack/{field}/{index}.png"
            data[field + "_sha256"] = hashlib.sha256(blob).hexdigest()
        region = {"id": f"proposal-{index}", "type": "rectanglelabels", "from_name": "pattern",
                  "to_name": "image", "original_width": 1280, "original_height": 742,
                  "image_rotation": 0, "value": {"x": 10, "y": 20, "width": 30, "height": 40,
                  "rotation": 0, "rectanglelabels": ["空头" if index % 2 else "多头"]}}
        state.tasks.append({"data": data, "predictions": [{"model_version": PROTOCOL, "result": [region]}]})
    state.tasks_file, config_file, state.receipt = (tmp_path / name for name in ("tasks.json", "config.xml", "receipt.json"))
    state.write = lambda: state.tasks_file.write_text(json.dumps(state.tasks, ensure_ascii=False))
    state.write()
    config_file.write_text('<View><Image name="image" value="$image"/>'
                          '<RectangleLabels name="pattern" toName="image"><Label value="多头"/>'
                          '<Label value="空头"/></RectangleLabels>'
                          '<Image name="reference" value="$reference_image"/>'
                          '<Image name="future" value="$future_image"/></View>')

    def api(_session, method, path, payload=None):
        state.calls.append((method, path, deepcopy(payload)))
        if path.startswith(("/api/projects/validate", "/api/storages/localfiles/validate")):
            return {}
        if method == "GET" and path.startswith("/api/projects?"):
            return {"count": len(state.projects), "results": deepcopy(state.projects)}
        if method == "POST" and path == "/api/projects":
            assert payload["show_collab_predictions"] is True and payload["model_version"] == PROTOCOL
            project = {"id": 42, **payload}
            state.projects.append(deepcopy(project))
            return project
        if method == "GET" and path.startswith("/api/tasks?"):
            query = parse_qs(urlsplit(path).query)
            assert query["project"] == ["42"] and query["fields"] == ["all"]
            assert set(query["include"][0].split(",")) == {"id", "data", "predictions", "total_predictions", "total_annotations"}
            assert "annotations" not in query["include"][0].split(",")
            return {"total": len(state.rows), "tasks": deepcopy(state.rows)}
        if method == "GET" and path.startswith("/api/storages/localfiles?"):
            return deepcopy(state.stores)
        if method == "POST" and path == "/api/storages/localfiles":
            assert payload["project"] == 42 and str(root) in payload["path"]
            state.stores.append(deepcopy(payload))
            return {"id": len(state.stores), **payload}
        if method == "POST" and path.startswith("/api/projects/42/import?"):
            ids = []
            for task in payload:
                assert set(task) == {"data", "predictions"}
                row = {"id": len(state.rows) + 100, **deepcopy(task), "total_predictions": 1, "total_annotations": 0}
                state.rows.append(row)
                ids.append(row["id"])
            return {"task_count": len(payload), "task_ids": ids}
        raise AssertionError(f"Unexpected or destructive API call: {method} {path}")

    def open_image(url, **_kwargs):
        path = root / parse_qs(urlsplit(url).query)["d"][0]
        state.probes.append(path)
        return nullcontext(SimpleNamespace(status=200, read=lambda: b"corrupt" if state.corrupt_http else path.read_bytes()))

    monkeypatch.setattr(base, "session", lambda: (SimpleNamespace(open=open_image), ""))
    monkeypatch.setattr(base, "api", api)
    monkeypatch.setattr(base, "import_blank_project", lambda *a, **kw: pytest.fail("Proposal import must not call blank import"))
    state.run = lambda: importer.import_proposal_project("refinement", state.tasks_file, config_file, state.receipt, root, batch_size=2)
    return state


def test_reentry_preserves_human_annotations_and_predictions(server):
    first = server.run()
    server.rows[0]["total_annotations"] = 1
    server.rows[0]["predictions"][0].update({"id": 901, "created_at": "2026-09-07T10:00:00Z"})
    server.human[100] = {"annotations": [{"id": 8, "result": [{"id": "owner-adjusted"}]}], "drafts": [{"id": 9}]}
    before = deepcopy((server.rows, server.human))
    calls_before = len(server.calls)
    second = server.run()
    assert first["imported"] == 5 and second["imported"] == 0 and second["reused"] == 5
    assert first["task_ids"] == second["task_ids"] and (server.rows, server.human) == before
    assert not any(method != "GET" and "/import" in path for method, path, _ in server.calls[calls_before:])


@pytest.mark.parametrize("defect", ["annotations", "negative_x", "overflow", "zero_width", "nan", "wrong_image", "wrong_label", "wrong_size", "empty_id", "rotation", "protocol", "multiple_predictions", "multiple_regions"])
def test_invalid_input_is_rejected_before_any_api_call(server, defect):
    task = server.tasks[0]
    region = task["predictions"][0]["result"][0]
    if defect == "annotations": task["annotations"] = []
    elif defect == "negative_x": region["value"]["x"] = -1
    elif defect == "overflow": region["value"].update(x=80, width=30)
    elif defect == "zero_width": region["value"]["width"] = 0
    elif defect == "nan": region["value"]["y"] = float("nan")
    elif defect == "wrong_image": region["to_name"] = "future"
    elif defect == "wrong_label": region["value"]["rectanglelabels"] = ["unknown"]
    elif defect == "wrong_size": region["original_width"] = 742
    elif defect == "empty_id": region["id"] = ""
    elif defect == "rotation": region["value"]["rotation"] = 10
    elif defect == "protocol": task["predictions"][0]["model_version"] = "other"
    elif defect == "multiple_predictions": task["predictions"].append(deepcopy(task["predictions"][0]))
    elif defect == "multiple_regions": task["predictions"][0]["result"].append(deepcopy(region))
    server.write()
    with pytest.raises(ValueError): server.run()
    assert not server.calls and not server.receipt.exists()


def test_existing_prediction_drift_blocks_import_without_touching_owner_work(server):
    server.run()
    server.rows[0]["predictions"][0]["result"][0]["value"]["width"] = 25
    server.rows[0]["total_annotations"] = 1
    before, calls_before = deepcopy(server.rows), len(server.calls)
    with pytest.raises(ValueError): server.run()
    assert server.rows == before
    assert not any("/import" in path for _, path, _ in server.calls[calls_before:])


def test_existing_project_selected_prediction_version_drift_is_rejected(server):
    server.run()
    server.projects[-1]["model_version"] = "another-proposal-version"
    calls_before = len(server.calls)
    with pytest.raises(ValueError): server.run()
    assert not any("/import" in path for _, path, _ in server.calls[calls_before:])


def test_duplicate_task_ids_cannot_be_reported_as_success(server):
    server.run()
    server.rows[1]["id"] = server.rows[0]["id"]
    server.receipt.unlink()
    with pytest.raises(ValueError): server.run()
    assert not server.receipt.exists()


@pytest.mark.parametrize("field", ["image", "reference_image", "future_image"])
def test_every_local_image_sha_is_checked_before_project_creation(server, field):
    # Index 1 is deliberately outside the common first/middle/last HTTP sample.
    (server.data_root / field / "1.png").write_bytes(b"changed")
    with pytest.raises(ValueError): server.run()
    assert not any(method == "POST" and path == "/api/projects" for method, path, _ in server.calls)
    assert not server.rows and not server.receipt.exists()


def test_all_image_fields_work_through_document_root_symlink(server):
    receipt = server.run()
    assert {row["field"] for row in receipt["image_checks"]} == {"image", "reference_image", "future_image"}
    assert len(server.stores) == 3 and len(server.rows) == 5


def test_http_sha_mismatch_never_writes_success_receipt(server):
    server.corrupt_http = True
    with pytest.raises(ValueError): server.run()
    assert not server.receipt.exists()
