"""Reconcile actual NAV-tenth fills, fee mark envelopes, and the single-ADA sensitivity."""
import json
from contextlib import nullcontext

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_efficiency_binance_bounds as risk
from yoyo.evaluation import cloud_close_tenth_sizing as study


def main():
    target=study.EXP/'risk_and_ada.json'
    if target.exists():
        raise FileExistsError('NAV-tenth risk check already exists')
    extra=set(json.loads((study.archive.EXP/'summary.json').read_text())['archived_additions'])
    results={}
    for venue in ['OKX','Binance']:
        positions=pd.read_csv(study.EXP/f'funded_{venue}.csv.gz').set_index('event_id')
        eq=pd.read_csv(study.EXP/f'hourly_{venue}.csv.gz',index_col='hour_end_utc',parse_dates=True)
        paths=study.trend.thirds.okx_paths() if venue=='OKX' else study.archive.pool(extra)
        paths=paths[~paths.is_control]
        with (study.archive.market_quotes(extra) if venue=='Binance' else nullcontext()):
            choices,selected=study.trend.inputs(venue,paths)
            legs=selected[selected.event_id.isin(positions.index)].copy()
            legs['signed_size']=legs.event_id.map(positions.notional)*legs.tp_r.map(risk.LEG_FRACTIONS)*legs.side/legs.entry_price
            sizes=legs.signed_size.abs()
            pnl=legs.signed_size*(legs.exit_price-legs.entry_price)-sizes*(legs.entry_price*risk.wallet.TAKER+legs.exit_price*legs.exit_fee_rate)
            sums=pnl.groupby(legs.event_id).sum().reindex(positions.index)
            if not np.allclose(sums,positions.cash_pnl,rtol=1e-8,atol=1e-5):
                raise ValueError('Actual quantities cannot reproduce frozen cash PnL')
            conservative,_=risk._fee_adjusted_envelope(eq,legs)
            observed=risk._observed_drawdown_lower(eq); cap=risk._max_drawdown_upper(conservative)
            devcap=risk._max_drawdown_upper(conservative[conservative.index<=risk.SPLIT])
            event=choices.symbol.eq('ADA') & choices.entry_ms.eq(1606392000000) & choices.choice.eq('full_runner')
            if event.sum()!=1 or choices.loc[event,'event_id'].iloc[0] not in positions.index:
                raise ValueError('Specified historic ADA trade not funded as a full runner')
            choices.loc[event,'choice']='partial_2_3'
            counter=study.trend.rotation._rotated_paths(paths,choices)
            if venue=='OKX':
                data=study.split.pair_data(counter[counter.tp_r.eq(0)],counter[counter.tp_r.eq(2)],9)
            else:
                data=study.archive.native.prepare(counter)
            metrics,revised,equity=study.split.simulate(data,2/3,exposure=.9)
            if set(revised.index)!=set(positions.index) or not np.isclose(1000+revised.cash_pnl.sum(),equity.nav.iloc[-1],rtol=1e-8):
                raise ValueError('Changed single exit displaced signals or broke settlement')
            results[venue]=dict(terminal_usdt=float(eq.nav.iloc[-1]),fee_adjusted_dd_interval_pct=[observed,cap],
                                dev_fee_adjusted_dd_upper_pct=devcap,ada_restored_at_original_partial_usdt=metrics['all']['final_usdt'],
                                max_parent_pnl_reconciliation_difference_usdt=float((positions.cash_pnl-sums).abs().max()))
    target.write_text(json.dumps(results,indent=2,allow_nan=False)+'\n')
    print(json.dumps(results,indent=2,allow_nan=False))


if __name__=='__main__':
    main()
