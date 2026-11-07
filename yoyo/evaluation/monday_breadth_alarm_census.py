"""Census of frozen and extended Binance 5m Monday 1.618 breadth alarms.

A symbol supplies its first weekly touch per direction. The alarm becomes known at the
triggering five-minute bar close. Counts use no BTC/ETH outcomes or per-event scores.
"""

import pandas as pd

from yoyo.evaluation.monday_limit_alarm_entry import alarms
from yoyo.evaluation.monday_limit_reclaim_patterns import SRC
from yoyo.evaluation.range_framework import ROOT


ACTIVE = ROOT/'experiments/active'
OUT = ACTIVE/'exp-monday-breadth-alarm-census-20261107-v1'
FROZEN = ACTIVE/'exp-monday-limit-alarm-entry-20261029-v1/alarms.csv.gz'
OCTOBER = ACTIVE/'exp-monday-limit-live-october-20261101-v1/events.csv.gz'


def ledger_rows():
    parts = []
    for path in sorted(SRC.glob('*/opportunities.csv.gz')):
        frame = pd.read_csv(path)
        if frame.empty or frame.source_group.iloc[0] != 'crypto_5m' or 'touch_interval_open' not in frame:
            continue
        # Controls have an extra suffix. Ignore order status and retain observed first touches.
        touched = frame[frame.touch_interval_open.notna() & frame.event_id.str.count(':').eq(2)]
        parts.append(touched[['event_id','symbol','side','touch_interval_open']])
    old = pd.concat(parts,ignore_index=True).rename(columns={'event_id':'event','touch_interval_open':'touch'})
    old['touch'] = pd.to_datetime(old.touch,utc=True)
    if old.event.duplicated().any() or old.symbol.nunique()!=626:
        raise ValueError('Unexpected historical native 5m universe')
    extension = pd.read_csv(OCTOBER,usecols=['event','symbol','side','t'],parse_dates=['t']).rename(columns={'t':'touch'})
    if extension.event.duplicated().any() or not extension.symbol.isin(old.symbol).all():
        raise ValueError('Unexpected October extension universe')
    # On the handover week the extended builder starts from each coin's original series.
    seam_week = old.touch.max().tz_convert('America/New_York').date()
    seam_week = (pd.Timestamp(seam_week)-pd.Timedelta(days=seam_week.weekday())).strftime('%Y-%m-%d')
    original = old[old.event.str.split(':').str[1].eq(seam_week)]
    bridge = original.merge(extension,on=['event','symbol','side'],how='left',validate='one_to_one')
    if not bridge.touch_x.eq(bridge.touch_y).all():
        raise ValueError('Existing handover-week touches must match the extension')
    new = extension[extension.touch.gt(old.touch.max())]
    combined = pd.concat([old,new],ignore_index=True)
    if combined.event.duplicated().any():
        raise ValueError('Duplicate weekly touch across handover')
    return old, combined


def replay(touches, name):
    t = touches.copy()
    t['ms'] = t.touch.dt.as_unit('ms').astype('int64')
    if not t.ms.mod(300000).eq(0).all():
        raise ValueError('Touch must use native 5m open timestamps')
    hits = alarms(t, threshold=25)
    breadth = t.groupby(['ms','side']).symbol.nunique().rename('coins')
    hits = hits.merge(breadth,on=['ms','side'],validate='one_to_one')
    hits['known_bjt'] = (pd.to_datetime(hits.ms,unit='ms',utc=True) + pd.Timedelta(minutes=5)).dt.tz_convert('Asia/Shanghai')
    hits['scope'] = name
    return hits[['scope','ms','known_bjt','side','coins']]


def main():
    old, extended = ledger_rows()
    features = pd.read_csv(ACTIVE/'exp-monday-limit-score-features-v2-20261025-v1/features_v2.csv.gz',
                           usecols=['event','touch','symbol','side'],parse_dates=['touch'])
    score_keys = pd.read_csv(ACTIVE/'exp-monday-limit-score-leak-audit-20261027-v1/scores_causal.csv.gz',usecols=['event'])
    frozen = score_keys.merge(features,on='event',how='left',validate='one_to_one')
    audit = frozen.merge(old,on=['event','symbol','side'],how='left',validate='one_to_one')
    if not audit.touch_x.eq(audit.touch_y).all():
        raise ValueError('Frozen first-touch times differ from observed ledger')
    scopes = dict(frozen_scored=frozen, original_symbols_all_touches=old[old.symbol.isin(frozen.symbol)],
                  all_5m_original=old, all_5m_extended=extended)
    results, scope_rows, calendar = [], [], []
    for name,t in scopes.items():
        hits = replay(t,name)
        if name == 'frozen_scored':
            check = pd.read_csv(FROZEN)
            if set(zip(hits.ms,hits.side)) != set(zip(check.ms,check.side)):
                raise ValueError('Frozen trigger timestamps differ')
        results.append(hits)
        row = dict(scope=name,symbols=t.symbol.nunique(),first_touches=len(t),lower_touches=t.side.eq(1).sum(),
                   upper_touches=t.side.eq(-1).sum(),first_touch_utc=t.touch.min(),last_touch_utc=t.touch.max(),
                   lower_alarms=hits.side.eq(1).sum(),upper_alarms=hits.side.eq(-1).sum(),
                   bjt_dates=hits.known_bjt.dt.date.nunique())
        scope_rows.append(row)
        period = hits.known_bjt.dt.tz_localize(None).dt.to_period('M')
        local = t.touch.dt.tz_convert('Asia/Shanghai').dt.tz_localize(None)
        months = pd.period_range(local.min().to_period('M'),local.max().to_period('M'),freq='M')
        for m in months:
            part = hits.loc[period==m]
            calendar.append(dict(scope=name,month=str(m),lower=part.side.eq(1).sum(),upper=part.side.eq(-1).sum(),
                                 days=part.known_bjt.dt.date.nunique(),first_touch_symbols=t.loc[local.dt.to_period('M')==m,'symbol'].nunique()))
    OUT.mkdir(exist_ok=True)
    pd.concat(results).sort_values(['scope','ms','side']).to_csv(OUT/'alarms.csv',index=False)
    pd.DataFrame(scope_rows).to_csv(OUT/'scope.csv',index=False)
    pd.DataFrame(calendar).to_csv(OUT/'monthly.csv',index=False)


if __name__ == '__main__':
    main()
