"""Adaptive exits in independent long-only VectorBT execution columns.

Entry is an observed open. The protective order and any limit are live on that
same bar after entry. ATR/EMA inputs use fully closed 1h/4h candles; trailing
orders can tighten only using 5m highs already closed before the execution bar.
If both a stop and a target trade in a bar, the protective stop executes first.
On a gap the market stop pays the worse open; limits receive the stated price.
"""
from dataclasses import asdict, dataclass
import numpy as np
import pandas as pd
import vectorbt as vbt
from numba import njit
from vectorbt.portfolio import nb
from vectorbt.portfolio.enums import NoOrder

TAKER=.0005
MAKER=.0002
KINDS=('time','percent_trail','atr_1h','atr_4h','profit_lock','trend_1h','trend_4h','btc_net_10000')


@dataclass(frozen=True)
class Policy:
    kind:str
    hours:int
    stop_pct:float
    activate_pct:float=0
    width:float=0
    take_pct:float=0
    take_fraction:float=0

    @property
    def key(self):
        return '_'.join(str(x) for x in asdict(self).values())

    def numeric(self):
        return (KINDS.index(self.kind),self.hours*12,self.stop_pct/100,self.activate_pct/100,
                self.width,self.take_pct/100,self.take_fraction)


def policies(btc=False):
    p=[]
    for h in (72,144,240):
        for sl in (0,3,6,12,20):
            p.append(Policy('time',h,sl))
    for sl in (6,12):
        for target in (5,10,20):
            p.append(Policy('time',144,sl,take_pct=target,take_fraction=1))
        for arm in (0,5,10,15):
            for width in (2,4,8,15):
                p.append(Policy('percent_trail',144,sl,arm,width))
        for kind in ('atr_1h','atr_4h'):
            for arm in (0,5,10):
                for width in (2,4,6):
                    p.append(Policy(kind,144,sl,arm,width))
        for arm in (5,10,15):
            for lock in (.5,.75,.90):
                p.append(Policy('profit_lock',144,sl,arm,lock))
        for kind in ('trend_1h','trend_4h'):
            for arm in (0,5,10):
                for ema in (20,50):
                    for horizon in (144,240):
                        p.append(Policy(kind,horizon,sl,arm,ema))
        for kind,arm,width in [('atr_4h',5,4),('percent_trail',8,8),('trend_4h',5,20),('time',0,0)]:
            for target in (5,10):
                for frac in (.25,.5):
                    p.append(Policy(kind,144,sl,arm,width,target,frac))
    if btc:
        for sl in (6,12):
            p.append(Policy('btc_net_10000',144,sl,take_fraction=1))
    return p


def prepare(frame):
    """Observed 5m OHLC, plus as-of-open Wilder ATR and EMA of prior completed higher candles."""
    data=frame[['open','high','low','close']].sort_index().asfreq('5min')
    cols=['open','high','low','close']
    for hours in (1,4):
        group=data[cols].resample(f'{hours}h',closed='left',label='right')
        ohlc=group.agg({'open':'first','high':'max','low':'min','close':'last'})
        complete=group.count().eq(hours*12).all(axis=1)
        ohlc.loc[~complete,:]=np.nan
        prev_close=ohlc.close.shift(1)
        true_range=pd.concat([ohlc.high-ohlc.low,(ohlc.high-prev_close).abs(),(ohlc.low-prev_close).abs()],axis=1).max(axis=1)
        # Restart warmup on missing higher candles.
        block=(~complete).cumsum()
        atr=true_range.groupby(block).transform(lambda a:a.ewm(alpha=1/14,adjust=False,min_periods=14).mean())
        defs={'atr':atr}
        for n in (20,50):
            ema=ohlc.close.groupby(block).transform(lambda a:a.ewm(span=n,adjust=False,min_periods=n).mean())
            defs['under'+str(n)]=(ohlc.close<ema).astype(float).where(ema.notna()&complete)
        for name,s in defs.items():
            key=f'{name}_{hours}h'
            data[key]=s.reindex(data.index,method='ffill')
            cols.append(key)
    return data


