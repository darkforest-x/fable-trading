"""Saved-only V18 retrospective path decomposition, not another price study.

Reads pinned case/control trades, mechanics, pending-event logs, and serial
tables plus the pinned V16 descriptive reference. No new entry filter, price,
inference, threshold grid or strategy import. All251/462 identities retained.
Pandas2.3.3 joins use unique event_id and exact source hashes before analysis.
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.merge.html
"""
from pathlib import Path
import hashlib
import json
import pandas as pd

DIRECTORY=Path(__file__).resolve().parent
RESULTS=DIRECTORY/'results'
SUMMARY_SHA='d866fb08f43fd5832e3b51e88b111b4af5b640692b9b9687c42035ce95c8a9b5'
V16_SHA='9a43891b81bf50c281db3a26d1e53188f6b7876c746b75509ae78a570826bc47'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def describe(frame):
    return {'n':len(frame),'known':int(frame.delta_net_bp.notna().sum()),
        'old_mean_bp':frame.baseline_net_bp.mean(),'new_mean_bp':frame.candidate_net_bp.mean(),
        'mean_delta_bp':frame.delta_net_bp.mean(),'sum_delta_event_bp':frame.delta_net_bp.sum(min_count=1)}


def run():
    output=DIRECTORY/'path_diagnostics.json'
    assert not output.exists(), 'Preserve diagnostic output'
    assert sha(RESULTS/'summary.json')==SUMMARY_SHA
    summary=json.loads((RESULTS/'summary.json').read_text())
    pins={}
    def read(name):
        path=RESULTS/name
        assert sha(path)==summary['output_hashes'][name],name
        pins[name]=sha(path)
        return pd.read_csv(path)
    result={'summary_sha256':SUMMARY_SHA,'raw_prices_read':False,'inference_recomputed':False,
        'posthoc_entry_selection':False,'source_hashes':pins}
    for population,expected in [('case',251),('control',462)]:
        m=read('confirmed_'+population+'_mechanics.csv')
        a=read('candidate/'+population+'_trades.csv.gz').set_index('event_id')
        b=read('baseline/'+population+'_trades.csv.gz').set_index('event_id')
        assert len(m)==len(a)==len(b)==expected and not m.event_id.duplicated().any()
        assert set(m.event_id)==set(a.index)==set(b.index)
        path=m.merge(a[['outcome','partial_fraction','max_favourable_r','risk_pct']],left_on='event_id',right_index=True,validate='one_to_one')
        oldfull=path[path.baseline_failed_full]
        cycles=[]
        for event_id,row in a.iterrows():
            for item in json.loads(row.failed_confirm_events):
                cycles.append({'event_id':event_id,'action':item['action'],'reason':item['reason']})
        events=pd.DataFrame(cycles)
        groups={str(key):describe(part) for key,part in oldfull.groupby('outcome')}
        reasons=[]
        for (action,reason),part in events.groupby(['action','reason']):
            reasons.append({'action':action,'reason':reason,'events':len(part),'distinct_trades':part.event_id.nunique()})
        loss=a[a.net_return<0]
        loss_scope={'losses':len(loss),'mfe_ge1r':int(loss.max_favourable_r.ge(1).sum()),
            'mfe_below1r':int(loss.max_favourable_r.lt(1).sum()),
            'mfe_ge1r_but_gross_not_above_fee':int((loss.max_favourable_r.ge(1)&(loss.max_favourable_r*loss.risk_pct).le(.002)).sum())}
        selected=read('candidate/single_pending.csv.gz') if population=='case' else None
        detail={'old_full_by_final_outcome':groups,'pending_reasons':reasons,'loss_scope':loss_scope,
            'restored_partials':describe(oldfull[oldfull.partial_fraction.eq(.5)]),
            'no_restored_partial':describe(oldfull[oldfull.partial_fraction.ne(.5)]),
            'top_improvement':m.sort_values(['delta_net_bp','event_id'],ascending=[False,True]).head(5).to_dict('records'),
            'top_damage':m.sort_values(['delta_net_bp','event_id']).head(5).to_dict('records'),
            'first_ten_by_entry':m.sort_values('mother_decision_time').head(10).to_dict('records'),
            'full_mean_without_largest_positive_delta_bp':m.delta_net_bp.sort_values().iloc[:-1].mean()}
        if selected is not None:
            chosen=selected.portfolio_selected
            assert chosen.isin([True,False]).all() and len(selected)==251
            detail['serial']={'selected':int(chosen.sum()),
                'selected_mean_net_bp':selected.loc[chosen,'episode_net_return'].mean()*1e4,
                'all_opportunity_mean_net_bp':selected.episode_net_return.where(chosen,0).mean()*1e4,
                'skipped_ids':selected.loc[~chosen,'event_id'].tolist()}
        result[population]=detail
    reference=DIRECTORY.parent/'exp-btcusdtp-1h-failed-launch-preholdout-20260906-v17/results/baseline/case_trades.csv.gz'
    assert sha(reference)==V16_SHA
    old=pd.read_csv(reference).set_index('event_id')
    b=read('baseline/case_trades.csv.gz').set_index('event_id').loc[old.index]
    a=read('candidate/case_trades.csv.gz').set_index('event_id').loc[old.index]
    cut=old.net_return.gt(0)&b.net_return.le(0)
    result['v16_reference']={'source':str(reference.relative_to(DIRECTORY.parents[2])),
        'sha256':V16_SHA,'original_sacrificed_winners':int(cut.sum()),
        'restored_winners':int(a.loc[cut].net_return.gt(0).sum()),
        'still_nonpositive':int(a.loc[cut].net_return.le(0).sum()),
        'mean_delta_bp_v18_minus_v16':(a.net_return-old.net_return).mean()*1e4,
        'remaining_sacrifice_ids':a.index[cut&a.net_return.le(0)].tolist()}
    encoded=json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False,default=lambda x:x.item())+'\n'
    with output.open('x') as handle:handle.write(encoded)
    print(encoded)


if __name__=='__main__':run()
