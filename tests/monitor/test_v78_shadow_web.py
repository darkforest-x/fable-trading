"""Contract checks for the isolated V7/V8 forward-shadow dashboard view."""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import shutil
import subprocess

import pytest


WEB = Path(__file__).resolve().parents[2] / "yoyo" / "monitor" / "static"


class _IdParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        value = dict(attrs).get("id")
        if value:
            self.ids.add(value)


def test_shadow_view_has_explicit_research_only_contract_and_required_dom() -> None:
    html = (WEB / "index.html").read_text()
    parser = _IdParser()
    parser.feed(html)
    assert {
        "shadow-view", "nav-shadow-count", "shadow-event-count", "shadow-v8-count",
        "shadow-cell-count", "shadow-market-grid", "shadow-rows", "shadow-empty",
        "shadow-activation", "shadow-last-scan", "primary-metrics",
    } <= parser.ids
    assert "仅使用启用后的已收盘 K 线 · 不推送 · 不下单" in html
    assert 'data-view="shadow"' in html
    assert 'data-shadow-timeframe="30m"' in html
    assert 'data-shadow-timeframe="1H"' in html
    assert 'data-shadow-timeframe="4H"' in html


def test_shadow_client_reads_isolated_routes_and_opens_full_card() -> None:
    script = (WEB / "app.js").read_text()
    assert 'api("/api/shadow/status")' in script
    assert 'api("/api/shadow/events?limit=200")' in script
    assert 'api("/api/shadow/market-state?limit=12")' in script
    assert 'data-tradingview-action="shadow"' in script
    assert 'type === "shadow"' in script


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_shadow_dashboard_javascript_parses() -> None:
    subprocess.run(["node", "--check", str(WEB / "app.js")], check=True)
