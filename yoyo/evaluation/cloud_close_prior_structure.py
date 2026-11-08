"""Evaluate existing pre-key structural screens with a shared, properly funded account."""
import argparse
import gc
import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT=Path(__file__).resolve().parents[2]
EXP=ROOT/'experiments/active/exp-cloud-close-prior-structure-20261109-v1'
SOURCE=EXP.parent/'exp-cloud-allmarket-screen-20261108-v1'
TOP=EXP.parent/'exp-cloud-top20-peak10r-20261108-v1'
OLD=EXP.parent/'exp-cloud-pretouch-structure-20261108-v1'


def features():
    from yoyo.evaluation import cloud_pretouch_structure as frozen
    config=json.loads((EXP/'config.json').read_text())
    rules=config['features']
    if rules!=frozen.RULES:
        raise ValueError('Frozen feature definitions have changed')
    src=pd.read_csv(SOURCE/'signals.csv',float_precision='round_trip',dtype={'symbol':str})
    parts=[]
    for symbol,group in src.groupby('symbol'):
        study=TOP if group.was_top20.iloc[0] else SOURCE
        parts.append(frozen.features(group,frozen.read_bars(study/'inputs'/symbol)))
    values=pd.concat(parts,ignore_index=True)
    old=pd.read_csv(OLD/'features.csv.gz',dtype={'symbol':str})
    compare=old.merge(values,on=['symbol','week'],suffixes=('_old',''),validate='one_to_one')
    if len(compare)!=len(old) or len(old)!=6386:
        raise ValueError('Original partial-history observations lost')
    for name in rules:
        if not compare[name].astype('boolean').equals(compare[name+'_old'].astype('boolean')):
            raise ValueError('Old and reconstructed feature disagree: '+name)
    ids=src[['symbol','week']].copy()
    ids['source_row_number']=np.arange(1,len(ids)+1)
    all_values=ids.merge(values,on=['symbol','week'],validate='one_to_one')
    if len(all_values)!=12707:
        raise ValueError('Frozen event universe changed')
    all_values.to_csv(EXP/'prior_structure.csv.gz',index=False)
    print('Verified 3 features on',len(compare),'frozen events; total',len(all_values))


def main():
    from yoyo.evaluation import cloud_close_lock_allocation as allocation
    from yoyo.evaluation import cloud_close_lock_profit as lock
    from yoyo.evaluation import cloud_close_reward_account as wallet
    cfg=json.loads((EXP/'config.json').read_text())
    rules=cfg['features']
    features=pd.read_csv(EXP/'prior_structure.csv.gz').set_index('source_row_number')
    ledger=pd.read_csv(lock.EXP/'profit_lock_paths.csv.gz',dtype={'symbol':str},keep_default_na=False)
    ledger=ledger[ledger.arm_at_r.eq(2.)].copy()
    parent=pd.read_csv(allocation.EXP/'account_metrics.csv')
    rows=parent[parent.allocation.eq(1.)].drop(columns='allocation').assign(rule='none').to_dict('records')
    samples={}
    def arms(rule):
        ok=ledger.source_row_number.map(features[rule]).astype('boolean').fillna(True).to_numpy(dtype=bool)
        return ledger[ok&~ledger.is_control],ledger[ok&ledger.is_control]
    for rule in rules:
        real,ctl=arms(rule)
        metrics,meta,tr,eq=wallet.evaluate(real,9)
        rows.extend(dict(rule=rule,period=p,**v) for p,v in metrics.items())
        samples[rule]=tr,eq
        gc.collect()
    frame=pd.DataFrame(rows)
    frame.to_csv(EXP/'account_metrics.csv',index=False)
    dev=frame[frame.period.eq('dev')].sort_values('multiple',ascending=False)
    eligible=dev[dev.apply(wallet.qualifies,axis=1)&dev.payoff_usdt.ge(2.5)&dev.trades.ge(100)]
    pick=eligible.rule.iloc[0] if len(eligible) else None
    checks={}
    if pick is not None:
        tr,eq=samples[pick]
        tr.to_csv(EXP/'funded_dev_pick.csv.gz',index=False)
        eq.to_csv(EXP/'hourly_dev_pick.csv.gz',index_label='hour_end_utc')
        real,ctl=arms(pick)
        for name,events,slip in [('paired_signals',real[real.parent_id.isin(ctl.parent_id)],0.),
                                 ('control',ctl,0.),('slippage_5bp',real,.0005)]:
            checks[name]=wallet.evaluate(events,9,slip=slip)[0]
            gc.collect()
    summary=dict(pick=pick,eligible=eligible.rule.tolist(),checks=checks)
    (EXP/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(frame[frame.period.eq('all')].to_string(index=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--features',action='store_true')
    if parser.parse_args().features:
        features()
    else:
        main()
