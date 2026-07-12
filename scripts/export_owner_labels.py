"""Export the owner's Label Studio annotations (round-1 golden set).

Logs into the local LS container with the bootstrap credentials from
scripts/.label_studio.env (values are never printed), grabs the newest
project's JSON export, and writes output/label_studio/export_round1.json.
"""
from __future__ import annotations

import http.cookiejar
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8081"
OUT = PROJECT_DIR / "output" / "label_studio" / "export_round1.json"


def load_creds() -> tuple[str, str]:
    env = {}
    for line in (PROJECT_DIR / "scripts" / ".label_studio.env").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    user = env.get("LABEL_STUDIO_USERNAME") or "fable-review@example.com"
    pw = env.get("LABEL_STUDIO_PASSWORD") or "fable-review-local"
    return user, pw


def main() -> int:
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    login_page = opener.open(f"{BASE}/user/login/", timeout=15).read().decode()
    m = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', login_page)
    csrf = m.group(1) if m else next(
        (c.value for c in jar if c.name == "csrftoken"), "")
    user, pw = load_creds()
    body = urllib.parse.urlencode({
        "email": user, "password": pw, "csrfmiddlewaretoken": csrf}).encode()
    req = urllib.request.Request(f"{BASE}/user/login/", data=body,
                                 headers={"Referer": f"{BASE}/user/login/"})
    opener.open(req, timeout=15)

    projects = json.loads(opener.open(f"{BASE}/api/projects", timeout=15).read())
    results = projects.get("results", projects)
    assert results, "no Label Studio projects found"
    pid = sorted(results, key=lambda p: p["id"])[-1]["id"]
    data = opener.open(
        f"{BASE}/api/projects/{pid}/export?exportType=JSON&download_all_tasks=false",
        timeout=60).read()
    OUT.write_bytes(data)
    tasks = json.loads(data)
    n_ann = sum(1 for t in tasks if t.get("annotations"))
    print(f"project {pid}: exported {len(tasks)} tasks, {n_ann} annotated -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
