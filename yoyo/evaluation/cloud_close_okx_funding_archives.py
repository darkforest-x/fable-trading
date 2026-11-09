"""Recover OKX native monthly settlement-rate records for the frozen 12H reference wallet."""
import io
import json
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_tenth_sizing as tenth

EXP = tenth.EXP.parent / 'exp-cloud-close-okx-funding-archives-20261109-v1'
RAW = tenth.trend.thirds.wallet.ROOT / 'data/research/cloud_close_okx_funding_20261109'
MONTHLY = 'https://static.okx.com/cdn/okex/traderecords/swaprates/monthly'
ZONE = 'Asia/Shanghai'
HOUR = 3_600_000


def local_month(ms):
    return pd.Timestamp(ms, unit='ms', tz='UTC').tz_convert(ZONE).strftime('%Y-%m')


def funded_legs():
    paths = tenth.trend.thirds.okx_paths()
    reference = pd.read_csv(tenth.EXP / 'funded_OKX.csv.gz', usecols=['event_id'])
    if len(reference) != 1569 or not reference.event_id.is_unique:
        raise ValueError('Unexpected original funded OKX roster')
    paths = paths[~paths.is_control & paths.event_id.isin(reference.event_id)]
    _, legs = tenth.trend.inputs('OKX', paths)
    if set(legs.event_id) != set(reference.event_id) or legs.duplicated(['event_id','tp_r']).any() or not legs.groupby('event_id').size().eq(2).all():
        raise ValueError('Native runner and target must be present exactly once for every funded parent')
    manifest = pd.read_csv(tenth.trend.thirds.wallet.SOURCE/'input_coverage.csv', usecols=['symbol','instId']).drop_duplicates()
    instruments = manifest.set_index('symbol', verify_integrity=True).instId
    legs['instrument'] = legs.symbol.map(instruments)
    if legs.instrument.isna().any() or not legs.exit_phase.isin(['open','intrabar','close']).all():
        raise ValueError('Missing contract identity or unknown native execution phase')
    return legs


def held_windows():
    legs = funded_legs()
    records = []
    for row in legs.itertuples(index=False):
        start = int(row.entry_ms) - 60_000
        end = int(row.exit_bar_ms) + (0 if row.exit_phase == 'open' else HOUR) + 60_000
        for month in pd.period_range(local_month(start), local_month(end), freq='M'):
            records.append((row.event_id, row.tp_r, row.instrument, str(month)))
    return pd.DataFrame(records, columns=['event_id','tp_r','instrument','month'])


def archive(inst, month, filename):
    path = RAW / inst / filename
    if path.exists():
        return path.read_bytes()
    not_found = path.with_suffix('.http404')
    if not_found.is_file():
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    url = f"{MONTHLY}/{month.replace('-','')}/{filename}"
    for attempt in range(4):
        try:
            with urlopen(url, timeout=25) as response:
                data = response.read()
            # Decode and validate before publishing the cached bytes.
            decode(data, inst, month, filename)
            tmp = path.with_suffix('.part')
            tmp.write_bytes(data)
            tmp.replace(path)
            return data
        except HTTPError as exc:
            if exc.code == 404:
                not_found.write_text('HTTP 404 observed in frozen 2026-11-09 research snapshot\n')
                return None
            if exc.code not in (408,429,500,502,503,504) or attempt == 3:
                raise
        except (URLError, TimeoutError):
            if attempt == 3:
                raise
        time.sleep(2 ** attempt)


def decode(data, inst, month, filename):
    csv_name = filename.removesuffix('.zip') + '.csv'
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        with z.open(csv_name) as handle:
            frame = pd.read_csv(handle, usecols=['instrument_name','funding_time','funding_rate'])
    if frame.empty:
        return frame
    times = frame.funding_time.astype('int64')
    if not frame.instrument_name.eq(inst).all() or not np.array_equal(times, frame.funding_time):
        raise ValueError(f'Wrong instrument or fractional native timestamp: {filename}')
    if np.isinf(frame.funding_rate).any() or times.duplicated().any():
        raise ValueError(f'Infinite rate or duplicated native timestamp: {filename}')
    frame = frame.assign(funding_time=times).sort_values('funding_time')
    if local_month(times.min()) != month or local_month(times.max()) != month:
        raise ValueError(f'Unexpected exchange month bounds: {filename}')
    return frame


def read_month(key):
    inst, month = key
    row = dict(instrument=inst, month=month, rows=0)
    filename = f'{inst}-fundingrates-{month}.zip'
    data = archive(inst, month, filename)
    if data is None:
        return dict(row, status='archive_404'), None
    frame = decode(data, inst, month, filename)
    if frame.empty:
        return dict(row, status='archive_empty'), None
    # Some delisting archives publish a settlement clock with a blank rate.
    # Retain that row and report its status instead of silently charging zero.
    times = frame.funding_time
    missing = int(frame.funding_rate.isna().sum())
    return dict(row, status='observed_with_missing_rates' if missing else 'observed', missing_rates=missing, rows=len(frame), first_ms=int(times.min()), last_ms=int(times.max()),
                long_internal_gaps=int(times.diff().gt(8 * HOUR + 120_000).sum())), frame


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Frozen funding archive coverage already exists')
    windows = held_windows()
    windows.to_csv(EXP/'held_months.csv.gz', index=False)
    keys = list(windows.groupby(['instrument','month'], sort=True).groups)
    rows, panels = [], []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for index, (row, panel) in enumerate(pool.map(read_month, keys), 1):
            rows.append(row)
            if panel is not None:
                panels.append(panel)
            if index % 250 == 0:
                print(index, len(keys), flush=True)
    receipts = pd.DataFrame(rows)
    receipts.to_csv(EXP/'archive_receipts.csv.gz', index=False)
    tape = pd.concat(panels, ignore_index=True)
    if tape.duplicated(['instrument_name','funding_time']).any():
        raise ValueError('Overlapping monthly files')
    tape.to_csv(EXP/'published_rates.csv.gz', index=False)
    observed = windows.merge(receipts[['instrument','month','status']], validate='many_to_one')
    parent = observed.status.str.startswith('observed').groupby(observed.event_id).all()
    clean = observed.status.eq('observed').groupby(observed.event_id).all()
    result = dict(funded_parents=len(parent), native_child_legs=len(windows[['event_id','tp_r']].drop_duplicates()),
                  instruments=windows.instrument.nunique(), required_exchange_month_keys=len(keys),
                  source_status=receipts.status.value_counts().to_dict(),
                  pre_march_2022_source_status=receipts.loc[receipts.month.lt('2022-03'),'status'].value_counts().to_dict(),
                  first_month=windows.month.min(),
                  last_month=windows.month.max(), native_monthly_records=len(tape), missing_rate_rows=int(tape.funding_rate.isna().sum()),
                  parents_with_all_held_month_files_free_of_null_rates=int(clean.sum()),
                  parents_with_all_held_month_files=int(parent.sum()),
                  parents_missing_at_least_one_held_month_file=int((~parent).sum()),
                  observed_archives_with_internal_gap_over_8h2m=int(receipts.long_internal_gaps.gt(0).sum()),
                  complete_file_roster=bool(parent.all()), settlement_marks_in_tape=False, full_history_net_nav=None)
    (EXP/'summary.json').write_text(json.dumps(result, indent=2, allow_nan=False)+'\n')
    print(result, flush=True)


if __name__ == '__main__':
    main()
