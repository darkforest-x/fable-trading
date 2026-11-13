"""Recover first confirmed 5m reads and then-known near edge on each original pair."""
from pathlib import Path
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OLD = HERE.parent/'exp-month-firstweek-owner-yolo-20261113-v1'

def main():
    primary = pd.read_csv(OLD/'paired_primary.csv').query('tf==5')
    cases = pd.concat([pd.read_csv(where/'cohort.csv') for where in (OLD,OLD/'ruler')])
    cards = pd.read_csv(ROOT/'data/research/alt-isolated-12h-wicks-20261111/expanded12/all_replay_v1/cards.csv.gz',
                        usecols=['symbol','touch_ms','side','near_edge'],float_precision='round_trip')
    tradable = primary[primary.day_found].merge(cases[['case_id','touch_ms','tape_path','month']],on='case_id',validate='one_to_one')
    joined = tradable.merge(cards,on=['symbol','touch_ms','side'],how='left',validate='one_to_one')
    assert len(primary)==28 and len(joined)==18 and joined.near_edge.notna().all()
    fields = joined.rename(columns={'case_id':'trade_id','day_read_ms':'confirm_close_ms','role':'pool'})
    fields['confirm_close_ms']=fields.confirm_close_ms.astype('int64')
    fields[['trade_id','symbol','side','confirm_close_ms','pool','near_edge','tape_path','known_ms','month']].to_csv(HERE/'primary_signals.csv',index=False)
if __name__=='__main__':
    main()
