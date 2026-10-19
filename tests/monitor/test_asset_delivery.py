"""Immutable URLs must track file bytes; live/private API responses stay no-store."""
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from fastapi.testclient import TestClient
from yoyo.monitor.asset_delivery import cache_policy, render_index, version


def test_versions_invalidate_on_content_change_and_escape_manifest(tmp_path):
    script = tmp_path / 'app.js'
    script.write_text('a' * 3000)
    (tmp_path / 'index.html').write_text('<!-- asset-versions --><script src="/static/app.js"></script>')
    old = version(script)
    assert f'app.js?v={old}' in render_index(tmp_path)
    assert cache_policy('/static/app.js', old, tmp_path, 200).endswith('immutable')
    script.write_text('b' * 3000)
    assert version(script) != old
    assert cache_policy('/static/app.js', old, tmp_path, 200) == 'private, no-cache'
    assert cache_policy('/api/status', old, tmp_path, 200) == 'no-store'
    assert cache_policy('/static/../private.json', None, tmp_path, 200) == 'no-store'
    assert cache_policy('/static/app.js', old, tmp_path, 404) == 'no-store'


def test_compression_revalidation_and_html_freshness(tmp_path):
    (tmp_path/'app.js').write_text('/* repeat */' * 2000)
    (tmp_path/'index.html').write_text('<!-- asset-versions --><script src="/static/app.js"></script>')
    app=FastAPI()
    app.add_middleware(GZipMiddleware,minimum_size=1000,compresslevel=6)
    @app.middleware('http')
    async def caching(request,call_next):
        response=await call_next(request)
        response.headers['Cache-Control']=cache_policy(request.url.path,request.query_params.get('v'),tmp_path,response.status_code)
        return response
    @app.get('/')
    def index():return HTMLResponse(render_index(tmp_path))
    app.mount('/static',StaticFiles(directory=tmp_path))
    with TestClient(app) as client:
        html=client.get('/')
        assert html.headers['cache-control']=='no-store'
        response=client.get('/static/app.js',headers={'Accept-Encoding':'gzip'})
        assert response.headers['content-encoding']=='gzip'
        assert int(response.headers['content-length']) < 1000
        assert response.headers['cache-control']=='private, no-cache'
        assert client.get('/static/app.js',headers={'If-None-Match':response.headers['etag']}).status_code==304
        url='/static/app.js?v='+version(tmp_path/'app.js')
        assert client.get(url).headers['cache-control'].endswith('immutable')
