"""Immutable URLs must track file bytes; live/private API responses stay no-store."""
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from fastapi.testclient import TestClient
from yoyo.monitor.asset_delivery import cache_policy, render_index, version, vision_root, boot_bundle, BOOT_PATH, style_bundles


def test_versions_invalidate_on_content_change_and_escape_manifest(tmp_path):
    script = tmp_path / 'app.js'
    script.write_text('a' * 3000)
    (tmp_path / 'index.html').write_text('<!-- asset-versions --><script src="/static/app.js"></script>')
    old = version(script)
    assert f'app.js?v={old}' in render_index(tmp_path)
    assert cache_policy('/static/app.js', old, tmp_path, 200) == 'public, max-age=31536000, immutable'
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


def test_inline_startup_and_versioned_chart_code_never_cache_api_or_study_data(tmp_path):
    root = tmp_path/'monitor'/'static'; root.mkdir(parents=True)
    vision = vision_root(root); (vision/'vendor').mkdir(parents=True)
    vendor = vision/'vendor'/'lightweight-charts.standalone.production.js'
    vendor.write_text('window.LightweightCharts = {}')
    (vision/'chart-tools.js').write_text('/* chart */')
    (vision/'study.json').write_text('{"private":true}')
    (root/'theme.js').write_text('const text = "</SCRIPT>";')
    (root/'startup-hints.js').write_text('/* start selected requests */')
    (root/'mobile.css').write_text('/* mobile */')
    (root/'index.html').write_text('<!-- asset-versions --><script src="/static/startup-hints.js"></script>'
                                 '<script src="/static/theme.js"></script><link href="/static/mobile.css?v=old">'
                                 '<script defer src="/vision-assets/chart-tools.js"></script>')
    html = render_index(root)
    assert '<script>/* start selected requests */</script>' in html
    assert '<script>const text = "<\\/script>";</script>' in html
    assert 'src="/static/theme.js' not in html
    assert 'mobile.css?v='+version(root/'mobile.css') in html
    assert BOOT_PATH+'?v='+boot_bundle(root)[1] in html
    assert '/* chart */' in boot_bundle(root)[0]
    assert '"/vision-assets/vendor/lightweight-charts.standalone.production.js":"'+version(vendor)+'"' in html
    assert cache_policy('/vision-assets/vendor/'+vendor.name, version(vendor), root, 200).startswith('public,')
    assert cache_policy('/vision-assets/vendor/'+vendor.name, 'stale', root, 200) == 'private, no-cache'
    for path in ('/', '/api/monday-fib', '/api/monday-fib/candles', '/api/paper/status', '/vision-assets/study.json'):
        assert cache_policy(path, version(vendor), root, 200) == 'no-store'


def test_boot_bundle_preserves_order_and_invalidates_when_any_script_changes(tmp_path):
    (tmp_path/'first.js').write_text('window.first = 1; // end comment')
    (tmp_path/'second.js').write_text('(function(){window.second = window.first+1;})();')
    (tmp_path/'index.html').write_text('<!-- asset-versions -->'
                                     '<script defer="" src="/static/first.js"></script>'
                                     '<meta id="workspace-polish">'
                                     '<script defer="" src="/static/second.js"></script>')
    body, old = boot_bundle(tmp_path)
    assert body.index('window.first =') < body.index('window.second =')
    assert '// end comment\n;\n(function' in body
    html = render_index(tmp_path)
    assert html.count('<script defer=') == 1
    assert '<meta id="workspace-polish">' in html
    assert cache_policy(BOOT_PATH, old, tmp_path, 200).startswith('public,')
    (tmp_path/'second.js').write_text('window.second = 3;')
    assert boot_bundle(tmp_path)[1] != old
    assert cache_policy(BOOT_PATH, old, tmp_path, 200) == 'private, no-cache'


def test_style_bundles_preserve_lazy_cascade_slots_and_inline_local_font_import(tmp_path):
    (tmp_path/'fonts').mkdir()
    (tmp_path/'fonts'/'stitch-fonts.css').write_text('@font-face{font-family:Demo;src:url(/static/fonts/a.ttf)}')
    (tmp_path/'base.css').write_text('.card{color:red}')
    (tmp_path/'shell.css').write_text('.card{color:blue}')
    (tmp_path/'polish.css').write_text("@import url('/static/fonts/stitch-fonts.css');\n.card{color:green}")
    (tmp_path/'index.html').write_text('<!-- asset-versions -->'
                                     '<link href="/static/base.css" rel="stylesheet">'
                                     '<meta id="workspace-page-style">'
                                     '<link rel="stylesheet" href="/static/shell.css?v=old">'
                                     '<meta id="workspace-polish">'
                                     '<link href="/static/polish.css" rel="stylesheet">')
    bundles = style_bundles(tmp_path); html = render_index(tmp_path)
    assert len(bundles) == 3
    assert html.index('href="/static/workspace-base.css') < html.index('<meta id="workspace-page-style">')
    assert html.index('<meta id="workspace-page-style">') < html.index('href="/static/workspace-shell.css') < html.index('<meta id="workspace-polish">')
    assert html.index('<meta id="workspace-polish">') < html.index('href="/static/workspace-polish.css')
    assert '@import' not in bundles['polish'][0] and '@font-face' in bundles['polish'][0]
    assert bundles['polish'][0].index('@font-face') < bundles['polish'][0].index('.card')
    assert cache_policy('/static/workspace-polish.css', bundles['polish'][1], tmp_path, 200).startswith('public,')
    (tmp_path/'fonts'/'stitch-fonts.css').write_text('@font-face{font-family:Changed;}')
    assert style_bundles(tmp_path)['polish'][1] != bundles['polish'][1]
    # A future unsupported relative asset must retain its own stylesheet URL.
    (tmp_path/'shell.css').write_text('.card{background:url(./relative.svg)}')
    assert 'shell' not in style_bundles(tmp_path)
    assert 'href="/static/shell.css?v='+version(tmp_path/'shell.css') in render_index(tmp_path)
