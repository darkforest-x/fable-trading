"""Touch-candle recovery and weighted targets on native Nautilus fills.

Owner 2026-10-26: no initial stop; at the touch 12H close, reclaimed positions
get a commission-neutral stop, otherwise replace targets with recovery limits.
Three independent native legs implement the fixed 70/15/15 allocation. No
sibling target changes protection, so weighted native results are equivalent
to those partial exits before contract-quantity rounding and market impact.
Only current fills, completed closes and native path endpoints are observed.
"""
from decimal import Decimal
from types import FunctionType
import numpy as np
import pandas as pd
from yoyo.layers.l3_backtest import nautilus_independent as native
from yoyo.layers.l3_backtest.nautilus_reclaim_fee import _commission_neutral_stop

class TouchRecoveryStrategy(native.IndependentCloseStrategy):
    def observe(self,state,price):
        r=state.source;move=r['side']*(price-r['entry'])/r['entry']*100
        r['mae_fixed_r']=min(r.get('mae_fixed_r',0.),move)
        r['mfe_fixed_r']=max(r.get('mfe_fixed_r',0.),move)
        if move>0 and r['side']*(price-r['near_edge'])>=-self.eps:r['returned_to_range']=True

    def on_order_filled(self,event):
        state,phase,_=self.order_map[str(event.client_order_id)];r=state.source
        if phase=='entry':
            opened=self.frame.index[self.indices[event.ts_event]]-pd.Timedelta(minutes=self.execution_minutes)
            due=opened.floor(f"{r['reclaim_minutes']}min")+pd.Timedelta(minutes=r['reclaim_minutes'])
            r.update(decision_time=str(due),decision_ns=due.value,decision_status='pending',decision_i=-1,
                     mae_fixed_r=0.,mfe_fixed_r=0.,returned_to_range=False,exit_kind='',entry_path_phase=self.point_phases[event.ts_event][1])
            for name,rate in [('fee_stop',self.policy.fill_taker_fee),('fee_limit',self.policy.fill_maker_fee)]:
                r[name]=float(_commission_neutral_stop(event.last_px.as_decimal(),r['side'],
                    event.last_qty.as_decimal(),event.commission.as_decimal(),Decimal(str(rate)),Decimal(str(self.tick))))
            super().on_order_filled(event)
            # Disable the inherited next-candle rule; this adapter owns timing.
            state.reclaim_due_ns=due.value;state.reclaim_status='touch_pending'
            return
        self.observe(state,float(event.last_px))
        r['exit_kind']=('fee_recovery' if phase=='target' and r['decision_status'] in ('failed_wait','fee_band_wait') else
            'target' if phase=='target' else 'fee_stop' if phase==13 or phase=='stop' and state.stop_kind==1 else 'censored')
        super().on_order_filled(event)

    def on_bar(self,bar):
        i,phase=self.point_phases[bar.ts_event]
        for state in list(self.active.values()):self.observe(state,float(bar.close))
        if phase==3:
            close=float(bar.close)
            for state in list(self.active.values()):
                r=state.source
                if r['decision_status']!='pending' or self.frame.index[i].value<r['decision_ns'] or i>=r['end_i']:continue
                if self.frame.index[i].value!=r['decision_ns']:raise RuntimeError('Missing exact confirmation close')
                r['decision_i']=i;r['decision_close']=close;state.reclaim_i=i
                reclaimed=r['side']*(close-r['entry'])>self.eps;r['close_reclaimed']=reclaimed
                if reclaimed and r['side']*(close-r['fee_stop'])>=-self.eps:
                    r['decision_status']='reclaimed';state.reclaim_status='touch_reclaimed'
                    state.stop,state.stop_kind=r['fee_stop'],1;state.be_price=r['fee_stop']
                    state.be_trigger_i=state.be_effective_i=i
                    if abs(close-r['fee_stop'])<=self.eps:
                        self._cancel_target(state);self._market(state,reason=13)
                    else:self._submit_protective_stop(state)
                else:
                    r['decision_status']='fee_band_wait' if reclaimed else 'failed_wait'
                    state.reclaim_status=r['decision_status']
                    self._cancel_target(state);r['original_target_price']=r['target_price']
                    r['target_price']=r['fee_stop'] if reclaimed else r['fee_limit']
                    self._submit_target(state)
        super().on_bar(bar)

_globals=dict(native.run_native.__globals__);_globals['IndependentCloseStrategy']=TouchRecoveryStrategy
_run=FunctionType(native.run_native.__code__,_globals,native.run_native.__name__,native.run_native.__defaults__,native.run_native.__closure__)
_run.__kwdefaults__=dict(native.run_native.__kwdefaults__)

def run_touch_recovery(frame,entries,policy,**kwargs):
    if not policy.reclaim_next_bar or policy.reclaim_backstop or not policy.monotone_segments:
        raise ValueError('Requires native no-initial-stop reclaim and monotone paths')
    return _run(frame,np.full(len(frame),np.nan),np.zeros(len(frame)),entries,policy,entry_order_mode='limit',**kwargs)
