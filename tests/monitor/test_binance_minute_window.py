"""Display source validation and cache reuse, without external requests."""
import io
import zipfile
from pathlib import Path
import pytest
from yoyo.data import binance_minute_window as source


def body():
    return 'open_time,open,high,low,close,volume\n0,10,12,9,11,4\n60000,11,14,8,13,6\n'


def test_registered_cache_is_read_only_and_missing_days_use_atomic_display_cache(tmp_path,monkeypatch):
    old,cache=tmp_path/'old',tmp_path/'display'
    path=old/'AAAUSDT/1970-01-01.csv';path.parent.mkdir(parents=True);path.write_text(body())
    requests=[]
    def fail(*args,**kwargs):requests.append(args);raise AssertionError('unexpected fetch')
    monkeypatch.setattr(source,'urlopen',fail)
    assert len(source.read('AAAUSDT',0,120000,registered=old,cache=cache))==2
    assert not requests and not cache.exists()
    original=path.read_text();path.unlink()
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as z:z.writestr('day.csv',body())
    monkeypatch.setattr(source,'urlopen',lambda *a,**k:io.BytesIO(stream.getvalue()))
    assert len(source.read('AAAUSDT',0,120000,registered=old,cache=cache))==2
    assert not path.exists()
    assert (cache/'AAAUSDT/1970-01-01.csv').read_text()==original
    monkeypatch.setattr(source,'urlopen',fail)
    assert len(source.read('AAAUSDT',0,120000,registered=old,cache=cache))==2


def test_invalid_or_missing_minutes_are_never_filled():
    assert len(source.parse('0,10,12,9,11,4\n120000,11,14,8,13,6\n',0))==2
    for text in ['0,10,9,8,11,4\n','0,10,12,9,11,-4\n','1,10,12,9,11,4\n','0,10\n','0,10,12,9,11,4\n0,10,12,9,11,4\n']:
        with pytest.raises(ValueError):source.parse(text,0)
    with pytest.raises(ValueError):source.read('../AAAUSDT',0,120000)
