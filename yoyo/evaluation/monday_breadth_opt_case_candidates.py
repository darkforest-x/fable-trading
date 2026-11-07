"""Conditional entry search at the exact named September clock; never described as blind testing.

Restrict candidate discovery to ordinary 5m initial candles with 30m confirmation
whose complete rule would actually enter the named episode. Retain all historical
admissions (including losing events) and calculate only missing native BTC fills.
"""
import numpy as np
import pandas as pd
from yoyo.evaluation import monday_breadth_opt_exit_study as study
from yoyo.evaluation import monday_breadth_opt_funding as funding
from yoyo.evaluation import monday_breadth_opt_selection as s


def run():
    fr,_=s.inputs.partition(5,30)
    slots,fold=s.time_axes(fr)
    case=np.flatnonzero(fr.decision.eq(study.CASE_ENTRY))
    if len(case)!=1:raise ValueError('Missing unique original case')
    i=case[0]
    b,d,c=s.rules(fr,5,30)
    full=fr.monitor_fraction.eq(1).to_numpy()
    seen={};candidates=[];pieces=[];mapping=[]
    for br,bb in b.items():
        if not bb[i]:continue
        for di,dd in d.items():
            if not dd[i]:continue
            for co,cc in c.items():
                if not cc[i]:continue
                ix=np.flatnonzero(full&bb&dd&cc)
                latest=s.admissions(ix[fold[ix]==3],slots,144*12,s.ENDS[3])
                if i not in latest:continue
                epochs=[s.admissions(ix[fold[ix]==j],slots,144*12,s.ENDS[j]) for j in range(3)]
                earlier=np.concatenate(epochs)
                # Collapse exact development + validation paths; pick the first simple representation.
                key=earlier.tobytes()
                if key not in seen:
                    name=f'casegrid_BTC_{len(seen):03d}'
                    seen[key]=name
                    selected=np.r_[earlier,latest]
                    ev=fr.iloc[selected][['minutes','known','decision','initial','new_coins']].copy()
                    ev['epoch']=fold[selected];ev['hours']=144;ev['asset']='BTC'
                    ev['candidate_id']=name;ev['expected']=1
                    pieces.append(ev)
                    candidates.append(dict(candidate_id=name,asset='BTC',minutes=5,wait_min=30,breadth=br,diffusion=di,confirmation=co))
                mapping.append(dict(breadth=br,diffusion=di,confirmation=co,candidate_id=seen[key]))
    if not pieces:raise ValueError('Even the frozen reference rule did not enter the September episode')
    events=pd.concat(pieces,ignore_index=True)
    pd.DataFrame(candidates).to_csv(study.OUT/'case_grid_entries.csv',index=False)
    pd.DataFrame(mapping).to_csv(study.OUT/'case_grid_equivalent_rules.csv',index=False)
    events.to_parquet(study.OUT/'case_grid_admissions.parquet',index=False)
    print('Case-conditioned search:',len(mapping),'rules,',len(candidates),'unique,',
          len(events),'admitted events,',events.decision.nunique(),'entry clocks',flush=True)
    funding.existing_or_simulate(events.decision.unique())


if __name__=='__main__':run()
