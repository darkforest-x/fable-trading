"""Test a 12H cloud entry confirmed by five price-correlated contracts on the same venue."""
import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_liquid_queue as queue

EXP = queue.EXP.parent/'exp-cloud-close-peer-confirmation-20261109-v1'
HOUR = queue.repair.wallet.HOUR
STEP = HOUR * 12


def relative_returns(quotes, symbols):
    """Native H1 closes only. Each 12H endpoint needs 12 sequential quotes, plus a prior complete close."""
    observed = {}
    for symbol in symbols:
        stamps, prices = quotes(symbol)
        if np.any(np.diff(stamps) <= 0):
            raise ValueError(f'Invalid hourly order in {symbol}')
        indices = np.flatnonzero((stamps + HOUR) % STEP == 0)
        indices = indices[indices >= 11]
        indices = indices[stamps[indices] - stamps[indices - 11] == 11 * HOUR]
        observed[symbol] = pd.Series(prices[indices, 3], index=stamps[indices] + HOUR)
    closes = pd.DataFrame(observed)
    closes = closes.reindex(np.arange(closes.index.min(), closes.index.max() + STEP, STEP))
    returns = np.log(closes.where(closes > 0)).diff()
    return returns.sub(returns.BTC,axis=0).drop(columns='BTC')


def select(events, returns):
    """Estimate peer correlations from 60 completed pre-key BTC-adjusted returns. Vote on the just-closed key."""
    records = []
    for clock, group in events.groupby('entry_ms'):
        position = returns.index.get_indexer([clock])[0]
        if position < 60:
            continue
        history = returns.iloc[position-60:position].dropna(axis=1)
        now = returns.iloc[position].reindex(history.columns)
        centered = history.to_numpy() - history.to_numpy().mean(axis=0)
        norm = np.linalg.norm(centered,axis=0)
        good = np.isfinite(norm) & (norm>0)
        names = history.columns[good]
        if len(names) < 11:
            continue
        # Ordering and peer membership depend only on pre-key history. Missing key returns do not trigger substitutions.
        vectors = centered[:,good]/norm[good]
        now = now.reindex(names).to_numpy()
        for event in group.itertuples():
            if event.symbol not in names:
                continue
            own = names.get_loc(event.symbol)
            others = np.flatnonzero(np.arange(len(names))!=own)
            corr = vectors[:,own] @ vectors[:,others]
            order = others[np.argsort(-corr,kind='stable')]
            near = order[:5]
            # Fixed comparison group near the median correlation, taken from the identical available set.
            middle_start = (len(order)-5)//2
            middle = order[middle_start:middle_start+5]
            if not np.isfinite(now[np.r_[near,middle]]).all():
                continue
            records.append(dict(event_id=event.event_id,entry_ms=clock,symbol=event.symbol,side=event.side,
                                nearby='|'.join(names[near]),middle='|'.join(names[middle]),
                                nearby_votes=int((event.side*now[near]>0).sum()),
                                middle_votes=int((event.side*now[middle]>0).sum())))
    return pd.DataFrame(records).set_index('event_id')


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed peer confirmation')
    rows, quotes, sources, _ = queue.repair.pool()
    events = rows[rows.tp_r.eq(0)].set_index('event_id',drop=False)
    features = select(events,relative_returns(quotes,sorted(sources)))
    features.to_csv(EXP/'peer_votes.csv.gz')
    allowed = dict(covered=features.index,nearby=features.index[features.nearby_votes>=3],
                   middle=features.index[features.middle_votes>=3])
    queue.main(experiment=EXP,policies=[(label,'balanced') for label in allowed],eligible_by_arm=allowed)


if __name__=='__main__':
    main()
