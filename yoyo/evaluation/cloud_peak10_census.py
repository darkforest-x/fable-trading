"""Census of the original SPIKE V1.7 12H reference MFE >10, before first stop/EMA flip.

Reuse cloud_signals.signal/ema_points and the website's planExcursion, matching
V1.7 historical 12H OHLC ambiguity. No simulated fills or return calculation.
Week anchors are exact New York Monday 00:00 to Tuesday 00:00. Missing prices
end observation; right-censored unresolved signals are distinct from misses.
"""
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.cloud_peak10_inputs import CONFIG, CUTOFF, EXP, HOUR, OHLC, RANKING, dump
from yoyo.evaluation.pepe_range_exits import signals as stoch_crosses
from yoyo.monitor.cloud_signal_plans import atr_points
from yoyo.monitor.cloud_signals import STEP, ema_points, signal
from yoyo.monitor.monday_fib import week_window

BJ = 'Asia/Shanghai'


def bjt(ms):
    if ms is not None:
        return pd.to_datetime(ms, unit='ms', utc=True).tz_convert(BJ).isoformat()
    return ''


def anchors(hour, bars, symbol):
    calendar = {}
    for t in bars.index:
        w = week_window(t)
        if w['week'] in calendar:
            continue
        hours = np.arange(w['start'], w['end'], HOUR)
        data = hour.reindex(hours)
        valid = data[OHLC].notna().all(axis=1)
        state = 'ready' if valid.all() else 'missing_hours'
        h, l = float(data.high.max()), float(data.low.min())
        if state == 'ready' and h <= l:
            state = 'empty_range'
        calendar[w['week']] = dict(week=w['week'], symbol=symbol, state=state,
            end_ms=w['end'], expires_ms=w['expires'], high=h, low=l,
            lower=l - .618*(h-l), upper=h + .618*(h-l), observed_hours=int(valid.sum()))
    return calendar


def features(s, original_bar, prior_atr):
    """At confirmation: wick and penetration from signal OHLC, directed close
    distance from touched level in original risk units, EMA spread normalized
    by previous 12H Wilder ATR14, previous ATR/close, NY Monday high/low width.
    """
    side = s['side']
    entry = s['entry']
    r = s['range']
    o, h, l, c = [original_bar[k] for k in OHLC]
    atr = prior_atr['atr14']
    outward = (entry-l if side == 1 else h-entry)
    wick = (min(o,c)-l if side == 1 else h-max(o,c))
    return dict(close_distance_r=(c-entry)*side / abs(entry-s['stop']),
        penetration_atr=outward/atr, ema_spread_atr=abs(s['ema20']-s['ema50'])/atr,
        wick_share=wick/(h-l), monday_width_pct=(r['high']/r['low']-1)*100,
        prior_atr_pct=100*atr/prior_atr['close'], planned_r=s['planned_rr'],
        pure_wick=(o-entry)*side > 0, signal_open=o, signal_high=h, signal_low=l,
        ema20=s['ema20'], ema50=s['ema50'])


def finer_order(frame, s, p):
    """Resolve a threshold straddle only if complete 1H OHLC can order the two
    prices with no tie. The historical 12H chart bounds remain authoritative."""
    t = p['exit_bar_ms']
    block = frame.reindex(np.arange(t, t+STEP, HOUR))
    if block[OHLC].isna().any().any():
        return 'missing_hours'
    side = s['side']
    target = s['entry'] + side*10*abs(s['entry']-s['stop'])
    for bar in block.itertuples():
        def ends(price):
            value = side*(price-p['trigger_level'])
            return value <= 0 if p['exit_state'] == 'stop' else value < 0
        if ends(bar.open):
            return 'miss'
        if side*(bar.open-target) > 0:
            return 'hit'
        favorable, adverse = (bar.high,bar.low) if side==1 else (bar.low,bar.high)
        over, exit_hit = side*(favorable-target) > 0, ends(adverse)
        if over and exit_hit:
            return 'same_hour'
        if over or exit_hit:
            return 'hit' if over else 'miss'
    return 'source_disagrees'


