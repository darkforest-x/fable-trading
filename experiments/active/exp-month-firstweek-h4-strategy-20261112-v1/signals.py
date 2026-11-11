"""Extract H4 strategy and shadow signals from all frozen first-touch identities."""
from pathlib import Path
import sys

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[2]), str(HERE.parent/'exp-month-firstweek-all-replay-20261111-v1')]
from replay import CUTOFF, HOUR, OUT, WINDOW, read_raw
from yoyo.evaluation.month_firstweek_reclaim_strategy import classify_fourth_close

DEST = OUT/'h4_strategy'


def extract():
    keys = ['id', 'symbol', 'month', 'side', 'rail', 'touch_ms']
    first = pd.read_csv(OUT/'group_first_watch.csv.gz', usecols=keys, float_precision='round_trip')
    assert first.id.is_unique and not first.duplicated(['symbol', 'month', 'side']).any()
    rows = []
    for symbol, group in first.groupby('symbol',sort=True):
        raw = read_raw(symbol)
        for event in group.itertuples():
            start = event.touch_ms//(12*HOUR)*(12*HOUR)
            decision = start+16*HOUR
            if decision > CUTOFF:
                continue
            a, b = raw[:,0].searchsorted([start, decision])
            label = classify_fourth_close(raw[a:b], side=event.side, rail=event.rail,
                    first_touch_ms=event.touch_ms, range_expires_ms=WINDOW[event.month][2])
            if label is not None:
                rows.append(dict(id=event.id,symbol=symbol,month=event.month,group=label,decision_ms=decision))
    return pd.DataFrame(rows)


if __name__=='__main__':
    signals = extract()
    # Frozen replay is an independent parity check, never an input to classify_fourth_close.
    old = pd.read_csv(OUT/'h4_forward_excursions/excursions.csv',usecols=[*signals.columns,'horizon_hours'])
    old = old.loc[old.horizon_hours==24,signals.columns].reset_index(drop=True)
    pd.testing.assert_frame_equal(signals,old)
    DEST.mkdir(exist_ok=True,parents=True)
    signals.to_csv(DEST/'signals.csv',index=False)
    print(signals.groupby('group').size().to_string())
