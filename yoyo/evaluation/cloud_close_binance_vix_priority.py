"""Transfer the frozen US volatility queue preference to the historical Binance candidate universe."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_spot_reclaim as wallet
from yoyo.evaluation import cloud_close_spot_repaired_wallet as repaired
from yoyo.evaluation import cloud_close_vix_regime as regime

EXP = wallet.EXP.parent/'exp-cloud-close-binance-vix-priority-20261109-v1'


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed independent venue VIX ranking')
    rank=wallet.rank
    admin=wallet.admin
    extra=set(json.loads((admin.archive.EXP/'summary.json').read_text())['archived_additions'])
    extra.update(json.loads((admin.study.EXP/'summary.json').read_text())['new_archive_cohorts']['priced'])
    spec,marks=rank.market_spec(EXP)
    cfg=json.loads((repaired.EXP/'config.json').read_text())
    sources=repaired._validate_settlement_sources(cfg)+[EXP.parent/'exp-cloud-close-perp-basis-20261109-v1']
    for directory in sources:
        spec.update(json.loads((directory/'config.json').read_text())['additional_settlements'])
        marks.update(pd.read_csv(directory/'settlement_index_1m.csv').groupby('symbol')[['low','close','high']].mean().to_dict('index'))
    base=pd.read_csv(rank.scores.EXP/'candidate_order.csv.gz',index_col='event_id',dtype={'symbol':str})
    vix=regime.daily_regime(regime.CACHE)
    paths=admin.archive.pool(extra)
    with admin.archive.market_quotes(extra):
        _,selected=admin.study.tenth.trend.inputs('Binance',paths[~paths.is_control])
        rows=admin.adjusted_legs(selected,extra,spec)
        candidates=rows.loc[rows.tp_r.eq(0),['event_id','symbol','entry_ms','side']].set_index('event_id')
        if set(candidates.index)!=set(base.index) or not candidates.entry_ms.equals(base.entry_ms.reindex(candidates.index)):
            raise ValueError('Native Binance opportunity keys changed')
        source=candidates.join(base.new_rank).sort_values('entry_ms').reset_index()
        source=pd.merge_asof(source,vix[['available_ms','direction','stale_direction']],left_on='entry_ms',
                             right_on='available_ms',direction='backward',tolerance=4*regime.DAY).set_index('event_id')
        if source[['direction','stale_direction','new_rank']].isna().any().any():
            raise ValueError('Original candidate coverage differs from predeclared VIX sources')
        source.to_csv(EXP/'signal_regimes.csv.gz')
        baseline=rows.assign(entry_order=rows.event_id.map(source.new_rank))
        reference=admin.admitted(baseline)
        prev=pd.read_csv(wallet.hourly.EXP/'funded_Binance.csv.gz',index_col='event_id')
        if set(reference.event_id)!=set(prev.index):
            raise ValueError('Published native venue control failed to reconcile')
        results={}
        for label,key in [('aligned','direction'),('lagged','stale_direction')]:
            width=source.groupby('entry_ms').new_rank.transform('max')+1
            order=source.new_rank+width*(source.side*source[key]>0).astype(int)
            taken=admin.admitted(rows.assign(entry_order=rows.event_id.map(order)))
            forced=taken[taken.delivery]
            forced[['event_id','symbol','tp_r','exit_bar_ms']].to_csv(EXP/f'delivery_{label}.csv',index=False)
            absent=set(forced.symbol+'USDT')-marks.keys()
            if absent or taken.delivery_ambiguous.eq(True).any():
                raise ValueError(f'Unresolved delivery source for {label}: {sorted(absent)}')
            paid=admin.resolve_settlements(taken.copy(),marks,'proxy')
            data=admin.archive.native.prepare(paid)
            metrics,parents,equity,trims=wallet.hourly.tested.trim_account(data,admin.archive.native.quotes,.9,.2,review_hours=1)
            if not np.isclose(1000+parents.cash_pnl.sum(),equity.nav.iloc[-1],rtol=1e-8,atol=.001):
                raise ValueError('Native cash returns fail reconciliation')
            parents.to_csv(EXP/f'funded_{label}.csv.gz')
            equity.to_csv(EXP/f'hourly_{label}.csv.gz',index_label='hour_end_utc')
            results[label]=dict(metrics=metrics,common_funded=len(set(prev.index)&set(parents.index)),
                                selected_deliveries=len(forced),trim_fills=len(trims))
    original=json.loads((wallet.hourly.EXP/'summary.json').read_text())['Binance']
    (EXP/'summary.json').write_text(json.dumps(dict(eligible=len(source),baseline=original,arms=results),indent=2,allow_nan=False)+'\n')


if __name__=='__main__':
    main()
