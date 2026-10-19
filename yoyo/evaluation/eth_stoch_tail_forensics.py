"""Task62 V2: every-signal tail accounting and opposite-exit ablation.

All OHLC after entry is an outcome, never an admission feature. Entry/risk,
cost, splits and controls are frozen V1 inputs. Initial-stop MFE is bounded
on the terminal minute and is NOT realized profit. Fixed-target comparisons
change only opposite-arrow exit within each target; no policy is promoted.
"""
from pathlib import Path
import json
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.eth_stoch_confirm_exits import (
    EXP as PARENT, load_minutes, prepare, trace, Policy, serial, metrics, weekly_test,
)

EXP = Path('experiments/active/exp-eth5m-stoch-tail-forensics-20261019-v2')
LEVELS = (1, 2, 5, 8, 10, 20)


def endpoint(ctx, cfg, segment):
    return int(ctx['minute_index'].searchsorted(pd.Timestamp(cfg[{'development': 'split', 'validation': 'examples_start', 'examples': 'end'}[segment]])))


def first_passage(ctx, start, end, side, entry, stop, levels=LEVELS):
    """Guarantee target before initial stop; retain same-minute ambiguity."""
    risk = side * (entry - stop)
    adverse = ctx['l'] if side == 1 else -ctx['h']
    favorable = ctx['h'] if side == 1 else -ctx['l']
    signed_open = side * ctx['o']
    stop_hits = np.flatnonzero(adverse[start:end] <= side * stop)
    stopped = len(stop_hits) > 0
    terminal = start + int(stop_hits[0]) if stopped else end
    gap = bool(stopped and signed_open[terminal] <= side * stop)
    guaranteed = favorable[start:terminal]
    best = max(side * entry, float(guaranteed.max()) if len(guaranteed) else side * entry)
    if stopped:
        best = max(best, signed_open[terminal])
    upper = max(best, favorable[terminal]) if stopped and not gap else best
    result = dict(initial_stop_i=terminal if stopped else -1, initial_stop_gap=gap,
        initial_stop_censored=not stopped, observed_minutes=terminal-start+int(stopped),
        static_mfe_r_lower=float((best-side*entry)/risk), static_mfe_r_upper=float((upper-side*entry)/risk))
    for level in levels:
        target = side * entry + level * risk
        hits = np.flatnonzero(guaranteed >= target - 1e-8)
        first = start + int(hits[0]) if len(hits) else (terminal if stopped and signed_open[terminal] >= target-1e-8 else -1)
        result[f'first_{level}r_i'] = first
        result[f'hit_{level}r'] = first >= 0
        result[f'ambiguous_{level}r'] = bool(first < 0 and stopped and not gap and favorable[terminal] >= target-1e-8)
        result[f'minutes_to_{level}r'] = first-start if first >= 0 else None
        if first >= 0:
            # Prior bars are fully observed; target minute open is before touch.
            worst = min(side*entry, float(adverse[start:first].min()) if first>start else side*entry, signed_open[first])
            result[f'mae_before_{level}r_lower'] = float((side*entry-worst)/risk)
        else:
            result[f'mae_before_{level}r_lower'] = None
    return result


def held_mfe(ctx, r):
    """Original trade excursion, excluding prices after an opening exit."""
    favorable = ctx['h'] if r.side == 1 else -ctx['l']
    before = favorable[r.entry_i:r.exit_i]
    guaranteed = max(r.side*r.entry, r.side*r.exit_price, r.side*ctx['o'][r.exit_i],
                     float(before.max()) if len(before) else r.side*r.entry)
    if r.reason in ('target', 'target_gap') or r.exit_at_open:
        upper = guaranteed
    else:
        upper = max(guaranteed, favorable[r.exit_i])
    if r.reason == 'boundary':
        guaranteed = upper
    return (guaranteed-r.side*r.entry)/r.risk, (upper-r.side*r.entry)/r.risk


