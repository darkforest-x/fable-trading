"""Order the protective trade stop and native mark/trade boundary during flagged H1s."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from yoyo.evaluation import cloud_close_waves_minute_risk as source

base = source.repair.old.exposure
minute = source.minute
wallet = base.wallet
EXP = source.EXP.parent/'exp-cloud-close-margin-before-stop-20261109-v1'
CACHE = wallet.ROOT/'data/research/cloud_close_margin_before_stop'
FIELDS = ['open','high','low','close']


def mark_bars(sym,hour):
    endpoint = 'https://www.okx.com/api/v5/market/history-mark-price-candles'
    cache = CACHE/sym/f'{hour}.json'
    cache.parent.mkdir(parents=True,exist_ok=True)
    if not cache.is_file():
        result = requests.get(endpoint,params={'instId':sym+'-USDT-SWAP','bar':'1m','after':hour+wallet.HOUR,'limit':60},timeout=(8,25))
        result.raise_for_status()
        body = result.json()
        if body.get('code') not in ['0','51001']:
            raise ValueError(f'Failed native mark query: {sym} {hour}')
        tmp = cache.with_suffix('.tmp');tmp.write_text(json.dumps(body));tmp.replace(cache)
    raw = json.loads(cache.read_text())['data']
    frame = pd.DataFrame(raw,columns=['t',*FIELDS,'confirmed']).apply(pd.to_numeric).set_index('t').sort_index()
    if not np.array_equal(frame.index,hour+np.arange(60)*60000) or not frame.confirmed.eq(1).all():
        return None
    if (not np.isfinite(frame[FIELDS]).all().all() or not frame.low.gt(0).all() or
        not frame.high.ge(frame[['open','close']].max(axis=1)).all() or
        not frame.low.le(frame[['open','close']].min(axis=1)).all()):
        raise ValueError('Malformed official minute marks')
    return frame


def first_touch(bars,side,level):
    return np.flatnonzero((bars.low.to_numpy()<=level) if side>0 else (bars.high.to_numpy()>=level))


def inspect(r,ratio,slip,quotes,*,trade_loader=minute.minute_quote,mark_loader=mark_bars):
    clock,ohlc = quotes(r.symbol)
    first,last = clock.searchsorted([r.entry_ms,r.exit_bar_ms])
    sample = ohlc[first:last+1]
    adverse = sample[:,2 if r.side>0 else 1].copy()
    if r.exit_phase=='open':
        adverse[-1] = sample[-1,0]
    threshold = base.study.leverage.liquidation_threshold(r.side,ratio,slip,base.study.leverage.MMR)*r.entry_price
    crossed = np.flatnonzero((adverse<=threshold) if r.side>0 else (adverse>=threshold))
    if not len(crossed):
        raise ValueError('Flagged parent has no native adverse H1 crossing')
    records = []
    for i in crossed:
        hour = int(clock[first+i])
        item = dict(event_id=r.event_id,symbol=r.symbol,side=r.side,multiple=ratio,market_slip=slip,
                    entry_price=r.entry_price,exit_price=r.exit_price,threshold=threshold,hour=hour,
                    exit_hour=bool(hour==r.exit_bar_ms),exit_reason=r.exit_reason,phase=r.exit_phase)
        if not (hour==r.exit_bar_ms and r.exit_reason=='stop' and r.exit_phase=='intrabar'):
            records.append(dict(item,status='boundary_while_held_or_non_stop_exit'))
            continue
        trades,url = trade_loader(r.symbol,hour,quotes)
        marks = mark_loader(r.symbol,hour)
        if trades is None or marks is None:
            records.append(dict(item,status='missing_trade_or_mark_minutes',trade_source=url))
            continue
        stop = first_touch(trades,r.side,r.exit_price)
        tb = first_touch(trades,r.side,threshold)
        mb = first_touch(marks,r.side,threshold)
        if not len(stop) or not len(tb):
            raise ValueError('H1 low/high crossing absent from reconciled trade minutes')
        i_stop = int(stop[0])
        prev = trades.open.iat[0] if i_stop==0 else trades.close.iat[i_stop-1]
        open_px = trades.open.iat[i_stop]
        nominal_gap = (open_px-r.exit_price)*r.side < -1.e-10
        first_mark = int(mb[0]) if len(mb) else 60
        early = i_stop < min(int(tb[0]),first_mark)
        status = ('stop_fill_gap_needs_repricing' if nominal_gap else
                  'stop_strictly_earlier' if early else 'same_or_earlier_boundary_minute')
        records.append(dict(item,status=status,trade_source=url,stop_minute=i_stop,
                    trade_boundary_minute=int(tb[0]),mark_boundary_minute=first_mark if len(mb) else None,
                    preceding_trade_close=prev,stop_minute_open=open_px,stop_minute_trade_low=trades.low.iat[i_stop],
                    stop_minute_mark_low=marks.low.iat[i_stop],stop_minute_trade_high=trades.high.iat[i_stop],
                    stop_minute_mark_high=marks.high.iat[i_stop],full_hour_mark_low=marks.low.min(),full_hour_mark_high=marks.high.max()))
    return records


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed 2x/3x historical boundary-source diagnostic')
    tape,quotes,_ = source.inputs()
    runners = tape[tape.tp_r.eq(0)].reset_index(drop=True)
    info = {}
    records = []
    for ratio in [2.,3.]:
        for slip in [0.,.0005]:
            table,audit = base.study.leverage._margin_audit(runners,ratio,slip,quote_loader=quotes)
            cases = []
            for r in table[table.maintenance_boundary_hit].itertuples():
                cases.extend(inspect(r,ratio,slip,quotes))
            records.extend(cases)
            closed = pd.DataFrame(cases).groupby('event_id').status.apply(lambda x:x.eq('stop_strictly_earlier').all())
            info[f'{ratio:g}x'+('_stress' if slip else '')] = dict(h1_flagged=audit['maintenance_boundary_hits'],
                minute_resolved=int(closed.sum()),unresolved_ids=closed.index[~closed].tolist(),
                status_counts=pd.Series([r['status'] for r in cases]).value_counts().to_dict())
            print(ratio,slip,info[f'{ratio:g}x'+('_stress' if slip else '')],flush=True)
    pd.DataFrame(records).to_csv(EXP/'flagged_hours.csv',index=False)
    source.repair.archive.prices.safe_json(EXP/'summary.json',dict(cases=info,scope='Native traded-price H1 flags checked against native minute marks; historical account-tier and previous unflagged-hour mark risk remain open.'))


if __name__=='__main__':
    main()
