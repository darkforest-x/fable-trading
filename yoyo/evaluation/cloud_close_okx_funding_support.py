"""Measure which actual OKX cloud holding intervals have usable public rate support."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_okx_funding_archives as archive

EXP = archive.EXP
MAX_GAP = 8 * archive.HOUR + 120_000


def main():
    if (EXP/'held_rate_support.json').exists():
        raise FileExistsError('Frozen actual holding-window coverage exists')
    keys = pd.read_csv(EXP/'held_months.csv.gz')
    months = pd.read_csv(EXP/'archive_receipts.csv.gz')
    recent = json.loads((EXP/'recent_rate_coverage.json').read_text())
    supported = set(zip(months.loc[months.status.str.startswith('observed'),'instrument'],
                        months.loc[months.status.str.startswith('observed'),'month']))
    supported.update((r['instrument'],r['month']) for r in recent['audit']
                     if r['api_range_traversed'] and r['rows'] and not r['missing_rate_rows']
                     and r['maximum_gap_including_month_edges_ms']<=MAX_GAP)
    keys['available'] = [(r.instrument,r.month) in supported for r in keys.itertuples()]
    covered = keys.groupby(['event_id','tp_r']).available.all()
    tape = pd.concat([pd.read_csv(EXP/name) for name in ('published_rates.csv.gz','recent_realized_rates.csv.gz')])
    if tape.duplicated(['instrument_name','funding_time']).any():
        raise ValueError('Duplicate settlements across monthly and recent sources')
    panels = {inst:(g.funding_time.to_numpy(dtype=np.int64),g.funding_rate.to_numpy())
              for inst,g in tape.sort_values('funding_time').groupby('instrument_name')}
    diagnostics = []
    for r in archive.funded_legs().itertuples():
        status = 'missing_contract_month'
        if covered.at[(r.event_id,r.tp_r)]:
            end = r.exit_bar_ms + (archive.HOUR if r.exit_phase!='open' else 0) + 60_000
            start = r.entry_ms - 60_000
            ts, rates = panels[r.instrument]
            lo = ts.searchsorted(start,side='right') - 1
            hi = ts.searchsorted(end,side='left')
            if lo<0 or hi>=len(ts):
                status = 'unbracketed'
            elif np.any(np.diff(ts[lo:hi+1])>MAX_GAP):
                status = 'source_gap_over_8h2m'
            elif np.any(~np.isfinite(rates[(ts>=start)&(ts<=end)])):
                status = 'null_rate_in_held_window'
            else:
                status = 'bracketed_native_records'
        diagnostics.append((r.event_id,r.tp_r,r.instrument,status))
    audit = pd.DataFrame(diagnostics,columns=['event_id','tp_r','instrument','rate_support'])
    audit.to_csv(EXP/'leg_rate_support.csv.gz',index=False)
    positions = pd.read_csv(archive.tenth.EXP/'funded_OKX.csv.gz',usecols=['event_id','cash_pnl'])
    groups = audit.groupby('event_id').rate_support
    file_ok = groups.apply(lambda s: s.ne('missing_contract_month').all())
    interior_ok = groups.apply(lambda s: s.eq('bracketed_native_records').all())
    positions['all_month_sources_present'] = positions.event_id.map(file_ok)
    positions['both_legs_bracketed'] = positions.event_id.map(interior_ok)
    missing = positions.loc[~positions.all_month_sources_present]
    result = dict(positions=len(positions), all_month_sources_present=int(file_ok.sum()),
                  any_month_source_absent=int((~file_ok).sum()),both_legs_bracketed=int(interior_ok.sum()),
                  native_child_rate_support=audit.rate_support.value_counts().to_dict(),
                  before_funding_cash_profit_usdt=float(positions.cash_pnl.sum()),
                  before_funding_cash_profit_from_parents_with_missing_months_usdt=float(missing.cash_pnl.sum()),
                  missing_group_fraction_of_price_profit=float(missing.cash_pnl.sum()/positions.cash_pnl.sum()),
                  limitations='Bracketing screens for obvious native gaps over 8h2m; shorter missing intervals can evade it. No cash amount is imputed for missing months or rates. No settlement marks or compounded post-funding wallet computed.')
    (EXP/'held_rate_support.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(result,flush=True)


if __name__=='__main__':
    main()
