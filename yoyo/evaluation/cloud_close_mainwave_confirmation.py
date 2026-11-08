"""Gate actual 12H long entries on an earlier independently confirmed Bit浪浪 setup."""
import gc
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_binance_gap_repair as repaired
from yoyo.evaluation import cloud_close_binance_transfer as venue
from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_scaleout as scaleout
from yoyo.evaluation import signal_center_score as scoring


EXP = wallet.ROOT/'experiments/active/exp-cloud-close-mainwave-confirmation-20261109-v1'
TEACHER = EXP.parent/'exp-bitlanglang-mainwave-20261011-v1'
FIVE = 300_000
DAY = 24*wallet.HOUR


def filter_entries(paths):
    """Use observable trigger closes, breakout day, box bottom and as-of five-minute price history."""
    cols = ['symbol','breakout_day','time','skipped','box_bottom','entry']
    triggers = pd.read_csv(TEACHER/'results_v1/trades.csv.gz',usecols=cols,keep_default_na=False)
    triggers = triggers[triggers.skipped.eq('')].copy()
    triggers['known_ms'] = pd.to_datetime(triggers.time,utc=True).array.as_unit('ms').asi8+FIVE
    triggers['symbol'] = triggers.symbol.str.removesuffix('USDT')
    triggers['until_ms'] = triggers.breakout_day.astype('int64')+11*DAY
    prices = {j['symbol'].removesuffix('USDT'):j for j in venue.jobs() if j['group']!='spot_gold'}
    candidates = paths[paths.tp_r.eq(0)]
    selected, overlap = set(), set()
    rejected_windows = dict(missing_or_different_trigger_open=0, missing_5m_to_decision=0,
                            broken_box=0)
    for asset, source in triggers.groupby('symbol'):
        group = candidates[candidates.symbol.eq(asset)]
        if group.empty:
            continue
        matches = []
        for row in source.itertuples():
            qualifying = group[group.entry_ms.ge(row.known_ms)&group.entry_ms.lt(row.until_ms)]
            if not qualifying.empty:
                matches.append((row,qualifying))
        if not matches:
            continue
        bars = scoring.five_minute(prices[asset])
        stamp = bars.index.as_unit('ms').asi8
        close, opening = bars.close.to_numpy(),bars.open.to_numpy()
        for row, qualifying in matches:
            overlap.update(qualifying.event_id)
            start = stamp.searchsorted(row.known_ms)
            if (start==0 or start>=len(stamp) or stamp[start-1]!=row.known_ms-FIVE
                    or stamp[start]!=row.known_ms or not np.isclose(opening[start],float(row.entry),rtol=1e-9,atol=1e-13)):
                rejected_windows['missing_or_different_trigger_open'] += len(qualifying)
                continue
            for event in qualifying.itertuples():
                end = stamp.searchsorted(event.entry_ms)
                if end>=len(stamp) or stamp[end]!=event.entry_ms or (end-start)*FIVE!=event.entry_ms-row.known_ms:
                    rejected_windows['missing_5m_to_decision'] += 1
                    continue
                if np.all(close[start:end]>=float(row.box_bottom)):
                    selected.add(event.event_id)
                else:
                    rejected_windows['broken_box'] += 1
    return selected, len(overlap), rejected_windows


def account(pool,start,slip=0.):
    data = scaleout.pair_data(pool[pool.tp_r.eq(0)],pool[pool.tp_r.eq(2)],9,start=start,end=venue.END,
                              quote_loader=repaired.quotes)
    metrics, positions, equity = scaleout.simulate(data,.75,slip)
    return metrics,positions,equity,data['gap_marks']


def main():
    if not (repaired.EXP/'summary.json').is_file():
        raise ValueError('Complete the Binance 2022 data repair before evaluating the overlay')
    history = json.loads((TEACHER/'config.json').read_text())
    start,end = [pd.Timestamp(history[x]).value//1_000_000 for x in ['start','end']]
    paths = repaired.assembled(set(repaired.patch.affected_tickers()))
    paths = paths[paths.side.eq(1)&paths.entry_ms.ge(start)&paths.entry_ms.lt(end)]
    real = paths[~paths.is_control]
    selected, overlap, rejected = filter_entries(paths)
    screened = real[real.event_id.isin(selected)]
    control = paths[paths.is_control & paths.event_id.isin(selected) & paths.parent_id.isin(screened.parent_id)]
    matched = screened[screened.parent_id.isin(control.parent_id)]
    rows,gaps = [],{}
    for name, events, slip in [('all_longs',real,0.),('screened',screened,0.),('matched_signals',matched,0.),
                               ('controls',control,0.),('stress',screened,.0005)]:
        if events.empty:
            continue
        metrics,trades,equity,gaps[name] = account(events,start,slip)
        rows.extend(dict(policy=name,period=p,**m) for p,m in metrics.items())
        if name=='screened':
            trades.to_csv(EXP/'funded.csv.gz')
            equity.to_csv(EXP/'hourly.csv.gz',index_label='hour_end_utc')
        gc.collect()
    result = pd.DataFrame(rows)
    result.to_csv(EXP/'account_metrics.csv',index=False)
    (EXP/'summary.json').write_text(json.dumps(dict(preliminary_overlaps_real_and_control=overlap,
                  rejected_trigger_event_pairs=rejected, signals=len(real)//2, screened=len(screened)//2, pairs=len(control)//2,
                  unquoted_position_leg_hours=gaps),indent=2)+'\n')
    print(result[result.period.eq('all')].to_string(index=False))


if __name__=='__main__':
    main()
