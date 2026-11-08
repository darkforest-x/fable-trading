"""Test intermediate resting order lifetimes between one 12H key and no expiry."""
import gc
import json
from contextlib import nullcontext

import pandas as pd

from yoyo.evaluation import cloud_close_target_expiry as study

EXP=study.EXP.parent/'exp-cloud-close-target-lifetime-20261109-v1'


def main():
    if (EXP/'summary.json').is_file():
        raise FileExistsError('Target lifetime study already complete')
    extra=set(json.loads((study.archive.EXP/'summary.json').read_text())['archived_additions'])
    reference=pd.read_csv(study.EXP/'account_metrics.csv')
    records=reference[reference.scenario.eq('actual')].to_dict('records')
    summary={};pick=None
    for venue in ['OKX','Binance']:
        paths=study.trend.thirds.okx_paths() if venue=='OKX' else study.archive.pool(extra)
        if venue=='OKX':
            study.trend.repair.check_parent_sets(paths)
        summary[venue]={}
        with (study.archive.market_quotes(extra) if venue=='Binance' else nullcontext()):
            for hours in [24,48,96,192]:
                changed,_=study.time_in_force(paths,hours=hours)
                real=changed[~changed.is_control]
                metrics,pos,eq=study.trend.market.fund(venue,real,0.)
                records.extend(dict(policy=f'{hours}h',venue=venue,scenario='actual',period=p,**m) for p,m in metrics.items())
                summary[venue][hours]=dict(dev=metrics['dev'],full=metrics['all'],dev_pass=study.trend.second.meets(metrics['dev']),full_pass=study.trend.second.meets(metrics['all']))
                if summary[venue][hours]['dev_pass'] or hours==pick:
                    pos.to_csv(EXP/f'funded_{venue}_{hours}h.csv.gz')
                    eq.to_csv(EXP/f'hourly_{venue}_{hours}h.csv.gz',index_label='hour_end_utc')
                del changed,real,pos,eq
                gc.collect()
            if venue=='OKX':
                eligible=[h for h,v in summary[venue].items() if v['dev_pass']]
                if eligible:
                    pick=max(eligible,key=lambda h:summary[venue][h]['dev']['final_usdt'])
            if pick is not None:
                selected,_=study.time_in_force(paths,hours=pick)
                real=selected[~selected.is_control]
                control=selected[selected.is_control & selected.parent_id.isin(real.parent_id)]
                for tag,pool,slip in [('paired',real[real.parent_id.isin(control.parent_id)],0.),('control',control,0.),('stress',real,.0005)]:
                    checked=study.trend.market.fund(venue,pool,slip)[0]
                    records.extend(dict(policy=f'{pick}h',venue=venue,scenario=tag,period=p,**m) for p,m in checked.items())
    summary['dev_selected_hours']=pick
    pd.DataFrame(records).to_csv(EXP/'account_metrics.csv',index=False)
    (EXP/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print('selected hours',pick,flush=True)


if __name__=='__main__':
    main()
