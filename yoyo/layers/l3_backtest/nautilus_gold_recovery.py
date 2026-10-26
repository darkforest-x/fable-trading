"""Gold recovery management on the existing native Nautilus execution engine.

Owner 2026-10-26: touch-candle 1H/4H close, 1% initial backstop, far Monday
target. A reclaimed close beyond fee breakeven installs a STOP_MARKET; failed
reclaim replaces the target with a fee-neutral resting LIMIT while retaining
the initial stop. A reclaimed close inside the fee band also waits at a limit:
an already-crossed protective stop cannot promise fee-inclusive breakeven.
Only current native fills, segment endpoints and completed closes are read.
"""
from decimal import Decimal
from types import FunctionType

import pandas as pd

from yoyo.layers.l3_backtest import nautilus_independent as native
from yoyo.layers.l3_backtest.nautilus_reclaim_fee import _commission_neutral_stop


class GoldRecoveryStrategy(native.IndependentCloseStrategy):
    def observe(self, state, price):
        r=state.source
        move=r['side']*(price-r['entry'])/r['risk']
        r['mae_r']=min(r.get('mae_r',0.),move)
        r['mfe_r']=max(r.get('mfe_r',0.),move)
        if move > self.eps and r['side']*(price-r['near_edge']) >= -self.eps:
            r['returned_to_range']=True

    def on_order_filled(self,event):
        state,phase,_=self.order_map[str(event.client_order_id)]
        r=state.source
        if phase=='entry':
            opened=self.frame.index[self.indices[event.ts_event]]-pd.Timedelta(minutes=self.execution_minutes)
            due=opened.floor(f"{int(r['confirmation_minutes'])}min")+pd.Timedelta(minutes=int(r['confirmation_minutes']))
            r.update(decision_time=str(due),decision_ns=due.value,decision_status='pending',decision_i=-1,
                     mae_r=0.,mfe_r=0.,returned_to_range=False,exit_kind='')
            for name,rate in [('fee_stop',self.policy.fill_taker_fee),('fee_limit',self.policy.fill_maker_fee)]:
                r[name]=float(_commission_neutral_stop(event.last_px.as_decimal(),r['side'],
                    event.last_qty.as_decimal(),event.commission.as_decimal(),Decimal(str(rate)),Decimal(str(self.tick))))
        else:
            self.observe(state,float(event.last_px))
            r['exit_kind']=('fee_recovery' if phase=='target' and r['decision_status'] in ('failed_wait','fee_band_wait') else
                            'far_target' if phase=='target' else 'fee_stop' if phase=='stop' and state.stop_kind==1 else
                            'backstop' if phase=='stop' else 'censored')
        super().on_order_filled(event)

    def on_bar(self,bar):
        i,phase=self.point_phases[bar.ts_event]
        for state in list(self.active.values()):
            self.observe(state,float(bar.close))
        if phase==3:
            close=float(bar.close)
            for state in list(self.active.values()):
                r=state.source
                if r['decision_status']!='pending' or self.frame.index[i].value<r['decision_ns'] or i>=r['end_i']:
                    continue
                if self.frame.index[i].value != r['decision_ns']:
                    raise RuntimeError('Missing confirmation close; caller must censor at source gap')
                r['decision_i']=i
                reclaimed=r['side']*(close-r['entry'])>self.eps
                r['close_reclaimed']=reclaimed
                if reclaimed and r['side']*(close-r['fee_stop']) >= -self.eps:
                    r['decision_status']='reclaimed'
                    state.stop,state.stop_kind=r['fee_stop'],1
                    state.be_price=r['fee_stop']
                    state.be_trigger_i=state.be_effective_i=i
                    self.modify_order(state.stop_order,trigger_price=self.instrument.make_price(state.stop))
                else:
                    r['decision_status']='fee_band_wait' if reclaimed else 'failed_wait'
                    self._cancel_target(state)
                    r['original_target_price']=r['target_price']
                    # A close inside the fee band can already be beyond the
                    # maker-only price. Keep this replacement strictly resting.
                    r['target_price']=r['fee_stop'] if reclaimed else r['fee_limit']
                    self._submit_target(state)
        super().on_bar(bar)


_globals=dict(native.run_native.__globals__)
_globals['IndependentCloseStrategy']=GoldRecoveryStrategy
_run=FunctionType(native.run_native.__code__,_globals,native.run_native.__name__,native.run_native.__defaults__,native.run_native.__closure__)
_run.__kwdefaults__=dict(native.run_native.__kwdefaults__)


def run_gold_recovery(frame,entries,policy,**kwargs):
    import numpy as np
    if policy.reclaim_next_bar or not policy.monotone_segments or policy.fill_maker_fee is None:
        raise ValueError('Gold recovery requires standalone native target/backstop and fee-configured monotone path')
    return _run(frame,np.full(len(frame),np.nan),np.zeros(len(frame)),entries,policy,entry_order_mode='limit',**kwargs)
