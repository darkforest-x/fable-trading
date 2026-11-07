"""Count distinct weekly first-touch symbols in each aligned 5m candle and direction.

Inputs are the extended native 5m census and frozen alarms. Bar counts are observable
at the candle close and retain touches that occur during a same-direction cooldown.
Candles with no first touch in either direction are outside the frequency denominator.
"""
import pandas as pd

from yoyo.evaluation import monday_breadth_alarm_census as census


OUT = census.ACTIVE/'exp-monday-breadth-bar-counts-20261107-v1'
FIVE_MINUTES = pd.Timedelta(minutes=5)
DIRECTIONS = {1: 'lower', -1: 'upper'}


def count_bars(touches, alarms):
    events = touches[['touch','side','symbol']]
    if events.duplicated().any() or not set(events.side.unique()) == set(DIRECTIONS):
        raise ValueError('Unexpected weekly touch identity or side')
    groups = events.sort_values(['touch','side','symbol']).groupby(['touch','side'])
    counts = groups.symbol.nunique().rename('coin_count').reset_index()
    flags = alarms[['ms','side','coins']].copy()
    flags['touch'] = pd.to_datetime(flags.pop('ms'),unit='ms',utc=True)
    counts = counts.merge(flags,on=['touch','side'],how='left',validate='one_to_one')
    counts['alarm'] = counts.coins.notna()
    tagged = counts[counts.alarm]
    if len(tagged)!=len(alarms) or not tagged.coin_count.eq(tagged.coins).all():
        raise ValueError('Alarm timestamps and breadth must match the full archived census')
    counts['cooldown'] = counts.coin_count.ge(25) & ~counts.alarm
    for side, group in counts.groupby('side'):
        latest = group.touch.where(group.alarm).ffill()
        since = (group.touch-latest)[group.cooldown]
        if not (since.gt(pd.Timedelta(0)) & since.lt(pd.Timedelta(minutes=30))).all():
            raise ValueError(f'Qualified bar missing a cooldown anchor on side {side}')
    counts['known_bjt'] = (counts.touch + FIVE_MINUTES).dt.tz_convert('Asia/Shanghai')
    bars = pd.DataFrame(index=events.touch.drop_duplicates().sort_values())
    for code,direction in DIRECTIONS.items():
        by_side = counts[counts.side.eq(code)].set_index('touch')
        bars[direction+'_coins'] = by_side.coin_count.reindex(bars.index, fill_value=0)
        bars[direction+'_alarm'] = by_side.alarm.reindex(bars.index, fill_value=False)
    bars['either_unique_coins'] = events.groupby('touch').symbol.nunique()
    bars.index = (bars.index + FIVE_MINUTES).tz_convert('Asia/Shanghai')
    bars.index.name = 'known_bjt'

    alarms_out = counts[counts.alarm].merge(events,on=['touch','side'],validate='one_to_many')
    alarms_out = (alarms_out.sort_values('symbol').groupby(['known_bjt','side','coin_count'])
                  .symbol.agg(' '.join).rename('symbols').reset_index().sort_values('known_bjt'))
    alarms_out['side'] = alarms_out.side.map(DIRECTIONS)
    counts['side'] = counts.side.map(DIRECTIONS)
    frequency = (counts.groupby(['coin_count','side']).agg(bars=('touch','size'),
                   alarms=('alarm','sum'), in_cooldown=('cooldown','sum')).reset_index())
    return bars, alarms_out, frequency


def main():
    _, touches = census.ledger_rows()
    alarms = pd.read_csv(census.OUT/'alarms.csv').query('scope == "all_5m_extended"')
    bars, alarms_out, frequency = count_bars(touches,alarms)
    OUT.mkdir(exist_ok=True)
    bars.to_csv(OUT/'bars_with_touches.csv.gz')
    alarms_out.to_csv(OUT/'alarm_coins.csv',index=False)
    frequency.to_csv(OUT/'frequency.csv',index=False)


if __name__ == '__main__':
    main()
