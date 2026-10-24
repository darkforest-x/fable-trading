"""Focused native close-entry and persisted-statistics contract checks."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation.monday_ma50_retest import execute, exposure
from yoyo.evaluation.monday_ma50_analysis import enrich, validate, summarize


def fixture(symbol='BTCUSDT'):
    frame = pd.DataFrame(dict(open=[100., 100., 100., 103.], high=[101., 110., 105., 104.],
                              low=[99., 90., 99., 102.], close=[100., 100., 103., 103.]),
                         index=pd.date_range('2024-01-03', periods=4, freq='5min', tz='UTC'))
    entries = pd.DataFrame([dict(trade_id='s', pair_id='s', population='signal', symbol=symbol,
                                side=1, entry_i=1, entry=100., stop=98., risk=2., target_price=104.,
                                end_i=3, touch_i=0, cross_i=0, confirm_i=1, bucket=1,
                                entry_time=str(frame.index[1]+pd.Timedelta(minutes=5)),
                                week='2024-01-01', segment=1)])
    return frame, entries


def test_close_entry_has_no_preentry_wick_exit_and_real_fee_classes(tmp_path):
    frame, entries = fixture()
    rows, fills, audit, invalid = execute(frame, entries, 'BTCUSDT')
    assert invalid.empty and audit['positions'] == 1 and len(fills) == 2
    assert rows.iloc[0].entry_i == 1 and rows.iloc[0].exit_i == 2
    assert rows.iloc[0].reason == 4
    np.testing.assert_allclose(rows.iloc[0].net_r, (4-.05-.0208)/2)
    result = exposure(frame, rows)
    assert result.iloc[0].interior_bars == 0
    assert result.iloc[0].interior_mfe_r == 0
    for name, table in [('trades', result), ('fills', fills), ('entries', entries), ('invalid', invalid)]:
        table.to_csv(tmp_path/f'{name}.csv.gz', index=False)
    (tmp_path/'done.json').write_text(json.dumps(dict(symbol='BTCUSDT',positions=1,fills=2,entries=1)))
    assert validate(tmp_path)['passed']
    enriched = enrich(result)
    assert enriched.iloc[0].actual_entry_week == '2024-01-01'
    assert summarize(enriched)['closed_n'] == 1


def test_gold_net_unknown_and_boundary_censored():
    frame, entries = fixture('XAUUSD')
    entries['target_price'] = 120.
    rows, fills, _, _ = execute(frame, entries, 'XAUUSD')
    result = exposure(frame, rows)
    assert result.iloc[0].reason == 0 and not result.iloc[0].closed
    assert result.net_r.isna().all() and result.fee_r.isna().all()
    assert fills.commission.eq(0).all()
    summary = summarize(enrich(result))
    assert summary['closed_n'] == 0 and summary['censored_n'] == 1
    assert summary['fee_positive_excursion_then_sl_n'] is None


def test_tick_invalid_geometry_is_retained_without_native_trade():
    frame, entries = fixture()
    entries['stop'] = 100.
    rows, fills, audit, rejected = execute(frame, entries, 'BTCUSDT')
    assert len(rejected) == 1 and rows.empty and fills.empty
    assert audit['positions'] == 0
