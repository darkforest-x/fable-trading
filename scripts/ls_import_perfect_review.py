"""Create Label Studio projects for the perfect-pattern review and import the tasks.

Chunked the way rounds 3-10 were chunked in this same database: a review session
should end, not scroll forever. Two keys only, because at 24k tasks every extra
keystroke is another hour.

Reads the API token straight out of the local sqlite so no password is handled.
"""
from __future__ import annotations
import argparse, json, sqlite3, sys, time
from pathlib import Path
import urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("--tasks", default="reports/ls_pack/tasks.json")
ap.add_argument("--chunk", type=int, default=2000)
ap.add_argument("--prefix", default="perfect_v1")
ap.add_argument("--url", default="http://127.0.0.1:8081")
ap.add_argument("--db", default="label_studio_data/label_studio.sqlite3")
a = ap.parse_args()

tok = list(sqlite3.connect(a.db).execute(
    "select key from authtoken_token order by created desc limit 1"))[0][0]

CONFIG = """<View>
  <Header value="$symbol · $t · side=$side"/>
  <Image name="img" value="$image" maxWidth="100%" zoom="true"/>
  <Choices name="verdict" toName="img" choice="single" showInLine="true">
    <Choice value="perfect" hotkey="1" background="#2e7d32"/>
    <Choice value="no" hotkey="2" background="#c62828"/>
  </Choices>
</View>"""


def api(path, payload=None, method=None):
    url = a.url.rstrip("/") + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method or ("POST" if data else "GET"))
    req.add_header("Authorization", f"Token {tok}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=180) as r:
        body = r.read()
        return json.loads(body) if body else {}


tasks = json.load(open(a.tasks))
print(f"{len(tasks):,} tasks -> chunks of {a.chunk}", flush=True)
made = []
for i in range(0, len(tasks), a.chunk):
    part = tasks[i:i + a.chunk]
    name = f"{a.prefix}_chunk{i // a.chunk + 1:02d}"
    proj = api("/api/projects/", {
        "title": name, "label_config": CONFIG,
        "description": "1 = 完美形态，2 = 不是。上图为形态放大，下图为 200 根上下文 + 后续 48 根走势（灰线=决策点）",
    })
    pid = proj["id"]
    for j in range(0, len(part), 500):
        api(f"/api/projects/{pid}/import", part[j:j + 500])
    made.append((pid, name, len(part)))
    print(f"  {name}  id={pid}  {len(part)} tasks", flush=True)

print("\n=== 已建 ===")
for pid, name, n in made:
    print(f"  {a.url}/projects/{pid}/data   {name}  {n} 张")
json.dump([{"id": p, "name": n, "n": c} for p, n, c in made],
          open("reports/ls_pack/projects.json", "w"), ensure_ascii=False, indent=1)
