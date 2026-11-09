"""Test the first-touch FVG exclusion on the unchanged scored and hourly-capped wallet."""

import pandas as pd

from yoyo.evaluation import cloud_close_fvg_transfer as fvg
from yoyo.evaluation import cloud_close_spot_reclaim as wallet

EXP = fvg.EXP.parent / 'exp-cloud-close-fvg-cap-filter-20261109-v1'


def main():
    labels = pd.read_csv(fvg.EXP / 'decisions.csv.gz', usecols=['parent_id', 'tag'])
    tags = labels.set_index('parent_id', verify_integrity=True).tag.rename_axis('event_id')
    accepted = tags.map({'outside': True, 'overlap': False}).astype('boolean')
    if not set(tags.loc[accepted.isna()]).issubset({'warmup', 'opened_beyond_line', 'incomplete_monday'}):
        raise ValueError('Unknown first-touch label in the frozen Binance decisions')
    panel = pd.DataFrame({'covered': accepted.notna().astype('boolean'), 'outside_gap': accepted})
    wallet.compare_filter(panel, 'outside_gap', EXP, (wallet.vwap.EXP, wallet.timing.EXP, fvg.EXP))


if __name__ == '__main__':
    main()
