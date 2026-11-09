"""Price exits triggered by the preceding original shadow wallet's observable parent cash gain."""
import pandas as pd

from yoyo.evaluation import cloud_close_cash_giveback as cash

queue=cash.queue
EXP=cash.ROOT/'exp-cloud-close-cash-trail-20261109-v1'


def change_children(children,triggers,quotes):
    rows=children.copy()
    records=[]
    for i in rows.index[rows.event_id.isin(triggers.index)]:
        row=rows.loc[i]
        t=int(triggers.at[row.event_id])
        if t<=row.entry_ms or t%cash.STEP!=0:
            raise ValueError('Invalid new close-confirmed order clock')
        if row.exit_bar_ms<t or (row.exit_bar_ms==t and
           (row.exit_phase=='open' or row.exit_reason=='delisting_settlement')):
            continue
        stamps,px=quotes(row.symbol)
        pos=stamps.searchsorted(t)
        if pos>=len(stamps) or stamps[pos]!=t:
            raise ValueError('No native open at the predeclared exit')
        records.append(dict(event_id=row.event_id,child=int(row.slot%4),symbol=row.symbol,exit_ms=t,
                           prior_exit_ms=int(row.exit_bar_ms),prior_exit_reason=row.exit_reason,price=px[pos,0]))
        rows.loc[i,['exit_bar_ms','exit_phase','exit_price','exit_fee_rate','exit_reason','same_bar_ambiguous']]=[
            t,'open',px[pos,0],queue.repair.wallet.TAKER,'shadow_cash_profit_retrace',False]
    return rows,pd.DataFrame(records)


def main(*,experiment=EXP,signal_loader=None):
    if (experiment/'summary.json').exists():
        raise FileExistsError('Completed cash-peak exits')
    data,_,quotes,parents,cap,ratios=cash.account()
    result={}
    for label,slip in [('median',0.),('median_stress',.0005)]:
        obs=pd.read_csv(cash.EXP/f'parents_{label}.csv.gz',index_col='event_id')
        if signal_loader is None:
            signal=obs.trigger_ms.dropna().astype('int64')
        else:
            signal=signal_loader(label,slip,quotes,parents)
        for name,delay in [('confirmed',0),('late',cash.STEP)]:
            key=label+'_'+name
            updated,changes=change_children(data['selected'],signal+delay,quotes)
            changes.to_csv(experiment/f'exits_{key}.csv.gz',index=False)
            prepared=queue.repair.wallet.prepare(None,data['actions'].shape[1],assigned=(updated,{}),quote_loader=quotes)
            market=queue.engine.base.underlying(prepared,quotes)
            case=queue.engine.simulate(prepared,market,quotes,ratios,slip,experiment,key,entry_limits=cap)
            result[key]=dict(case=case,signals=len(signal),changed_parents=changes.event_id.nunique(),
                             changed_children=len(changes),missing_holding_hours=prepared['gap_marks'])
            if 'metrics' in case:
                funds=pd.read_csv(experiment/f'funded_{key}.csv.gz',index_col='event_id')
                if set(funds.index)!=set(obs.index):
                    raise ValueError('Positive opening roster changed despite reserved shadow slots')
                print(key,case['metrics']['all'],flush=True)
    queue.repair.archive.prices.safe_json(experiment/'summary.json',result)


if __name__=='__main__':
    main()
