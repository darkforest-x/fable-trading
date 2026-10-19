"""Fixed exposure sizing and durable, isolated journal raster attachments."""
import base64
import io
from decimal import Decimal
from types import SimpleNamespace

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from PIL import Image
import pytest

from yoyo.research_workspace import paper_desk as book, paper_desk_api as api
from yoyo.research_workspace.api import same_origin
from yoyo.research_workspace.paper_desk_images import JournalImages, MAX_BYTES, MAX_IMAGES
from yoyo.research_workspace.paper_desk_journal import PositionJournal

HEADERS = {'origin': 'http://testserver', 'sec-fetch-site': 'same-origin'}


def raster(color=0, fmt='PNG'):
    stream = io.BytesIO()
    Image.new('RGB', (12, 8), (color, 40, 80)).save(stream, format=fmt)
    return stream.getvalue()


@pytest.fixture
def env(tmp_path):
    desk = book.PaperDesk(tmp_path)
    account = desk.create_account('Amount QA', '1000', 'account-amount')['id']
    at = book.clock_ms()
    quote = desk.save_quote(dict(symbol='BTC-USDT-SWAP', bid='100', ask='100', source_ms=at, received_ms=at))
    return SimpleNamespace(desk=desk, account=account, quote=quote, images=JournalImages(desk))


def open_position(env):
    return env.desk.open(env.account, 'long', '1', env.quote['id'], '', 'position-amount', {}, 100)['position']['id']


def client_for(env):
    app = FastAPI()
    router = APIRouter(prefix='/api/research', dependencies=[Depends(same_origin)])
    api.install(router, app, SimpleNamespace(runtime=env.desk.path.parent))
    app.state.paper_desk_quotes = SimpleNamespace(get=lambda symbols: {env.quote['symbol']: env.quote})
    app.include_router(router)
    return TestClient(app)


@pytest.mark.parametrize('side,leverage', [('long',100), ('short',100), ('long',20)])
def test_fixed_amount_is_exposure_not_collateral(env, side, leverage):
    sized = env.desk.size_by_notional(env.account, side, env.quote['id'], leverage, '10000', {})
    assert sized['sizing'] == 'fixed_notional'
    assert Decimal(sized['notional']) == 10000
    assert Decimal(sized['position_multiple']) == 10
    assert Decimal(sized['quantity']) == 100
    assert Decimal(sized['margin']) == Decimal(10000)/leverage
    assert Decimal(sized['fee']) == 5
    result = env.desk.open(env.account, side, sized['quantity'], env.quote['id'], '', 'fixed-entry', {}, leverage)
    assert Decimal(result['position']['quantity']) == 100


def test_fixed_amount_rounds_down_and_keeps_budget_stop_and_freshness_checks(env, monkeypatch):
    sized = env.desk.size_by_notional(env.account, 'long', env.quote['id'], 100, '123.4567891234567', {})
    assert Decimal(sized['notional']) <= Decimal('123.4567891234567')
    with pytest.raises(ValueError, match='资金不足'):
        env.desk.size_by_notional(env.account, 'long', env.quote['id'], 100, '100000', {})
    with pytest.raises(ValueError):
        env.desk.size_by_notional(env.account, 'long', env.quote['id'], 100, '10000', {}, stop_loss='101')
    monkeypatch.setattr(book, 'clock_ms', lambda: env.quote['expires_ms']+1)
    with pytest.raises(ValueError, match='过期'):
        env.desk.size_by_notional(env.account, 'long', env.quote['id'], 100, '10000', {})


@pytest.mark.parametrize('amount', ['0', '-1', 'nan', 'Infinity', '1e100'])
def test_invalid_amount_never_opens_a_position(env, amount):
    with pytest.raises(ValueError):
        env.desk.size_by_notional(env.account, 'long', env.quote['id'], 100, amount, {})
    assert env.desk.export(env.account)['positions'] == []


def test_amount_api_rejects_ambiguous_modes_before_writing_quote(env):
    with client_for(env) as client:
        url='/api/research/paper-desk/open-preview'
        params=dict(account_id=env.account,symbol=env.quote['symbol'],leverage=100,notional_usdt='10000')
        response=client.get(url,params=params)
        assert response.status_code == 200, response.text
        assert response.json()['sizing'] == 'fixed_notional'
        assert Decimal(response.json()['notional']) == 10000
        with env.desk.connect() as db:
            count=db.execute('SELECT count(*) FROM quotes').fetchone()[0]
        assert client.get(url,params=dict(params,position_multiple=10)).status_code == 422
        with env.desk.connect() as db:
            assert db.execute('SELECT count(*) FROM quotes').fetchone()[0] == count


