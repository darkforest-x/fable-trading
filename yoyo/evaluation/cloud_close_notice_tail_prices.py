"""Append actual OKX candles through the ICX/LRC delivery clocks without replacing their original histories."""
import json
import numpy as np
import pandas as pd
import requests

from yoyo.evaluation import cloud_close_okx_retired_account as archive

EXP = archive.EXP.parent/'exp-cloud-close-notice-tail-prices-20261109-v1'
HOUR = archive.wallet.HOUR
FIELDS = archive.FIELDS


def run_market(sym,metadata):
    folder = archive.wallet.SOURCE/'inputs'/sym
    old1 = pd.read_csv(folder/'bars1h.csv.gz',index_col='time',float_precision='round_trip')
    old12 = pd.read_csv(folder/'bars12h.csv.gz',index_col='time',float_precision='round_trip')
    end = pd.Timestamp(metadata['end']).value//1_000_000
    start = int(old1.index[-1]+HOUR)
    source_month = pd.Timestamp(start,unit='ms',tz='UTC').strftime('%Y-%m')
    with requests.Session() as client:
        native,receipt = archive.prices.archive_month(client,sym+'-USDT-SWAP',source_month)
    if native is None:
        raise ValueError(f'Official monthly source is absent for {sym}')
    overlap = native.index.intersection(old1.index)
    diffs = ~np.isclose(native.loc[overlap,FIELDS],old1.loc[overlap,FIELDS],rtol=1.e-8,atol=1.e-12).all(axis=1)
    new = native.loc[(native.index>=start)&(native.index<end),FIELDS].copy()
    new['source'] = 'OKX_native_60_confirmed_minutes'
    total = pd.concat([old1,new])
    if not total.index.is_unique or not total.index.is_monotonic_increasing:
        raise ValueError('Unexpected overlap in historical append')
    new12 = archive.native.twelve_hour(total[FIELDS])
    new12 = new12.loc[new12.index>old12.index[-1]].assign(source='twelve_actual_native_or_frozen_H1')
    completed = pd.concat([old12,new12])
    if not np.array_equal(completed.iloc[:len(old12)].index,old12.index):
        raise ValueError('Historical 12H prefix changed')
    dest = EXP/sym
    dest.mkdir(exist_ok=True)
    total.to_csv(dest/'bars1h.csv.gz',index_label='time')
    completed.to_csv(dest/'bars12h.csv.gz',index_label='time')
    missing = sorted(set(range(start,end,HOUR))-set(new.index))
    return dict(start_utc=pd.Timestamp(start,unit='ms',tz='UTC').isoformat(),delivery=metadata['end'],
        expected_hours=(end-start)//HOUR,added_confirmed_hours=len(new),new_completed_12h=len(new12),
        overlapping_h1=len(overlap),disagreeing_prior_h1=int(diffs.sum()),
        last_frozen_h1_in_overlap=bool(old1.index[-1] in overlap),
        last_frozen_h1_disagrees=bool(old1.index[-1] in overlap[diffs]),
        missing_h1_utc=[pd.Timestamp(t,unit='ms',tz='UTC').isoformat() for t in missing],
        source=receipt,notice=metadata['notice'])


def main():
    if (EXP/'summary.json').is_file():
        raise FileExistsError('Completed administrative-tail price study')
    cfg = json.loads((EXP/'config.json').read_text())
    out = {sym:run_market(sym,meta) for sym,meta in cfg['symbols'].items()}
    archive.prices.safe_json(EXP/'summary.json',dict(markets=out))
    print({s:(v['added_confirmed_hours'],v['expected_hours'],v['disagreeing_prior_h1']) for s,v in out.items()})


if __name__=='__main__':
    main()
