"""Compare matched ruler episodes, with one primary pair at most per original image.
Outcomes are directional 24h price paths from the next open. Cloud flags use
only EMA20/EMA50 of completed 5m and 15m closes at the actual model read.
"""
import numpy as np
import pandas as pd
from windows import HERE, DATA, MINUTE, after_read, cohort, frame

def main():
    anchors = pd.read_csv(HERE / 'primary_anchors.csv')
    primary = anchors[anchors.in_primary]
    assert primary.photo.is_unique and primary.case_id.is_unique
    cases = cohort().set_index('case_id', drop=False)
    owners = cases[cases.index.isin(primary.case_id)]
    assert owners.role.eq('owner').all()
    usable = cases[cases.pair_id.isin(owners.pair_id)]
    assert usable.groupby('pair_id').role.nunique().eq(2).all()
    day = pd.read_csv(HERE / 'first_in_day.csv')
    reads = pd.read_csv(HERE / 'case_comparison.csv')
    keys = ['case_id', 'tf']
    cols = keys + ['hours_available', 'found', 'delay', 'read_ms', 'before_pct',
                    'favorable24', 'adverse24', 'change24', 'ref_open']
    joined = reads[reads.case_id.isin(usable.index)].merge(
        day[cols].rename(columns={c:'day_'+c for c in cols if c not in keys}),
        on=keys, validate='one_to_one')
    assert (joined.day_found | ~joined.found).all()
    assert joined.groupby(['role','tf']).size().eq(len(owners)).all()
    for case_id, group in joined.groupby('case_id'):
        row = cases.loc[case_id]
        tape = np.load(DATA/'tapes'/f'{case_id}.npy')
        baseline = after_read(row, int(row.known_ms), tape)
        for metric in ('favorable24','adverse24','change24'):
            joined.loc[group.index,'from_h1_'+metric] = baseline[metric]
        direction = 1 if row.side=='long' else -1
        joined.loc[group.index,'distance_from_rail_pct'] = 100*direction*(group.day_ref_open/row.rail-1)
        for tf in (5,15):
            b = frame(row, tf)
            closes = b.open_ms.to_numpy()+tf*MINUTE
            spread = b.ema20-b.close.ewm(span=50,adjust=False).mean()
            for idx, entry in group[group.day_found].iterrows():
                pos = closes.searchsorted(entry.day_read_ms, side='right')-1
                assert 0 <= entry.day_read_ms-closes[pos] < tf*MINUTE
                joined.loc[idx,f'cloud{tf}_same'] = bool((1 if row.side=='long' else -1)*spread.iat[pos]>0)
    joined.to_csv(HERE/'paired_primary.csv',index=False)
    summary = joined.groupby(['role','tf']).agg(
        cases=('case_id','size'), found4=('found','sum'), found24=('day_found','sum'),
        visual4=('clear','sum'), match4=('matched','sum'), match_grace=('extended','sum'),
        delay_min=('day_delay','median'), already_pct=('day_before_pct','median'),
        favorable24=('day_favorable24','median'), adverse24=('day_adverse24','median'),
        change24=('day_change24','median'))
    summary.to_csv(HERE/'primary_summary.csv',float_format='%.6f')
    print(summary.to_string())
    print('distinct', joined.groupby(['role','case_id'])[['found','day_found','clear','matched']].any().groupby(level=0).sum().to_dict())
    print('matched pairs',len(owners))
if __name__=='__main__':main()
