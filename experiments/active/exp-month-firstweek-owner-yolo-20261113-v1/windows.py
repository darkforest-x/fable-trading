"""Fixed owner-photo replay, using only completed bars for the original YOLO inputs."""
from pathlib import Path
import sys
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DATA = ROOT / 'data/research/month-firstweek-owner-yolo-20261113-v1'
OUT = ROOT / 'output/month-firstweek-owner-yolo-20261113'
OLD = HERE.parent / 'exp-month-firstweek-cloud-dense-20261112-v1'
sys.path.insert(0, str(OLD))
from observe import resample, avg, ew, expiry
MINUTE = 60_000
HOURS = 4
MA_COLUMNS = [f'{kind}{n}' for n in (20,60,120) for kind in ('sma','ema')]

def cohort():
    rows = pd.read_csv(HERE / 'cohort.csv',float_precision='round_trip')
    complete = rows[rows.coverage.eq('complete')].copy()
    assert complete.case_id.is_unique
    return complete.sort_values('case_id')

def frame(row, tf):
    a = np.load(DATA/'tapes'/f'{row.case_id}.npy', allow_pickle=False)
    assert len(a) and (np.diff(a[:,0]) == 5*MINUTE).all()
    if tf == 15: a = resample(a)
    f = pd.DataFrame(a,columns=['open_ms','open','high','low','close'])
    for n in (20,60,120):
        f[f'sma{n}'] = avg(a[:,4],n)
        f[f'ema{n}'] = f.close.ewm(span=n,adjust=False).mean()
    f['cloud_long'] = f.ema20 > f.close.ewm(span=50,adjust=False).mean()
    return f

def window_end(row):
    return min(int(row.known_ms)+HOURS*60*MINUTE, expiry(row.month)-1)

def after_read(row, read_ms, raw):
    "Reference open at decision time; probe only the following 288 5m OHLC bars."
    start = int(raw[:,0].searchsorted(read_ms)); interval=raw[start:start+288]
    assert len(interval)==288 and interval[0,0]==read_ms
    d = 1 if row.side == 'long' else -1
    ref = interval[0,1]
    best = interval[:,2].max() if d==1 else interval[:,3].min()
    worst = interval[:,3].min() if d==1 else interval[:,2].max()
    return dict(ref_open=ref, before_pct=100*d*(ref/row.direction_close-1),
                favorable24=max(0,100*d*(best/ref-1)), adverse24=min(0,100*d*(worst/ref-1)),
                change24=100*d*(interval[-1,4]/ref-1))
