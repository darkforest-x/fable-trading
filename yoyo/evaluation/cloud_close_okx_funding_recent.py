"""Fill the unpublished current OKX calendar month from the exchange's realized-rate API."""
import json

import numpy as np
import pandas as pd

from yoyo.data.altseason_funding import FundingClient, collect
from yoyo.evaluation import cloud_close_okx_funding_archives as archive

EXP = archive.EXP
MONTH = '2026-11'
CUTOFF = archive.tenth.trend.thirds.wallet.END


def main():
    if (EXP/'recent_rate_coverage.json').exists():
        raise FileExistsError('Frozen recent OKX funding source already exists')
    windows = pd.read_csv(EXP/'held_months.csv.gz')
    instruments = sorted(windows.loc[windows.month.eq(MONTH),'instrument'].unique())
    start = pd.Timestamp(MONTH+'-01',tz=archive.ZONE).value//1_000_000
    query_start = start - 24 * archive.HOUR
    client = FundingClient('okx',archive.RAW/'recent_api')
    audits, frames = [], []
    for inst in instruments:
        info = collect(client,inst,query_start,CUTOFF+1000)
        if not info['api_range_traversed']:
            raise ValueError(f'{inst} incomplete recent settlement query')
        rows = pd.read_csv(info['path'],usecols=['symbol','funding_time','realized_rate'])
        if not rows.symbol.eq(inst).all() or rows.funding_time.duplicated().any():
            raise ValueError(f'Bad recent source identity or repeated time for {inst}')
        raw = rows.rename(columns={'symbol':'instrument_name','realized_rate':'funding_rate'})
        previous = archive.RAW/inst/f'{inst}-fundingrates-2026-10.zip'
        overlap = 0
        if previous.is_file():
            historical = archive.decode(previous.read_bytes(),inst,'2026-10',previous.name)
            actual = raw[raw.funding_time.lt(start)]
            joined = actual.merge(historical,on=['instrument_name','funding_time'],suffixes=('_api','_file'),validate='one_to_one')
            if not len(joined)==len(actual) or not np.allclose(joined.funding_rate_api,joined.funding_rate_file,
                                                               rtol=1.e-8,atol=1.e-12,equal_nan=True):
                raise ValueError(f'Historical and REST actual rates disagree for {inst}')
            overlap = len(joined)
        observed = raw[raw.funding_time.between(start,CUTOFF)].copy()
        times = observed.funding_time.to_numpy(dtype=np.int64)
        maximum_gap = int(np.diff(np.r_[start,times,CUTOFF]).max())
        frames.append(observed)
        audits.append(dict(instrument=inst,month=MONTH,rows=len(observed),overlap_checked=overlap,
                           maximum_gap_including_month_edges_ms=maximum_gap,missing_rate_rows=int(observed.funding_rate.isna().sum()),
                           stops=info['stop_reason'],api_range_traversed=bool(info['api_range_traversed'])))
    tape = pd.concat(frames,ignore_index=True)
    tape.to_csv(EXP/'recent_realized_rates.csv.gz',index=False)
    result = dict(month=MONTH,cutoff_ms=int(CUTOFF),instruments=len(instruments),recent_records=len(tape),
                  previous_month_rates_cross_checked=sum(v['overlap_checked'] for v in audits),
                  missing_rate_rows=int(tape.funding_rate.isna().sum()),audit=audits)
    (EXP/'recent_rate_coverage.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(json.dumps(result,indent=2),flush=True)


if __name__=='__main__':
    main()
