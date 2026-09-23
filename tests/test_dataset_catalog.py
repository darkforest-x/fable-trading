"""Dataset reuse must preserve clocks, conflicts, lineage and old file paths."""
import gzip
import json
from pathlib import Path
import subprocess
import sys

import pytest

from yoyo.data.dataset_catalog import DatasetCatalog, build_index, dataset_id, read_market_data, sources
from yoyo.data.dataset_maintenance import clone_duplicate, digest, move_dataset

CSV = 'open_time,open,high,low,close,volume,confirm\n2026-01-01T00:00:00Z,1,2,1,2,10,1\n2026-01-01T00:05:00Z,2,3,2,3,11,1\n2026-01-01T00:10:00Z,3,4,3,4,12,1\n'


def market(tmp_path, text=CSV):
    folder=tmp_path/'data/kline_binance_um5m'
    folder.mkdir(parents=True)
    path=folder/'binance_um_ETHUSDT_5m_3.csv';path.write_text(text)
    return folder,path


def read(root, **extra):
    return read_market_data(root=root,dataset_id=dataset_id('data/kline_binance_um5m'),symbol='ETHUSDT',timeframe='5m',
        start='2026-01-01T00:00:00Z',end='2026-01-01T00:10:00Z',**extra)


def test_bounded_offline_read_and_future_rows(tmp_path):
    _,path=market(tmp_path);build_index(tmp_path)
    result=read(tmp_path)
    assert len(result)==2 and result.close.tolist()==[2,3]
    assert result.open_time.dt.tz is not None
    path.write_text(CSV.replace(',3,4,3,4,12,1',',3,9000,3,9000,12,1'))
    with pytest.raises(ValueError,match='变动'):read(tmp_path)
    build_index(tmp_path)
    assert result.equals(read(tmp_path))
    with pytest.raises(ValueError,match='请求过大'):read(tmp_path,max_rows=1)


def test_conflicting_candle_and_gap_fail_closed(tmp_path):
    folder,_=market(tmp_path)
    (folder/'binance_um_ETHUSDT_5m_4.csv').write_text(CSV.replace(',2,3,2,3,11,1',',2,4,2,4,11,1'))
    build_index(tmp_path)
    with pytest.raises(ValueError,match='冲突'):read(tmp_path)
    (folder/'binance_um_ETHUSDT_5m_4.csv').unlink()
    (folder/'binance_um_ETHUSDT_5m_3.csv').write_text(CSV.replace('2026-01-01T00:05:00Z,2,3,2,3,11,1\n',''))
    build_index(tmp_path)
    with pytest.raises(ValueError,match='缺口'):read(tmp_path)


def test_gzip_provenance_and_explicit_range(tmp_path):
    folder=tmp_path/'data/research/frozen';folder.mkdir(parents=True)
    with gzip.open(folder/'ETHUSDT.csv.gz','wt') as f:f.write(CSV)
    (folder/'manifest.json').write_text(json.dumps({'streams':[{'path':'ETHUSDT.csv.gz','inputs':[{'path':'data/kline_binance_um5m/series.csv'}],'rows':3,'last_ms':1767226200000}]}))
    build_index(tmp_path)
    key=dataset_id('data/research/frozen')
    item=DatasetCatalog(tmp_path).detail(key)['dataset']
    assert item['backtest_ready'] and item['exchanges']==['binance']
    frame=read_market_data(root=tmp_path,dataset_id=key,symbol='ETHUSDT',timeframe='5m',start='2026-01-01',end='2026-01-01T00:15:00Z')
    assert len(frame)==3


def test_catalog_excludes_secrets_and_external_files(tmp_path):
    _,path=market(tmp_path)
    (tmp_path/'data/okx_demo_keys.json').write_text('secret')
    outside=tmp_path.parent/(tmp_path.name+'-outside');outside.mkdir()
    (outside/'keys.txt').write_text('secret')
    (path.parent/'external.txt').symlink_to(outside/'keys.txt')
    (tmp_path/'data/kline_cache').symlink_to(outside,target_is_directory=True)
    build_index(tmp_path);cat=DatasetCatalog(tmp_path)
    assert cat.overview()['summary']['missing_count']==1
    key=dataset_id('data/kline_binance_um5m')
    assert cat.detail(key,1,0)['total']==1
    with pytest.raises(KeyError):cat.file(key,'data/okx_demo_keys.json')
    with pytest.raises(KeyError):cat.file(key,'data/kline_binance_um5m/external.txt')
    assert next(x for x in sources(tmp_path) if x['name']=='kline_cache')['storage']=='external'


def test_relocation_preserves_bytes_clock_and_dataset_id(tmp_path):
    folder,path=market(tmp_path);before=digest(path);mtime=path.stat().st_mtime_ns
    subprocess.run(['git','init','-q',str(tmp_path)],check=True)
    record=move_dataset('data/kline_binance_um5m',root=tmp_path)
    assert folder.is_symlink() and record['status']=='complete'
    assert digest(path)==before and path.stat().st_mtime_ns==mtime
    build_index(tmp_path)
    assert DatasetCatalog(tmp_path).detail(dataset_id('data/kline_binance_um5m'))['dataset']['storage']=='managed'
    assert len(read(tmp_path))==2
    assert move_dataset('data/kline_binance_um5m',root=tmp_path)['status']=='already_managed'


