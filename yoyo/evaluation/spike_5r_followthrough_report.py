"""Time-separated frozen single-axis experiments; no model or outcome tuning.

Dynamic thresholds read only previous three full UTC months of features from
the same timeframe. Outcomes score rules only. Independent paths are not a
portfolio, and all history has previously been exposed.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess
import numpy as np
import pandas as pd
from yoyo.evaluation import spike_10r_search as search
from yoyo.evaluation.spike_5r_followthrough import ARMS,EXP,DATASET
from yoyo.evaluation.spike_high_r_entry_report import auc_score,block_test
from yoyo.evaluation.spike_v8_six_filters import _committed

FIELDS = {'btc_trailing24h_return':1,'btc_completed4h_close_sma20_fraction':1,
 'symbol_completed4h_close_sma20_fraction':1,'symbol_completed4h_sma20_slope3_fraction':1,
 'symbol_relative_strength24h':1,'six_ma_span_to_prior96_median':-1,
 'prior_compact_span_duration':1,'span_expansion_last4_vs_previous16':1,
 'prior32_failed_upbreak_count':-1,'reference_risk_fraction':-1}
PARENT_COMPARISONS = (
    ('confirm1-vs-wait1','confirm1','wait1'),
    ('confirm2-vs-wait2','confirm2','wait2'),
    ('retest2-vs-wait2','retest2','wait2'),
    ('structure0-vs-original','structure0','original'),
    ('structure2-vs-structure0','structure2','structure0'),
)


def past_threshold(frame,column,q):
    """Same-TF prior three whole months, >=100 finite features; no labels."""
    result=pd.Series(np.nan,index=frame.index);month=frame.available_at.dt.strftime('%Y-%m')
    for m in sorted(month.unique()):
        end=pd.Timestamp(m+'-01',tz='UTC');start=end-pd.DateOffset(months=3)
        for tf in sorted(frame.timeframe_min.unique()):
            history=frame.loc[frame.available_at.ge(start)&frame.available_at.lt(end)&frame.timeframe_min.eq(tf),column].dropna()
            if len(history)>=100:result.loc[month.eq(m)&frame.timeframe_min.eq(tf)]=float(history.quantile(q))
    return result


def feature_gates(f):
    definitions={'btc_return_pos':('btc_trailing24h_return','positive'),
      'btc_htf_up':('btc_completed4h_close_sma20_fraction','positive'),
      'symbol_htf_up':('symbol_completed4h_close_sma20_fraction','positive'),
      'symbol_htf_slope_pos':('symbol_completed4h_sma20_slope3_fraction','positive'),
      'relative_strength_pos':('symbol_relative_strength24h','positive'),
      'ma_contraction':('six_ma_span_to_prior96_median','below_one'),
      'platform_long':('prior_compact_span_duration','past_median'),
      'expansion_now':('span_expansion_last4_vs_previous16','above_one'),
      'no_failed_breaks':('prior32_failed_upbreak_count','zero'),
      'old_risk_low10':('reference_risk_fraction','past_low10')}
    masks={};known={};thresholds=[]
    for arm,(column,kind) in definitions.items():
        x=pd.to_numeric(f[column],errors='coerce');ok=np.isfinite(x)
        if column+'_known' in f:ok &= f[column+'_known']
        if kind.startswith('past'):
            threshold=past_threshold(f,column,.5 if kind=='past_median' else .1)
            ok &= threshold.notna();mask=x.ge(threshold) if kind=='past_median' else x.le(threshold)
            thresholds.append(pd.DataFrame(dict(event_key=f.event_key,arm=arm,threshold=threshold)))
        else:mask=x.gt(0) if kind=='positive' else x.lt(1) if kind=='below_one' else x.gt(1) if kind=='above_one' else x.eq(0)
        masks[arm]=mask & ok;known[arm]=ok
    return masks,known,pd.concat(thresholds,ignore_index=True)


def period_mask(f,period):
    if period=='full':return pd.Series(True,index=f.index)
    # Cancelled requests have no fill time but must remain in period coverage.
    clock=f.entry_time
    if 'valid_entry' in f and 'decision_at' in f:
        clock=clock.where(f.valid_entry,f.decision_at)
    resolved=f.exit_time.lt(search.SPLIT)
    if 'valid_entry' in f:resolved |= ~f.valid_entry
    if period=='earlier':return clock.lt(search.SPLIT)&resolved
    if period=='later':return clock.ge(search.SPLIT)
    if period=='crossing_or_unresolved':return clock.lt(search.SPLIT)&~resolved
    raise ValueError(f'unknown period: {period}')


def closed(f):
    return f.loc[f.valid_entry & ~f.censored & np.isfinite(f.net_r)]


def parent_comparison(left,right,comparison):
    """Compare two prescribed arms on same-event, fully closed net outcomes.

    Coverage and ``gt5`` retain each arm's full request denominator.  The
    paired net-return effect includes only event keys closed in both arms and
    groups its sign-flip null by the original signal's ``available_at`` UTC
    month.  This prevents an arm's delayed entry/stop cancellation from being
    presented as an adding or confirmation effect without its own coverage.
    """
    def arm_metrics(frame,prefix):
        complete=closed(frame)
        return {f'{prefix}_events':len(frame),f'{prefix}_invalid':int((~frame.valid_entry).sum()),
                f'{prefix}_censored':int((frame.valid_entry&frame.censored).sum()),
                f'{prefix}_closed':len(complete),f'{prefix}_coverage':len(complete)/len(frame) if len(frame) else np.nan,
                f'{prefix}_gt5':int(complete.net_r.gt(5).sum())},complete
    result={'comparison':comparison}
    left_metrics,left_closed=arm_metrics(left,'left');right_metrics,right_closed=arm_metrics(right,'right')
    result.update(left_metrics);result.update(right_metrics)
    columns=['event_key','available_at','net_return']
    pairs=left_closed.loc[:,columns].merge(right_closed.loc[:,columns],on='event_key',suffixes=('_left','_right'),validate='one_to_one')
    if len(pairs) and not pairs.available_at_left.eq(pairs.available_at_right).all():
        raise ValueError('parent arms disagree on original event clock')
    pairs['net_return_delta']=pairs.net_return_left-pairs.net_return_right
    month_sums=pairs.groupby(pairs.available_at_left.dt.strftime('%Y-%m')).net_return_delta.sum()
    test=block_test(month_sums)
    result.update(paired_closed_events=len(pairs),paired_mean_net_delta_bp=float(pairs.net_return_delta.mean()*1e4) if len(pairs) else np.nan,
                  paired_total_net_delta=float(pairs.net_return_delta.sum()),paired_net_month_block_p=test['p'],
                  paired_net_month_blocks=test['blocks'],paired_net_total_ci_low=test['total_ci_low'],
                  paired_net_total_ci_high=test['total_ci_high'])
    return result


def _receipt_file_sha(receipt,name):
    value=receipt.get('files',{}).get(name)
    if isinstance(value,dict):value=value.get('sha256')
    if not isinstance(value,str) or len(value)!=64:
        raise ValueError(f'dataset receipt lacks {name} hash')
    return value


def load_authenticated_original(dataset,expected_receipt_sha):
    """Read candidates only after config/run-pinned dataset receipt authenticates it."""
    receipt_path=Path(dataset)/'receipt.json';manifest_path=Path(dataset)/'manifest.json';candidates_path=Path(dataset)/'candidates.csv.gz'
    if not isinstance(expected_receipt_sha,str) or search.digest(receipt_path)!=expected_receipt_sha:
        raise ValueError('dataset receipt identity drift')
    receipt=json.loads(receipt_path.read_text())
    if receipt.get('status')!='complete' or search.digest(candidates_path)!=_receipt_file_sha(receipt,'candidates.csv.gz'):
        raise ValueError('dataset candidates aggregate hash drift')
    manifest_sha=_receipt_file_sha(receipt,'manifest.json')
    if receipt.get('manifest_sha256')!=manifest_sha or search.digest(manifest_path)!=manifest_sha:
        raise ValueError('dataset manifest receipt drift')
    manifest=json.loads(manifest_path.read_text())
    if not manifest.get('complete') or manifest.get('candidates')!=49207 or manifest.get('candidate_sha256')!=_receipt_file_sha(receipt,'candidates.csv.gz'):
        raise ValueError('dataset manifest identity mismatch')
    candidates=pd.read_csv(candidates_path)
    if len(candidates)!=49207 or candidates.event_key.duplicated().any():
        raise ValueError('dataset candidate coverage mismatch')
    return candidates


def rate_interval(part,baseline):
    """Paired calendar-month bootstrap retaining empty selected months."""
    p,b=closed(part).copy(),closed(baseline).copy();months=sorted(b.entry_time.dt.strftime('%Y-%m').unique())
    if not len(p) or not len(months):return dict(rate_delta=np.nan,rate_ci_low=np.nan,rate_ci_high=np.nan)
    values=[]
    for x in [b,p]:
        x['month']=x.entry_time.dt.strftime('%Y-%m');x['hit']=x.net_r.gt(5).astype(int)
        values.append(x.groupby('month').agg(n=('hit','size'),h=('hit','sum')).reindex(months,fill_value=0).to_numpy(float))
    a=np.array(values);rng=np.random.default_rng(921621)
    draw=a[:,rng.integers(len(months),size=(2000,len(months))),:].sum(axis=2);valid=(draw[:,:,0]>0).all(axis=0)
    diff=draw[1,valid,1]/draw[1,valid,0]-draw[0,valid,1]/draw[0,valid,0];lo,hi=np.quantile(diff,[.025,.975])
    return dict(rate_delta=float(p.net_r.gt(5).mean()-b.net_r.gt(5).mean()),rate_ci_low=float(lo),rate_ci_high=float(hi))


def metrics(p,b,control,period):
    c,bc=closed(p),closed(b);r=c.net_r
    old=set(bc.loc[bc.net_r.gt(5),'event_key']);new=set(c.loc[r.gt(5),'event_key'])
    x=c.merge(control[['event_key','matched','control_censored','control_entry_time','control_exit_time','control_net_r','control_net_return']],on='event_key',validate='one_to_one')
    valid=x.matched & ~x.control_censored
    if period=='earlier':valid &= x.control_entry_time.lt(search.SPLIT)&x.control_exit_time.lt(search.SPLIT)
    if period=='later':valid &= x.control_entry_time.ge(search.SPLIT)
    x=x.loc[valid].copy();x['month']=x.entry_time.dt.strftime('%Y-%m')
    x['net_delta']=x.net_return-x.control_net_return;x['tail_delta']=x.net_r.gt(5).astype(int)-x.control_net_r.gt(5).astype(int)
    null=block_test(x.groupby('month').net_delta.sum());tail=block_test(x.groupby('month').tail_delta.sum())
    return dict(events=len(p),invalid=int((~p.valid_entry).sum()),censored=int((p.valid_entry&p.censored).sum()),closed=len(c),
        gt5=int(r.gt(5).sum()),gt10=int(r.gt(10).sum()),precision=float(r.gt(5).mean()),gt10_rate=float(r.gt(10).mean()),
        win_rate=float(r.gt(0).mean()),mean_net_bp=float(c.net_return.mean()*1e4),mean_gross_bp=float(c.gross_return.mean()*1e4),mean_net_r=float(r.mean()),
        assets=c.asset.nunique(),months=c.entry_time.dt.strftime('%Y-%m').nunique(),coverage=len(c)/len(bc) if len(bc) else np.nan,
        retained_gt5=len(old&new),lost_gt5=len(old-new),gained_gt5=len(new-old),recall=len(old&new)/len(old) if old else np.nan,
        retained_gt10=len(set(bc.loc[bc.net_r.gt(10),'event_key']) & set(c.loc[r.gt(10),'event_key'])),
        matched_pairs=len(x),unmatched_closed=len(c)-len(x),control_gt5=int(x.control_net_r.gt(5).sum()),
        control_mean_net_bp=float(x.control_net_return.mean()*1e4),paired_excess_net_bp=float(x.net_delta.mean()*1e4),
        random_net_p=null['p'],random_tail_p=tail['p'],**rate_interval(p,b))


def holm(values):
    a=np.asarray(values,float);a=np.where(np.isfinite(a),a,1.);order=np.argsort(a);out=np.ones(len(a));running=0.
    for rank,i in enumerate(order):running=max(running,min(1.,a[i]*(len(a)-rank)));out[i]=running
    return out


def run(source,output):
    deps=[Path(__file__),Path('tests/evaluation/test_spike_5r_followthrough_report.py'),EXP/'config.json']
    if not _committed(deps):raise ValueError('commit report builder before evaluation')
    if output.exists():raise ValueError('refuse to overwrite analysis')
    manifest=json.loads((source/'manifest.json').read_text())
    if not manifest['completed'] or manifest['candidates']!=49207:raise ValueError('incomplete source')
    config=json.loads((EXP/'config.json').read_text())
    expected_receipt_sha=manifest.get('source_receipt_sha256')
    if expected_receipt_sha!=config.get('source_receipt_sha256'):
        raise ValueError('run manifest and config disagree on dataset receipt')
    for name,sha in manifest['files'].items():
        if search.digest(source/name)!=sha:raise ValueError('aggregate hash drift')
    for key,sha in manifest['receipts'].items():
        folder=source/'streams'/key
        if search.digest(folder/'completion.json')!=sha:raise ValueError('stream receipt drift')
        rec=json.loads((folder/'completion.json').read_text())
        if rec['identity']!=manifest['identity'] or any(search.digest(folder/n)!=h for n,h in rec['files'].items()):raise ValueError('stream leaves drift')
    f=pd.read_csv(source/'features.csv.gz');p=pd.read_csv(source/'paths.csv.gz');ctrl=pd.read_csv(source/'controls.csv.gz');bounds=pd.read_csv(source/'bounds.csv.gz')
    for x in [f,p,ctrl,bounds]:
        for col in ['available_at','decision_at','entry_time','exit_time','control_entry_time','control_exit_time']:
            if col in x:x[col]=pd.to_datetime(x[col],utc=True)
    original=load_authenticated_original(DATASET,expected_receipt_sha)[['event_key','reference_risk_fraction','net_r','censored']]
    f=f.merge(original[['event_key','reference_risk_fraction']],on='event_key',validate='one_to_one')
    b=p.loc[p.arm.eq('original')].copy();check=b.merge(original,on='event_key',suffixes=('','_source'),validate='one_to_one')
    if not np.allclose(check.net_r,check.net_r_source,equal_nan=True) or not check.censored.eq(check.censored_source).all():raise ValueError('baseline differs')
    if p.duplicated(['arm','event_key']).any() or ctrl.duplicated(['arm','event_key']).any():raise ValueError('duplicate arm/event')
    masks,known,thresholds=feature_gates(f);parent_tables={a:g.copy() for a,g in p.groupby('arm')};tables=parent_tables.copy();control_tables={a:g.copy() for a,g in ctrl.groupby('arm')};flags=[]
    if set(parent_tables)!=set(ARMS):raise ValueError('missing parent arm output')
    for arm,mask in masks.items():
        keys=set(f.loc[mask,'event_key']);tables[arm]=b.loc[b.event_key.isin(keys)].assign(arm=arm)
        control_tables[arm]=control_tables['original'].loc[lambda x:x.event_key.isin(keys)].assign(arm=arm)
        flags.append(pd.DataFrame(dict(event_key=f.event_key,arm=arm,known=known[arm],selected=mask)))
    results=[];ranking=[]
    parent_rows=[]
    for period in ['full','earlier','later','crossing_or_unresolved']:
        baseline=b.loc[period_mask(b,period)]
        for arm,t in tables.items():
            part=t.loc[period_mask(t,period)]
            results.append(dict(period=period,arm=arm,**metrics(part,baseline,control_tables[arm],period)))
        for comparison,left_arm,right_arm in PARENT_COMPARISONS:
            left=parent_tables[left_arm].loc[period_mask(parent_tables[left_arm],period)]
            right=parent_tables[right_arm].loc[period_mask(parent_tables[right_arm],period)]
            parent_rows.append(dict(period=period,left_arm=left_arm,right_arm=right_arm,
                                    **parent_comparison(left,right,comparison)))
    result=pd.DataFrame(results)
    for period in result.period.unique():
        sel=result.period.eq(period)&~result.arm.isin(['original','old_risk_low10'])
        result.loc[sel,'random_net_holm_p']=holm(result.loc[sel,'random_net_p'])
    later=result.loc[result.period.eq('later')].set_index('arm');reference=max(later.loc['original','precision'],later.loc['old_risk_low10','precision']);passed=[]
    for arm,row in later.iterrows():
        if arm in ['original','old_risk_low10']:continue
        accept=(row.closed>=250 and row.gt5>=20 and row.recall>=.1 and row.months>=6 and row.assets>=20 and row.mean_net_bp>0 and row.precision>reference and row.rate_ci_low>0 and row.random_net_holm_p<.01)
        passed.append(dict(arm=arm,passed=bool(accept)))
    for column,direction in FIELDS.items():
        scored=f.copy();scored['_score']=pd.to_numeric(scored[column],errors='coerce')*direction
        threshold=past_threshold(scored,'_score',.9);selected=scored._score.ge(threshold)&threshold.notna()
        joined=b.merge(scored[['event_key','_score']],on='event_key',validate='one_to_one')
        for period in ['earlier','later']:
            universe=joined.loc[period_mask(joined,period)];u=closed(universe);top=universe.loc[universe.event_key.isin(set(scored.loc[selected,'event_key']))]
            ranking.append(dict(feature=column,period=period,auc_gt5=auc_score(u.net_r.gt(5),u._score),**metrics(top,universe,control_tables['original'],period)))
    output.mkdir(parents=True)
    for name,table in [('comparison',result),('parent_comparisons',pd.DataFrame(parent_rows)),('ranking',pd.DataFrame(ranking)),('research_gates',pd.DataFrame(passed)),('gate_flags',pd.concat(flags)),('thresholds',thresholds)]:
        table.to_csv(output/f'{name}.csv.gz',index=False,compression={'method':'gzip','mtime':0})
    bounds=bounds.merge(b[['event_key','entry_time','exit_time']],on='event_key',validate='one_to_one');rows=[]
    for period in ['full','earlier','later','crossing_or_unresolved']:
        g=bounds.loc[period_mask(bounds,period)]
        for label,n in g.path_group.value_counts().items():rows.append(dict(period=period,path_group=label,n=int(n)))
    pd.DataFrame(rows).to_csv(output/'path_groups.csv',index=False)
    summary=dict(source_manifest_sha256=search.digest(source/'manifest.json'),source_dataset_receipt_sha256=expected_receipt_sha,source_dataset_candidates_sha256=_receipt_file_sha(json.loads((DATASET/'receipt.json').read_text()),'candidates.csv.gz'),source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        dependencies={str(d):search.digest(d) for d in deps},source_candidates=49207,baseline_closed=len(closed(b)),baseline_gt5=int(closed(b).net_r.gt(5).sum()),
        passed_arms=[r['arm'] for r in passed if r['passed']],files={x.name:search.digest(x) for x in output.iterdir() if x.is_file()},training_eligible=False,production_eligible=False,
        scope='Independent candidate paths, not a serial portfolio or prospective unseen test. Passed arms require further serial verification.')
    search.dump(output/'summary.json',summary)
    print(result.loc[result.period.eq('later'),['arm','closed','gt5','precision','mean_net_bp','recall','paired_excess_net_bp','random_net_holm_p']].to_string(index=False))
    print('Passed arms:',summary['passed_arms'])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--source',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();run(args.source,args.output)
