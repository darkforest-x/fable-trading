"""Content-version public code for edge reuse; live/account responses stay no-store.

Only matching immutable JS/CSS URLs enter a shared cache. HTML and unversioned
resources remain fresh, including the small inline pre-paint/startup scripts.
"""
from __future__ import annotations

from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path
import re

VISION_CODE = frozenset({"chart-tools.js", "chart-tools.css", "vendor/lightweight-charts.standalone.production.js"})


@lru_cache(maxsize=512)
def _digest(path: str, mtime: int, size: int) -> str:
    return sha256(Path(path).read_bytes()).hexdigest()[:20]


def version(path: Path) -> str:
    stat = path.stat()
    return _digest(str(path), stat.st_mtime_ns, stat.st_size)


def vision_root(root: Path) -> Path:
    return root.parent.parent / "vision_research" / "static"


def render_index(root: Path) -> str:
    versions = {f.name: version(f) for f in root.iterdir() if f.suffix in {".js", ".css"}}
    versions.update({"/vision-assets/"+name: version(vision_root(root)/name)
                     for name in VISION_CODE if (vision_root(root)/name).is_file()})
    page = (root / "index.html").read_text()
    # These local scripts must run before the first stylesheet, without a
    # parser-blocking network round trip. Keep source files independently tested.
    for name in ("theme.js", "startup-hints.js"):
        source = root / name
        if source.is_file():
            code = re.sub(r"</script", r"<\\/script", source.read_text(), flags=re.I)
            page = page.replace(f'<script src="/static/{name}"></script>', f'<script>{code}</script>')
    def rewrite(match):
        prefix, name, end = match.groups()
        key = name if prefix.endswith('/static/') else '/vision-assets/'+name
        return f'{prefix}{name}?v={versions[key]}{end}' if key in versions else match[0]
    page = re.sub(r'((?:src|href)="/(?:static|vision-assets)/)([^"?]+)(?:\?[^"]*)?(")', rewrite, page)
    manifest = json.dumps(versions, separators=(",", ":")).replace("<", "\\u003c")
    return page.replace("<!-- asset-versions -->", f'<script type="application/json" id="asset-versions">{manifest}</script>')


def cache_policy(path: str, query_version: str | None, root: Path, status: int) -> str:
    if status not in {200, 206, 304}:
        return "no-store"
    if path.startswith("/static/"):
        relative = path.removeprefix("/static/")
    elif path.startswith("/vision-assets/") and path.removeprefix("/vision-assets/") in VISION_CODE:
        relative, root = path.removeprefix("/vision-assets/"), vision_root(root)
    else:
        return "no-store"
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root.resolve()) or not candidate.is_file():
        return "no-store"
    if query_version and candidate.suffix in {".js", ".css"} and query_version == version(candidate):
        return "public, max-age=31536000, immutable"
    return "private, no-cache"
