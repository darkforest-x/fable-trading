"""Protect part of realized portfolio equity while compounding only available cash in relative leaders."""
import numpy as np
import pandas as pd
from numba import njit
from vectorbt.portfolio import nb
from vectorbt.portfolio.enums import NoOrder

from yoyo.evaluation import cloud_close_rotation_market_filter as gate

root=gate.source
rotation=root.rotation
EXP=gate.EXP.parent/'exp-cloud-close-rotation-cash-reserve-20261109-v1'


@njit(cache=True)
def allocate(c,actions,mode,state,slip,fee):
    action=actions[c.i,c.col]
    if np.isnan(action):
        return NoOrder
    price=c.close[c.i,c.col]
    if action<0:
        return nb.order_nb(size=-c.position_now,price=price,fees=fee,slippage=slip,allow_partial=False,raise_reject=True)
    if c.last_position.sum()>1e-9:
        raise ValueError('Rotation purchase while previous market still open')
    available=c.cash_now
    if mode==1:
        retained=.5*available
    elif mode==2:
        state[0]=max(state[0],.5*available)
        retained=state[0]
    else:
        retained=0.
    cost=price*(1.+slip)*(1.+fee)
    size=(available-retained)/cost*(1.-1e-12)
    if size<=0:
        raise ValueError('Reserve exhausted deployable capital')
    return nb.order_nb(size=size,price=price,fees=fee,slippage=slip,allow_partial=False,raise_reject=True)


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed comparative cash reserve study')
    loaded,_=root.clocks_and_prices()
    picks=pd.read_csv(gate.EXP/'decisions.csv.gz',index_col=0).market_bull
    cases={}
    for mode,name in enumerate(['unrestricted','half_current','half_realized_high']):
        for slip in [0.,.0005]:
            state=np.array([500. if mode==2 else 0.])
            def portfolio(marks,actions,call_seq,slippage):
                return rotation.source.wallet.vbt.Portfolio.from_order_func(marks,allocate,actions,mode,state,slippage,
                    rotation.source.TAKER,cash_sharing=True,group_by=True,init_cash=1000.,call_seq=call_seq,
                    max_orders=np.count_nonzero(~np.isnan(actions))+1)
            suffix='_stress' if slip else ''
            ref=None
            if mode==0:
                ref=(pd.read_csv(gate.EXP/f'funded_market_bull{suffix}.csv.gz'),
                     pd.read_csv(gate.EXP/f'hourly_market_bull{suffix}.csv.gz',index_col=0,parse_dates=True))
            case=rotation.run(loaded,picks.to_frame(name),name,slip,symbols=root.SYMBOLS,directory=EXP,
                              portfolio_builder=portfolio,reference=ref)
            case['terminal_locked_cash_usdt']=float(state[0]) if mode==2 else None
            cases[name+suffix]=case
            print(name+suffix,case['metrics']['all'],flush=True)
    rotation.source._write_json(EXP/'summary.json',dict(arms=cases))


if __name__=='__main__':
    main()
