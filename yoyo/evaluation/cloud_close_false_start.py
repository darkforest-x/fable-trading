"""Leave a V1.7 parent when an early profit turns negative before the trade ever establishes a 2R cash peak."""
import pandas as pd

from yoyo.evaluation import cloud_close_cash_trail as trail

cash=trail.cash
EXP=cash.ROOT/'exp-cloud-close-false-start-20261109-v1'


def signals(label,slip,quotes,parents):
    funds=pd.read_csv(cash.SOURCE/f'aligned/funded_{label}.csv.gz',index_col='event_id')
    fills=pd.read_csv(cash.EXP/f'orders_{label}.csv.gz',float_precision='round_trip')
    records=[]
    def save(row,values):
        risk=funds.at[row.name,'notional']*abs(row.stop_price-row.entry_price)/row.entry_price
        past=values.cummax().shift()
        loss=values[(past>0)&(past<2*risk)&(values<=0)]
        if len(loss):
            clock=int(loss.index[0])
            records.append(dict(event_id=row.name,trigger_ms=clock,value=loss.iat[0],
                                previous_peak=past.at[clock],risk=risk))
    for eid,orders in fills.groupby('underlying_event_id'):
        cash.parent_marks(orders,parents.loc[eid],funds.loc[eid],quotes,slip,live_sink=save)
    detected=pd.DataFrame(records).set_index('event_id')
    detected.to_csv(EXP/f'flags_{label}.csv.gz')
    return detected.trigger_ms


if __name__=='__main__':
    trail.main(experiment=EXP,signal_loader=signals)
