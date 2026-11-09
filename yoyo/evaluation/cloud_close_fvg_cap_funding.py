"""Fund only the outside-gap entries selected by the prior capped account."""
import pandas as pd

from yoyo.evaluation import cloud_close_fvg_cap_filter as previous
from yoyo.evaluation import cloud_close_fvg_priority_funding as funding

EXP = previous.EXP.parent / 'exp-cloud-close-fvg-cap-funding-20261109-v1'


def main():
    source = pd.read_csv(previous.wallet.rank.scores.EXP / 'candidate_order.csv.gz')
    observed = pd.read_csv(previous.EXP / 'observed_candidates.csv.gz')
    selected = observed.loc[observed.outside_gap, 'event_id']
    subset = source[source.event_id.isin(selected)].copy()
    if len(subset) != len(selected) or not subset.event_id.is_unique:
        raise ValueError('Outside-gap roster differs from the original classification')
    # The ranker needs one parent row per event. tp_r=0 denotes that row.
    _, ranking = previous.wallet.rank.scores.rerank(subset.assign(tp_r=0.), subset.set_index('event_id').score)
    rank = ranking.set_index('event_id').new_rank
    expected = pd.read_csv(previous.EXP / 'funded_confirmed.csv.gz', usecols=['event_id']).event_id
    directories = (previous.wallet.vwap.EXP, previous.wallet.timing.EXP, previous.fvg.EXP, previous.EXP)
    funding.fund_ranked_roster(expected, rank, EXP, directories)


if __name__ == '__main__':
    main()
