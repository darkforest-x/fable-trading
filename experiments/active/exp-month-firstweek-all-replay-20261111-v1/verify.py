"""Check scope, clock boundaries, evidence from the previous blind deck and native timeframe caches."""
from pathlib import Path
import numpy as np
import pandas as pd
from replay import HERE,OUT,DEST,INPUT,BAR,HOUR,WINDOW,geometry,read_raw,form_card,make_tape,ROUNDTRIP


def verify():
    rows=pd.read_csv(OUT/'reviewed.csv.gz',**ROUNDTRIP)
    groups=pd.read_csv(OUT/'group_first_watch.csv.gz',**ROUNDTRIP)
    levels=pd.read_csv(INPUT,**ROUNDTRIP)
    assert rows.id.nunique()==len(rows)==len(levels)==50986
    assert groups.id.nunique()==len(groups)==12564 and groups.touch_days.sum()==len(rows)
    original=levels['币安合约']+'|'+levels['首次触价小时（北京）'].str[:10]
    assert set(original)==set(rows.id)
    for field in ('first_confirmation_ms','first_outside_ms','first_reclaim_ms'):
        seen=rows[field].notna()
        assert (rows.loc[seen,field]<=rows.loc[seen,'path_until_ms']).all()
        assert (rows.loc[seen,field]>=rows.loc[seen,'scheduled_close_ms']).all()
    confirmed=rows[rows.first_confirmation_ms.notna()]
    assert (confirmed.confirmation_close_pct>confirmed.confirmation_reference_pct).all()
    assert (confirmed.first_reclaim_ms<confirmed.first_confirmation_ms).all()

    prev=pd.read_csv(HERE.parent/'exp-month-firstweek-adjacent-replay-20261111-v1'/'observations.csv',**ROUNDTRIP)
    refs=rows.merge(prev[['card','symbol','month','side','second_close','outcome']],on=['symbol','month','side'])
    refs=refs[refs.adjacent_ms==refs.second_close]
    assert refs.card.nunique()==len(prev) and refs.adjacent_result_24h.eq(refs.outcome).all()

    # Recreate prices from physically truncated raw histories, including both realized and unavailable first closes.
    probes=pd.concat([rows.sort_values('id').iloc[::241], rows[rows.close_unavailable.notna()].iloc[::29]]).drop_duplicates('id')
    for x in probes.itertuples():
        start,ready,end=WINDOW[x.month]
        raw=read_raw(x.symbol)
        cutoff=min(x.scheduled_close_ms,x.path_until_ms)
        raw=raw[raw[:,0]+HOUR<=cutoff]
        week=raw[(raw[:,0]>=start)&(raw[:,0]<ready)]
        tape=make_tape(raw,x.touch_ms,ready,min(end,cutoff),x.rail,1 if x.side=='long' else -1)
        card=form_card(x,tape,raw,(week[:,3].min(),week[:,2].max()),end)
        assert card['first_close_state']==x.first_close_state, x.id
        if 'close_pct' in card:
            assert np.isclose(card['close_pct'],x.close_pct) and card['last_touch_hour']==x.last_touch_hour

    # Archived 15/30/60/240/720-minute rows are views of the same touches, including the older BTC ledger.
    native=[]
    for file in sorted((DEST.parent/'scanned').glob('*/events.csv.gz')):
        try: native.append(pd.read_csv(file,usecols=['symbol','month','side','tf','touch_ms','level'],**ROUNDTRIP))
        except pd.errors.EmptyDataError: continue
    native=pd.concat(native)
    native.side=native.side.map({'upper':'short','lower':'long'})
    native['hour']=native.touch_ms//HOUR*HOUR
    matched=native.merge(rows[['symbol','month','side','touch_ms','rail']],left_on=['symbol','month','side','hour'],
                         right_on=['symbol','month','side','touch_ms'],how='left',validate='many_to_one')
    assert matched.rail.notna().all() and np.allclose(matched.level,matched.rail,rtol=1e-10,atol=0)
    original45=levels.set_index(original)
    ext=pd.read_csv(OUT/'45day_path.csv.gz',**ROUNDTRIP).set_index('id').reindex(original)
    assert np.allclose(ext.first_extreme20_hours,original45['第几小时达到20%'],equal_nan=True)
    full=ext.observed_hours_45==1080
    assert full.equals(original45['45天最大反向幅度%'].notna())
    assert np.allclose(ext.peak_after_touch_pct[full],original45.loc[full,'45天最大反向幅度%'])
    two=pd.read_csv(OUT/'two_reclaims_followup.csv.gz')
    assert not two.duplicated(['symbol','month','side']).any()
    totals=pd.read_csv(HERE/'two_reclaims_counts.csv').iloc[:,1:].sum().sum()
    assert len(two)==totals
    print(f'validated {len(rows)} days, {len(groups)} groups, {len(probes)} prefix truncations, '
          f'{len(prev)} prior cases, {len(matched)} cross-timeframe references, {len(two)} paired reclaims')


if __name__=='__main__': verify()
