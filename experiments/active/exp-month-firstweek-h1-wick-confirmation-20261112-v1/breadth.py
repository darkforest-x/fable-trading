"""At the touch-hour close count other contracts first touching the same side in that H1.

Use the complete Beijing-day-first touch catalogue, including reclaims and failures.
The comparison is zero other contracts versus at least one, with no fitted cutoff.
"""
import pandas as pd

from replay import BOOK, HERE, HOUR, OUT, SPLIT


def main():
    cards = pd.read_csv(BOOK.parents[1]/'all_replay_v1/cards.csv.gz', usecols=['symbol','touch_ms','side'])
    assert len(cards) == 50986 and not cards.duplicated(['symbol','touch_ms']).any()
    cards['other_contracts'] = cards.groupby(['touch_ms','side']).symbol.transform('size') - 1
    entries = pd.read_csv(OUT/'readings.csv.gz')
    g = entries[entries.clock.eq('entry_cloud')].copy()
    g['touch_ms'] = g.entry_ms - HOUR
    g = g.merge(cards, on=['symbol','side','touch_ms'], how='left', validate='one_to_one')
    assert g.other_contracts.notna().all()
    g['another_touch'] = g.other_contracts.gt(0)
    g = g[g.original_net_pct.notna()]
    rows = []
    for period, frame in [('all',g), ('through_2024',g[g.entry_ms.lt(SPLIT)&g.exit_ms.le(SPLIT)]),
                          ('from_2025',g[g.entry_ms.ge(SPLIT)])]:
        for side, subset in [('both',frame), *frame.groupby('side')]:
            for flag,group in subset.groupby('another_touch'):
                rows.append(dict(period=period,side=side,another_touch=flag,settled=len(group),
                                 touch_hours=group.touch_ms.nunique(),
                                 targets=int(group.exit_reason.str.startswith('TAKE_PROFIT').sum()),
                                 win_pct=100*group.original_net_pct.gt(0).mean(),
                                 net_pct=group.original_net_pct.mean(),
                                 equal_hour_net_pct=group.groupby('touch_ms').original_net_pct.mean().mean()))
    pd.DataFrame(rows).to_csv(HERE/'breadth_counts.csv', index=False, float_format='%.6f')


if __name__ == '__main__':
    main()