def census(spec):
    symbol = spec['instId']
    folder = EXP/'inputs'/spec['symbol']
    bars = pd.read_csv(folder/'bars12h.csv.gz', index_col='time', float_precision='round_trip')
    hour = pd.read_csv(folder/'bars1h.csv.gz', index_col='time', float_precision='round_trip')
    calendar = anchors(hour, bars, symbol)
    # On an eligible key the proper directional wick already clears its 1.618.
    # V1.7's optional chart-timeframe Stoch marks hide NY weekend opens by default.
    crosses = stoch_crosses(bars)
    records = bars[OHLC].reset_index().to_dict(orient='records')
    ema, atr = ema_points(records), atr_points(records)
    selected = []
    used = set()
    for idx, bar in enumerate(records):
        week = week_window(bar['time'])['week']
        if week in used:
            continue
        s = signal(symbol, bar, ema[idx], calendar[week], CUTOFF)
        if s:
            assert idx and atr[idx-1]['atr14'] > 0 and ema[idx]['count'] >= 50
            used.add(week)
            selected.append(dict(idx=idx, signal=s))
    payload = json.dumps(dict(bars=records, signals=selected, cutoff=CUTOFF), allow_nan=False)
    js = Path(__file__).with_name('cloud_peak10_paths.cjs')
    paths = json.loads(subprocess.run(['node', str(js)], input=payload, text=True,
                                      check=True, capture_output=True).stdout)
    rows = []
    for item, p in zip(selected, paths):
        s, idx = item['signal'], item['idx']
        ended = bool(p.get('exit_state'))
        lo, hi = p['lowerR'], p['upperR']
        order = ''
        if lo > 10:
            status = 'hit'
        elif hi > 10:
            status = 'possible'
            order = finer_order(hour, s, p)
        else:
            status = 'miss' if ended else 'censored'
        feat = features(s, records[idx], atr[idx-1])
        distance = feat['close_distance_r']
        ny_weekday = pd.Timestamp(s['signal_open_ms'],unit='ms',tz='UTC').tz_convert('America/New_York').weekday()
        rows.append(dict(rank=spec['rank'], symbol=spec['symbol'], week=s['week'], side=s['side'],
            key_open_bjt=bjt(s['signal_open_ms']), confirmed_bjt=bjt(s['confirmed_ms']),
            reference_entry=s['entry'], confirmation_close=s['confirmation_close'],
            reference_stop=s['stop'], stop_pct=s['stop_pct'], opposite_target=s['target'],
            status=status, peak_lower_r=lo, peak_upper_r=hi,
            first_gt10_bjt=bjt(p.get('first')), peak_time_bjt=bjt(p.get('peakAt')),
            path_end_bar_bjt=bjt(p.get('exit_bar_ms')), exit_reason=p.get('exit_state') or p['state'],
            exit_reference_r=p.get('exit_r'), first_12h_exit=p.get('exit_bar_ms') == s['confirmed_ms'],
            hour_order_if_possible=order, pre_current_listing=s['signal_open_ms'] < int(spec['list_time']),
            same_side_stoch=bool(ny_weekday < 5 and crosses.iloc[idx] == s['side']),
            confirmation_basis_lower_r=max(0., (lo-distance)/(1+distance)),
            confirmation_basis_upper_r=max(0., (hi-distance)/(1+distance)),
            bar_source=bars.source.iloc[idx], evaluation_cutoff_bjt=bjt(CUTOFF),
            **feat))
    coverage = dict(symbol=spec['symbol'], first_12h=bjt(int(bars.index[0])), through=bjt(int(bars.index[-1])+STEP),
        n12h=len(bars), observed_weeks=len(calendar),
        ready_mondays=sum(w['state']=='ready' for w in calendar.values()), signals=len(rows))
    return rows, coverage


def delivery(frame, filename):
    names = {'rank':'成交额排名','symbol':'币种','week':'纽约周一','side':'方向','key_open_bjt':'关键K开盘_北京时间',
        'confirmed_bjt':'信号确认_北京时间','reference_entry':'触线参考价','confirmation_close':'确认收盘价',
        'reference_stop':'参考止损价','opposite_target':'对侧目标价','planned_r':'到对侧目标R',
        'peak_lower_r':'峰值R下界','peak_upper_r':'峰值R上界','first_gt10_bjt':'首次明确过10R所在12H开盘_北京时间',
        'peak_time_bjt':'峰值下界所在12H开盘_北京时间','path_end_bar_bjt':'退出所在12H开盘_北京时间',
        'exit_reason':'结束原因','hour_order_if_possible':'小时线先后核对','pre_current_listing':'早于当前合约上市',
        'same_side_stoch':'关键K同向勾叉_默认设置','confirmation_basis_lower_r':'按确认收盘到原止损重算的峰值R下界',
        'close_distance_r':'确认时已离触线多少R','prior_atr_pct':'确认前12H_ATR14百分比'}
    out = frame.loc[:, list(names)].copy()
    out['side'] = out.side.map({1:'多',-1:'空'})
    out['exit_reason'] = out.exit_reason.map({'stop':'固定止损','cloud_flip':'盘中云带反向','gap':'行情缺口',
                                              'open':'仍在观察','unavailable':'后续行情缺失'})
    out['hour_order_if_possible'] = out.hour_order_if_possible.replace({'hit':'先超过10R','miss':'先退出',
        'same_hour':'同一小时内顺序不明','missing_hours':'小时数据不足','source_disagrees':'小时源与12H不一致'})
    out.rename(columns=names).to_csv(EXP/filename, index=False, encoding='utf-8-sig', float_format='%.10g')


def main():
    specs = json.loads(RANKING.read_text())['top20']
    rows, coverage = [], []
    for spec in specs:
        signals, meta = census(spec)
        rows.extend(signals)
        coverage.append(meta)
    frame = pd.DataFrame(rows).sort_values(['rank','confirmed_bjt'])
    assert len(specs)==20 and not frame.duplicated(['symbol','week']).any()
    assert (frame.peak_lower_r <= frame.peak_upper_r).all()
    assert frame[frame.status=='hit'].first_gt10_bjt.ne('').all()
    eth = frame[(frame.symbol=='ETH') & (frame.week=='2025-08-18')].iloc[0]
    oct_eth = frame[(frame.symbol=='ETH') & (frame.week=='2025-10-06')].iloc[0]
    assert (round(eth.peak_lower_r, 2), round(oct_eth.peak_lower_r, 2)) == (13.85,.07)
    frame.to_csv(EXP/'signals.csv',index=False)
    delivery(frame[frame.status=='hit'], '峰值明确超过10R.csv')
    delivery(frame[frame.status=='possible'], '仅峰值上界超过10R.csv')
    pd.DataFrame(coverage).to_csv(EXP/'coverage.csv',index=False)
    dump(EXP/'summary.json', dict(config=CONFIG,counts=frame.status.value_counts().to_dict(),
        distinct_symbols_with_hit=int(frame[frame.status=='hit'].symbol.nunique()),
        hourly_check=frame[frame.status=='possible'].hour_order_if_possible.value_counts().to_dict(),
        source_revision=subprocess.check_output(['git','log','-1','--format=%H','--',__file__],text=True).strip(),
        screenshot_eth_august=float(eth.peak_lower_r),screenshot_eth_october=float(oct_eth.peak_lower_r)))


if __name__=='__main__':
    main()