@pytest.mark.parametrize('fmt,mime', [('PNG','image/png'),('JPEG','image/jpeg'),('WEBP','image/webp')])
def test_images_survive_restart_and_export_without_financial_mutation(env, fmt, mime):
    position=open_position(env);before=env.desk.export(env.account);raw=raster(fmt=fmt)
    images=env.images.add(position, '../截图.'+fmt.lower(), raw)
    assert len(images)==1 and images[0]['mime']==mime and images[0]['name'].startswith('截图')
    assert env.images.add(position, 'retry',raw)==images
    restarted=book.PaperDesk(env.desk.path.parent)
    assert PositionJournal(restarted).get(position)['images']==images
    exported=restarted.export(env.account)
    assert exported['schema_version']==8
    assert base64.b64decode(exported['journal_images'][0]['data_base64'])==raw
    exported['journal_images']=[]
    assert exported==before


def test_images_follow_closed_position_and_cannot_be_read_from_another_position(env):
    position=open_position(env);image=env.images.add(position,'x.png',raster())[0]
    env.desk.close(position,'1',env.quote['id'],'close-for-images')
    other=env.desk.open(env.account,'long','1',env.quote['id'],'','new-lifecycle',{},100)['position']['id']
    assert PositionJournal(env.desk).get(position)['images']==[image]
    assert PositionJournal(env.desk).get(other)['images']==[]
    with pytest.raises(KeyError):env.images.get(other,image['id'])
    with pytest.raises(KeyError):env.images.visibility(other,image['id'],True)
    with pytest.raises(KeyError):env.images.add('missing','x.png',raster())


def test_removal_restore_limits_and_deduplication(env):
    position=open_position(env)
    for color in range(MAX_IMAGES):env.images.add(position,'x.png',raster(color))
    with pytest.raises(ValueError,match='12'):env.images.add(position,'extra',raster(100))
    images=env.images.add(position,'retry',raster(0));first=images[0]['id']
    assert len(images)==MAX_IMAGES
    assert sum(not i['deleted'] for i in env.images.visibility(position,first,True))==MAX_IMAGES-1
    assert len(env.images.visibility(position,first,False))==MAX_IMAGES
    env.images.visibility(position,first,True)
    env.images.add(position,'new',raster(100))
    with pytest.raises(ValueError,match='12'):env.images.visibility(position,first,False)


@pytest.mark.parametrize('raw', [b'', b'<svg onload="alert(1)"></svg>', b'not an image', b'x'*(MAX_BYTES+1)])
def test_invalid_images_do_not_persist(env,raw):
    position=open_position(env)
    with pytest.raises(ValueError):env.images.add(position,'bad',raw)
    assert env.desk.export(env.account)['journal_images']==[]


def test_image_api_checks_origin_limits_mime_and_scope(env):
    position=open_position(env);url=f'/api/research/paper-desk/positions/{position}/images'
    with client_for(env) as client:
        assert client.post(url,content=raster()).status_code==403
        assert client.post(url,content=b'x'*(MAX_BYTES+1),headers=HEADERS).status_code==413
        assert client.post(url,content=b'<svg/>',headers=HEADERS).status_code==409
        uploaded=client.post(url,content=raster(),headers=dict(HEADERS,**{'x-filename':'%E6%88%AA%E5%9B%BE.png','content-type':'image/jpeg'}))
        assert uploaded.status_code==200,uploaded.text
        image=uploaded.json()[0];assert image['name']=='截图.png'
        response=client.get(url+'/'+image['id'])
        assert response.content==raster() and response.headers['content-type']=='image/png'
        assert response.headers['x-content-type-options']=='nosniff'
        assert client.get(url.replace(position,'wrong')+'/'+image['id']).status_code==404
        assert client.post(url+'/'+image['id']+'/visibility',json={'deleted':'yes'},headers=HEADERS).status_code==422
        assert client.post(url+'/'+image['id']+'/visibility',json={'deleted':True},headers=HEADERS).json()[0]['deleted']==1
