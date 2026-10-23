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
BOOT_PATH = "/static/workspace-boot.js"
DEFERRED = re.compile(r'<script defer(?:="")? src="(/(?:static|vision-assets)/[^"?]+)"></script>')
STYLE_LINK = re.compile(r'<link\b(?=[^>]*\brel="stylesheet")(?=[^>]*\bhref="(/(?:static|vision-assets)/[^"?]+)(?:\?[^"]*)?")[^>]*>')
STYLE_GROUPS = ('base', 'shell', 'polish')


@lru_cache(maxsize=512)
def _digest(path: str, mtime: int, size: int) -> str:
    return sha256(Path(path).read_bytes()).hexdigest()[:20]


def version(path: Path) -> str:
    stat = path.stat()
    return _digest(str(path), stat.st_mtime_ns, stat.st_size)


def vision_root(root: Path) -> Path:
    return root.parent.parent / "vision_research" / "static"


@lru_cache(maxsize=8)
def _bundle(sources: tuple) -> tuple[str, str]:
    # Preserve the original execution order and script boundaries. No minifier
    # or external dependency is needed; gzip compresses the combined response.
    code = '\n'.join(';\n' + Path(path).read_text() for path, _, _ in sources)
    return code, sha256(code.encode()).hexdigest()[:20]


def boot_bundle(root: Path) -> tuple[str, str]:
    sources = []
    for url in DEFERRED.findall((root/'index.html').read_text()):
        base = root if url.startswith('/static/') else vision_root(root)
        relative = url.split('/', 2)[2]
        path = (base/relative).resolve()
        if not path.is_relative_to(base.resolve()) or path.suffix != '.js':
            raise ValueError('invalid_boot_script')
        stat = path.stat()
        sources.append((str(path), stat.st_mtime_ns, stat.st_size))
    return _bundle(tuple(sources))


def style_sections(page: str) -> list[str]:
    # Lazy page styles are inserted at these exact cascade boundaries.
    return re.split(r'(?=<meta id="workspace-(?:page-style|polish)")', page)


@lru_cache(maxsize=12)
def _styles(sources: tuple, fonts: tuple | None) -> tuple[str, str] | None:
    parts = []
    for path, _, _ in sources:
        code = Path(path).read_text()
        if fonts:
            code = code.replace("@import url('/static/fonts/stitch-fonts.css');", Path(fonts[0]).read_text())
        # Unsupported future imports/relative URLs keep their original links.
        # Current shell sheets use only absolute asset and data URLs.
        if re.search(r'@import\s', code) or any(not url.strip(' \"\'').startswith(('/', 'data:', 'http:', 'https:', '#'))
                                               for url in re.findall(r'url\(([^)]+)\)', code)):
            return None
        parts.append(code)
    body = '\n'.join(parts)
    return body, sha256(body.encode()).hexdigest()[:20]


def style_bundles(root: Path) -> dict:
    sections = style_sections((root/'index.html').read_text())
    if len(sections) != 3:
        return {}
    font = root/'fonts'/'stitch-fonts.css'
    fonts = (str(font), font.stat().st_mtime_ns, font.stat().st_size) if font.is_file() else None
    result = {}
    for name, section in zip(STYLE_GROUPS, sections):
        sources = []
        for url in STYLE_LINK.findall(section):
            base = root if url.startswith('/static/') else vision_root(root)
            path = (base/url.split('/', 2)[2]).resolve()
            if not path.is_relative_to(base.resolve()) or path.suffix != '.css':
                raise ValueError('invalid_boot_style')
            stat = path.stat(); sources.append((str(path), stat.st_mtime_ns, stat.st_size))
        if sources:
            bundle = _styles(tuple(sources), fonts)
            if bundle:
                result[name] = bundle
    return result


def render_index(root: Path) -> str:
    versions = {f.name: version(f) for f in root.iterdir() if f.suffix in {".js", ".css"}}
    versions.update({"/vision-assets/"+name: version(vision_root(root)/name)
                     for name in VISION_CODE if (vision_root(root)/name).is_file()})
    page = (root / "index.html").read_text()
    if DEFERRED.search(page):
        _, digest = boot_bundle(root)
        versions['workspace-boot.js'] = digest
        first = True
        def combine(match):
            nonlocal first
            if not first:
                return ''
            first = False
            return '<script defer="" src="'+BOOT_PATH+'"></script>'
        page = DEFERRED.sub(combine, page)
    sheets = style_bundles(root)
    sections = style_sections(page)
    for i, name in enumerate(STYLE_GROUPS):
        if name not in sheets:
            continue
        asset = f'workspace-{name}.css'
        versions[asset] = sheets[name][1]
        first = True
        def combine_style(match):
            nonlocal first
            if not first:
                return ''
            first = False
            return f'<link rel="stylesheet" href="/static/{asset}">'
        sections[i] = STYLE_LINK.sub(combine_style, sections[i])
    page = ''.join(sections)
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
    if path == BOOT_PATH:
        return ("public, max-age=31536000, immutable" if query_version == boot_bundle(root)[1]
                else "private, no-cache")
    for name in STYLE_GROUPS:
        if path == f'/static/workspace-{name}.css':
            bundle = style_bundles(root).get(name)
            return ("public, max-age=31536000, immutable" if bundle and query_version == bundle[1]
                    else "private, no-cache")
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
