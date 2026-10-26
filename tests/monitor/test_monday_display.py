"""Prior-week display must never change current-week trading authority."""
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace

import pytest

from yoyo.monitor.monday_chart_levels import MondayChartLevels
from yoyo.monitor.monday_display import snapshot
from yoyo.monitor.monday_fib import week_window


def stamp(iso):
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


@pytest.mark.parametrize('iso', ['2026-10-26T12:00:00+00:00',
                                '2026-11-02T12:00:00+00:00',
                                '2026-03-09T12:00:00+00:00'])
def test_display_uses_previous_monday_until_current_monday_closes(iso):
    now = stamp(iso)
    current = week_window(now)
    calls = []

    def get(path, params):
        calls.append(params)
        end = int(params['after'])
        return [[str(t), '100', '110', '90', '105', '1', '1', '1', '1']
                for t in range(end-86_400_000, end, 300_000)]

    reader = MondayChartLevels(client=SimpleNamespace(get=get))
    strict = reader.read('BTC-USDT-SWAP', now)
    assert strict['state'] == 'developing' and not calls
    old = reader.read_display('BTC-USDT-SWAP', now)
    assert old['state'] == 'ready' and old['display_previous']
    assert old['current_week'] == current['week']
    assert old['expires_ms'] == current['start']
    assert old['week'] == week_window(current['start']-1)['week']
    assert int(calls[0]['after']) == old['end_ms']
    assert reader.read_display('BTC-USDT-SWAP', now) == old
    assert len(calls) == 1
    fresh = reader.read_display('BTC-USDT-SWAP', current['end'])
    assert fresh['week'] == current['week'] and 'display_previous' not in fresh
    assert len(calls) == 2


def test_missing_previous_history_is_normal_developing_not_fabricated_levels():
    reader = MondayChartLevels(client=SimpleNamespace(get=lambda *args: []))
    result = reader.read_display('BTC-USDT-SWAP', stamp('2026-10-26T12:00:00+00:00'))
    assert result['state'] == 'developing' and 'high' not in result


def test_display_projects_only_prior_touches_without_mutating_current_snapshot():
    now = stamp('2026-10-26T12:00:00+00:00')
    strict = dict(now_ms=now, week='2026-10-26', monday_complete=False,
                  items=[dict(symbol='BTC-USDT-SWAP', state='developing', high=None,
                              price=101, touches={})])
    original = deepcopy(strict)
    reads = []

    def records(week):
        reads.append(week)
        return [dict(symbol='BTC-USDT-SWAP', side='lower', candle_ms=now-86_400_000,
                     observed_ms=now-86_400_000, source='history', status='sent')]

    service = SimpleNamespace(snapshot=lambda **kw: strict,
                              book=SimpleNamespace(records=records))
    display = snapshot(service)
    assert display['week'] == '2026-10-26'
    assert display['display_week'] == '2026-10-19'
    assert display['display_previous'] and reads == ['2026-10-19']
    row = display['items'][0]
    assert row['price'] == 101 and row['state'] == 'developing' and row['high'] is None
    assert set(row['touches']['lower']) == {'source', 'candle_ms', 'observed_ms'}
    assert strict == original
    strict['monday_complete'] = True
    assert snapshot(service)['items'][0]['touches'] == {}
    assert reads == ['2026-10-19']
