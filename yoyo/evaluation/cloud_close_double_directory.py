"""Transfer the fixed doubling-limit isolated account to the documented historical 360-name roster."""
import gc
import json
from functools import lru_cache

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_double_exposure as exposure
from yoyo.evaluation import cloud_close_okx_retired_account as archive

parent = exposure.parent
wallet = exposure.wallet
EXP = exposure.EXP.parent/'exp-cloud-close-double-directory-20261109-v1'
BASE = EXP.parent/'exp-cloud-close-okx-directory-account-20261109-v1'
DELIVERY = EXP.parent/'exp-cloud-close-okx-directory-delivery-20261109-v1'


def inputs():
    info = json.loads((DELIVERY/'summary.json').read_text())
    roster = pd.read_csv(DELIVERY/'funded_proxy.csv.gz',usecols=['event_id']).event_id
    retirees = pd.read_csv(BASE/'funded_retired_legs.csv.gz',float_precision='round_trip')
    extras = set(retirees.symbol)
    src = archive.risk.hourly.tested.expiry.trend.thirds.okx_paths()
    rows,_ = archive.risk.hourly.tested.expiry.time_in_force(src[src.event_id.isin(roster)])
    tape = pd.concat([rows,retirees[rows.columns]],ignore_index=True)
    settle = tape.event_id.isin(info['event_ids'])&tape.exit_reason.eq('series_tail_close')
    estimates = {k:v['close'] for k,v in info['official_price_hour'].items()}
    tape.loc[settle,'exit_price'] = tape.loc[settle,'symbol'].map(estimates)
    tape.loc[settle,'exit_fee_rate'] = 0.
    tape.loc[settle,'exit_reason'] = 'delisting_settlement'
    if (set(tape.event_id)!=set(roster) or not tape.groupby('event_id').size().eq(2).all()
            or set(tape.loc[settle,'symbol'])!=set(estimates)):
        raise ValueError('Expanded two-child wallet does not match its native historical roster')

    @lru_cache(maxsize=None)
    def quotes(symbol):
        if symbol not in extras:
            return wallet.quotes(symbol)
        path = archive.prices.CACHE/'series'/(symbol+'-USDT-SWAP')/'bars1h.csv.gz'
        f = pd.read_csv(path,float_precision='round_trip')
        return f.time.to_numpy(),f[archive.FIELDS].to_numpy()

    return tape,quotes,info


def changed_limits(tape,quotes):
    old_ids = set(pd.read_csv(parent.EXP/'funded_OKX.csv.gz',usecols=['event_id']).event_id)
    new = parent.new_exits(tape[~tape.event_id.isin(old_ids)],quotes)
    saved = pd.read_csv(parent.EXP/'doublings_OKX.csv').drop_duplicates('event_id')
    cached = []
    for r in saved[saved.event_id.isin(tape.event_id)].itertuples():
        clock,ohlc = quotes(r.symbol)
        i = clock.searchsorted(r.limit_ms)
        if clock[i]!=r.limit_ms:
            raise ValueError(f'Cached doubling absent at {r.event_id}')
        early = ohlc[i,0]>=r.price
        cached.append(dict(event_id=r.event_id,exit_bar_ms=r.limit_ms,
                           exit_reason='take_profit_open' if early else 'take_profit',
                           exit_phase='open' if early else 'intrabar',exit_price=r.price,
                           exit_fee_rate=exposure.study.leverage.MAKER,same_bar_ambiguous=False))
    changes = pd.concat([pd.DataFrame(cached),new],ignore_index=True)
    if not changes.empty and not changes.event_id.is_unique:
        raise ValueError('Cached and new native doubling events overlap')
    return changes


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed historical transfer of fixed leveraged candidate')
    choice = json.loads((exposure.EXP/'summary.json').read_text())['development_pick']
    rows,quotes,prior = inputs()
    limits = changed_limits(rows,quotes)
    data,triggers = parent.assemble(rows,limits,'OKX',quotes)
    pd.DataFrame(triggers).to_csv(EXP/'doubling_fills.csv',index=False)
    tape = exposure.underlying(data,quotes)
    scenarios = {}
    for label,multiple,slip in [('reference',1.,0.),('fixed',choice,0.),('fixed_5bp',choice,.0005)]:
        scenarios[label] = exposure.simulate(data,tape,multiple,slip,directory=EXP,quote_loader=quotes)
        print(label,scenarios[label].get('metrics',{}).get('all'),flush=True)
        gc.collect()
    archive.prices.safe_json(EXP/'summary.json',dict(cases=scenarios,fixed_multiple=choice,
        funded_parents=rows.event_id.nunique(),newly_doubled_child_exits=len(triggers),
        known_unresolved_gap_events=prior['remaining_source_gap_events'],historical_funding='omitted'))


if __name__=='__main__':
    main()
