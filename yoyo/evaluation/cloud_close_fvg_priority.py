"""Use first-touch gap overlap only to rank simultaneous 12H opportunities."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_fvg_transfer as fvg

spot = fvg.spot
admin = spot.admin
EXP = admin.EXP.parent/'exp-cloud-close-fvg-priority-20261109-v1'


def priority(source,tags):
    frame = source[['event_id','entry_ms','new_rank']].copy().rename(columns={'new_rank':'reference_rank'})
    frame['tag'] = frame.event_id.map(tags)
    if frame.tag.isna().any():
        raise ValueError('Unexplained missing coverage row for a frozen candidate')
    frame['rank'] = frame.reference_rank
    eligible = frame[frame.tag.isin(['overlap','outside'])].sort_values(['entry_ms','reference_rank'])
    for _,group in eligible.groupby('entry_ms'):
        order = group.sort_values(['tag','reference_rank'])  # outside precedes overlap
        frame.loc[order.index,'rank'] = group.reference_rank.to_numpy()
    if frame.duplicated(['entry_ms','rank']).any() or not frame.loc[~frame.index.isin(eligible.index),'rank'].equals(
            frame.loc[~frame.index.isin(eligible.index),'reference_rank']):
        raise ValueError('Source rank positions were not preserved')
    return frame


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed FVG priority')
    original = pd.read_csv(spot.rank.scores.EXP/'candidate_order.csv.gz')
    tags = pd.read_csv(fvg.EXP/'decisions.csv.gz',usecols=['parent_id','tag']).set_index('parent_id').tag
    ranked = priority(original,tags)
    ranked.to_csv(EXP/'priority.csv.gz',index=False)
    extra = set(json.loads((admin.archive.EXP/'summary.json').read_text())['archived_additions'])
    extra.update(json.loads((admin.study.EXP/'summary.json').read_text())['new_archive_cohorts']['priced'])
    spec,marks = spot.rank.market_spec(EXP)
    for directory in (spot.vwap.EXP,spot.timing.EXP,fvg.EXP):
        spec.update(json.loads((directory/'config.json').read_text())['additional_settlements'])
        marks.update(pd.read_csv(directory/'settlement_index_1m.csv').groupby('symbol')[['low','close','high']].mean().to_dict('index'))
    with admin.archive.market_quotes(extra):
        holds = admin.archive.pool(extra)
        _,raw = admin.study.tenth.trend.inputs('Binance',holds[~holds.is_control])
        legs = admin.adjusted_legs(raw,extra,spec)
        if set(legs.event_id)!=set(original.event_id):
            raise ValueError('Treatment opportunities differ from the full original population')
        legs['entry_order'] = legs.event_id.map(ranked.set_index('event_id')['rank'])
        paid = admin.admitted(legs)
        paid.loc[paid.delivery].to_csv(EXP/'funded_settlements.csv',index=False)
        ready = admin.resolve_settlements(paid.copy(),marks,'proxy')
        data = admin.archive.native.prepare(ready)
        metrics,pos,eq,trims = spot.hourly.tested.trim_account(data,admin.archive.native.quotes,.9,.2,review_hours=1)
    if not np.isclose(1000+pos.cash_pnl.sum(),eq.nav.iloc[-1],rtol=1.e-8,atol=.001):
        raise ValueError('Funded account is not reconciled')
    pos.to_csv(EXP/'funded.csv.gz')
    eq.to_csv(EXP/'hourly.csv.gz',index_label='hour_end_utc')
    frozen = pd.read_csv(spot.hourly.EXP/'funded_Binance.csv.gz',usecols=['event_id']).event_id
    result = dict(ranked=metrics,reference=json.loads((spot.hourly.EXP/'summary.json').read_text())['Binance']['metrics'],
                  eligible=len(ranked),changed_ranks=int(ranked.reference_rank.ne(ranked['rank']).sum()),
                  newly_funded=len(set(pos.index)-set(frozen)),dropped_funded=len(set(frozen)-set(pos.index)),
                  positions_trimmed=trims.event_id.nunique())
    (EXP/'summary.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(json.dumps(result,indent=2),flush=True)


if __name__=='__main__':
    main()
