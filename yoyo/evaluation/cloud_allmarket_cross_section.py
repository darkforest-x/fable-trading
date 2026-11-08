"""Two pre-specified cross-sectional descriptions on frozen V1.7 confirmations.

At any 12H confirmation the peer universe is all covered OKX alt contracts with
50 contiguous closed bars by the prior 12H close. All returns, BTC exclusions,
EMA directions, ranks and coverage use prices ending strictly before the key K.
For tied prices rank at midrank; direction-mirror ranks for shorts. Exclude the
focus contract from trend breadth and the peer count. Later history plays no
role in the cross-sectional values; the recoverable contract list is retrospective.
"""
import json
import numpy as np
import pandas as pd
from yoyo.evaluation.cloud_allmarket_census import EXP, TOP, TOP_COINS, read_bars, context, STEP
from yoyo.evaluation.cloud_peak10_inputs import dump


def cross_section(universe):
    returns, trends = {}, {}
    for symbol in universe:
        if symbol in ('BTC', 'ETH'):
            continue
        folder=(TOP if symbol in TOP_COINS else EXP)/'inputs'/symbol
        if not (folder/'bars12h.csv.gz').is_file():
            continue
        bars=read_bars(folder)
        if len(bars)<50:
            continue
        c=context(bars)
        valid=(c.span>=51) & c.return14.notna() & c.ema_side.shift().ne(0)
        returns[symbol]=c.return14.where(valid)
        trends[symbol]=c.ema_side.shift().where(valid)
    moves=pd.DataFrame(returns).sort_index()
    trend=pd.DataFrame(trends).reindex_like(moves)
    count=moves.count(axis=1)
    ranks=(moves.rank(axis=1,method='average')-1).div((count-1).where(count>1),axis=0)
    positive=trend.eq(1).sum(axis=1)
    return ranks,trend,positive,count


def rows_for_filters(x, rule, year):
    group=x[x.year==year].copy()
    kept=group[group[rule]]
    resolved=group.status.isin(['hit','miss'])
    selected_resolved=kept.status.isin(['hit','miss'])
    signal_count=len(group); chosen=len(kept)
    wins=(group.status=='hit').sum(); saved=(kept.status=='hit').sum()
    early=group.early_stop.sum(); admitted=kept.early_stop.sum()
    # First-day observation is settled by a stop/flip, 24h of contiguous data,
    # or (for still-open paths) the end of the frozen run once the first day elapsed.
    observed=group.early_observed.sum(); selected_observed=kept.early_observed.sum()
    assert selected_observed >= admitted
    risk=lambda n,d: float(n/d) if d else None
    # Same-time random expectation for a filter choosing k of n available labels.
    # Restrict to known hit outcomes for the hit comparison only; use independently
    # available stop outcomes for the early-stop comparison.
    def random_expected(known, outcome):
        s=group[known].copy()
        s['outcome']=outcome.loc[s.index].astype(int)
        blocks=s.groupby(['confirmed_bjt','side']).agg(n=('symbol','size'),k=(rule,'sum'),yes=('outcome','sum'))
        return dict(expected=float((blocks.k*blocks.yes/blocks.n).sum()),observed=int(s.loc[s[rule],'outcome'].sum()),
                    informative_blocks=int(((blocks.k>0)&(blocks.k<blocks.n)).sum()))
    return dict(year=year,rule=rule,signals=signal_count,kept=chosen,coverage=risk(chosen,signal_count),
        hits=int(wins),retained_hits=int(saved),hit_recall=risk(saved,wins),
        hit_rate=risk(saved,int(selected_resolved.sum())),unfiltered_hit_rate=risk(wins,int(resolved.sum())),
        early_stops=int(early),retained_early_stops=int(admitted),removed_early_stops=risk(early-admitted,early),
        early_stop_rate=risk(admitted,int(selected_observed)),unfiltered_early_stop_rate=risk(early,int(observed)),
        matched_time_hit=random_expected(resolved,group.status.eq('hit')),
        matched_time_early_stop=random_expected(group.early_observed,group.early_stop))


def main():
    ledger=pd.read_csv(EXP/'signals.csv')
    u=pd.read_csv(EXP/'universe.csv',keep_default_na=False)
    ranks,trend,positive,n=cross_section(u.symbol)
    x=ledger[(~ledger.was_top20) & (~ledger.symbol.isin(['BTC','ETH']))].copy()
    x['year']=x.confirmed_bjt.str[:4].astype(int)
    t=pd.to_datetime(x.confirmed_bjt,utc=True)
    exit_t=pd.to_datetime(x.path_end_bar_bjt,utc=True,format='mixed',errors='coerce')
    delay=(exit_t-t).dt.total_seconds()/3600
    x['early_stop']=x.exit_reason.eq('stop') & delay.ge(0) & delay.lt(24)
    # Unavailable and gap outcomes do not establish 24h exposure in this secondary table.
    x['early_observed']=x.exit_reason.isin(['stop','cloud_flip']) | x.observed_first_24h_bars
    strength=[]; breadth=[]; peers=[]
    for ms, row in zip(t.astype('int64')//1_000_000-STEP,x.itertuples()):
        rank=ranks.at[ms,row.symbol]; own=trend.at[ms,row.symbol]
        count=int(n.at[ms])-1
        if pd.isna(rank) or not np.isfinite(own) or count<10:
            strength.append(np.nan); breadth.append(np.nan); peers.append(max(0,count)); continue
        favorable=(positive.at[ms]-int(own==1))/count
        strength.append(rank if row.side==1 else 1-rank)
        breadth.append(favorable if row.side==1 else 1-favorable)
        peers.append(count)
    x['relative_rank']=strength; x['breadth']=breadth; x['peers']=peers
    # Missing historical breadth keeps the original alert as in the primary study.
    x['retain_upper_two_thirds']=x.relative_rank.isna() | x.relative_rank.ge(1/3)
    x['retain_majority_aligned']=x.breadth.isna() | x.breadth.ge(.5)
    rows=[rows_for_filters(x,rule,year) for rule in ('retain_upper_two_thirds','retain_majority_aligned')
          for year in (2025,2026)]
    dump(EXP/'cross_section.json',dict(method='Two prespecified diagnostic rules; same-time/side opportunity controls; no later-year tuning.',
        missing=int(x.relative_rank.isna().sum()),rows=rows))
    x[['symbol','week','confirmed_bjt','side','relative_rank','breadth','peers','retain_upper_two_thirds',
       'retain_majority_aligned']].to_csv(EXP/'cross_section.csv.gz',index=False)


if __name__=='__main__':
    main()
