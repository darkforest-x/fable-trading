"""Share one fixed capital pool between already priced standard and shifted 12H cloud positions."""
import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_session_clock as session

EXP=session.EXP.parent/'exp-cloud-close-dual-session-20261109-v1'


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed two-session cloud account')
    cases=['utc_00_12','utc_06_18']
    cohort=pd.concat([pd.read_csv(session.EXP/f'paths_{key}.csv.gz').assign(signal_clock=key)
                      for key in cases],ignore_index=True)
    if not cohort.event_id.is_unique or not cohort.groupby('entry_ms').signal_clock.nunique().eq(1).all():
        raise ValueError('Frozen clock families unexpectedly overlap on the same clock or ID')
    wallet=session.spread.wallet
    data=wallet.prepare(cohort,3)
    if data['gap_marks']:
        raise ValueError('Missing occupied two-session H1')
    results={}
    for name,slip in [('fee',0.),('stress',.0005)]:
        funds,curve=wallet.account(data,slip=slip)
        if not np.isclose(1000+funds.cash_pnl.sum(),curve.nav.iat[-1],atol=1e-5):
            raise ValueError('Combined cash fails terminal reconciliation')
        funds.to_csv(EXP/f'funded_{name}.csv.gz',index=False)
        curve.to_csv(EXP/f'hourly_{name}.csv.gz',index_label='hour_end_utc')
        counts=funds.event_id.map(cohort.set_index('event_id').signal_clock).value_counts()
        results[name]=dict(metrics=session.stats.summarize(funds,curve),by_clock=counts.to_dict(),skips=data['skips'])
        print(name,results[name]['metrics']['all'],flush=True)
    session.branch._write_json(EXP/'summary.json',results)


if __name__=='__main__':
    main()
