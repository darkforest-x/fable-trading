"""Monthly high-10R-rule selection using only completed prior-six-month labels.

Single change versus fixed V3 discovery: update the same finite rule family's
choice at each UTC month start. Label availability is conservatively the exit
bar's close; unknown paths never enter selection. Features, previous-three-month
unlabelled thresholds, risk, exits and costs remain the receipt-bound V3 inputs.
This post-failure exploratory hypothesis is not blind confirmation or ML fit.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_10r_search as s
from yoyo.evaluation.spike_v8_six_filters import _committed

EXP=Path('experiments/active/exp-spike-10r-adaptive-20260921-v31')
NAME='adaptive_6m'


def monthly_history(c, month_start):
    """Labels may be consumed only after the full exit bar has completed."""
    label_at=c.exit_time+pd.to_timedelta(c.timeframe_min,unit='min')
    start=month_start-pd.DateOffset(months=6)
    return (c.valid_entry & ~c.censored & c.available_at.ge(start) & c.available_at.lt(month_start)
            & label_at.le(month_start))


def rank_history(history,matrix,cfg):
    """Apply V3's identical objective to rows already proven label-mature."""
    label=history.net_r.to_numpy(float)>10
    asset=history.asset.astype(str).to_numpy()
    month=history.entry_time.dt.strftime('%Y-%m').to_numpy()
    asset_month=np.char.add(np.char.add(asset.astype(str),'|'),month.astype(str))
    best=None
    order=None
    for ids,rid in s.rule_specs():
        take=s.mask_for(matrix,ids)
        n,h=int(take.sum()),int((take & label).sum())
        recall=h/label.sum() if label.sum() else 0.
        coverage=n/len(history) if len(history) else 0.
        if (n<cfg['selection_min_closed'] or h<cfg['selection_min_gt10']
                or recall<cfg['selection_min_gt10_recall'] or coverage<.01):
            continue
        assets=len(np.unique(asset[take]))
        months=len(np.unique(month[take]))
        positive_months=len(np.unique(asset_month[take & label]))
        if (assets<cfg['selection_min_assets'] or months<cfg['selection_min_active_months']
                or positive_months<cfg['selection_min_positive_asset_months']):
            continue
        precision=h/n
        lower=s.wilson_lower(h,n)
        key=(-lower,-precision,-h,rid)
        if order is None or key<order:
            order=key
            best=dict(rule=rid,terms=list(ids),closed=n,gt10=h,precision=precision,recall=recall,
                      coverage=coverage,wilson_lower=lower,assets=assets,active_months=months,
                      positive_asset_months=positive_months)
    return best


def choose_month(c,matrix,month_start,cfg):
    """Choose a rule using the actual mature history clocks, never future rows."""
    start=month_start-pd.DateOffset(months=6)
    known=start>=s.START
    mask=monthly_history(c,month_start).to_numpy()
    out=dict(month=month_start.strftime('%Y-%m'),history_start=start.isoformat(),history_end=month_start.isoformat(),
             history_closed=int(mask.sum()),known=False,rule=None,terms=None)
    if not known or not mask.any():
        return out
    choice=rank_history(c.loc[mask],matrix[mask],cfg)
    label_at=c.loc[mask].exit_time+pd.to_timedelta(c.loc[mask].timeframe_min,unit='min')
    out.update(history_gt10=int(c.loc[mask].net_r.gt(10).sum()),max_label_available_at=label_at.max().isoformat())
    if choice:
        out.update(known=True,rule=choice['rule'],terms=choice['terms'],selection=choice)
    return out


def build(c,matrix,cfg):
    """Freeze one choice per month, then emit booleans without target labels."""
    months=c.available_at.dt.strftime('%Y-%m')
    admitted=np.zeros(len(c),bool)
    decisions=[]
    for month in sorted(months.unique()):
        start=pd.Timestamp(month+'-01',tz='UTC')
        selection=choose_month(c,matrix,start,cfg)
        decisions.append(selection)
        if selection['known']:
            admitted[months.eq(month)]=s.mask_for(matrix,tuple(selection['terms']))[months.eq(month)]
    return admitted,decisions


