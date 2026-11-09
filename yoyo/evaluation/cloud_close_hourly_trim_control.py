"""Compare original 12H signals with their seeded matched later entries under hourly weight caps."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_funding_rank as rank
from yoyo.evaluation import cloud_close_hourly_trim as hourly
from yoyo.evaluation import cloud_close_key_vwap as vwap

admin = rank.admin
EXP = rank.EXP.parent/'exp-cloud-close-hourly-trim-control-20261109-v1'


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed matched hourly-cap comparison')
    source = pd.read_csv(rank.scores.EXP/'candidate_order.csv.gz',usecols=['event_id','entry_ms','score']).set_index('event_id')
    extra = set(json.loads((admin.archive.EXP/'summary.json').read_text())['archived_additions'])
    extra.update(json.loads((admin.study.EXP/'summary.json').read_text())['new_archive_cohorts']['priced'])
    spec,marks = rank.market_spec(EXP)
    spec.update(json.loads((vwap.EXP/'config.json').read_text())['additional_settlements'])
    marks.update(pd.read_csv(vwap.EXP/'settlement_index_1m.csv').groupby('symbol')[['low','close','high']].mean().to_dict('index'))
    paths = admin.archive.pool(extra)
    with admin.archive.market_quotes(extra):
        _, selected = admin.study.tenth.trend.inputs('Binance',paths)
        rows = admin.adjusted_legs(selected,extra,spec)
        actual = rows[~rows.is_control]
        if set(actual.parent_id)!=set(source.index) or not actual.parent_id.equals(actual.event_id):
            raise ValueError('Actual population differs from verified scored signal roster')
        control = rows[rows.is_control & rows.parent_id.isin(actual.parent_id)]
        actual = actual[actual.parent_id.isin(control.parent_id)]
        a = actual[actual.tp_r.eq(0)].set_index('parent_id').sort_index()
        c = control[control.tp_r.eq(0)].set_index('parent_id').sort_index()
        if not a.index.is_unique or not c.index.is_unique or not a.index.equals(c.index):
            raise ValueError('Each admitted opportunity must have exactly one later placebo')
        if not a.symbol.equals(c.symbol) or not a.side.equals(c.side) or not (c.entry_ms>a.entry_ms).all():
            raise ValueError('Later control changed underlying, direction or causal sequence')
        table = pd.DataFrame(dict(symbol=a.symbol,side=a.side,actual_entry_ms=a.entry_ms,
                                  later_entry_ms=c.entry_ms,score=a.index.map(source.score)),index=a.index)
        table.to_csv(EXP/'matched_opportunities.csv.gz')
        rosters, deliveries = {}, []
        for label,pool in [('actual',actual),('later',control)]:
            base = pool[pool.tp_r.eq(0)].set_index('event_id')
            priority = base.parent_id.map(source.score)
            ranked,_ = rank.scores.rerank(pool,priority)
            rosters[label] = admin.admitted(ranked)
            paid = rosters[label]
            deliveries.append(paid.loc[paid.delivery,['event_id','symbol','tp_r','exit_bar_ms','delivery_ambiguous']].assign(arm=label))
        settled = pd.concat(deliveries,ignore_index=True)
        settled.to_csv(EXP/'selected_deliveries.csv',index=False)
        absent = sorted({s+'USDT' for s in settled.symbol}-marks.keys())
        if absent:
            raise ValueError(f'Missing official settlements {absent}')
        results = {}
        for label,paid in rosters.items():
            ready = admin.resolve_settlements(paid,marks,'proxy')
            data = admin.archive.native.prepare(ready)
            metrics,parents,eq,trims = hourly.tested.trim_account(data,admin.archive.native.quotes,.9,.2,review_hours=1)
            if not np.isclose(1000+parents.cash_pnl.sum(),eq.nav.iloc[-1],rtol=1.e-8,atol=.001):
                raise ValueError('Wallet does not reconcile')
            parents.to_csv(EXP/f'funded_{label}.csv.gz')
            eq.to_csv(EXP/f'hourly_{label}.csv.gz',index_label='hour_end_utc')
            results[label] = dict(metrics=metrics,trimmed=trims.event_id.nunique(),funded=parents.index.size)
    result = dict(matched_opportunities=len(a),scored_parent_opportunities=int(table.score.count()),
                  unpaired_signals=len(source)-len(a),results=results,
                  shared_final_multiple_ratio=results['actual']['metrics']['all']['final_usdt']/results['later']['metrics']['all']['final_usdt'])
    (EXP/'summary.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(json.dumps(result,indent=2),flush=True)


if __name__=='__main__':
    main()
