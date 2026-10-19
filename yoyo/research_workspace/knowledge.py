"""Versioned learning notes, evidence-aware knowledge and platform drafts.

Records use the research workspace's existing append-only SQLite notes table.
Content references freeze the selected knowledge revision in each content
revision, so later edits cannot rewrite the source that an exported draft used.
This module contains no publishing client or external sync.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Literal, Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .store import WorkspaceStore


TOPICS = [
    {"id": "market", "name": "市场机制", "description": "市场结构、参与者行为、流动性与价格形成。"},
    {"id": "strategy", "name": "形态与策略", "description": "形态定义、策略假设、入场与退出规则。"},
    {"id": "risk", "name": "风险与执行", "description": "风险边界、成本、执行条件与人工操作纪律。"},
    {"id": "data", "name": "数据工程", "description": "数据来源、时间边界、质量、版本与复现。"},
    {"id": "models", "name": "模型方法", "description": "模型设计、特征、标签、评估与局限。"},
    {"id": "methodology", "name": "研究验证", "description": "研究设计、对照、统计检验与证据解释。"},
]
TOPIC_IDS = {item["id"] for item in TOPICS}

# These are editing suggestions, not platform API or character-limit claims.
PLATFORMS = [
    {"id": "zhihu", "name": "知乎", "format": "长文 / 问答",
     "guidance": "从具体问题切入，逐步说明机制、证据、反例和适用边界；把引用放在对应论点附近。"},
    {"id": "wechat", "name": "微信公众号", "format": "文章",
     "guidance": "用清晰标题和分节组织背景、推理与结论；结尾交代读者可复核的来源和限制。"},
    {"id": "xiaohongshu", "name": "小红书", "format": "图文笔记 / 口播提纲",
     "guidance": "先给读者一个明确场景，再拆成易读要点；若配图，标出事实、推测和风险边界。"},
    {"id": "x", "name": "X", "format": "短帖 / 线程",
     "guidance": "每条围绕一个可核查主张展开；线程中保留推理顺序，并链接原始资料。"},
    {"id": "bilibili", "name": "哔哩哔哩", "format": "视频脚本 / 分段提纲",
     "guidance": "写出口语化旁白、画面提示和章节顺序；重要数字说明口径，末尾列来源与未解决问题。"},
    {"id": "douyin", "name": "抖音", "format": "短视频脚本",
     "guidance": "用一个具体问题开场，安排简洁的画面与字幕提示；避免把观察包装成确定收益结论。"},
    {"id": "youtube", "name": "YouTube", "format": "视频脚本 / 章节提纲",
     "guidance": "先写核心问题和章节，再组织旁白、演示与来源说明；让观众能定位关键证据。"},
    {"id": "telegram", "name": "Telegram", "format": "频道帖 / 讨论提纲",
     "guidance": "简要说明新发现、状态和下一步；给出源链接，并把待验证内容明确标出来。"},
    {"id": "custom", "name": "自定义平台", "format": "自由格式",
     "guidance": "按目标平台编辑标题、正文和引用；发布状态由本人手动记录。"},
]
PLATFORM_IDS = {item["id"] for item in PLATFORMS}
NOTION_HUB = "https://app.notion.com/p/3d98856479af80d9ad43f622dd7c3efd"
NOTION_RESOURCES = "https://app.notion.com/p/b4da71cf12cf4b58a00b85b8dce976d5"
NOTION_NOTES = "https://app.notion.com/p/20dcfabc21bc4c59abec37afe89a2320"


def validate_http_url(value: str, *, allowed_hosts: set[str] | None = None) -> str:
    """Accept absolute HTTP(S) links only; never accept credentials or scripts."""
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise ValueError("链接必须是 1–2048 个字符的 HTTP(S) 地址。")
    if any(ord(char) < 0x20 or char.isspace() or char == "\\" for char in value):
        raise ValueError("链接不能包含空白、控制字符或反斜杠。")
    try:
        parsed = urlsplit(value)
        # Accessing .port also validates malformed port syntax.
        _ = parsed.port
    except ValueError as error:
        raise ValueError("链接格式不正确。") from error
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("只接受绝对 HTTP(S) 链接。")
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        raise ValueError("链接不能包含用户名或密码。")
    if allowed_hosts is not None and parsed.hostname.casefold().rstrip(".") not in allowed_hosts:
        raise ValueError("Notion 链接域名不在允许列表中。")
    return value


class StrictPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


class VersionedPayload(StrictPayload):
    expected_revision: int = Field(default=0, ge=0)
    title: str = Field(min_length=2, max_length=200)
    topic: str = Field(min_length=1, max_length=40)

    @field_validator("topic")
    @classmethod
    def known_topic(cls, value: str) -> str:
        if value not in TOPIC_IDS:
            raise ValueError("主题必须选择六个研究主题之一。")
        return value


class LearningPayload(VersionedPayload):
    question: str = Field(default="", max_length=4000)
    goal: str = Field(default="", max_length=4000)
    stage: Literal["planned", "learning", "review", "completed", "archived"] = "planned"
    source_urls: list[str] = Field(default_factory=list, max_length=40)
    notes: str = Field(default="", max_length=16000)
    retrieval_notes: str = Field(default="", max_length=6000)
    next_review: str = Field(default="", max_length=10)
    knowledge_ids: list[str] = Field(default_factory=list, max_length=80)

    @field_validator("source_urls")
    @classmethod
    def valid_sources(cls, values: list[str]) -> list[str]:
        return [validate_http_url(value) for value in values]

    @field_validator("next_review")
    @classmethod
    def valid_review_date(cls, value: str) -> str:
        if value:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise ValueError("下次复习日期须使用 YYYY-MM-DD 或留空。")
            try:
                datetime.strptime(value, "%Y-%m-%d")
            except ValueError as error:
                raise ValueError("下次复习日期须使用 YYYY-MM-DD 或留空。") from error
        return value

    @field_validator("knowledge_ids")
    @classmethod
    def valid_knowledge_ids(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values):
            raise ValueError("知识条目不能重复关联。")
        if any(not re.fullmatch(r"knowledge-[0-9a-f]{16}", value) for value in values):
            raise ValueError("知识条目 ID 格式不正确。")
        return values


class KnowledgePayload(VersionedPayload):
    kind: Literal["concept", "principle", "hypothesis", "lesson", "method"]
    status: Literal["draft", "organized", "archived"] = "draft"
    evidence_status: Literal["unverified", "testing", "supported", "refuted"] = "unverified"
    summary: str = Field(default="", max_length=3000)
    body: str = Field(default="", max_length=20000)
    source_urls: list[str] = Field(default_factory=list, max_length=40)
    evidence: str = Field(default="", max_length=12000)
    limitations: str = Field(default="", max_length=8000)
    experiment_ids: list[str] = Field(default_factory=list, max_length=80)
    notion_url: Optional[str] = Field(default=None, max_length=2048)

    @field_validator("source_urls")
    @classmethod
    def valid_sources(cls, values: list[str]) -> list[str]:
        return [validate_http_url(value) for value in values]

    @field_validator("experiment_ids")
    @classmethod
    def unique_experiments(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values):
            raise ValueError("实验不能重复关联。")
        if any(not value or len(value) > 180 for value in values):
            raise ValueError("实验 ID 长度须为 1–180 个字符。")
        return values

    @field_validator("notion_url")
    @classmethod
    def valid_notion_url(cls, value: Optional[str]) -> Optional[str]:
        if value is None or value == "":
            return None
        return validate_http_url(value, allowed_hosts={"notion.so", "app.notion.com", "www.notion.so"})

    @model_validator(mode="after")
    def evidence_for_conclusion(self):
        if self.evidence_status in {"supported", "refuted"} and (not self.evidence or not self.limitations):
            raise ValueError("支持或反驳结论必须同时记录证据和适用限制。")
        return self


class KnowledgeReference(StrictPayload):
    id: str = Field(pattern=r"^knowledge-[0-9a-f]{16}$")
    revision: int = Field(ge=1)


class ContentVariant(StrictPayload):
    platform: Literal["zhihu", "wechat", "xiaohongshu", "x", "bilibili", "douyin", "youtube", "telegram", "custom"]
    title: str = Field(default="", max_length=200)
    body: str = Field(default="", max_length=60000)
    status: Literal["draft", "ready", "published"] = "draft"
    published_url: str = Field(default="", max_length=2048)
    published_at: str = Field(default="", max_length=80)

    @field_validator("published_url")
    @classmethod
    def valid_published_url(cls, value: str) -> str:
        return validate_http_url(value) if value else value

    @field_validator("published_at")
    @classmethod
    def valid_published_time(cls, value: str) -> str:
        if not value:
            return value
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("发布时间须为带时区的 ISO 8601 日期时间。") from error
        if parsed.utcoffset() is None:
            raise ValueError("发布时间须包含时区。")
        return value

    @model_validator(mode="after")
    def status_has_receipt(self):
        if self.status == "ready" and not self.body:
            raise ValueError("标记为 ready 前必须填写正文。")
        if self.status == "published":
            if not self.body:
                raise ValueError("手动记录为 published 前必须填写正文。")
            if not self.published_url or not self.published_at:
                raise ValueError("手动记录为 published 必须提供发布链接和带时区的发布时间。")
        return self


class ContentPayload(StrictPayload):
    expected_revision: int = Field(default=0, ge=0)
    title: str = Field(min_length=2, max_length=200)
    audience: str = Field(default="", max_length=1200)
    angle: str = Field(default="", max_length=3000)
    body: str = Field(default="", max_length=60000)
    knowledge_refs: list[KnowledgeReference] = Field(default_factory=list, max_length=50)
    variants: list[ContentVariant] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def unique_content_links(self):
        ids = [item.id for item in self.knowledge_refs]
        if len(set(ids)) != len(ids):
            raise ValueError("知识引用不能重复。")
        platforms = [item.platform for item in self.variants]
        if len(set(platforms)) != len(platforms):
            raise ValueError("每个平台只能有一个内容版本。")
        return self


class KnowledgeWorkspace:
    """Save versioned learning/knowledge/content records in WorkspaceStore."""

    def __init__(self, store: WorkspaceStore):
        self.store = store

    @staticmethod
    def new_id(kind: str) -> str:
        return f"{kind}-{uuid.uuid4().hex[:16]}"

    def list(self, kind: str) -> list[dict]:
        return sorted(
            [dict(value, id=key) for key, value in self.store.notes(kind).items()],
            key=lambda value: value.get("updated_at", ""), reverse=True,
        )

    def current(self, kind: str, record_id: str) -> dict:
        item = self.store.notes(kind).get(record_id)
        if item is None:
            raise KeyError(f"{kind} 记录不存在")
        return dict(item, id=record_id)

    def history(self, kind: str, record_id: str) -> list[dict]:
        rows = self.store.history(kind, record_id)
        if not rows:
            raise KeyError(f"{kind} 记录不存在")
        return rows

    def snapshot_knowledge(self, references: list[KnowledgeReference]) -> list[dict]:
        snapshots = []
        for reference in references:
            history = self.store.history("knowledge", reference.id)
            if not history:
                raise KeyError("关联知识记录不存在")
            selected = next((item for item in history if item["revision"] == reference.revision), None)
            if selected is None:
                raise ValueError("关联知识版本不存在，请刷新后重新选择。")
            snapshots.append({
                "id": reference.id,
                "revision": selected["revision"],
                "title": selected["title"],
                "summary": selected["summary"],
                "body": selected["body"],
                "evidence_status": selected["evidence_status"],
                "evidence": selected["evidence"],
                "limitations": selected["limitations"],
                "source_urls": list(selected["source_urls"]),
            })
        return snapshots

    def save(self, kind: str, record_id: str, payload: BaseModel, snapshots: list[dict] | None = None) -> dict:
        data = payload.model_dump(mode="json", exclude={"expected_revision"})
        if kind == "content":
            data["knowledge_snapshots"] = snapshots or []
        return self.store.save_note(kind, record_id, data, payload.expected_revision)


def overview_counts(workspace: KnowledgeWorkspace) -> dict:
    learning = workspace.list("learning")
    knowledge = workspace.list("knowledge")
    content = workspace.list("content")

    def group(items: list[dict], field: str) -> dict[str, int]:
        result: dict[str, int] = {}
        for item in items:
            key = item.get(field, "unknown")
            result[key] = result.get(key, 0) + 1
        return result

    variant_statuses: dict[str, int] = {}
    for item in content:
        for variant in item.get("variants", []):
            status = variant.get("status", "unknown")
            variant_statuses[status] = variant_statuses.get(status, 0) + 1
    return {
        "learning": len(learning),
        "knowledge": len(knowledge),
        "content": len(content),
        "learning_by_stage": group(learning, "stage"),
        "knowledge_by_status": group(knowledge, "status"),
        "knowledge_by_evidence_status": group(knowledge, "evidence_status"),
        "content_variants_by_status": variant_statuses,
    }


def content_markdown(content: dict) -> str:
    """Render a local Markdown export; links are emitted verbatim, never fetched."""
    evidence_names = {"unverified": "待验证", "testing": "验证中", "supported": "有支持证据", "refuted": "有反面证据"}
    content_status_names = {"draft": "草稿", "ready": "待人工发布", "published": "已手动登记发布"}
    lines = [f"# {content['title']}", "", f"- 受众：{content.get('audience') or '—'}",
             f"- 表达角度：{content.get('angle') or '—'}",
             f"- 记录：{content['id']}（版本 {content['revision']}）", "", "## 内容母稿", "",
             content.get("body") or "_（尚无母稿。）_", ""]
    variants = content.get("variants", [])
    if variants:
        lines.extend(["## 平台版本", ""])
        platform_names = {item["id"]: item["name"] for item in PLATFORMS}
        for variant in variants:
            lines.extend([f"### {platform_names.get(variant['platform'], variant['platform'])}", "",
                          f"- 状态：{content_status_names.get(variant['status'], variant['status'])}"])
            if variant.get("published_url"):
                lines.append(f"- 手动登记的发布链接：{variant['published_url']}")
            if variant.get("published_at"):
                lines.append(f"- 手动登记的发布时间：{variant['published_at']}")
            if variant.get("title"):
                lines.extend(["", f"**{variant['title']}**"])
            lines.extend(["", variant.get("body") or "_（尚无平台稿。）_", ""])
    snapshots = content.get("knowledge_snapshots", [])
    if snapshots:
        lines.extend(["## 来源知识快照", ""])
        for snapshot in snapshots:
            lines.extend([f"### {snapshot['title']} — {snapshot['id']} 第 {snapshot['revision']} 版", "",
                          f"- 证据状态（保留来源原值）：{evidence_names.get(snapshot['evidence_status'], snapshot['evidence_status'])}",
                          f"- 摘要：{snapshot['summary'] or '—'}", "", "证据记录：", "",
                          snapshot.get("evidence") or "—", "", "适用限制：", "",
                          snapshot.get("limitations") or "—", "", "来源正文：", "",
                          snapshot.get("body") or "—", "", "引用来源："])
            citations = snapshot.get("source_urls", [])
            if citations:
                lines.extend([f"- {url}" for url in citations])
            else:
                lines.append("- 未记录")
            lines.append("")
    lines.extend(["## 发布记录", "", "发布状态和回执由本人手动记录；导出不会发布内容。", ""])
    return "\n".join(lines)
