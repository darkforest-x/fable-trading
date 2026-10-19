"""Serve the exact Stitch document with separately versioned data adapters.

The source export is immutable. Production replaces only the CDN compiler and
demo handlers; markup, utility classes, and authored style rules stay intact.
No monitor, scanner, or execution service is instantiated by this module.
"""
from pathlib import Path
import json
import re

from fastapi import HTTPException
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse

from yoyo.monitor.asset_delivery import version

SOURCE = Path(__file__).parent / "source"
ASSETS = Path(__file__).parents[1] / "static" / "pro"
PAGES = tuple(json.loads((SOURCE / "manifest.json").read_text())["screens"])


def render(page: str, reference: bool = False) -> str:
    if page not in PAGES:
        raise HTTPException(404, "Unknown Pro screen")
    html = (SOURCE / f"{page}.html").read_text()
    # Source scripts include fake order confirmations. They must never execute
    # in the connected application, even if an adapter fails to initialize.
    html = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.S | re.I)
    html = re.sub(r"<style\b[^>]*>.*?</style>", "", html, flags=re.S | re.I)
    html = re.sub(r'\s+on\w+=(?:"[^"]*"|\'[^\']*\')', "", html)
    css = ASSETS / f"{page}.css"
    assets = f'<link rel="stylesheet" href="/static/pro/{page}.css?v={version(css)}">'
    mode = "reference" if reference else "connected"
    html = html.replace("<body ", f'<body data-pro-page="{page}" data-pro-mode="{mode}" ', 1)
    if not reference:
        # Hide demo contents until the adapter has replaced them with pending
        # states. The source reference remains available for fidelity checks.
        assets += '<style id="pro-pending">body{visibility:hidden}</style>'
    js = ASSETS / "app.js"
    assets += f'<script type="module" src="/static/pro/app.js?v={version(js)}"></script>'
    return html.replace("</head>", assets + "</head>", 1)


def install(app, *, connected: bool = False):
    @app.get("/pro", include_in_schema=False)
    def pro_redirect():
        return RedirectResponse("/pro/market")

    @app.get("/pro/", include_in_schema=False)
    def pro_home():
        return RedirectResponse("/pro/market")

    @app.get("/pro/source/DESIGN.md", include_in_schema=False)
    def pro_design():
        return FileResponse(SOURCE / "DESIGN.md", media_type="text/markdown")

    @app.get("/pro/source/{page}.html", include_in_schema=False)
    def pro_source(page: str):
        if page not in PAGES:
            raise HTTPException(404)
        return FileResponse(SOURCE / f"{page}.html", media_type="text/plain",
                            filename=f"stitch-{page}.html")

    @app.get("/pro/{page}", response_class=HTMLResponse, include_in_schema=False)
    def pro_page(page: str, reference: bool = not connected):
        if not reference and not connected:
            raise HTTPException(409, "Pro 功能接入尚未验收；原稿预览请使用 reference=true")
        return HTMLResponse(render(page, reference))
