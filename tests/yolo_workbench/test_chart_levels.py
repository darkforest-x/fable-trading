"""Historical overlays require a complete, causal NY Monday anchor."""
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from yoyo.monitor.monday_fib import HOUR, baseline, week_window
from yoyo.yolo_workbench.chart_levels import ChartLevels


def stamp(iso):
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


def fixture(at='2026-10-25T05:05:00+00:00', symbol='BAT-USDT-SWAP'):
    at = stamp(at)
    window = week_window(at)
    raw = [[str(t), '100', str(110+i//12), '90', '105', '1', '1', '1', '1']
           for i, t in enumerate(range(window['start'], window['end'], 300_000))]
    snapshot = dict(symbol=symbol, episodes=[dict(id='box', detected_at_ms=at)])
    calls = []
    client = SimpleNamespace(get=lambda path, params: (calls.append((path, params)), raw)[1])
    return snapshot, raw, calls, ChartLevels(client=client)


def test_levels_match_existing_monitor_and_request_only_selected_monday():
    snapshot, raw, calls, service = fixture()
    value = service.read(snapshot, 'box')
    reference = baseline(raw[::12], snapshot['episodes'][0]['detected_at_ms'])
    assert reference['state'] == 'missing_history'
    assert value['state'] == 'ready'
    assert [value[k] for k in ('high', 'low', 'upper', 'lower')] == [reference[k] for k in ('high', 'low', 'upper', 'lower')]
    assert value['high'] == 133 and value['low'] == 90
    assert value['high_ms'] == value['start_ms'] + 23*HOUR
    assert value['low_ms'] == value['start_ms']
    assert calls == [('/api/v5/market/history-candles', dict(instId='BAT-USDT-SWAP', bar='5m', limit='300', after=str(value['end_ms'])))]
    assert value['as_of_ms'] == snapshot['episodes'][0]['detected_at_ms']


@pytest.mark.parametrize('at,start', [
    ('2026-10-25T05:05:00+00:00', '2026-10-19T04:00:00+00:00'),
    ('2026-11-08T05:05:00+00:00', '2026-11-02T05:00:00+00:00'),
    ('2026-03-08T05:05:00+00:00', '2026-03-02T05:00:00+00:00'),
])
def test_historical_week_and_dst_are_anchored_to_detection(at, start):
    snapshot, _, _, service = fixture(at)
    assert service.read(snapshot, 'box')['start_ms'] == stamp(start)


def test_monday_in_progress_never_uses_future_or_previous_week():
    snapshot, _, calls, service = fixture('2026-10-19T12:00:00+00:00')
    value = service.read(snapshot, 'box')
    assert value['state'] == 'developing' and 'high' not in value
    assert calls == []


@pytest.mark.parametrize('change', ['missing', 'unconfirmed', 'flat'])
def test_incomplete_or_invalid_monday_has_no_lines_and_can_retry(change):
    snapshot, raw, calls, service = fixture()
    if change == 'missing':
        raw.pop()
    elif change == 'unconfirmed':
        raw[-1][8] = '0'
    else:
        for row in raw:
            row[1:5] = ['100'] * 4
    value = service.read(snapshot, 'box')
    assert value['state'] in ('missing_monday', 'invalid_range')
    assert 'high' not in value
    service.read(snapshot, 'box')
    assert len(calls) == 2


def test_cache_keeps_symbol_week_separate_and_updates_detection_identity():
    snapshot, _, calls, service = fixture()
    first = service.read(snapshot, 'box')
    snapshot['episodes'].append(dict(id='later', detected_at_ms=first['as_of_ms']+HOUR))
    second = service.read(snapshot, 'later')
    assert second['detection_id'] == 'later' and second['as_of_ms'] == first['as_of_ms']+HOUR
    assert len(calls) == 1
    snapshot['symbol'] = 'ETH-USDT-SWAP'
    service.read(snapshot, 'later')
    assert len(calls) == 2


def test_unknown_box_or_busy_reader_does_not_start_network_work():
    snapshot, _, calls, service = fixture()
    with pytest.raises(KeyError):
        service.read(snapshot, 'unknown')
    service.slots.acquire(); service.slots.acquire()
    with pytest.raises(ValueError, match='稍后重试'):
        service.read(snapshot, 'box')
    assert calls == []


def test_api_uses_stored_detection_and_never_starts_inference(tmp_path, monkeypatch):
    from yoyo.yolo_workbench import api
    from yoyo.yolo_workbench.store import Book
    snapshot, raw, calls, service = fixture()
    monkeypatch.setattr('yoyo.yolo_workbench.chart_levels.ChartLevels', lambda runtime: service)
    monkeypatch.setattr(Book, 'get', lambda *args: {'id': 'job'})
    monkeypatch.setattr(Book, 'result', lambda *args: snapshot)
    app = FastAPI()
    api.install(app, tmp_path, launch_worker=False)
    client = TestClient(app)
    path = '/api/yolo-detect/jobs/job/results/BAT-USDT-SWAP/levels/'
    assert client.get(path+'box').json()['high'] == 133
    assert client.get(path+'missing').status_code == 404
    assert len(calls) == 1
