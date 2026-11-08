"""Evaluate the age of the published Monday range at independently funded 12H entries."""
import gc
import json

import pandas as pd

from yoyo.evaluation import cloud_close_binance_gap_repair as binance
from yoyo.evaluation import cloud_close_eligibility_repair as repair
from yoyo.evaluation import cloud_close_second_reclaim as second
from yoyo.evaluation import cloud_close_thirds as thirds
from yoyo.evaluation import cloud_close_volatility_expansion as market

EXP = thirds.wallet.ROOT/'experiments/active/exp-cloud-close-week-age-20261109-v1'


def elapsed_days(clocks):
    """Calendar distance to Tuesday 00 NY based on the confirmed key's own localized UTC close."""
    local = pd.to_datetime(clocks,unit='ms',utc=True).dt.tz_convert('America/New_York')
    return (local.dt.dayofweek-1)%7


def cohorts(paths):
    rows = paths.loc[paths.tp_r.eq(0),['parent_id','event_id','is_control','entry_ms']].copy()
    rows['calendar_days'] = elapsed_days(rows.entry_ms)
    local = pd.to_datetime(rows.entry_ms,unit='ms',utc=True).dt.tz_convert('America/New_York')
    if (rows.calendar_days.eq(6) & (local.dt.hour.ne(0) | local.dt.minute.ne(0))).any():
        raise ValueError('Trade/paired-control close after expiry of its NY Monday range')
    for policy,selected in [('early',rows.calendar_days.lt(3)),('late',rows.calendar_days.ge(3))]:
        sample = paths[paths.event_id.isin(rows.loc[selected,'event_id'])]
        real = sample[~sample.is_control]
        control = sample[sample.is_control & sample.parent_id.isin(real.parent_id)]
        yield policy,real,control


def main():
    if (EXP/'summary.json').is_file():
        raise FileExistsError('Frozen Monday-age rule already evaluated')
    baseline = pd.read_csv(repair.EXP/'thirds'/'account_metrics.csv')
    records = baseline[baseline.scenario.eq('actual')].assign(policy='unfiltered').to_dict('records')
    summary = {}
    pick = None
    for venue in ['OKX','Binance']:
        if venue=='OKX':
            paths = thirds.okx_paths()
            repair.check_parent_sets(paths)
        else:
            paths = binance.assembled(set(binance.patch.affected_tickers()))
        summary[venue] = {}
        for policy,real,control in cohorts(paths):
            metrics,positions,equity = market.fund(venue,real,0.)
            positions.to_csv(EXP/f'funded_{venue}_{policy}.csv.gz')
            equity.to_csv(EXP/f'hourly_{venue}_{policy}.csv.gz',index_label='hour_end_utc')
            records.extend(dict(policy=policy,venue=venue,scenario='actual',period=p,**m) for p,m in metrics.items())
            summary[venue][policy] = dict(actual=len(real)//2,matched=len(control)//2,dev=metrics['dev'],full=metrics['all'],
                                            dev_pass=second.meets(metrics['dev']),full_pass=second.meets(metrics['all']))
            del positions,equity
            gc.collect()
        if venue=='OKX':
            eligible = [p for p,r in summary[venue].items() if r['dev_pass']]
            pick = max(eligible,key=lambda p:summary[venue][p]['dev']['final_usdt']) if eligible else None
        if pick is not None:
            for policy,real,control in cohorts(paths):
                if policy!=pick:
                    continue
                for tag,pool,slip in [('paired',real[real.parent_id.isin(control.parent_id)],0.),('control',control,0.),('stress',real,.0005)]:
                    m = market.fund(venue,pool,slip)[0]
                    records.extend(dict(policy=policy,venue=venue,scenario=tag,period=p,**v) for p,v in m.items())
        del paths
        gc.collect()
    summary['pre2025_selected_half'] = pick
    pd.DataFrame(records).to_csv(EXP/'account_metrics.csv',index=False)
    (EXP/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(json.dumps(summary,indent=2,allow_nan=False))


if __name__=='__main__':
    main()