@njit(cache=True)
def execute_order(c,q,starts,settings,can_trade,highwater,protection,sold_part,gap_partial,why,ambiguous,fee_in,fee_out,fee_limit):
    col=c.from_col
    event=col//len(settings);policy=col%len(settings); bar=c.i
    if not can_trade[event,policy]:return -1,NoOrder
    s=settings[policy];kind=int(s[0]);deadline=int(s[1])
    pos=starts[event]+bar
    entry=q[starts[event],0]
    if bar>deadline or pos>=len(q):return -1,NoOrder
    if bar==0 and c.call_idx==0:
        highwater[col]=entry
        protection[col]=entry*(1-s[2]) if s[2]>0 else 0.
        return col,nb.order_nb(size=1.,price=entry,fees=fee_in,direction=0,raise_reject=True)
    base_call=1 if bar==0 else 0
    residual=c.call_idx==base_call+1 and gap_partial[col]==bar
    if (c.call_idx!=base_call and not residual) or c.last_position[col]<=0:return -1,NoOrder
    qty=c.last_position[col];op=q[pos,0];hi=q[pos,1];lo=q[pos,2]
    if bar==deadline:
        why[col]=1
        return col,nb.order_nb(size=-qty,price=op,fees=fee_out,direction=0,raise_reject=True)
    peak=highwater[col]
    if bar:
        peak=max(peak,q[pos-1,1]);highwater[col]=peak
    armed=peak>=entry*(1+s[3]) and bar>0
    level=protection[col]
    if armed:
        if kind==1:level=max(level,peak*(1-s[4]/100))
        elif kind==2 or kind==3:
            atr=q[pos,4 if kind==2 else 7]
            if np.isfinite(atr):level=max(level,peak-s[4]*atr)
        elif kind==4:level=max(level,entry+(peak-entry)*s[4])
    protection[col]=level
    target=entry*(1+s[5]) if s[5]>0 else np.inf
    if kind==7:
        # Aim for 10,000 USDT after both fees on one BTC.
        target=(entry*(1+fee_in)+10000)/(1-fee_limit)
    hit_limit=not sold_part[col] and hi>=target
    if hit_limit and op>=target:
        sold_part[col]=True;why[col]=3
        if s[6]<qty:gap_partial[col]=bar
        return col,nb.order_nb(size=-min(qty,s[6]),price=target,fees=fee_limit,direction=0,raise_reject=True)
    if armed and (kind==5 or kind==6):
        ix=(5 if kind==5 else 8)+(1 if s[4]==50 else 0)
        if q[pos,ix]==1:
            why[col]=4
            return col,nb.order_nb(size=-qty,price=op,fees=fee_out,direction=0,raise_reject=True)
    if level>0 and lo<=level:
        if hit_limit and op<target and op>level:ambiguous[col]=1
        why[col]=2
        return col,nb.order_nb(size=-qty,price=min(op,level),fees=fee_out,direction=0,raise_reject=True)
    if hit_limit:
        sold_part[col]=True;why[col]=3
        return col,nb.order_nb(size=-min(qty,s[6]),price=target,fees=fee_limit,direction=0,raise_reject=True)
    return -1,NoOrder


