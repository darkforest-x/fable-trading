"""Bound favorable *post-fill* prices using frozen OHLC and native entry events.

Chart lower/upper bounds use the favorable extreme of complete held bars.
A retest can occur after the favorable wick of the first minute. Only its close
is then certain; an exit minute only guarantees its open. Refinement uses actual
1m in the first 12h, complete 1H in the terminal 12h when available, and the
chart's original 12H OHLC otherwise. No OHLC extrema decide order fills here.
"""
from functools import lru_cache
import numpy as np
import pandas as pd

from yoyo.evaluation.cloud_retest_cases import EXP, SOURCE, STEP, MINUTE

HOUR=60*MINUTE
OHLC=['open','high','low','close']

@lru_cache(maxsize=4)
def symbol_bars(symbol):
    folder=SOURCE/'inputs'/symbol
    return tuple(pd.read_csv(folder/file,index_col='time',float_precision='round_trip')[OHLC]
                 for file in ('bars12h.csv.gz','bars1h.csv.gz'))

@lru_cache(maxsize=2)
def window_minutes(symbol,t):
    path=EXP/'inputs'/symbol/'minutes'/f'{t}.csv.gz'
    if not path.exists():
        return None
    bars=pd.read_csv(path,index_col='time',float_precision='round_trip')[OHLC]
    if np.array_equal(bars.index.to_numpy(),np.arange(t,t+STEP,MINUTE)):
        return bars
    return None

def fine_block(series,t,step):
    if series is None:
        return None
    block=series.loc[t:t+STEP-1]
    if np.array_equal(block.index,np.arange(t,t+STEP,step)):
        return block
    return None

def first_exit(block,r,original_open):
    """The fixed stop is inclusive, the EMA crossing is strict; a gap fills at the open."""
    adverse='low' if r.side==1 else 'high'
    trigger=r.chart_exit_price
    if np.isclose(original_open,trigger,rtol=2e-13,atol=0):
        return int(block.index[0]),True
    eps=abs(trigger)*1e-13
    def crossed(series):
        v=(series-trigger)*r.side
        return v<=eps if r.exit_reason=='stop' else v < -eps
    matches=block[crossed(block[adverse])|crossed(block.open)]
    if matches.empty:
        return None,False
    return int(matches.index[0]),bool(crossed(matches.open.iloc[0]))

def chart_path(case,entry_ms,price,market_entry):
    """entry_ms is the actual fill minute's UTC OPEN; market_entry fills at its open."""
    b,h=symbol_bars(case.symbol)
    t=int(case.confirm_ms)
    ix=b.index.get_indexer([t])[0]
    if ix<0:
        return None
    closed=case.exit_reason in ('stop','cloud_flip')
    if closed:
        end=int(case.chart_exit_ms)
    else:
        future_times=b.index[ix:].to_numpy()
        gaps=np.flatnonzero(np.diff(future_times)!=STEP)
        end=int(future_times[gaps[0]] if len(gaps) else future_times[-1])
    risk=(price-case.reference_stop)*case.side
    if risk<=0:
        return None
    lower=upper=0.
    exit_left=exit_right=None
    terminal_basis=''
    fav='high' if case.side==1 else 'low'
    def best(vals):
        if len(vals)==0:
            return 0.
        best_price=vals.max() if case.side==1 else vals.min()
        return max(0.,float((best_price-price)*case.side/risk))
    def value(p):
        return max(0.,float((p-price)*case.side/risk))

    interior=b.loc[t+STEP:end-1] if closed else b.loc[t+STEP:end]
    lower=upper=best(interior[fav])
    special=sorted({t,end} if closed else {t})
    for opening in special:
        terminal=closed and opening==end
        minute_block=window_minutes(case.symbol,t) if opening==t else None
        hour_block=fine_block(h,opening,HOUR)
        levels=[(minute_block,MINUTE),(hour_block,HOUR),(b.loc[[opening]],STEP)]
        for block,step in levels:
            if block is None:
                continue
            if terminal:
                ex,at_open=first_exit(block,case,b.at[opening,'open'])
                if ex is None:
                    if step<STEP:
                        continue
                    ex=opening
                exit_left=ex
                exit_right=ex if at_open else ex+step
                terminal_basis={MINUTE:'1m',HOUR:'1h',STEP:'12h'}[step]
            break
        if terminal and exit_right < entry_ms:
            return None
        left=(entry_ms-opening)//step*step+opening if opening==t else opening
        first_intrabar=(opening==t and not market_entry)
        if first_intrabar and left in block.index:
            first_intrabar=(step>MINUTE or (block.at[left,'open']-price)*case.side>abs(price)*1e-13)
        right=exit_left if terminal else opening+STEP
        # Entirely held candles. Entry/exit boundary candles handled separately.
        held=block.loc[left+(step if first_intrabar else 0):right-1]
        low=hi=best(held[fav])
        if first_intrabar and left<right:
            low=max(low,value(block.at[left,'close']))
            hi=max(hi,value(block.at[left,fav]))
        if terminal and left<=right:
            if not (left==right and first_intrabar):
                low=max(low,value(block.at[right,'open']))
            if not at_open:
                hi=max(hi,value(block.at[right,fav]))
        lower=max(lower,low)
        upper=max(upper,hi,lower)
    if not closed and end+STEP < entry_ms+24*HOUR:
        early='unknown'
    elif case.exit_reason!='stop':
        early='no'
    else:
        first_day=entry_ms+24*HOUR
        # A 1m limit fill is within its minute; exact minute-boundary gaps are
        # carried as ambiguous unless the whole stop interval is on one side.
        entry_right=entry_ms if market_entry else entry_ms+MINUTE
        if exit_right < first_day or (exit_right==first_day and exit_right>exit_left):
            early='yes'
        elif exit_left>=entry_right+24*HOUR:
            early='no'
        else:
            early='unknown'
    status='hit' if lower>10 else ('possible' if upper>10 else ('miss' if closed else 'censored'))
    return {'peak_lower_r':lower,'peak_upper_r':upper,'postfill_10r':status,'early_stop':early,
            'terminal_resolution':terminal_basis,'terminal_start_ms':exit_left,'terminal_end_ms':exit_right}
