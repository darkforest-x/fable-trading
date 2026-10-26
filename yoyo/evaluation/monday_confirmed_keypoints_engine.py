"""Absolute Monday targets with the existing native VectorBT matching adapter.

Only target geometry changes from confirmed_r_engine. Entry, completed-close
BE timing and pin stop stay fixed. Size uses a declared planned loss budget,
including entry/stop taker fees and adverse stop slippage. Gaps may exceed it.
Native first_hit and Portfolio perform matching and accounting; no bar loop.
"""
import numpy as np
import pandas as pd
import vectorbt as vbt
from numba import njit
from yoyo.evaluation.monday_wick_r_engine import first_hit

TARGETS=['near_monday_edge','range_0.5','range_0.618','opposite_monday_edge','opposite_1618']


def planned_loss(entry,stop,side,taker=.0005,slip=.001):
    """USDT risk per base unit at known entry and an ungapped stop execution."""
    stop_fill=stop*(1-side*slip)
    return side*(entry-stop_fill)+taker*(entry+stop_fill)


@njit(cache=True)
def dispatch(prices,closes5,starts,ends,entries,stops,sides,targets,valid,taker):
    n=len(prices);nt=targets.shape[1]
    o=np.ascontiguousarray(prices[:,0:1]);h=np.ascontiguousarray(prices[:,1:2])
    l=np.ascontiguousarray(prices[:,2:3]);c=np.ascontiguousarray(prices[:,3:4])
    po=np.empty((n,1));ty=np.empty((n,1),dtype=np.int64);scratch=np.empty(1,dtype=np.int64)
    out=np.empty((len(entries)*nt*2,8));row=0
    for k in range(len(entries)):
        a,z=starts[k],ends[k];entry,stop,side=entries[k],stops[k],sides[k]
        be=entry*(1+side*taker)/(1-side*taker)
        eligible=np.flatnonzero(closes5[a:z+1]&(side*(c[a:z+1,0]-be)>1e-12*entry))
        arm=a+eligible[0] if len(eligible) else -1
        for j in range(nt):
            target=targets[k,j]
            for mode in range(2):
                if not valid[k,j]:
                    out[row,:]=np.array([k,j,mode,a,np.nan,-1,0,0]);row+=1;continue
                boundary=arm if mode==1 and arm>=0 else z
                i,price,why,amb=first_hit(o,h,l,c,a,boundary,entry,stop,target,side,po,ty,scratch)
                armed=0
                if not why and mode==1 and arm>=0:
                    armed=1
                    i,price,why,amb=first_hit(o,h,l,c,arm+1,z,c[arm,0],be,target,side,po,ty,scratch)
                    if why==1:why=5
                if not why:i,price=z,c[z,0]
                out[row,:]=np.array([k,j,mode,i,price,why,armed,amb]);row+=1
    return out


def execute(frame,events,targets,maker=.0002,taker=.0005,slip=.001,step=5,budget=100.):
    """One native execution sized to a fixed planned-USDT-risk per column.

Equal-notional statistics are linear rescalings of the same native PnL, not a
second backtest. Independent cash columns are not a funded account. Skipped
target opportunities have no simulated orders. Target order follows TARGETS.
"""
    if events.empty:return pd.DataFrame(),pd.DataFrame()
    ep=events.entry.to_numpy(float)[:,None];s=events.side.to_numpy(float)[:,None]
    geometry=np.isfinite(targets)&(targets>0)&(s*(targets-ep)>0)
    fee_space=s*(targets-ep)-ep*taker-targets*maker
    valid=geometry&(fee_space>1e-12*ep)
    p=frame[['open','high','low','close']].to_numpy(float);p=np.vstack((p[0],p))
    close_ns=frame.index.as_unit('ns').asi8+step*60_000_000_000
    raw=dispatch(p,np.r_[False,close_ns%300_000_000_000==0],events.entry_i.to_numpy(int)+1,
        events.end_i.to_numpy(int)+1,ep[:,0],events.stop.to_numpy(float),s[:,0],targets,valid,taker)
    event_ids=raw[:,0].astype(int);target_ids=raw[:,1].astype(int)
    e=events.iloc[event_ids].reset_index(drop=True).copy()
    e['target_name']=np.asarray(TARGETS)[target_ids];e['target_price']=targets[event_ids,target_ids]
    e['target_r']=e.side*(e.target_price-e.entry)/e.risk
    e['be_mode']=np.where(raw[:,2]==1,'fee_close','fixed_stop')
    e['exit_i']=raw[:,3].astype(int)-1;e['exit_quote']=raw[:,4];e['reason']=raw[:,5].astype(int)
    e['be_armed']=raw[:,6].astype(bool);e['ambiguous']=raw[:,7].astype(bool)
    e['entered']=e.reason.ge(0);e['closed']=e.reason.gt(0);e['resolved']=~e.entered|e.closed
    e['skip_reason']=np.where(e.entered,'',np.where(geometry[event_ids,target_ids],'insufficient_fee_space','target_passed_or_unreachable'))
    e['exit_price']=e.exit_quote*np.where(e.reason.eq(4),1.,1-e.side*slip)
    e['exit_ns']=np.where(e.entered,close_ns[e.exit_i.to_numpy(int)],e.entry_ns)
    e['planned_loss_unit']=planned_loss(e.entry,e.stop,e.side,taker,slip)
    assert e.planned_loss_unit.gt(0).all()
    e['quantity']=np.where(e.entered,budget/e.planned_loss_unit,0.)
    e['notional']=e.quantity*e.entry;e['budget_usdt']=budget
    e['planned_loss_pct']=100*e.planned_loss_unit/e.entry
    e['net_bp']=0.;e['net_r']=0.;e['budget_r']=0.;e['pnl_usdt']=0.;e['fee_bp']=0.
    ids=np.flatnonzero(e.entered);c=e.iloc[ids]
    if len(c):
        prices=np.vstack((c.entry,c.exit_price));qty=c.quantity.to_numpy(float);side=c.side.to_numpy(float)
        fees=np.vstack((np.full(len(c),taker),np.where(c.reason.eq(4),maker,taker)))
        pf=vbt.Portfolio.from_orders(prices,size=np.vstack((side*qty,-side*qty)),price=prices,fees=fees,
            size_type='amount',direction='both',init_cash=np.max(prices,axis=0)*qty*4,cash_sharing=False,
            allow_partial=False,raise_reject=True,max_orders=2*len(c))
        rec=pf.trades.records.sort_values('col');assert len(rec)==len(c) and rec.status.eq(1).all()
        pnl=rec.pnl.to_numpy();notional=c.notional.to_numpy()
        e.loc[ids,'pnl_usdt']=pnl;e.loc[ids,'budget_r']=pnl/budget
        e.loc[ids,'net_bp']=pnl/notional*10000;e.loc[ids,'net_r']=pnl/(qty*c.risk.to_numpy())
        e.loc[ids,'fee_bp']=(rec.entry_fees.to_numpy()+rec.exit_fees.to_numpy())/notional*10000
        fills=pf.orders.records.copy();fills['trade_row']=ids[fills.col.to_numpy(int)]
        assert len(fills)==2*len(c)
    else:fills=pd.DataFrame()
    e['boundary_mark_budget_r']=e.budget_r.where(e.entered&~e.closed)
    e['boundary_mark_bp']=e.net_bp.where(e.entered&~e.closed)
    e.loc[e.entered&~e.closed,['net_bp','net_r','budget_r','pnl_usdt']]=np.nan
    return e,fills
