"""The source export stays exact; previews cannot accidentally become live orders."""
import re
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from yoyo.monitor.pro.delivery import ASSETS, PAGES, SOURCE, render
from yoyo.monitor.pro.preview import create_app

ARCHIVE = Path(__file__).resolve().parents[3] / 'docs/design/stitch-20261017'


def body(html):
    value = re.search(r'<body\b[^>]*>(.*?)</body>', html, re.S).group(1)
    value = re.sub(r'<script\b[^>]*>.*?</script>', '', value, flags=re.S | re.I)
    return re.sub(r'\s+on\w+=(?:"[^"]*"|\'[^\']*\')', '', value)


@pytest.mark.parametrize('page', PAGES)
def test_source_and_rendered_markup_preserved(page):
    source = (SOURCE / f'{page}.html').read_text()
    assert (SOURCE / f'{page}.html').read_bytes() == (ARCHIVE / f'{page}.html').read_bytes()
    result = render(page, reference=True)
    assert body(source) == body(result)
    assert 'cdn.tailwindcss.com' not in result
    assert 'onclick=' not in result
    assert len((ASSETS / f'{page}.css').read_text()) > 10000
    assert 'data-pro-mode="reference"' in result


def test_native_design_document_preserved():
    assert (SOURCE / 'DESIGN.md').read_bytes() == (ARCHIVE / 'DESIGN.md').read_bytes()


def test_routes_are_allowlisted_and_live_is_not_claimed_ready():
    with TestClient(create_app()) as client:
        for page in PAGES:
            response = client.get('/pro/' + page)
            assert response.status_code == 200
            assert 'data-pro-mode="reference"' in response.text
        assert client.get('/pro/not-a-screen').status_code == 404
        assert client.get('/pro/account?reference=false').status_code == 409
        assert client.post('/api/manual-live/id/confirm', json={}).status_code == 404
        assert client.get('/pro/source/account.html').content == (SOURCE / 'account.html').read_bytes()


def test_preview_does_not_create_execution_or_scan_services():
    assert not vars(create_app().state).get('_state')
