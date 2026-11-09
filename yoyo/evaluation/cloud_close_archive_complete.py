"""Reprice the fixed 661-contract accounts after checking the additional archival crypto contracts."""
import gc
import json
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_archive_cohort as archive
from yoyo.evaluation import cloud_close_tenth_sizing as tenth

EXP = archive.EXP.parent/'exp-cloud-close-archive-complete-20261109-v1'


def add_contract(item):
    ticker, metadata = item
    months,local = archive.months_for(ticker)
    first = pd.Timestamp(min(months)+'-01',tz='UTC')
    end = pd.Timestamp(archive.venue.END,unit='ms',tz='UTC')
    late = (end-first) < pd.Timedelta(days=90)
    rec = dict(ticker=ticker,first_published_month=min(months),last_published_month=max(months),
               onboard_ms=metadata['onboardDate'],cannot_observe_90d=late)
    if not late:
        rec.update(archive.price(ticker,onboard_ms=metadata['onboardDate']))
    return rec


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Historical archive-account expansion already exists')
    cfg=json.loads((EXP/'config.json').read_text())
    listing=cfg['supplemental_exchange_records']
    with ThreadPoolExecutor(max_workers=3) as executor:
        receipts=list(executor.map(add_contract,listing.items()))
    (EXP/'source_coverage.json').write_text(json.dumps(receipts,indent=2,ensure_ascii=False,allow_nan=False)+'\n')
    old_extra=set(json.loads((archive.EXP/'summary.json').read_text())['archived_additions'])
    new=set(r['ticker'] for r in receipts if not r['cannot_observe_90d'])
    if old_extra.intersection(listing):
        raise ValueError('Archival cohorts overlap')
    extra=old_extra|new
    paths=archive.pool(extra)
    baseline=pd.read_csv(archive.EXP/'account_metrics.csv')
    base_tenth=pd.read_csv(tenth.EXP/'account_metrics.csv')
    base_tenth=base_tenth[base_tenth.venue.eq('Binance')].assign(policy='tenth')
    previous=pd.concat([baseline,base_tenth],ignore_index=True)
    metrics_out=[];summary={}
    with archive.market_quotes(extra):
        _,trend_paths=tenth.trend.inputs('Binance',paths)
        for policy,selection,exposure in [('normal_partial',paths,1.),('trend_efficiency',trend_paths,1.),('tenth',trend_paths,.9)]:
            real=selection[~selection.is_control]
            control=selection[selection.is_control & selection.parent_id.isin(real.parent_id)]
            arms=[('actual',real,0.)]
            if policy=='tenth':
                arms.extend([('stress',real,.0005),('paired',real[real.parent_id.isin(control.parent_id)],0.),('control',control,0.)])
            for name,pool,slip in arms:
                data=archive.native.prepare(pool)
                m,pos,eq=tenth.split.simulate(data,2/3,slip,exposure=exposure)
                if not np.isclose(1000+pos.cash_pnl.sum(),eq.nav.iloc[-1],rtol=1e-8,atol=1e-4):
                    raise ValueError('Portfolio failed funded-cash reconciliation')
                metrics_out.extend(dict(policy=policy,scenario=name,period=p,**v) for p,v in m.items())
                if name=='actual':
                    pre=previous[previous.period.eq('dev') & previous.policy.eq(policy) & previous.scenario.eq('actual')].iloc[0]
                    if not np.isclose(m['dev']['final_usdt'],pre.final_usdt,rtol=1e-9,atol=1e-4) or m['dev']['trades']!=pre.trades:
                        raise ValueError('Adding 2026 regular contracts changed historical development account')
                    summary[policy]=dict(full=m['all'],dev=m['dev'],dev_pass=tenth.trend.second.meets(m['dev']),
                                         full_pass=tenth.trend.second.meets(m['all']),all_real=len(real)//2,
                                         newly_eligible=int((real[real.tp_r.eq(0)].symbol+'USDT').isin(new).sum()),
                                         newly_funded=int((pos.symbol+'USDT').isin(new).sum()))
                    pos.to_csv(EXP/f'funded_{policy}.csv.gz')
                    eq.to_csv(EXP/f'hourly_{policy}.csv.gz',index_label='hour_end_utc')
                del data,pos,eq
                gc.collect()
    summary['universe_size']=632+len(old_extra)+len(listing)
    summary['new_archive_cohorts']=dict(priced=sorted(new),too_recent=sorted(set(listing)-new))
    pd.DataFrame(metrics_out).to_csv(EXP/'account_metrics.csv',index=False)
    (EXP/'summary.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False,allow_nan=False)+'\n')
    print(json.dumps(summary,indent=2,ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
