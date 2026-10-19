"""Version display assets by content; never cache live API or account responses.

The HTML stays fresh, while matching content hashes allow browser-private reuse.
Unversioned study material revalidates its ETag so local edits remain visible.
"""
from __future__ import annotations

from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path
import re


@lru_cache(maxsize=512)
def _digest(path: str, mtime: int, size: int) -> str:
    return sha256(Path(path).read_bytes()).hexdigest()[:20]


def version(path: Path) -> str:
    stat = path.stat()
    return _digest(str(path), stat.st_mtime_ns, stat.st_size)


def render_index(root: Path) -> str:
    versions = {f.name: version(f) for f in root.iterdir() if f.suffix in {".js", ".css"}}
    page = (root / "index.html").read_text()
    page = re.sub(r'((?:src|href)="/static/)([^"?]+)(")',
                  lambda m: f'{m[1]}{m[2]}?v={versions[m[2]]}{m[3]}' if m[2] in versions else m[0], page)
    manifest = json.dumps(versions, separators=(",", ":")).replace("<", "\\u003c")
    return page.replace("<!-- asset-versions -->", f'<script type="application/json" id="asset-versions">{manifest}</script>')


def cache_policy(path: str, query_version: str | None, root: Path, status: int) -> str:
    if status not in {200, 206, 304} or not path.startswith("/static/"):
        return "no-store"
    relative = path.removeprefix("/static/")
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root.resolve()) or not candidate.is_file():
        return "no-store"
    if query_version and candidate.suffix in {".js", ".css"} and query_version == version(candidate):
        return "private, max-age=31536000, immutable"
    return "private, no-cache"
