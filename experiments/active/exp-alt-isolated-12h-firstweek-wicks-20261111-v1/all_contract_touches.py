"""All-underlying Binance USDT perpetual touch catalogue. Descriptive outcomes only.

One first literal touch per symbol and Beijing day. Both rails intersecting
the first H1 bar have unknown order and are skipped as in the original catalogue.
Observe favorable prices after the touch hour, using contiguous closed hours.
Record a confirmed move as soon as it occurs, including in incomplete windows.
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from census12 import BASE, OUT, HOUR, read_raw
from level_reversals import COLS, per_symbol
from scan import CUTOFF, scan

DEST = BASE.parent / 'expanded12'
HERE = Path(__file__).resolve().parent
KEY = [COLS[0], COLS[1], COLS[4]]


def new_market_observations(symbol, raw):
    raw = raw[raw[:, 0] + HOUR <= CUTOFF]
    if not len(raw):
        return [], [], '尚无可读行情'
    _, months = scan(symbol, raw, slot_ms=HOUR, periods=())
    valid = pd.DataFrame([row for row in months if row['status'] == 'valid'])
    if valid.empty:
        return [], months, '无完整首周及可观察月份'
    return per_symbol((symbol,valid), raw), months, '已扫描'


def photo_comparison(daily, roster):
    photos = pd.read_csv(HERE / '原图15张逐项核对.csv')
    ref = photos[['图片','合约','区间月（纽约）','侧','币安标的类型','OKX_轨价',
                  'OKX_K线开盘（北京）','OKX_首次触轨小时（北京）',
                  'OKX_轨价起算首次反向20%的小时数','OKX_首次触轨后已观察最大反向幅度%']].copy()
    for i, row in ref.iterrows():
        symbol,month,side = row['合约'],row['区间月（纽约）'],row['侧']+'轨'
        same = daily[(daily[COLS[0]]==symbol)&(daily[COLS[1]]==month)&(daily[COLS[4]]==side)]
        success = same[same[COLS[10]].notna()]
        ref.loc[i,'币安同月同侧触轨日数'] = len(same)
        ref.loc[i,'币安已观察反转至少20%的触轨日数'] = len(success)
        ref.loc[i,'币安已达标的起始触轨小时（北京）'] = '、'.join(success[COLS[2]])
        member = roster.loc[symbol]
        ref.loc[i,'币安挂牌日（北京）'] = (pd.to_datetime(member.listed_ms,unit='ms',utc=True)
                                             .tz_convert('Asia/Shanghai').strftime('%Y-%m-%d')
                                             if pd.notna(member.listed_ms) else '')
    return ref


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--available', action='store_true', help='Emit current coverage, including pending symbols')
    args = parser.parse_args()
    market = pd.read_csv(BASE/'universe_perpetuals.csv')
    legacy = pd.read_csv(OUT / '逐日首次碰轨_7天14天45天.csv.gz')
    old_cov = pd.read_csv(OUT/'coverage_months.csv')
    old_valid = old_cov[old_cov.status.eq('valid')].groupby('symbol').size()
    rows, states = [], {}
    for item in market.itertuples():
        if item.in_legacy_altcoin_cohort:
            states[item.symbol] = (int(old_valid.get(item.symbol,0)), '已扫描')
            continue
        raw = read_raw(item.symbol)
        events, cov, status = new_market_observations(item.symbol, raw)
        rows.extend(events)
        states[item.symbol] = (sum(r['status']=='valid' for r in cov),status)
    additional = pd.DataFrame(rows, columns=COLS)
    for col in COLS[2:4]:
        additional[col] = pd.to_datetime(additional[col],unit='h',utc=True).dt.tz_convert('Asia/Shanghai').dt.strftime('%Y-%m-%d %H:%M')
    daily = pd.concat([legacy,additional],ignore_index=True).drop(columns=COLS[11])
    assert not daily.assign(day=daily[COLS[2]].str[:10]).duplicated([COLS[0],'day']).any()
    daily = daily.sort_values([COLS[2],COLS[0]])
    roster = market.set_index('symbol')
    daily.insert(1,'标的类别',daily[COLS[0]].map(roster.underlying).fillna('历史分类未注明'))
    daily['已观察到反向20%'] = daily[COLS[10]].notna()
    daily['45天路径完整'] = daily[COLS[9]].notna()
    daily['观察结果'] = np.select([daily['已观察到反向20%'], daily['45天路径完整']],
                            ['已达到20%','完整45天内未达20%'], default='观察不足45天且尚未达标')
    for s,(n,st) in states.items():
        roster.loc[s,'可用区间月数'] = n
        roster.loc[s,'扫描状态'] = st
    pending = roster[roster['扫描状态'].eq('尚无可读行情')]
    if len(pending) and not args.available:
        raise RuntimeError(f'No candles for {len(pending)} symbols; pass --available to record coverage explicitly')
    dest = DEST / ('available' if args.available else 'catalogue')
    dest.mkdir(exist_ok=True,parents=True)
    daily.to_csv(dest/'所有合约_每日首次触轨.csv.gz',index=False,compression='gzip')
    good = daily[daily['已观察到反向20%']]
    good.to_csv(dest/'所有合约_触轨后已反转至少20%.csv',index=False,encoding='utf-8-sig')
    ref = photo_comparison(daily,roster)
    ref.to_csv(dest/'原图15张_双市场逐张核对.csv',index=False,encoding='utf-8-sig')
    roster.to_csv(dest/'合约覆盖.csv',encoding='utf-8-sig')
    print('合约数',len(roster),'无K线',len(pending),'有可用月份',(roster['可用区间月数']>0).sum(),
          '触轨日',len(daily),'已达20%触轨日',len(good),'已达标合约月份方向组',good.groupby(KEY).ngroups)
    print(ref[['合约','币安同月同侧触轨日数','币安已观察反转至少20%的触轨日数']].to_string(index=False))


if __name__=='__main__':
    main()
