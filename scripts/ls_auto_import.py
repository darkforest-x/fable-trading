"""Auto-create Label Studio projects and import task packs via API.
Usage: PYTHONPATH=. python3 scripts/ls_auto_import.py <title> <tasks.json> [config.xml]
Creates project + local-files storage + imports tasks. Credentials from env file.
"""
from __future__ import annotations
import http.cookiejar, json, re, sys, urllib.parse, urllib.request
from pathlib import Path
PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR / "scripts"))
from export_owner_labels import BASE, load_creds

def session():
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    page = op.open(f"{BASE}/user/login/", timeout=15).read().decode()
    m = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page)
    csrf = m.group(1) if m else next((c.value for c in jar if c.name == "csrftoken"), "")
    u, p = load_creds()
    op.open(urllib.request.Request(f"{BASE}/user/login/",
        data=urllib.parse.urlencode({"email": u, "password": p, "csrfmiddlewaretoken": csrf}).encode(),
        headers={"Referer": f"{BASE}/user/login/"}), timeout=15)
    tok = json.loads(op.open(f"{BASE}/api/current-user/token", timeout=15).read())["token"]
    return tok

def api(tok, method, path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method,
        headers={"Authorization": f"Token {tok}", "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=120).read() or b"{}")

def main() -> int:
    title, tasks_file = sys.argv[1], sys.argv[2]
    config = Path(sys.argv[3] if len(sys.argv) > 3 else "output/label_studio/label_config_v2.xml").read_text()
    tok = session()
    existing = api(tok, "GET", "/api/projects?page_size=100").get("results", [])
    hit = next((p for p in existing if p.get("title") == title), None)
    if hit:
        pid = hit["id"]
        print(f"reusing existing project '{title}' (id {pid})")
    else:
        proj = api(tok, "POST", "/api/projects", {"title": title, "label_config": config})
        pid = proj["id"]
    api(tok, "POST", "/api/storages/localfiles", {
        "project": pid, "title": "datasets", "path": "/label-studio/files",
        "use_blob_urls": False})
    tasks = json.loads(Path(tasks_file).read_text())
    api(tok, "POST", f"/api/projects/{pid}/import", tasks)
    print(f"project '{title}' (id {pid}): imported {len(tasks)} tasks")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
