"""Extend frozen 12H keys and their native VectorBT exits across the September tape boundary."""
from __future__ import annotations

import argparse
import json
import re
import zipfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_archive_cohort as archive
from yoyo.evaluation import signal_center_score as keys
from yoyo.data import binance_um_vision_listing as listing
from yoyo.data.binance_um_vision_fetch import fetch

venue = archive.venue
engine = venue.engine
HOUR = engine.HOUR_MS
EXP = venue.EXP.parent / 'exp-cloud-close-november-paths-20261109-v1'
ROOT = venue.wallet.ROOT
CACHE = ROOT / 'data/research/cloud_close_november_paths'
SOURCE = ROOT / 'data/research/cloud_close_november_prices'
SUPPLEMENTAL_EVIDENCE = ROOT / 'experiments/active/exp-cloud-close-archive-complete-20261109-v1/supplemental_contract_evidence.csv'
SUPPLEMENTAL_CACHE = ROOT / 'data/research/cloud_close_november_supplemental'
SUPPLEMENTAL_SYMBOLS = SUPPLEMENTAL_CACHE / 'symbols'
END = pd.Timestamp(json.loads((EXP/'config.json').read_text())['end_exclusive_utc']).value // 1_000_000
SUPPLEMENTAL_HISTORY_END = pd.Timestamp('2026-11-01T00:00:00Z').value // 1_000_000
KEY_COLS = ['tf','close_ns','signal_id','week','side','stop']
OHLC = venue.FIELDS


def history_slot(ticker):
    for parent in (SUPPLEMENTAL_SYMBOLS,archive.CACHE/'symbols',archive.native.WORK,venue.WORK):
        if (parent/ticker/'quotes.npz').is_file():
            return parent/ticker
    raise FileNotFoundError(f'Missing frozen quote archive for {ticker}')


def load_history(ticker):
    slot = history_slot(ticker)
    with np.load(slot/'quotes.npz') as data:
        frame = pd.DataFrame(data['prices'],columns=OHLC,index=pd.Index(data['clock'],name='time'))
    paths = pd.read_csv(slot/'fills.csv.gz',dtype={'symbol':str},keep_default_na=False,
                        float_precision='round_trip')
    return frame,paths


def utc_frame(frame):
    return frame.set_axis(pd.to_datetime(frame.index,unit='ms',utc=True).as_unit('ns'))


def twelve_hour(frame):
    agg = utc_frame(frame).resample('12h',origin='epoch').agg(open=('open','first'),high=('high','max'),
                low=('low','min'),close=('close','last'),n=('close','count'))
    agg = agg.loc[agg.n.eq(12),OHLC]
    agg.index = pd.Index(agg.index.as_unit('ms').asi8,name='time')
    return agg


def keys_on_clock(ticker,frame,decimals):
    result = keys.build_inputs(utc_frame(frame),ticker,None,key_timeframes=['12H'],
                               source_minutes=60,price_decimals=decimals)[4]
    return result[KEY_COLS].reset_index(drop=True) if len(result) else pd.DataFrame(columns=KEY_COLS)


def _monthly_prices(path):
    """Read official Binance USD-M 1h monthly ZIP OHLCV without substituting another market."""
    names = ['open_time_ms','open','high','low','close','volume']
    with zipfile.ZipFile(path) as zf:
        members = [name for name in zf.namelist() if name.endswith('.csv')]
        if len(members) != 1:
            raise ValueError(f'Expected one CSV in Binance archive {path}')
        with zf.open(members[0]) as stream:
            frame = pd.read_csv(stream, header=None, names=names, usecols=range(6))
    parsed = pd.to_numeric(frame.open_time_ms, errors='coerce')
    if parsed.isna().any():
        if len(frame) and str(frame.open_time_ms.iloc[0]).lower() in ('open_time','open time','open time (ms)'):
            frame = frame.iloc[1:].copy()
            parsed = pd.to_numeric(frame.open_time_ms, errors='raise')
        else:
            raise ValueError(f'Non-numeric open clock in Binance archive {path}')
    frame['open_time_ms'] = parsed.astype('int64')
    frame = frame.set_index('open_time_ms')[['open','high','low','close']].apply(pd.to_numeric, errors='raise')
    if (not frame.index.is_unique or not frame.index.is_monotonic_increasing or
            (frame.index % HOUR).any() or not np.isfinite(frame.to_numpy()).all() or
            (frame[OHLC] <= 0).any().any()):
        raise ValueError(f'Invalid native 1h OHLC clocks/values in {path}')
    if (frame.high < frame[['open','close','low']].max(axis=1)).any() or (frame.low > frame[['open','close','high']].min(axis=1)).any():
        raise ValueError(f'Invalid OHLC envelope in {path}')
    return frame


