"""Initialize dense_15m_val_audit on the public Label Studio VPS via API.

Reads credentials from untracked output/offline_tasks/LABEL_STUDIO_VPS_ACCESS.md
in-process (never prints password/token). Idempotent: reuses project if present,
imports tasks only when task_number < 80, ensures LocalFilesImportStorage so
/data/local-files/ can serve pack images (LS 1.15 returns 404 without it).

Usage (isolated QA venv recommended):
  .venv_label_studio_qa/bin/python scripts/init_label_studio_vps_project.py

Does not enable job executor, does not touch judgment holdout, does not print secrets.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ACCESS = ROOT / "output/offline_tasks/LABEL_STUDIO_VPS_ACCESS.md"
DEFAULT_TASKS = ROOT / "output/label_studio/tasks_val.json"
DEFAULT_CONFIG = ROOT / "output/label_studio/label_config.xml"
PROJECT_TITLE = "dense_15m_val_audit"
STORAGE_PATH = "/opt/fable-label-studio/files/dense_15m_full"
EXPECTED_TASKS = 80


def _val(text: str, key: str) -> str:
    m = re.search(rf"-\s*{key}:\s*\*?\*?`?([^\s*`]+)`?\*?\*?", text, re.I)
    if not m:
        raise SystemExit(f"missing credential key: {key}")
    return m.group(1).strip()


def _session(url: str, email: str, password: str) -> requests.Session:
    s = requests.Session()
    s.verify = False
    s.headers.update({"User-Agent": "fable-label-studio-init/1.0"})
    s.get(f"{url}/user/login/", timeout=30)
    csrf = s.cookies.get("csrftoken")
    headers = {"X-CSRFToken": csrf, "Referer": f"{url}/user/login/"} if csrf else {}
    s.post(
        f"{url}/user/login/",
        data={"email": email, "password": password},
        headers=headers,
        allow_redirects=True,
        timeout=30,
    )
    token = s.get(f"{url}/api/current-user/token", timeout=30).json().get("token")
    if not token:
        raise SystemExit("failed to obtain API token after login")
    s.headers["Authorization"] = f"Token {token}"
    who = s.get(f"{url}/api/current-user/whoami", timeout=30)
    if who.status_code != 200:
        raise SystemExit(f"whoami failed: {who.status_code}")
    return s


def _sanitize_tasks(raw: list) -> list:
    """Strip invalid completed_by; import prelabels as predictions only."""
    out = []
    for t in raw:
        nt = {"data": t["data"]}
        preds = t.get("predictions") or []
        anns = t.get("annotations") or []
        clean_preds = []
        for p in preds:
            cp = {
                k: v
                for k, v in p.items()
                if k not in ("completed_by", "id", "task", "project")
            }
            if "result" in cp:
                clean_preds.append(cp)
        if not clean_preds and anns:
            for a in anns:
                clean_preds.append(
                    {
                        "result": a.get("result") or [],
                        "model_version": "e21_gt_prelabel",
                        "score": 1.0,
                    }
                )
        if clean_preds:
            nt["predictions"] = clean_preds
        out.append(nt)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--access", type=Path, default=DEFAULT_ACCESS)
    ap.add_argument("--tasks", type=Path, default=DEFAULT_TASKS)
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = ap.parse_args()

    if not args.access.is_file():
        print(f"FAIL: missing access file {args.access}", file=sys.stderr)
        return 2
    if not args.tasks.is_file() or not args.config.is_file():
        print("FAIL: missing tasks_val.json or label_config.xml", file=sys.stderr)
        return 2

    text = args.access.read_text(encoding="utf-8")
    url = _val(text, "URL").rstrip("/")
    email = _val(text, "Email")
    password = _val(text, "Password")
    print(f"AUTH_PARSE_OK host_ok={('103.214.174.58' in url)} email_has_at={'@' in email}")

    s = _session(url, email, password)
    print("AUTH_OK")

    label_config = args.config.read_text(encoding="utf-8")
    r = s.get(f"{url}/api/projects/", timeout=30)
    r.raise_for_status()
    projects = r.json().get("results", r.json())
    if not isinstance(projects, list):
        projects = []
    proj = next((p for p in projects if p.get("title") == PROJECT_TITLE), None)
    if proj is None:
        r = s.post(
            f"{url}/api/projects/",
            json={
                "title": PROJECT_TITLE,
                "description": "Phase C audit pack: 80 val dense_cluster prelabels",
                "label_config": label_config,
                "is_published": True,
            },
            timeout=60,
        )
        if r.status_code not in (200, 201):
            print(f"FAIL create project status={r.status_code}", file=sys.stderr)
            return 3
        proj = r.json()
        print(f"CREATED_PROJECT id={proj['id']}")
    else:
        r = s.patch(
            f"{url}/api/projects/{proj['id']}/",
            json={"label_config": label_config, "is_published": True},
            timeout=60,
        )
        print(f"REUSE_PROJECT id={proj['id']} patch_config={r.status_code}")

    pid = proj["id"]

    # LocalFilesImportStorage is required for /data/local-files/ (else 404 even when
    # LOCAL_FILES_SERVING_ENABLED=true). Path must be a child of DOCUMENT_ROOT.
    r = s.get(f"{url}/api/storages/localfiles/", params={"project": pid}, timeout=30)
    storages = r.json() if r.status_code == 200 else []
    if not any(st.get("path") == STORAGE_PATH for st in storages):
        r = s.post(
            f"{url}/api/storages/localfiles/",
            json={
                "project": pid,
                "path": STORAGE_PATH,
                "use_blob_urls": True,
                "title": "dense_15m_full",
            },
            timeout=30,
        )
        print(f"CREATE_LOCAL_STORAGE status={r.status_code}")
        if r.status_code not in (200, 201):
            print(f"FAIL storage: {r.text[:300]}", file=sys.stderr)
            return 4
    else:
        print("LOCAL_STORAGE_OK")

    pinfo = s.get(f"{url}/api/projects/{pid}/", timeout=30).json()
    task_number = pinfo.get("task_number") or 0
    if task_number < EXPECTED_TASKS:
        raw = json.loads(args.tasks.read_text(encoding="utf-8"))
        sanitized = _sanitize_tasks(raw)
        r = s.post(
            f"{url}/api/projects/{pid}/import",
            json=sanitized,
            timeout=180,
        )
        print(
            f"IMPORT status={r.status_code} "
            f"payload_tasks={len(sanitized)}"
        )
        if r.status_code not in (200, 201):
            print(f"FAIL import: {r.text[:400]}", file=sys.stderr)
            return 5
        ir = r.json()
        print(
            f"IMPORT_OK task_count={ir.get('task_count')} "
            f"prediction_count={ir.get('prediction_count')}"
        )
    else:
        print(f"SKIP_IMPORT task_number={task_number}")

    pinfo = s.get(f"{url}/api/projects/{pid}/", timeout=30).json()
    tasks = s.get(f"{url}/api/projects/{pid}/tasks/?page_size=100", timeout=60).json()
    if isinstance(tasks, dict):
        tasks = tasks.get("tasks") or tasks.get("results") or []
    with_pred = sum(1 for t in tasks if (t.get("total_predictions") or 0) > 0)
    # image probe
    sample = next(
        (t for t in tasks if (t.get("data") or {}).get("stem") == "BNB_USDT_016560"),
        tasks[0] if tasks else None,
    )
    img_status = None
    if sample:
        img = (sample.get("data") or {}).get("image", "")
        if img.startswith("/"):
            img_status = s.get(url + img, timeout=30).status_code

    anon = requests.Session()
    anon.verify = False
    anon_status = anon.get(f"{url}/api/projects/{pid}/tasks/", timeout=30).status_code

    print(
        f"SUMMARY project_id={pid} title={PROJECT_TITLE} "
        f"task_number={pinfo.get('task_number')} listed={len(tasks)} "
        f"with_pred_on_page={with_pred} image_http={img_status} "
        f"anon_tasks={anon_status}"
    )
    ok = (
        pinfo.get("task_number") == EXPECTED_TASKS
        and img_status == 200
        and anon_status in (401, 403)
    )
    print("PASS" if ok else "FAIL")
    return 0 if ok else 6


if __name__ == "__main__":
    raise SystemExit(main())
