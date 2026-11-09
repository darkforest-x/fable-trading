"""Check continuous-volume sizing against subsequent minute evidence without feeding it to order creation."""
import pandas as pd

from yoyo.evaluation import cloud_close_cash_giveback as cash

EXP=cash.ROOT/'exp-cloud-close-continuous-volume-20261109-v1'
AUDIT=cash.ROOT/'exp-cloud-close-deferred-fill-capacity-audit-20261109-v1'
SOURCE=cash.ROOT/'exp-cloud-close-second-window-capacity-20261109-v1'


def main():
    if (EXP/'turnover_check.json').exists():
        raise FileExistsError('Completed continuous-volume minute check')
    windows=pd.read_csv(AUDIT/'minute_windows.csv.gz')
    members=pd.read_csv(SOURCE/'second_window_capacity.csv.gz',usecols=['event_id','symbol','second_entry_ms','capacity_zero_minutes'])
    cases={}
    receipts=[]
    for cost in ('median','median_stress'):
        for gate in ('continuous_nonpositive','continuous_all'):
            arm=cost+'_'+gate
            adds=pd.read_csv(EXP/f'additions_{arm}.csv.gz').rename(columns={'underlying_event_id':'event_id'})
            source=adds.merge(members,on='event_id',validate='one_to_one')
            if len(source)!=len(adds) or not source.capacity_zero_minutes.eq(0).all() or not source.hour.equals(source.second_entry_ms):
                raise ValueError('Source clock or previous sixty minutes disagree with actual addition')
            measured=source.merge(windows[['symbol','hour','status','minute_quote_volume_usdt']],on=['symbol','hour'],
                                  how='left',validate='one_to_one')
            measured['arm']=arm
            ok=measured.status.eq('observed')
            observed=measured[ok]
            exceeds=observed.actual_notional>observed.minute_quote_volume_usdt+1e-6
            cases[arm]=dict(additions=len(adds),observed=len(observed),unobserved=int((~ok).sum()),
                observed_zero=int(observed.minute_quote_volume_usdt.eq(0).sum()),exceeds_whole_minute=int(exceeds.sum()),
                observed_over_ten_percent=int((observed.actual_notional>.1*observed.minute_quote_volume_usdt+1e-6).sum()))
            receipts.append(measured)
    pd.concat(receipts,ignore_index=True).to_csv(EXP/'actual_minute_check.csv.gz',index=False)
    cash.queue.repair.archive.prices.safe_json(EXP/'turnover_check.json',cases)


if __name__=='__main__':
    main()
