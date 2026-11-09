"""Apply the confirmed native WAVES H1 repair to the identical 360-name account."""
import gc
import json
from functools import lru_cache

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_double_directory as old
from yoyo.evaluation import cloud_close_weak_rally_branch as market

archive = old.archive
wallet = old.wallet
EXP = old.EXP.parent / 'exp-cloud-close-waves-wallet-20261109-v1'
WAVES = old.EXP.parent / 'exp-cloud-close-okx-waves-trades-20261109-v1' / 'bars1h.csv.gz'


def assemble_paths():
    orig, returns = archive.frozen_data()
    sources = market.sources()
    current = set(returns)
    old_rows = pd.read_csv(old.BASE / 'retired_paths.csv.gz', dtype={'symbol':str}, float_precision='round_trip')
    for symbol in sorted(set(sources)-current):
        _,bars = market.frames(sources[symbol])
        returns[symbol] = archive.leadership.trailing_return(bars.reset_index())
    prior = archive.screen(pd.concat([orig,old_rows], ignore_index=True), returns)
    previous = json.loads((old.BASE / 'summary.json').read_text())
    if len(prior) != 2*previous['eligible_after']:
        raise ValueError('Frozen pre-repair eligible universe mismatch')
    old_clock_count = returns['WAVES'].count()
    ranks, rows, meta = archive.retired_symbol('WAVES',sources['WAVES'][2],hour_file=WAVES)
    returns['WAVES'] = ranks
    replacement = pd.DataFrame(rows)
    replacement.to_csv(EXP / 'waves_paths.csv.gz',index=False)
    updated = archive.screen(pd.concat([orig,old_rows[old_rows.symbol.ne('WAVES')],replacement],
                                      ignore_index=True),returns)
    results = dict(previous_eligible=previous['eligible_after'],new_eligible=len(updated)//2,
                   waves_source=meta,former_waves_90d_clocks=int(old_clock_count),
                   added_opportunities=sorted(set(updated.event_id)-set(prior.event_id)),
                   removed_opportunities=sorted(set(prior.event_id)-set(updated.event_id)))
    return updated,sources,results


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed WAVES wallet measurement')
    expanded,sources,result = assemble_paths()
    rows,_ = archive.risk.hourly.tested.expiry.time_in_force(expanded)
    roster,_ = wallet.admit(rows[rows.tp_r.eq(0)],9)
    held = set(roster.event_id)
    old_tape,_,delivery = old.inputs()
    old_ids = set(old_tape.event_id)
    news = held-old_ids
    gone = old_ids-held
    result.update(funded=len(held),added_funded=sorted(news),displaced_funded=sorted(gone))
    if held==old_ids and not roster.symbol.eq('WAVES').any():
        result.update(account_changed=False,reference=old.EXP.name,minute_risk_reference='exp-cloud-close-double-minute-risk-20261109-v1')
    else:
        tape = rows[rows.event_id.isin(held)].copy()
        old_index = old_tape[old_tape.exit_reason.eq('delisting_settlement')].set_index(['event_id','tp_r'])
        for idx,r in tape[tape.exit_reason.eq('series_tail_close') & tape.symbol.isin(set(sources)-set(pd.read_csv(wallet.SOURCE/'input_coverage.csv').symbol))].iterrows():
            key = (r.event_id,r.tp_r)
            if key not in old_index.index:
                raise ValueError(f'Unresolved new administrative tail {key}')
            s = old_index.loc[key]
            tape.loc[idx,['exit_price','exit_fee_rate','exit_reason']] = [s.exit_price,s.exit_fee_rate,s.exit_reason]

        @lru_cache(maxsize=None)
        def quotes(symbol):
            f = WAVES if symbol=='WAVES' else sources[symbol][0]
            h = pd.read_csv(f,float_precision='round_trip')
            return h.time.to_numpy(),h[archive.FIELDS].to_numpy()

        limits = old.changed_limits(tape,quotes)
        data,changed = old.parent.assemble(tape,limits,'OKX',quotes)
        underlying = old.exposure.underlying(data,quotes)
        cases = {}
        for name,slip in [('fixed',0.),('fixed_5bp',.0005)]:
            cases[name] = old.exposure.simulate(data,underlying,1.5,slip,directory=EXP,quote_loader=quotes)
            gc.collect()
        pd.DataFrame(changed).to_csv(EXP/'doubling_fills.csv',index=False)
        result.update(account_changed=True,cases=cases,known_gap_events=sorted(
            tape.loc[tape.gap_flag | tape.exit_reason.isin(archive.GAP_REASONS),'event_id'].unique()))
    archive.prices.safe_json(EXP/'summary.json',result)
    print(json.dumps({k:v for k,v in result.items() if k not in ('cases','added_opportunities')},indent=2))


if __name__ == '__main__':
    main()
