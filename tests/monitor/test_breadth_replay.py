import json

import pandas as pd
import pytest

from yoyo.monitor import breadth_replay as replay


def test_a_candle_request_must_belong_to_the_exact_frozen_trade():
    windows = replay._windows(replay.LEDGER, replay.LEDGER.stat().st_mtime_ns)
    case = 'BTC|2026-09-15T19:20:00Z'
    assert windows[case, 'BTCUSDT'] == (replay.ms('2026-09-15T19:20:00Z'), replay.ms('2026-09-21T19:20:00Z'))
    assert any(symbol == '我踏马来了USDT' for _, symbol in windows)
    with pytest.raises(KeyError):
        replay.candles(case, 'ETHUSDT', '15m')


def test_only_complete_exchange_buckets_are_shown_and_missing_volume_stays_missing(tmp_path, monkeypatch):
    five = replay.FIVE
    receipt = tmp_path / 'receipt.json'
    receipt.write_text(json.dumps(dict(symbol='BTCUSDT', interval='5m', venue='Binance USD-M')))
    raw = pd.DataFrame(dict(open=[6.]*5, high=[8.]*5, low=[5.]*5, close=[7.]*5, volume=[2.,float('nan'),2.,2.,2.]),
                       index=[0,five,2*five,3*five,5*five])
    monkeypatch.setattr(replay,'source_frame',lambda *_:raw)
    bars, weeks = replay._chart_window('BTCUSDT',0,6*five,3*five,receipt,receipt.stat().st_mtime_ns)
    assert weeks == []
    assert bars == [dict(time=0,open=6.,high=8.,low=5.,close=7.,volume=None)]
