"""Display data must stay real, closed, contiguous and separate from trading."""
import pytest
from yoyo.okx_execution.manual_chart import candles, reference


class Public:
    def __init__(self, rows): self.rows = rows
    def get(self, path, params):
        assert path == '/api/v5/market/candles'
        assert params == dict(instId='A-USDT-SWAP', bar='5m', limit='100')
        return self.rows


def row(t, confirm='1'):
    return [str(t), '100', '102', '98', '101', '2', '0', '0', confirm]


def test_excludes_open_and_future_bars_and_sorts_closed_ohlc():
    d = candles(Public([row(900000,'0'),row(600000),row(300000),row(1200000)]), 'A-USDT-SWAP', lambda: 910000)
    assert [r['time'] for r in d['candles']] == [300,600]
    assert d['cutoff_ms'] == 900000 and d['candles'][-1]['close'] == 101


@pytest.mark.parametrize('rows', [[],[row(300000)],[row(0),row(600000)]])
def test_missing_latest_or_internal_gap_never_becomes_a_fabricated_candle(rows):
    with pytest.raises(ValueError, match='尚未到齐'):
        candles(Public(rows), 'A-USDT-SWAP', lambda: 910000)


def ma_snapshot(symbol):
    return dict(symbol=symbol, cutoff_ms=900000, observed_ms=910000, ma=dict(sma20='100',sma60='99',sma120='98.01',ema20='100',ema60='101',ema120='101.99'))


class PublicReference:
    def __init__(self, stamp=910000): self.stamp, self.paths = stamp, []
    def get(self, path, params):
        self.paths.append(path)
        if path.endswith('/candles'): return [row(900000, '0'), row(600000)]
        if path.endswith('/instruments'):
            return [dict(instId='A-USDT-SWAP', state='live', ctType='linear', settleCcy='USDT', tickSz='.01')]
        if path.endswith('/ticker'): return [dict(askPx='100', bidPx='100', ts=str(self.stamp))]
        raise AssertionError('private read: '+path)


@pytest.mark.parametrize('side,rr,sl,tp', [('long',3,'98','106'),('long',5,'98','110'),('short',3,'102','94'),('short',5,'102','90')])
def test_public_reference_has_real_closed_stop_and_no_execution_identity(side, rr, sl, tp):
    client = PublicReference()
    d = reference(client, dict(symbol='A-USDT-SWAP', timeframe='15m', proposal_side=side), rr, None, lambda:910000, ma_snapshot=ma_snapshot)
    assert (d['initial_stop'], d['take_profit']) == (sl, tp)
    assert d['stop_candle']['open_ms'] == 600000 and d['display_only'] is True
    assert not {'id','queue_state','size','account_uid'} & d.keys()
    assert len(client.paths) == 2


def test_crossed_stop_still_displays_prices_without_inventing_profit_target():
    d = reference(PublicReference(), dict(symbol='A-USDT-SWAP', timeframe='15m', proposal_side='long'), 3, '101.123', lambda:910000, ma_snapshot=ma_snapshot)
    assert d['price'] == '100' and d['initial_stop'] == '101.12'
    assert d['take_profit'] is None and d['price_error']


def test_public_reference_does_not_accept_stale_prices():
    with pytest.raises(ValueError, match='expired'):
        reference(PublicReference(900000), dict(symbol='A-USDT-SWAP', timeframe='15m', proposal_side='long'), 3, None, lambda:910000, ma_snapshot=ma_snapshot)
