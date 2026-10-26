"""VectorBT static risk and optional fee protection after confirmed market entry.

Reuse the audited native first_hit adapter. Entry is the next observed open
with separately recorded10bp adverse execution slippage. Stop is the known
touch wick; R is actual simulated entry minus this stop. No close-confirmation
event from before entry can activate breakeven on a new position.
"""
import numpy as np
import pandas as pd
import vectorbt as vbt
from numba import njit
from yoyo.evaluation.monday_wick_r_engine import first_hit


@njit(cache=True)
def dispatch(prices,closes5,starts,ends,entries,stops,sides,targets,maker,taker):
    n=len(prices)
    o=np.ascontiguousarray(prices[:,0:1]);h=np.ascontiguousarray(prices[:,1:2])
    l=np.ascontiguousarray(prices[:,2:3]);c=np.ascontiguousarray(prices[:,3:4])
    po=np.empty((n,1));ty=np.empty((n,1),dtype=np.int64);scratch=np.empty(1,dtype=np.int64)
    out=np.empty((len(entries)*len(targets)*2,8));row=0
    for k in range(len(entries)):
        a,z=starts[k],ends[k];entry,stop,side=entries[k],stops[k],sides[k]
        r=side*(entry-stop);be=entry*(1+side*taker)/(1-side*taker)
        eligible=np.flatnonzero(closes5[a:z+1]&(side*(c[a:z+1,0]-be)>1e-12*entry))
        arm=a+eligible[0] if len(eligible) else -1
        for tp in targets:
            target=entry+side*tp*r
            if target<=0:target=np.nan
            for mode in range(2):
                boundary=arm if mode==1 and arm>=0 else z
                i,price,why,amb=first_hit(o,h,l,c,a,boundary,entry,stop,target,side,po,ty,scratch)
                armed=0
                if not why and mode==1 and arm>=0:
                    armed=1
                    i,price,why,amb=first_hit(o,h,l,c,arm+1,z,c[arm,0],be,target,side,po,ty,scratch)
                    if why==1:why=5
                if not why:i,price=z,c[z,0]
                out[row,:]=np.array([k,tp,mode,i,price,why,armed,amb]);row+=1
    return out


def execute(frame,events,targets,maker=.0002,taker=.0005,slip=.001,step=5):
    if events.empty:return pd.DataFrame(),pd.DataFrame()
    p=frame[['open','high','low','close']].to_numpy(float);p=np.vstack((p[0],p))
    close_ns=frame.index.as_unit('ns').asi8+step*60_000_000_000
    fives=np.r_[False,close_ns%300_000_000_000==0]
    raw=dispatch(p,fives,events.entry_i.to_numpy(int)+1,events.end_i.to_numpy(int)+1,
        events.entry.to_numpy(float),events.stop.to_numpy(float),events.side.to_numpy(float),np.asarray(targets,float),maker,taker)
    e=events.iloc[raw[:,0].astype(int)].reset_index(drop=True).copy()
    e['target_r']=raw[:,1].astype(int);e['be_mode']=np.where(raw[:,2]==1,'fee_close','fixed_stop')
    e['exit_i']=raw[:,3].astype(int)-1;e['exit_quote']=raw[:,4];e['reason']=raw[:,5].astype(int)
    e['be_armed']=raw[:,6].astype(bool);e['ambiguous']=raw[:,7].astype(bool)
    e['exit_price']=e.exit_quote*np.where(e.reason.eq(4),1.,1-e.side*slip)
    e['exit_ns']=close_ns[e.exit_i.to_numpy(int)];e['closed']=e.reason.ne(0)
    prices=np.vstack((e.entry,e.exit_price));s=e.side.to_numpy(float)
    fees=np.vstack((np.full(len(e),taker),np.where(e.reason.eq(4),maker,taker)))
    pf=vbt.Portfolio.from_orders(prices,size=np.vstack((s,-s)),price=prices,fees=fees,
        size_type='amount',direction='both',init_cash=np.max(prices,axis=0)*4,cash_sharing=False,
        allow_partial=False,raise_reject=True,max_orders=2*len(e))
    rec=pf.trades.records.sort_values('col');assert len(rec)==len(e) and rec.status.eq(1).all()
    e['fee_bp']=(rec.entry_fees.to_numpy()+rec.exit_fees.to_numpy())/e.entry*10000
    e['net_bp']=rec.pnl.to_numpy()/e.entry*10000;e['net_r']=rec.pnl.to_numpy()/e.risk
    e['gross_r']=(rec.pnl.to_numpy()+rec.entry_fees.to_numpy()+rec.exit_fees.to_numpy())/e.risk
    e['entry_slip_bp']=e.side*(e.entry-e.entry_quote)/e.entry*10000
    e['exit_slip_bp']=e.side*(e.exit_quote-e.exit_price)/e.entry*10000
    e['net_ex_slip_cost_bp']=e.net_bp+e.entry_slip_bp+e.exit_slip_bp
    e['boundary_mark_bp']=e.net_bp.where(~e.closed)
    e.loc[~e.closed,['net_bp','net_r','gross_r','net_ex_slip_cost_bp']]=np.nan
    fills=pf.orders.records.copy()
    assert len(fills)==2*len(e)
    return e,fills
