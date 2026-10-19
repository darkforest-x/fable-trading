"""Same-origin REST routes for the local learning and publishing workspace."""
from __future__ import annotations

import re

from fastapi import HTTPException
from fastapi.responses import Response

from .knowledge import (
    ContentPayload,
    KnowledgePayload,
    KnowledgeWorkspace,
    LearningPayload,
    NOTION_HUB,
    NOTION_NOTES,
    NOTION_RESOURCES,
    PLATFORMS,
    TOPICS,
    content_markdown,
    overview_counts,
)


_ID_PATTERNS = {
    "learning": re.compile(r"^learning-[0-9a-f]{16}$"),
    "knowledge": re.compile(r"^knowledge-[0-9a-f]{16}$"),
    "content": re.compile(r"^content-[0-9a-f]{16}$"),
}
def install(api, app, store, catalog):
    """Register routes on the parent's /api/research same-origin router."""
    workspace = KnowledgeWorkspace(store)
    app.state.knowledge_workspace = workspace

    def check_id(kind: str, record_id: str) -> None:
        if not _ID_PATTERNS[kind].fullmatch(record_id):
            raise HTTPException(404, f"{kind} 记录不存在")

    def check_links(kind: str, payload) -> list[dict] | None:
        if kind == "learning" and payload.knowledge_ids:
            available = set(store.notes("knowledge"))
            if set(payload.knowledge_ids) - available:
                raise HTTPException(400, "关联知识记录不存在，请刷新后重新选择。")
        if kind == "knowledge" and payload.experiment_ids:
            available = {
                row.get("id") or row.get("experiment_id")
                for row in catalog.experiments()
            }
            if set(payload.experiment_ids) - available:
                raise HTTPException(400, "关联实验不存在，请刷新后重新选择。")
        if kind == "content" and payload.knowledge_refs:
            available = set(store.notes("knowledge"))
            if {item.id for item in payload.knowledge_refs} - available:
                raise HTTPException(400, "关联知识记录不存在，请刷新后重新选择。")
            return workspace.snapshot_knowledge(payload.knowledge_refs)
        return None

    def save(kind: str, payload, record_id: str | None = None) -> dict:
        if record_id is None:
            if payload.expected_revision != 0:
                raise HTTPException(409, "新记录必须从 revision 0 开始。")
            record_id = workspace.new_id(kind)
        else:
            check_id(kind, record_id)
            # A PUT never creates a missing record, even when revision is zero.
            try:
                workspace.current(kind, record_id)
            except KeyError as error:
                raise HTTPException(404, str(error))
        try:
            snapshots = check_links(kind, payload)
        except KeyError as error:
            raise HTTPException(400, str(error))
        except ValueError as error:
            raise HTTPException(409, str(error))
        try:
            value = workspace.save(kind, record_id, payload, snapshots)
        except ValueError as error:
            raise HTTPException(409, str(error))
        return dict(value, id=record_id)

    def detail(kind: str, record_id: str) -> dict:
        check_id(kind, record_id)
        try:
            result = workspace.current(kind, record_id)
            result["history"] = workspace.history(kind, record_id)
            return result
        except KeyError as error:
            raise HTTPException(404, str(error))

    @api.get("/knowledge-hub")
    def knowledge_hub():
        return {
            "topics": TOPICS,
            "platforms": PLATFORMS,
            "notion": {
                "hub": NOTION_HUB,
                "resources": NOTION_RESOURCES,
                "notes": NOTION_NOTES,
            },
            "counts": overview_counts(workspace),
        }

    @api.get("/learning")
    def learning_list():
        return {"items": workspace.list("learning")}

    @api.get("/knowledge")
    def knowledge_list():
        return {"items": workspace.list("knowledge")}

    @api.get("/content")
    def content_list():
        return {"items": workspace.list("content")}

    @api.post("/learning", status_code=201)
    def learning_create(payload: LearningPayload):
        return save("learning", payload)

    @api.post("/knowledge", status_code=201)
    def knowledge_create(payload: KnowledgePayload):
        return save("knowledge", payload)

    @api.post("/content", status_code=201)
    def content_create(payload: ContentPayload):
        return save("content", payload)

    @api.get("/learning/{record_id}")
    def learning_detail(record_id: str):
        return detail("learning", record_id)

    @api.get("/knowledge/{record_id}")
    def knowledge_detail(record_id: str):
        return detail("knowledge", record_id)

    @api.get("/content/{content_id}/export")
    def content_export(content_id: str):
        record = detail("content", content_id)
        filename = f"spike-{content_id}.md"
        return Response(
            content=content_markdown(record),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @api.get("/content/{record_id}")
    def content_detail(record_id: str):
        return detail("content", record_id)

    @api.put("/learning/{record_id}")
    def learning_update(record_id: str, payload: LearningPayload):
        return save("learning", payload, record_id)

    @api.put("/knowledge/{record_id}")
    def knowledge_update(record_id: str, payload: KnowledgePayload):
        return save("knowledge", payload, record_id)

    @api.put("/content/{record_id}")
    def content_update(record_id: str, payload: ContentPayload):
        return save("content", payload, record_id)
