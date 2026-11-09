"""Test independent spot confirmation at the unchanged original futures 12H entry."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_funding_rank as rank
from yoyo.evaluation import cloud_close_hourly_trim as hourly
from yoyo.evaluation import cloud_close_hourly_trim_control as timing
from yoyo.evaluation import cloud_close_key_vwap as vwap

admin = rank.admin
EXP = rank.EXP.parent/'exp-cloud-close-spot-reclaim-20261109-v1'
PANEL = admin.archive.native.wallet.ROOT/'data/research/cloud_close_spot_reclaim_20261109/event_panel.csv.gz'


def sample_placebo(candidates):
    sample = pd.Series(False,index=candidates.index)
    rng = np.random.default_rng(1029)
    movable = 0
    for _,part in candidates.sort_index().groupby(['entry_ms','side','has_score'],sort=True):
        count = int(part.spot_reclaim.sum())
        if 0<count<len(part):
            movable += count
        if count:
            sample.loc[rng.choice(part.index.to_numpy(),size=count,replace=False)] = True
    return sample,movable


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed same-market spot reclaim test')
    source = pd.read_csv(rank.scores.EXP/'candidate_order.csv.gz',usecols=['event_id','entry_ms','score']).set_index('event_id',verify_integrity=True)
    panel = pd.read_csv(PANEL,usecols=['event_id','covered','spot_reclaim'],dtype={'covered':'boolean','spot_reclaim':'boolean'}).set_index('event_id',verify_integrity=True)
    if set(panel.index)!=set(source.index) or panel.covered.isna().any() or not panel.covered.equals(panel.spot_reclaim.notna().astype('boolean')):
        raise ValueError('Every frozen candidate must have exactly one covered or unknown spot observation')
    extra = set(json.loads((admin.archive.EXP/'summary.json').read_text())['archived_additions'])
    extra.update(json.loads((admin.study.EXP/'summary.json').read_text())['new_archive_cohorts']['priced'])
    spec,marks = rank.market_spec(EXP)
    for source_dir in (vwap.EXP,timing.EXP):
        spec.update(json.loads((source_dir/'config.json').read_text())['additional_settlements'])
        marks.update(pd.read_csv(source_dir/'settlement_index_1m.csv').groupby('symbol')[['low','close','high']].mean().to_dict('index'))
    paths = admin.archive.pool(extra)
    with admin.archive.market_quotes(extra):
        _,selected = admin.study.tenth.trend.inputs('Binance',paths[~paths.is_control])
        rows = admin.adjusted_legs(selected,extra,spec)
        candidates = rows[rows.tp_r.eq(0)].set_index('event_id')
        if set(candidates.index)!=set(source.index):
            raise ValueError('Frozen original 12H population changed')
        candidates = candidates[['entry_ms','side','symbol']].join(panel).join(source.score)
        candidates['has_score'] = candidates.score.notna()
        observed = candidates[candidates.covered].copy()
        observed['placebo'],movable = sample_placebo(observed)
        if int(observed.placebo.sum())!=int(observed.spot_reclaim.sum()):
            raise ValueError('Observability-matched placebo changed contemporaneous cohort sizes')
        observed[['entry_ms','side','symbol','spot_reclaim','has_score','placebo']].to_csv(EXP/'observed_candidates.csv.gz')
        results,rosters = {},{}
        for name,part in [('covered',observed),('confirmed',observed[observed.spot_reclaim]),
                           ('placebo',observed[observed.placebo])]:
            if part.empty:
                raise ValueError(f'{name} has no eligible opportunities')
            sample = rows[rows.event_id.isin(part.index)]
            ranked,_ = rank.scores.rerank(sample,source.score)
            rosters[name] = admin.admitted(ranked)
        settlements = pd.concat([v.loc[v.delivery,['symbol','event_id','exit_bar_ms','tp_r']].assign(arm=k)
                                 for k,v in rosters.items()],ignore_index=True)
        settlements.to_csv(EXP/'selected_deliveries.csv',index=False)
        absent = sorted({s+'USDT' for s in settlements.symbol}-marks.keys())
        if absent:
            raise ValueError(f'Official settlement inputs required for {absent}')
        for label,paid in rosters.items():
            data = admin.archive.native.prepare(admin.resolve_settlements(paid.copy(),marks,'proxy'))
            metrics,parents,equity,trims = hourly.tested.trim_account(data,admin.archive.native.quotes,.9,.2,review_hours=1)
            if not np.isclose(1000+parents.cash_pnl.sum(),equity.nav.iloc[-1],rtol=1.e-8,atol=.001):
                raise ValueError('Funded positions do not reconcile to final account equity')
            parents.to_csv(EXP/f'funded_{label}.csv.gz')
            equity.to_csv(EXP/f'hourly_{label}.csv.gz',index_label='hour_end_utc')
            results[label] = dict(metrics=metrics,trimmed_positions=trims.event_id.nunique())
    summary = dict(full_roster_no_filter=json.loads((hourly.EXP/'summary.json').read_text())['Binance']['metrics'],
                   results=results,original_candidates=len(candidates),covered_candidates=len(observed),
                   confirmed_candidates=int(observed.spot_reclaim.sum()),unknown_candidates=len(candidates)-len(observed),
                   controls_with_multiple_choices=movable,seeded_placebo_overlap=int((observed.placebo&observed.spot_reclaim).sum()))
    (EXP/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(json.dumps(summary,indent=2),flush=True)


if __name__=='__main__':
    main()