def simulate(frame,entry_times,policy_list,*,batch_size=8):
    """Simulate each supplied entry and policy once, returning actual weighted fill PnL and timing."""
    if not frame.index.is_unique or not frame.index.is_monotonic_increasing:raise ValueError('Invalid quote clock')
    times=pd.DatetimeIndex(entry_times)
    positions=frame.index.get_indexer(times)
    q=np.ascontiguousarray(frame.to_numpy(dtype=float))
    settings=np.asarray([p.numeric() for p in policy_list])
    horizons=settings[:,1].astype(int)
    n_policy=len(policy_list);max_bars=horizons.max()
    bad=~np.isfinite(q[:,:4]).all(axis=1)
    counts=np.r_[0,np.cumsum(bad)]
    result=[]
    for begin in range(0,len(positions),batch_size):
        starts=positions[begin:begin+batch_size]
        ends=starts[:,None]+horizons
        last=np.clip(ends,0,len(q)-1)
        safe=np.clip(starts,0,len(q)-1)
        allow=(starts[:,None]>=0)&(ends<len(q))&(counts[last]-counts[safe,None]==0)&np.isfinite(q[last,0])
        # Require each indicator applicable to the policy to have warmed up before entry.
        for j,p in enumerate(policy_list):
            idx={'atr_1h':4,'atr_4h':7,'trend_1h':5+int(p.width==50),'trend_4h':8+int(p.width==50)}.get(p.kind)
            if idx is not None:allow[:,j]&=np.isfinite(q[safe,idx])
        if not allow.any():continue
        col_count=len(starts)*n_policy
        price=q[np.clip(safe[None,:]+np.arange(max_bars+1)[:,None],0,len(q)-1),3]
        # Reference prices mark each independent column; all actual executions have explicit OHLC-based prices.
        marked=np.repeat(price,n_policy,axis=1)
        capital=np.repeat(np.where(np.isfinite(q[safe,0]),q[safe,0]*2,1.),n_policy)
        why=np.zeros(col_count,dtype=np.int8);ambig=np.zeros_like(why)
        portfolio=vbt.Portfolio.from_order_func(marked,execute_order,q,safe,settings,allow,
            np.zeros(col_count),np.zeros(col_count),np.zeros(col_count,np.bool_),np.full(col_count,-1),why,ambig,TAKER,TAKER,MAKER,
            flexible=True,init_cash=capital,cash_sharing=False,max_orders=3*col_count,freq='5min')
        fills=portfolio.orders.records
        opened=fills[fills.side==0].set_index('col')
        sales=fills[fills.side==1].copy()
        sales['value']=sales['size']*sales.price
        aggregated=sales.groupby('col').agg(value=('value','sum'),size=('size','sum'),fees=('fees','sum'),
                                              last_bar=('idx','max'),legs=('idx','size'))
        expected=np.flatnonzero(allow.ravel())
        if not np.array_equal(opened.index.to_numpy(),expected) or not np.array_equal(aggregated.index.to_numpy(),expected):
            raise ValueError('Incomplete entry/exit paths')
        if not np.allclose(aggregated['size'],1) or not opened.idx.eq(0).all():raise ValueError('Incomplete liquidation')
        buy=opened.price.to_numpy(); vwap=aggregated.value.to_numpy()
        net=vwap-buy-opened.fees.to_numpy()-aggregated.fees.to_numpy()
        sig=expected//n_policy;pid=expected%n_policy
        first=sales.groupby('col').idx.min().to_numpy()
        records=pd.DataFrame(dict(entry_time=times[begin+sig],policy_id=pid,
            exit_time=frame.index[safe[sig]+aggregated.last_bar.to_numpy()],entry_open=buy,exit_vwap=vwap,
            net_points=net,net_pct=100*net/buy,exit_legs=aggregated.legs.to_numpy(),
            first_exit_time=frame.index[safe[sig]+first],reason=why[expected],ambiguous=ambig[expected]))
        # One observable peak over the same prespecified opportunity horizon even if the policy leaves early.
        for h in np.unique(horizons):
            rows=records.index[horizons[pid]==h]
            if len(rows):
                ids=sig[rows]
                observed={i:max(np.max(q[safe[i]:safe[i]+h,1]),q[safe[i]+h,0]) for i in np.unique(ids)}
                records.loc[rows,'peak_pct']=100*(np.array([observed[i] for i in ids])/buy[rows]-1)
        result.append(records)
    return pd.concat(result,ignore_index=True) if result else pd.DataFrame()