def verify_parent(parent_analysis):
    """Bind both parent choices and comparisons before forming the Holm family."""
    receipt=json.loads((parent_analysis/'evaluation_receipt.json').read_text())
    for required in ('comparison.csv','selection.json'):
        if required not in receipt['files']:
            raise ValueError('parent required artifact missing')
    for name,sha in receipt['files'].items():
        if s.digest(parent_analysis/name)!=sha:
            raise ValueError('parent evidence drift')
    return receipt


def run(dataset,parent_analysis,output):
    deps=[Path(__file__),Path('tests/evaluation/test_spike_10r_adaptive.py'),EXP/'PROJECT_PLAN.md',EXP/'config.json',
          Path(s.__file__),s.EXP/'config.json']
    if not _committed(deps):
        raise ValueError('commit adaptive builder/plan before analysis')
    if output.exists():
        raise ValueError('refuse to overwrite adaptive results')
    adaptive_cfg=json.loads((EXP/'config.json').read_text())
    if adaptive_cfg['history_months']!=6 or adaptive_cfg['coverage_floor']!=.01:
        raise ValueError('adaptive config differs from fixed implementation')
    c,controls,_=s.load_candidates(dataset)
    cfg=json.loads((s.EXP/'config.json').read_text())
    s.validate_config(cfg)
    verify_parent(parent_analysis)
    matrix,ranks,_=s.calibrate(c)
    admitted,months=build(c,matrix,cfg)
    output.mkdir(parents=True)
    s.dump(output/'monthly_selection.json',s.clean(months))
    c[['event_key']].assign(admitted=admitted).to_csv(output/'decisions.csv.gz',index=False,compression={'method':'gzip','mtime':0})
    rows=[]
    for period,clock in s.periods(c):
        u=c.loc[clock]
        part=c.loc[clock & admitted]
        rows.append(dict(rule=NAME,period=period,**s.metrics(part,u),**s.control_statistics(part,controls,period),**s.rate_interval(u,part)))
    table=pd.concat([pd.read_csv(parent_analysis/'comparison.csv'),pd.DataFrame(rows)],ignore_index=True)
    # Include all five parent-selected arms plus the new hypothesis in each
    # family; exposed-history hypothesis selection remains an explicit caveat.
    parent_selection=json.loads((parent_analysis/'selection.json').read_text())
    names={x['choice']['rule'] for x in parent_selection['selected'] if x['choice']}|{NAME}
    mask=table.period.eq('later') & table.rule.isin(names)
    for what in ('tail','net'):
        table.loc[mask,'random_'+what+'_holm_p']=s.holm(table.loc[mask,'random_'+what+'_p'])
    table.loc[mask,'historical_gate_passed']=(table.loc[mask,'precision_ci_low'].gt(0) &
        table.loc[mask,'random_tail_holm_p'].lt(.01) & table.loc[mask,'random_net_holm_p'].lt(.01) &
        table.loc[mask,'mean_net_bp'].gt(0) & table.loc[mask,'recall'].ge(.1))
    table.to_csv(output/'comparison.csv',index=False)
    s.dump(output/'receipt.json',dict(name=NAME,dataset_receipt_sha256=s.digest(dataset/'receipt.json'),
        parent_evaluation_receipt_sha256=s.digest(parent_analysis/'evaluation_receipt.json'),
        dependencies={str(p):s.digest(p) for p in deps},
        generated_at=pd.Timestamp.now(tz='UTC').isoformat(),source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        files={p.name:s.digest(p) for p in output.iterdir() if p.is_file()}))
    print(table.loc[table.rule.eq(NAME)].to_string(index=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dataset',type=Path,required=True)
    p.add_argument('--parent-analysis',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    run(a.dataset,a.parent_analysis,a.output)
