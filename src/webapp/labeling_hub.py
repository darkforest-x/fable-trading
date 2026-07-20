"""Labeling hub payload — owner entries, audit pages, task packs.

Read-only. Website entries are maintained in ``output/label_studio/hub.json``
(or ``data/labeling_hub.json``); missing file falls back to VPS defaults.
Does not call Label Studio APIs (no secrets, no write-back).
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]

LABEL_STUDIO_DIR = PROJECT_ROOT / "output" / "label_studio"
STATIC_DIR = PROJECT_ROOT / "src" / "webapp" / "static"
HUB_CANDIDATES = (
    PROJECT_ROOT / "data" / "labeling_hub.json",
    LABEL_STUDIO_DIR / "hub.json",
)

# Public VPS defaults (owner can override via hub.json).
DEFAULT_SITES: list[dict[str, str]] = [
    {
        "name": "Label Studio（主入口 HTTPS）",
        "url": "https://103.214.174.58:8081",
        "role": "primary",
        "note": "nginx → 本机/隧道后端；日常打标点这里",
    },
    {
        "name": "Label Studio（反向隧道 :18081）",
        "url": "http://103.214.174.58:18081",
        "role": "tunnel",
        "note": "Mac 上 docker LS + bash scripts/tunnel_labelstudio.sh",
    },
    {
        "name": "本机 Label Studio",
        "url": "http://127.0.0.1:8081",
        "role": "local",
        "note": "仅在 Mac 本机浏览器有效",
    },
]

DEFAULT_AUDITS = (
    ("label_audit.html", "标签审计 Round1", "抽样对照 GT 框"),
    ("label_audit_e1_compare.html", "E1 宽度对照", "pad 前后红绿对比"),
    ("label_audit_e2_compare.html", "E2 长段收核对照", "MAX_DENSE_BARS=24"),
)

TASK_RE = re.compile(
    r"^tasks_(?P<label>.+)\.json$",
    re.IGNORECASE,
)
MANIFEST_RE = re.compile(r"^round(?P<n>\d+)_manifest\.json$", re.IGNORECASE)


def labeling_hub_payload() -> dict[str, Any]:
    hub = _load_hub()
    sites = hub.get("sites") or list(DEFAULT_SITES)
    # Allow env override for primary host without editing JSON.
    env_primary = (os.environ.get("LABEL_STUDIO_URL") or "").strip()
    if env_primary:
        sites = [
            {
                "name": "Label Studio（环境变量）",
                "url": env_primary,
                "role": "primary",
                "note": "LABEL_STUDIO_URL",
            },
            *[s for s in sites if s.get("role") != "primary"],
        ]

    audits = _audit_pages()
    packs = _task_packs()
    manifests = _manifests()
    latest_round = max((m.get("round") or 0) for m in manifests) if manifests else None

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sites": sites,
        "account_hint": hub.get("account_hint")
        or "账号见 docs/OWNER_LABELING_PLAYBOOK.md（弱口令，勿公开传播）",
        "maintain": hub.get("maintain")
        or _default_maintain(),
        "audits": audits,
        "packs": packs,
        "manifests": manifests,
        "summary": {
            "n_sites": len(sites),
            "n_audits": len(audits),
            "n_packs": len(packs),
            "latest_round": latest_round,
            "hub_config": str(hub.get("_path") or "(defaults)"),
        },
        "playbook": "docs/OWNER_LABELING_PLAYBOOK.md",
    }


def _default_maintain() -> list[dict[str, str]]:
    return [
        {
            "title": "改网站入口",
            "body": (
                "编辑 output/label_studio/hub.json（或 data/labeling_hub.json）的 sites 列表，"
                "然后 rsync 到 VPS 或 bash scripts/deploy_vps.sh。"
            ),
        },
        {
            "title": "起 Label Studio（本机）",
            "body": "docker compose -f scripts/label_studio_compose.yml up -d  →  http://127.0.0.1:8081",
        },
        {
            "title": "远程打标隧道",
            "body": "export VPS_HOST=root@103.214.174.58 VPS_PORT=18081 && bash scripts/tunnel_labelstudio.sh",
        },
        {
            "title": "新任务包",
            "body": "python3 scripts/make_round10_packs.py（或当前轮次脚本）→ output/label_studio/tasks_*.json",
        },
    ]


def _load_hub() -> dict[str, Any]:
    for path in HUB_CANDIDATES:
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        try:
            data["_path"] = str(path.relative_to(PROJECT_ROOT))
        except ValueError:
            data["_path"] = str(path)
        return data
    return {}


def _audit_pages() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for name, title, note in DEFAULT_AUDITS:
        path = STATIC_DIR / name
        out.append({
            "name": title,
            "url": f"/{name}",
            "note": note,
            "exists": path.is_file(),
            "size_kb": round(path.stat().st_size / 1024, 1) if path.is_file() else None,
        })
    # Any extra label_audit*.html
    if STATIC_DIR.is_dir():
        known = {a["url"].lstrip("/") for a in out}
        for path in sorted(STATIC_DIR.glob("label_audit*.html")):
            if path.name in known:
                continue
            out.append({
                "name": path.stem,
                "url": f"/{path.name}",
                "note": "额外审计页",
                "exists": True,
                "size_kb": round(path.stat().st_size / 1024, 1),
            })
    return out


def _task_packs() -> list[dict[str, Any]]:
    if not LABEL_STUDIO_DIR.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(LABEL_STUDIO_DIR.glob("tasks_*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        m = TASK_RE.match(path.name)
        if not m:
            continue
        n_tasks = _count_tasks(path)
        st = path.stat()
        rows.append({
            "file": path.name,
            "label": m.group("label"),
            "n_tasks": n_tasks,
            "mtime": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
            "size_mb": round(st.st_size / 1e6, 2),
            "path": f"output/label_studio/{path.name}",
        })
    return rows[:40]


def _manifests() -> list[dict[str, Any]]:
    if not LABEL_STUDIO_DIR.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(LABEL_STUDIO_DIR.glob("round*_manifest.json")):
        m = MANIFEST_RE.match(path.name)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append({
            "file": path.name,
            "round": raw.get("round") or (int(m.group("n")) if m else None),
            "count": raw.get("count"),
            "chunks": raw.get("chunks"),
            "seed": raw.get("seed"),
            "weights": raw.get("weights"),
            "guards": raw.get("guards") or [],
            "by_reservoir": raw.get("by_reservoir") or {},
        })
    out.sort(key=lambda r: r.get("round") or 0, reverse=True)
    return out


def _count_tasks(path: Path) -> int | None:
    # Avoid loading multi‑MB packs into memory on every hub open.
    try:
        if path.stat().st_size > 4_000_000:
            # Cheap heuristic: top-level JSON array of task objects.
            text = path.read_text(encoding="utf-8", errors="ignore")
            # Count "data" keys typical of LS task export (approx).
            n = text.count('"data"')
            return n if n > 0 else None
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, MemoryError):
        return None
    if isinstance(raw, list):
        return len(raw)
    if isinstance(raw, dict):
        for key in ("tasks", "items", "data"):
            if isinstance(raw.get(key), list):
                return len(raw[key])
    return None