def bracket_oracle(ctx, r, end, target_r, opposite):
    """Independent vector first-event oracle, separate from scalar trace."""
    start=r.entry_i;side=r.side;entry=r.entry;stop=r.stop;target=round(entry+side*target_r*r.risk,2)
    sh=np.flatnonzero((ctx['l'][start:end] <= stop) if side==1 else (ctx['h'][start:end]>=stop))
    th=np.flatnonzero((ctx['h'][start:end] >= target) if side==1 else (ctx['l'][start:end]<=target))
    candidates=[start+int(h[0]) for h in (sh,th) if len(h)]
    if opposite:
        five=np.flatnonzero(ctx['arrow']==-side)
        positions=(five+1)*5
        hits=positions[(positions>start)&(positions<end)]
        if len(hits):candidates.append(int(hits[0]))
    if not candidates:return end-1,float(ctx['c'][end-1]),'boundary'
    j=min(candidates);op=ctx['o'][j]
    if side*(op-stop)<=0:return j,float(op),'stop_gap'
    if side*(op-target)>=0:return j,float(op),'target_gap'
    if opposite and j>start and j%5==0 and ctx['arrow'][j//5-1]==-side:return j,float(op),'opposite'
    if (ctx['l'][j]<=stop if side==1 else ctx['h'][j]>=stop):return j,float(stop),'stop'
    return j,float(target),'target'


def tail_metrics(rows):
    closed=rows[rows.closed];gross_r=closed.gross_points/closed.risk
    return dict(**metrics(rows),gross_10r=int((gross_r>=10-1e-8).sum()),gross_20r=int((gross_r>=20-1e-8).sum()),
        net_10r=int((closed.net_r>=10-1e-8).sum()),net_20r=int((closed.net_r>=20-1e-8).sum()),
        max_gross_r=float(gross_r.max()) if len(rows) else None,
        max_net_r=float(closed.net_r.max()) if len(rows) else None,
        gross_win_points=float(closed.loc[closed.gross_points>1e-8,'gross_points'].sum()),
        gross_loss_points=float(closed.loc[closed.gross_points<-1e-8,'gross_points'].sum()),
        gross_10r_points=float(closed.loc[gross_r>=10-1e-8,'gross_points'].sum()))


def groups(facts, pop):
    rows=[]
    for column in ('segment','side','risk_bucket','bucket'):
        for group,z in facts.groupby(column):
            rows.append(dict(population=pop,dimension=column,group=str(group),n=len(z),
                hit10=int(z.hit_10r.sum()),hit20=int(z.hit_20r.sum()),
                p10=float(z.hit_10r.mean()),p20=float(z.hit_20r.mean()),
                risk_median=float(z.risk.median()),fee_r_median=float(z.cost_r.median())))
    return rows


def accounting(out):
    """Posthoc path patterns and exact all-trade loss accounting, not filters."""
    out=Path(out);facts=pd.read_csv(out/'all_signals.csv')
    old=pd.read_csv(out/'all_policy_trades.csv.gz');ct=pd.read_csv(PARENT/'results_v1/controls.csv.gz')
    distributions=[];retention=[]
    for name in ('opposite_arrow','be_price_1r','be_price_2r'):
        rows=old[old.policy.eq(name)];random=ct[ct.policy.eq(name)]
        for population,z,control in [('independent',rows,random),('single_position',rows[rows.single_position_taken],serial(random))]:
            for seg in ('all','development','validation','examples'):
                q=z if seg=='all' else z[z.segment.eq(seg)];c=control if seg=='all' else control[control.segment.eq(seg)]
                g=q.gross_r
                masks={'gross_loss':g.lt(-1e-8),'gross_zero':g.abs().le(1e-8),'positive_under_3r':g.gt(1e-8)&g.lt(3),
                    '3_to_10r':g.ge(3)&g.lt(10-1e-8),'10_to_20r':g.ge(10-1e-8)&g.lt(20-1e-8),'at_least_20r':g.ge(20-1e-8)}
                assert sum(int(m.sum()) for m in masks.values())==len(q)
                distributions.append(dict(policy=name,population=population,segment=seg,**tail_metrics(q),
                    buckets={key:dict(n=int(mask.sum()),gross_points=float(q.loc[mask,'gross_points'].sum()),net_points=float(q.loc[mask,'net_points'].sum())) for key,mask in masks.items()},
                    random_closed=int(c.closed.sum()),random_net_points=float(c.loc[c.closed,'net_points'].sum())))
        for level in (10,20):
            z=facts[facts[f'hit_{level}r']];exitcol=name+'__exit_i';reasoncol=name+'__reason';grosscol=name+'__gross_r'
            later=z[f'first_{level}r_i']>z[exitcol]
            # Same-minute opening exits happen before a later high/low unless target was met at open.
            retention.append(dict(policy=name,level=level,static_hit=len(z),realized=int(z[grosscol].ge(level-1e-8).sum()),
                gross_loss=int(z[grosscol].lt(-1e-8).sum()),gross_zero=int(z[grosscol].abs().le(1e-8).sum()),
                exit_before_target_minute=int(later.sum()),exit_same_target_minute=int(z[f'first_{level}r_i'].eq(z[exitcol]).sum()),
                before_target_by_reason=z.loc[later,reasoncol].value_counts().to_dict(),
                serial_omitted=int((~z[name+'__single_position_taken']).sum())))
    patterns={}
    for level in (10,20):
        z=facts[facts[f'hit_{level}r']]
        # Connected same-side temporal windows are a descriptive overlap count, not an independent-event claim.
        clusters=0
        for _,q in z.groupby(['segment','side']):
            right=-1
            for r in q.sort_values('entry_i').itertuples(index=False):
                if r.entry_i>right:clusters+=1
                right=max(right,getattr(r,f'first_{level}r_i'))
        patterns[str(level)]=dict(n=len(z),overlap_components=clusters,
            minutes_quantiles=z[f'minutes_to_{level}r'].quantile([.1,.5,.9]).to_dict(),
            risk_quantiles=z.risk.quantile([.1,.5,.9]).to_dict(),fee_r_quantiles=z.cost_r.quantile([.1,.5,.9]).to_dict(),
            mae_lower_quantiles=z[f'mae_before_{level}r_lower'].quantile([.1,.5,.9]).to_dict(),
            ambiguous_stop_minute=int(facts[f'ambiguous_{level}r'].sum()))
    findings=dict(distributions=distributions,retention=retention,tail_patterns=patterns,
        qualifier='Descriptive reused-history patterns. First-passage duration and MFE are outcomes, not entry-time predictors.')
    (out/'accounting.json').write_text(json.dumps(findings,indent=2)+'\n')
    return findings


def main():
    cfg=json.loads((PARENT/'config.json').read_text());v2=json.loads((EXP/'config.json').read_text())
    out=EXP/'results';out.mkdir(exist_ok=False)
    minute,receipt=load_minutes(cfg);ctx=prepare(minute);quiet=dict(ctx,arrow=np.zeros_like(ctx['arrow']))
    entries=pd.read_csv(PARENT/'results_v1/entries.csv');controls=pd.read_csv(PARENT/'results_v1/control_entries.csv')
    original=pd.read_csv(PARENT/'results_v1/paths.csv.gz');old_serial=pd.read_csv(PARENT/'results_v1/serial_trades.csv.gz')
    passage={};group_rows=[]
    for pop,events in [('signal',entries),('random',controls)]:
        rows=[]
        for r in events.itertuples(index=False):
            rows.append(dict(r._asdict(),cost_r=.002*r.entry/r.risk,
                **first_passage(ctx,r.entry_i,endpoint(ctx,cfg,r.segment),r.side,r.entry,r.stop)))
        facts=pd.DataFrame(rows);facts['risk_bucket']=pd.cut(facts.risk,[0,3,5,8.0000001],labels=['0-3','3-5','5-8']).astype(str)
        passage[pop]=facts;group_rows.extend(groups(facts,pop))
        print('first-passage',pop,len(facts),flush=True)
    facts=passage['signal']
    holding=[];old_summary=[]
    for policy,rows in original.groupby('policy',sort=False):
        admitted=set(old_serial[old_serial.policy.eq(policy)].pair_id)
        for r in rows.itertuples(index=False):
            lower,upper=held_mfe(ctx,r)
            assert lower+1e-7 >= r.gross_points/r.risk
            holding.append(dict(r._asdict(),gross_r=r.gross_points/r.risk,held_mfe_r_lower=lower,held_mfe_r_upper=upper,
                                single_position_taken=r.pair_id in admitted))
        for pop,z in [('independent',rows),('single_position',rows[rows.pair_id.isin(admitted)])]:
            for seg in ('all','development','validation','examples'):
                old_summary.append(dict(policy=policy,population=pop,segment=seg,**tail_metrics(z if seg=='all' else z[z.segment.eq(seg)])))
    holding=pd.DataFrame(holding);holding.to_csv(out/'all_policy_trades.csv.gz',index=False)
    for policy in ('opposite_arrow','be_price_1r','be_price_2r'):
        z=holding[holding.policy.eq(policy)].set_index('pair_id')
        for column in ('gross_r','net_r','exit_i','exit_price','reason','held_mfe_r_lower','held_mfe_r_upper','single_position_taken'):
            facts[policy+'__'+column]=facts.pair_id.map(z[column])
        facts[policy+'__exit_local']=pd.to_datetime(ctx['minute_index'][facts[policy+'__exit_i'].to_numpy(int)]).tz_convert('Asia/Shanghai').astype(str)
    facts['entry_local']=pd.to_datetime(facts.entry_time).dt.tz_convert('Asia/Shanghai').astype(str)
    facts.to_csv(out/'all_signals.csv',index=False)
    passage['random'].to_csv(out/'controls_first_passage.csv',index=False)
    pd.DataFrame(old_summary).to_csv(out/'original_policy_tail_summary.csv',index=False)
    pd.DataFrame(group_rows).to_csv(out/'first_passage_groups.csv',index=False)
    # Counterfactual target arms share entries, targets, stops and costs; only the common early exit changes.
    ledgers={};oracle_checks=0
    for pop,events in [('signal',entries),('random',controls)]:
        records=[]
        for target in v2['target_r']:
            for opposite in (True,False):
                name=f'target_{target}r_'+('opposite' if opposite else 'no_opposite')
                for r in events.itertuples(index=False):
                    end=endpoint(ctx,cfg,r.segment)
                    z=trace(ctx if opposite else quiet,r.entry_i,r.side,r.entry,r.stop,Policy(name,target=target,target_tick=.01),end_i=end)
                    j,price,reason=bracket_oracle(ctx,r,end,target,opposite)
                    assert (j,reason)==(z['exit_i'],z['reason']) and abs(price-z['exit_price'])<1e-8
                    records.append(dict(r._asdict(),policy=name,**z));oracle_checks+=1
                print('bracket',pop,name,len(events),flush=True)
        ledgers[pop]=pd.DataFrame(records);ledgers[pop].to_csv(out/f'{pop}_brackets.csv.gz',index=False)
    summaries=[];pairs=[]
    for name,a in ledgers['signal'].groupby('policy'):
        b=ledgers['random'][ledgers['random'].policy.eq(name)]
        for pop,x,y in [('independent',a,b),('single_position',serial(a),serial(b))]:
            for seg in ('all','development','validation','examples'):
                x1=x if seg=='all' else x[x.segment.eq(seg)];y1=y if seg=='all' else y[y.segment.eq(seg)]
                summaries.append(dict(policy=name,population=pop,segment=seg,**tail_metrics(x1),random_net_points=metrics(y1)['net_points'],random_closed=int(y1.closed.sum())))
        pair=a.merge(b,on='pair_id',suffixes=('_signal','_random'),validate='one_to_one')
        pair=pair[pair.closed_signal & pair.closed_random]
        for seg in ('all','development','validation','examples'):
            q=pair if seg=='all' else pair[pair.segment_signal.eq(seg)]
            excess=q.net_points_signal-q.net_points_random
            sums=excess.groupby(q.week_signal).sum()
            pairs.append(dict(policy=name,segment=seg,pairs=len(q),excess_points=float(excess.sum()),**weekly_test(sums)))
    sums=pd.DataFrame(summaries);sums.to_csv(out/'bracket_summary.csv',index=False)
    inf=pd.DataFrame(pairs)
    for seg,g in inf.groupby('segment'):
        peak=0.
        for i,(idx,r) in enumerate(g.sort_values('p').iterrows()):
            peak=max(peak,min(1.,r.p*(len(g)-i)));inf.loc[idx,'holm_p']=peak
    inf.to_csv(out/'matched_bracket_inference.csv',index=False)
    # All12 drawings, including rejected setups, retain their original denominator.
    candidates=pd.read_csv(PARENT/'results_v1/signal_candidates.csv')
    candidates[candidates.is_signal].to_csv(out/'all_confirmed_candidates.csv',index=False)
    drawings=[]
    for drawing in json.loads((PARENT/'chart_observations.json').read_text())['all_positions_read']:
        stamp=pd.Timestamp(drawing['matching_confirmation_close'][0]['signal_local']).tz_convert('UTC')
        r=candidates[pd.to_datetime(candidates.signal_time).eq(stamp)].iloc[0]
        static=first_passage(ctx,int(r.entry_i),endpoint(ctx,cfg,r.segment),int(r.side),r.entry,r.stop)
        draw_r=drawing['side']*(drawing['target_drawn']-drawing['entry_drawn'])/drawing['drawn_risk']
        initial_draw_r=int(r.side)*(drawing['target_drawn']-r.entry)/(int(r.side)*(r.entry-drawing['stop_drawn']))
        draw_path=first_passage(ctx,int(r.entry_i),endpoint(ctx,cfg,r.segment),int(r.side),r.entry,drawing['stop_drawn'],levels=(initial_draw_r,))
        row=dict(ordinal=drawing['ordinal'],signal_local=str(stamp.tz_convert('Asia/Shanghai')),
            side=int(r.side),entry=float(r.entry),causal_stop=float(r.stop),risk=float(r.risk),eligible=bool(r.eligible),
            drawn_risk=drawing['drawn_risk'],drawn_target=drawing['target_drawn'],drawn_target_r=draw_r,
            drawn_target_with_causal_r=int(r.side)*(drawing['target_drawn']-r.entry)/r.risk,
            drawn_target_before_drawn_stop=draw_path[f'hit_{initial_draw_r}r'],**static)
        for name in ('opposite_arrow','be_price_1r','be_price_2r'):
            z=trace(ctx,int(r.entry_i),int(r.side),r.entry,r.stop,Policy(name,be=1 if name=='be_price_1r' else 2 if name=='be_price_2r' else 0),end_i=endpoint(ctx,cfg,r.segment))
            row[name+'_gross_r']=z['gross_points']/r.risk;row[name+'_reason']=z['reason'];row[name+'_exit_local']=str(ctx['minute_index'][z['exit_i']].tz_convert('Asia/Shanghai'))
            chosen=old_serial[old_serial.policy.eq(name)]
            row[name+'_single_taken']=bool(pd.to_datetime(chosen.entry_time).eq(pd.Timestamp(r.entry_time)).any())
        drawings.append(row)
    pd.DataFrame(drawings).to_csv(out/'drawings.csv',index=False)
    output=dict(experiment_id=v2['experiment_id'],git_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        old_paths=len(original),signal_opportunities=len(entries),random_opportunities=len(controls),bracket_oracle_checks=oracle_checks,
        source_receipt=receipt,original_tail_summary=old_summary,
        passage_totals={pop:{str(k):int(z[f'hit_{k}r'].sum()) for k in LEVELS} for pop,z in passage.items()},
        static_mfe_warning='Maximum future excursion before original stop is not an executable exit or additive profit; terminal-minute lower/upper bounds retained.',
        unchanged_cost=.002,posthoc=True,training_eligible=False,production_eligible=False)
    (out/'receipt.json').write_text(json.dumps(output,indent=2)+'\n')
    accounting(out)
    print(json.dumps({k:v for k,v in output.items() if k in ('old_paths','signal_opportunities','bracket_oracle_checks','passage_totals')}))


if __name__=='__main__':main()
