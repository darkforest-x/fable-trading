"""Check an independent historical OKX swap directory against the frozen 291-name account."""
import io
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from zipfile import ZipFile

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / 'experiments/active/exp-cloud-close-okx-roster-audit-20261109-v1'
SOURCE = EXP.parent / 'exp-cloud-allmarket-screen-20261108-v1'
CACHE = ROOT / 'data/research/cloud_close_okx_roster_audit'
END = pd.Timestamp('2026-11-08T12:00Z')
INDEX = 'https://api.tardis.dev/v1/exchanges/okex-swap'
ARCHIVE = 'https://static.okx.com/cdn/okex/traderecords/candlesticks/monthly'


def download(url, dest):
    if not dest.is_file():
        r = requests.get(url,timeout=(10,30))
        if r.status_code == 404:
            return False
        r.raise_for_status()
        dest.parent.mkdir(parents=True,exist_ok=True)
        tmp = dest.with_suffix('.tmp')
        tmp.write_bytes(r.content)
        tmp.replace(dest)
    return True


def native_check(row):
    # Probe the preceding calendar month even when the provider stops recording mid-month.
    month = (pd.Timestamp(row['recorded_to']).tz_convert('Asia/Shanghai').replace(day=1) -
             pd.Timedelta(days=1)).strftime('%Y-%m')
    inst = row['instrument']
    name = f'{inst}-candlesticks-{month}.zip'
    url = f'{ARCHIVE}/{month.replace("-","")}/{name}'
    path = CACHE/'raw'/inst/name
    row.update(probe_month=month,archive_url=url)
    if not download(url,path):
        return dict(row,native_status='archive_404')
    with ZipFile(path) as archive:
        files = [n for n in archive.namelist() if n.endswith('.csv')]
        if len(files)!=1:
            raise ValueError(f'Invalid native archive {inst} {month}')
        frame = pd.read_csv(io.BytesIO(archive.read(files[0])),
                            usecols=['instrument_name','open_time','close','confirm','vol'])
    if not len(frame):
        return dict(row,native_status='empty_native_archive')
    if set(frame.instrument_name)!={inst} or not frame['confirm'].isin([0,1]).all() or not frame.close.gt(0).all():
        raise ValueError(f'Archive observations do not verify the requested instrument {inst}')
    traded = frame.vol.gt(0)
    if not traded.any():
        status = 'no_native_volume'
    elif frame['confirm'].eq(1).all():
        status = 'official_confirmed_candles'
    else:
        status = 'official_file_contains_unconfirmed_rows'
    return dict(row,native_status=status,archived_minutes=frame.open_time.nunique(),
                confirmed_minutes=int(frame['confirm'].eq(1).sum()),positive_volume_minutes=int(traded.sum()),
                first_ms=int(frame.open_time.min()),last_ms=int(frame.open_time.max()))


def main():
    if (EXP/'summary.json').is_file():
        raise FileExistsError('Completed OKX lifecycle-directory check')
    cache = CACHE/'okex-swap-provider.json'
    if not download(INDEX,cache):
        raise ValueError('Historical instrument directory unavailable')
    directory = json.loads(cache.read_text())['datasets']['symbols']
    original = pd.read_csv(SOURCE/'universe.csv').instId
    if len(set(original))!=291:
        raise ValueError('Unexpected original OKX roster')
    observed = pd.read_json(SOURCE/'instruments_snapshot.json.gz',typ='series')['data']
    last_type = {x['instId']:x.get('instCategory','') for x in observed}
    candidates = []
    for item in directory:
        inst = item['id']
        if (item.get('type')!='perpetual' or not inst.endswith('-USDT-SWAP') or inst in set(original)
                or pd.Timestamp(item['availableSince'])>=END or pd.Timestamp(item['availableTo'])>=END):
            continue
        candidates.append(dict(instrument=inst,recorded_from=item['availableSince'],recorded_to=item['availableTo'],
                               category_at_november_snapshot=last_type.get(inst,'')))
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = pd.DataFrame(pool.map(native_check, candidates))
    rows.to_csv(EXP/'historical_directory_checks.csv.gz',index=False)
    native = rows[rows.native_status.eq('official_confirmed_candles')]
    example = rows[rows.instrument.eq('WAVES-USDT-SWAP')]
    if len(example)!=1 or not example.positive_volume_minutes.gt(0).all():
        raise ValueError('Native delisted reference volume was not reproduced')
    j = dict(original_roster=len(original),historical_provider_only_ended_by_cutoff=len(rows),
             confirmed_with_native_month=len(native),archive_not_found=int(rows.native_status.eq('archive_404').sum()),
             archive_status=rows.native_status.value_counts().to_dict(),
             independently_documented_omission='WAVES-USDT-SWAP',
             scope='Provider recording endpoints are not listing/delisting times or product-class proofs. An official monthly file is independent evidence of trading in that month. No all-market census or counterfactual wallet.',
             source=INDEX, native_reference=rows.loc[rows.instrument.eq('WAVES-USDT-SWAP'),'archive_url'].iat[0])
    (EXP/'summary.json').write_text(json.dumps(j,indent=2)+'\n')
    print(j,flush=True)


if __name__=='__main__':
    main()