def prepare_supplemental_history():
    """Fetch official monthly 1h history for supplemental contracts missing frozen quote slots.

    Supplementals are retained as unscored paths. This input provides actual listing-age,
    90-day eligibility and contemporaneous raw-event counts; it does not alter the frozen
    632-contract booster roster.
    """
    evidence = pd.read_csv(SUPPLEMENTAL_EVIDENCE, dtype={'symbol':str}).fillna('')
    active = pd.read_csv(SOURCE/'coverage.csv.gz', dtype={'symbol':str,'contract_period_id':str}).fillna('')
    ids = ['DATAIPUSDT','GRAMUSDT','GRVTUSDT','DOSUSDT','牛来USDT','MARSCOINUSDT','PONSUSDT','哈基米USDT']
    rows = evidence[evidence.symbol.isin(ids)].set_index('symbol')
    if set(rows.index) != set(ids) or len(rows) != len(ids):
        raise ValueError('The frozen 8-contract supplemental evidence roster changed')
    source_rows = active[active.symbol.isin(ids)].set_index('symbol')
    if set(source_rows.index) != set(ids) or len(source_rows) != len(ids):
        raise ValueError('Official H1 extension coverage does not include all 8 supplemental contracts')
    records = []
    for ticker in ids:
        onboard = pd.Timestamp(rows.at[ticker,'onboardDate_utc']).value // 1_000_000
        slot = SUPPLEMENTAL_SYMBOLS/ticker
        cached_receipt = slot/'source_receipt.json'
        if cached_receipt.is_file() and (slot/'quotes.npz').is_file() and (slot/'fills.csv.gz').is_file():
            cached = json.loads(cached_receipt.read_text())
            if (cached.get('symbol') != ticker or int(cached.get('onboard_ms',-1)) != onboard or
                    cached.get('state') != 'complete'):
                raise ValueError(f'Existing supplemental source receipt changed for {ticker}')
            records.append(cached)
            continue
        first_month = pd.Timestamp(onboard, unit='ms', tz='UTC').strftime('%Y-%m')
        expected_months = pd.period_range(first_month, '2026-10', freq='M').strftime('%Y-%m').tolist()
        keys_by_month = {}
        for key in listing.files(ticker, interval='1h'):
            match = re.search(r'-1h-(\d{4}-\d{2})\.zip$', key)
            if match and first_month <= match.group(1) <= '2026-10':
                keys_by_month[match.group(1)] = key
        missing_months = sorted(set(expected_months) - set(keys_by_month))
        if missing_months:
            records.append(dict(symbol=ticker,onboard_ms=onboard,state='official_month_key_missing',
                                missing_months=missing_months,history_bars=0,raw_keys=0,source=''))
            continue
        frames, month_receipts = [], []
        for month in expected_months:
            key = keys_by_month[month]
            status = fetch(SUPPLEMENTAL_CACHE, 'monthly_1h', ticker, f'https://data.binance.vision/{key}')[2]
            raw_path = SUPPLEMENTAL_CACHE/'monthly_1h'/ticker/Path(key).name
            if status not in ('fetched','cached') or not raw_path.is_file():
                raise IOError(f'Official Binance monthly 1h history unavailable for {ticker} {month}: {status}')
            part = _monthly_prices(raw_path)
            part = part[(part.index >= onboard) & (part.index < SUPPLEMENTAL_HISTORY_END)]
            frames.append(part)
            month_receipts.append(dict(month=month,archive=key,status=status,rows=len(part),
                                       bytes=raw_path.stat().st_size,
                                       local_path=str(raw_path.relative_to(ROOT))))
        history = pd.concat(frames).sort_index()
        if history.empty or not history.index.is_unique or not history.index.is_monotonic_increasing:
            raise ValueError(f'No valid official H1 history for supplemental contract {ticker}')
        expected = pd.Index(np.arange(history.index[0], history.index[-1] + HOUR, HOUR), name='open_time_ms')
        row = source_rows.loc[ticker]
        extension_path = SOURCE/'series'/(str(row.contract_period_id)+'.csv.gz')
        extension = pd.read_csv(extension_path, usecols=['open_time_ms',*OHLC], dtype={'open_time_ms':'int64'},
                                float_precision='round_trip').set_index('open_time_ms')[OHLC]
        if (len(extension) != int(row.complete_h1) or not extension.index.is_unique or
                not extension.index.is_monotonic_increasing or (extension.index % HOUR).any()):
            raise ValueError(f'{ticker}: official FAPI-derived H1 extension does not match its coverage receipt')
        overlap = history.index.intersection(extension.index)
        same = np.allclose(history.loc[overlap,OHLC], extension.loc[overlap,OHLC], rtol=2e-11, atol=1e-13) if len(overlap) else True
        if not same:
            raise ValueError(f'{ticker}: monthly official H1 and November source disagree on {len(overlap)} overlap bars')
        if len(overlap) != len(extension[(extension.index >= history.index[0]) & (extension.index < history.index[-1] + HOUR)]):
            raise ValueError(f'{ticker}: H1 source overlap is incomplete against the monthly archive')
        monthly_missing = expected.difference(history.index)
        repairable = monthly_missing.intersection(extension.index)
        if len(repairable):
            history = pd.concat([history,extension.loc[repairable]]).sort_index()
        missing = expected.difference(history.index)
        if len(missing):
            raise ValueError(f'{ticker}: {len(missing)} historical H1 clocks remain unavailable after official FAPI repair')
        slot.mkdir(parents=True, exist_ok=True)
        if (slot/'quotes.npz').is_file() or (slot/'fills.csv.gz').is_file():
            raise FileExistsError(f'Refusing to overwrite supplemental history paths for {ticker}')
        cutoff = int(history.index[-1] + HOUR)
        decimals = max(2,min(9,int(np.ceil(-np.log10(history.low.min())))+7))
        joined = history.copy()
        twelve = twelve_hour(joined)
        signals, raw = keys.build_inputs(utc_frame(joined),ticker,None,key_timeframes=['12H'],
                                        source_minutes=60,price_decimals=decimals)[4:6]
        signals = signals[signals.close_ns < cutoff*1_000_000].copy() if len(signals) else signals
        priced = venue.price_frames(ticker,slot,joined,twelve,signals,end=cutoff,save_quotes=True)
        raw = raw[raw.close_ns < cutoff*1_000_000].copy() if len(raw) else raw
        if len(raw):
            raw.to_csv(slot/'raw_events.csv.gz',index=False)
        receipt = dict(symbol=ticker,onboard_ms=onboard,history_cutoff_ms=cutoff,first_h1_ms=int(history.index[0]),
                       last_h1_ms=int(history.index[-1]),history_bars=len(history),missing_h1_clocks=0,
                       monthly_missing_h1_clocks=int(len(monthly_missing)),
                       h1_repaired_from_official_fapi_5m_aggregate=int(len(repairable)),
                       repair_clock_utc=[str(x) for x in pd.to_datetime(repairable,unit='ms',utc=True)],
                       monthly_archive_bars=int(sum(x['rows'] for x in month_receipts)),source_months=month_receipts,
                       source_period_id=str(row.contract_period_id),extension_source_period=str(extension_path.relative_to(ROOT)),
                       extension_overlap_bars=len(overlap),
                       extension_overlap_ohlc_equal=bool(same),raw_keys=int(len(raw)),path_keys=int(len(signals)),
                       source_signals=int(priced.get('source_signals',0)),price_decimals=decimals,
                       mature_at_ms=onboard+90*24*HOUR,state='complete')
        (slot/'source_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
        records.append(receipt)
        print(f"Prepared official warmup {ticker}: {len(history)} H1 bars, {len(signals)} V1.7 keys",flush=True)
    (SUPPLEMENTAL_CACHE/'source_receipt.json').write_text(json.dumps(records,ensure_ascii=False,indent=2)+'\n')
    if len(records) != len(ids) or any(r.get('state') not in (None,'complete') for r in records):
        raise ValueError('Supplemental source collection is incomplete; unknown histories are not zero-signal')
    return records


def parity(ticker,signals,old,frame,cutoff,precision):
    # Exclude the final partially observed decision day. Its first missing entry is
    # specifically eligible for the new native tape below.
    end = cutoff - 24*HOUR
    start = end - 90*24*HOUR
    candidates = signals[signals.close_ns.between(start*1_000_000,end*1_000_000,inclusive='left')].copy()
    candidates['ms'] = candidates.close_ns.astype('int64')//1_000_000
    candidates['entry'] = candidates.ms.map(frame.open)
    valid = candidates[candidates.side*(candidates.entry-candidates.stop)>0].set_index('signal_id')
    base = old[(~old.is_control) & old.tp_r.eq(0) & old.entry_ms.between(start,end,inclusive='left')].set_index('event_id')
    common = valid.index.intersection(base.index)
    diff = set(valid.index)^set(base.index)
    for name in common:
        if abs(float(valid.at[name,'stop'])-float(base.at[name,'stop_price'])) > 1.01*10**(-precision):
            diff.add(name)
    return len(common),[dict(symbol=ticker,event_id=name,in_frozen=name in base.index,
                             in_rebuilt=name in valid.index) for name in sorted(diff)]


def extend(path,output,*,separate=False):
    suffix = pd.read_csv(path,dtype={'symbol':str,'contract_period_id':str},float_precision='round_trip')
    if suffix.empty or suffix.symbol.nunique()!=1 or suffix.contract_period_id.nunique()!=1:
        raise ValueError(f'Invalid period source {path}')
    ticker = suffix.symbol.iat[0]
    suffix = suffix.set_index('open_time_ms')[OHLC].rename_axis('time')
    if not suffix.index.is_unique or not suffix.index.is_monotonic_increasing or (suffix.index%HOUR).any():
        raise ValueError(f'Invalid hourly clocks in {path}')
    if (suffix.index>=END).any():
        raise ValueError(f'The source exceeds the registered closed boundary in {path}')
    frozen,old = load_history(ticker)
    if separate:
        if len(frozen) and suffix.index[0]<=frozen.index[-1]:
            raise ValueError(f'Overlapping lifetimes for independent listing {ticker}')
        frozen = frozen.iloc[:0]
        old = old.iloc[:0]
    overlap = suffix.index.intersection(frozen.index)
    if len(overlap) and not np.allclose(suffix.loc[overlap],frozen.loc[overlap],rtol=2.e-11,atol=1.e-13):
        raise ValueError(f'Historical OHLC conflicts on {ticker} native overlap')
    cutoff = frozen.index[-1]+HOUR if len(frozen) else suffix.index[0]
    before = suffix[suffix.index<cutoff]
    new = suffix[suffix.index>=cutoff]
    output.mkdir(parents=True,exist_ok=True)
    supplemental_receipt = SUPPLEMENTAL_SYMBOLS/ticker/'source_receipt.json'
    supplemental = json.loads(supplemental_receipt.read_text()) if supplemental_receipt.is_file() else {}
    meta = dict(symbol=ticker,period=path.stem.removesuffix('.csv'),separate_contract=separate,
                supplemental_new_history=bool(supplemental),onboard_ms=int(supplemental.get('onboard_ms',0)),
                supplemental_mature_at_ms=int(supplemental.get('mature_at_ms',0)),
                frozen_bars=len(frozen),overlap_bars=len(overlap),old_interior_missing=len(before)-len(overlap),
                frozen_cutoff_ms=int(cutoff),new_bars=len(new),matched_previous_keys=0,key_disagreements=[],
                carried_events=0,changed_carry_legs=0,fresh_keys=0)
    if new.empty:
        return meta
    joined = pd.concat([frozen,new]) if len(frozen) else new
    decimals = max(2,min(9,int(np.ceil(-np.log10((frozen if len(frozen) else new.iloc[:1]).low.min())))+7))
    signals = keys_on_clock(ticker,joined,decimals)
    if len(frozen):
        meta['matched_previous_keys'],meta['key_disagreements'] = parity(ticker,signals,old,frozen,cutoff,decimals)
    fresh = signals[signals.close_ns.ge(cutoff*1_000_000)].reset_index(drop=True)
    carry_ids = set(old.loc[old.exit_reason.eq('series_tail_close') & old.exit_bar_ms.eq(cutoff-HOUR),'event_id'])
    carry = old[old.event_id.isin(carry_ids) & old.tp_r.eq(0)]
    if not set(carry.event_id)==carry_ids:
        raise ValueError(f'Missing original runner path for {ticker}')
    twelve = twelve_hour(joined)
    rank = venue.leader.trailing_return(twelve.reset_index()).dropna()
    np.save(output/'rank_times.npy',rank.index[rank.index>=cutoff].to_numpy(dtype='int64'))
    # Original H1 source is immutable. Persist only the small suffix needed by the wallet.
    np.savez_compressed(output/'extension.npz',clock=new.index.to_numpy(dtype='int64'),prices=new[OHLC].to_numpy())
    fresh.to_csv(output/'fresh_keys.csv.gz',index=False)
    priced = venue.price_frames(ticker,output,joined,twelve,fresh,end=END,carry=carry,save_quotes=False)
    fills = pd.read_csv(output/'fills.csv.gz',dtype={'symbol':str},keep_default_na=False,float_precision='round_trip')
    if carry_ids:
        a = old[old.event_id.isin(carry_ids)].set_index(['event_id','tp_r']).sort_index()
        b = fills[fills.event_id.isin(carry_ids)].set_index(['event_id','tp_r']).sort_index()
        keep = ~a.exit_reason.eq('series_tail_close')
        attrs = ['entry_ms','exit_bar_ms','exit_reason','exit_phase','entry_order','eligible_at_own_time']
        if not a.index.equals(b.index) or not a.loc[keep,attrs].equals(b.loc[keep,attrs]) or not np.allclose(
                a.loc[keep,'exit_price'],b.loc[keep,'exit_price'],rtol=1.e-12,atol=0):
            raise ValueError(f'Extending {ticker} rewrote a previously completed child leg')
        meta['changed_carry_legs'] = int((a.exit_reason.ne(b.exit_reason)|a.exit_bar_ms.ne(b.exit_bar_ms)).sum())
    meta.update(carried_events=len(carry),fresh_keys=len(fresh),priced=priced,price_decimals=decimals,
                first_added_ms=int(new.index[0]),last_added_ms=int(new.index[-1]))
    return meta


def job(args):
    path,separate = args
    slot = CACHE/path.name.removesuffix('.csv.gz')
    receipt = slot/'result.json'
    if receipt.exists():
        return json.loads(receipt.read_text())
    result = extend(path,slot,separate=separate)
    tmp = receipt.with_suffix('.tmp')
    tmp.write_text(json.dumps(result,allow_nan=False))
    tmp.replace(receipt)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers',type=int,default=2)
    args = parser.parse_args()
    if not (SOURCE/'manifest.json').is_file():
        raise FileNotFoundError('Official H1 extension collection has not finished')
    prepare_supplemental_history()
    source = pd.read_csv(SOURCE/'coverage.csv.gz',dtype={'symbol':str,'contract_period_id':str})
    if not source.contract_period_id.is_unique:
        raise ValueError('Contract source contains duplicate period IDs')
    work = []
    for p in sorted((SOURCE/'series').glob('*.csv.gz')):
        period = p.name.removesuffix('.csv.gz')
        row = source.loc[source.contract_period_id.eq(period)]
        if len(row)!=1:
            raise ValueError(f'No unique source receipt for {period}')
        separate = 'separate_period_no_continuity_claim' in str(row.iloc[0].period_link_status)
        work.append((p,separate))
    if not work:
        raise ValueError('No official in-window tapes found')
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        results = []
        for n,result in enumerate(pool.map(job,work),1):
            results.append(result)
            if n%50==0:
                print(f'Extended {n}/{len(work)} contract periods',flush=True)
    coverage = pd.DataFrame([{k:v for k,v in r.items() if k not in ('priced','key_disagreements')} for r in results])
    coverage.to_csv(EXP/'periods.csv.gz',index=False)
    disagreements = [d for record in results for d in record['key_disagreements']]
    pd.DataFrame(disagreements,columns=['symbol','event_id','in_frozen','in_rebuilt']).to_csv(EXP/'key_differences.csv',index=False)
    summary = dict(periods=len(results),source_symbols=source.symbol.nunique(),fresh_keys=int(coverage.fresh_keys.sum()),
                   carry_events=int(coverage.carried_events.sum()),changed_legs=int(coverage.changed_carry_legs.sum()),
                   earlier_parity_keys=int(coverage.matched_previous_keys.sum()),parity_differences=len(disagreements),
                   old_interior_missing=int(coverage.old_interior_missing.sum()),
                   supplemental_periods=int(coverage.supplemental_new_history.sum()),
                   supplemental_mature_by_end=int((coverage.supplemental_new_history &
                       (coverage.supplemental_mature_at_ms<=END)).sum()))
    (EXP/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2),flush=True)


if __name__ == '__main__':
    main()