def test_tracked_data_and_external_cache_cannot_move(tmp_path):
    _,path=market(tmp_path);subprocess.run(['git','init','-q',str(tmp_path)],check=True)
    subprocess.run(['git','-C',str(tmp_path),'add',str(path)],check=True)
    with pytest.raises(ValueError,match='Tracked'):move_dataset('data/kline_binance_um5m',root=tmp_path)
    (tmp_path/'data/kline_cache').symlink_to(tmp_path.parent,target_is_directory=True)
    with pytest.raises(ValueError,match='symlink'):move_dataset('data/kline_cache',root=tmp_path)


@pytest.mark.skipif(sys.platform!='darwin',reason='APFS COW behavior is platform-specific')
def test_clone_cleanup_preserves_independent_writes(tmp_path):
    a,b=tmp_path/'a.csv',tmp_path/'b.csv';a.write_text(CSV);b.write_text(CSV)
    result=clone_duplicate(a,b)
    assert result['bytes']==a.stat().st_size and digest(a)==digest(b)
    assert a.stat().st_ino!=b.stat().st_ino
    b.write_text('private edit')
    assert a.read_text()==CSV
    assert clone_duplicate(a,b) is None


def test_move_recovers_interrupt_after_rename(tmp_path, monkeypatch):
    import yoyo.data.dataset_maintenance as maintenance
    _,path=market(tmp_path);sha=digest(path)
    subprocess.run(['git','init','-q',str(tmp_path)],check=True)
    rename=maintenance.os.rename
    def interrupted(a,b):
        rename(a,b)
        raise RuntimeError('simulated interruption')
    with monkeypatch.context() as patch:
        patch.setattr(maintenance.os,'rename',interrupted)
        with pytest.raises(RuntimeError):move_dataset('data/kline_binance_um5m',root=tmp_path)
    assert not path.exists()
    recovered=move_dataset('data/kline_binance_um5m',root=tmp_path)
    assert recovered['recovered'] and digest(path)==sha


def test_api_dataset_pagination_origin_and_download(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from yoyo.research_workspace.api import install
    _,path=market(tmp_path)
    app=FastAPI();install(app,tmp_path/'runtime',root=tmp_path,launch_worker=False)
    with TestClient(app) as client:
        assert client.get('/api/research/datasets').json()['indexed'] is False
        assert client.post('/api/research/datasets/reindex').status_code==403
        assert client.post('/api/research/datasets/reindex',headers={'origin':'http://testserver'}).status_code==202
        build_index(tmp_path)
        key=dataset_id('data/kline_binance_um5m');base='/api/research/datasets/'+key
        assert client.get(base,params={'limit':0}).status_code==422
        assert client.get(base,params={'offset':1}).json()['files']==[]
        detail=client.get(base).json();rel=detail['files'][0]['path']
        assert client.get(base+'/file',params={'path':rel}).text==CSV
        assert client.get(base+'/file',params={'path':'../secrets'}).status_code==404
        assert client.get('/api/research/datasets/missing').status_code==404


def test_ancestor_symlink_cannot_relocate_external_input(tmp_path):
    outside=tmp_path.parent/(tmp_path.name+'-outside');(outside/'inputs').mkdir(parents=True)
    (outside/'inputs/bars.csv').write_text(CSV)
    (tmp_path/'experiments/active').mkdir(parents=True)
    (tmp_path/'experiments/active/exp-outside').symlink_to(outside,target_is_directory=True)
    with pytest.raises(ValueError,match='ancestor'):
        move_dataset('experiments/active/exp-outside/inputs',root=tmp_path)
    assert (outside/'inputs/bars.csv').read_text()==CSV


def test_unknown_and_nested_venues_do_not_merge(tmp_path):
    from yoyo.data.dataset_catalog import market_metadata
    folder=tmp_path/'experiments/active/exp-mixed/inputs';folder.mkdir(parents=True)
    unknown=folder/'ETH_USDT_5m.csv';unknown.write_text(CSV)
    assert market_metadata(unknown,str(unknown))['exchange'] is None
    for ex in ('binance','okx'):
        sub=folder/ex;sub.mkdir();(sub/unknown.name).write_text(CSV)
    build_index(tmp_path)
    key=dataset_id('experiments/active/exp-mixed/inputs')
    entry=DatasetCatalog(tmp_path).detail(key)
    assert entry['dataset']['exchanges']==['binance','okx']
    with pytest.raises(ValueError,match='身份不唯一'):
        read_market_data(root=tmp_path,dataset_id=key,symbol='ETH_USDT',timeframe='5m',start='2026-01-01',end='2026-01-01T00:10:00Z')
    result=read_market_data(root=tmp_path,dataset_id=key,symbol='ETH_USDT',timeframe='5m',exchange='binance',start='2026-01-01',end='2026-01-01T00:10:00Z')
    assert len(result)==2
