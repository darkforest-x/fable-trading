"""Use the documented official index-hour delivery for the newly funded historical OKX exits."""
import json
from functools import lru_cache

import numpy as np
import pandas as pd
import requests

from yoyo.evaluation import cloud_close_okx_retired_account as prior

risk = prior.risk
wallet = prior.wallet
EXP = prior.EXP.parent/'exp-cloud-close-okx-retired-delivery-20261109-v1'


def index_data(cfg,end):
    blocks = []
    for symbol in cfg['symbols']:
        index = symbol+'-USDT'
        query = dict(instId=index,bar='1m',after=end,limit=cfg['minutes'])
        response = requests.get(cfg['source'],params=query,timeout=(10,30))
        response.raise_for_status()
        raw = response.json()
        if raw.get('code')!='0':
            raise ValueError(f'Index request failed for {index}')
        frame = pd.DataFrame(raw['data'],columns=['time','open','high','low','close','confirm']).apply(pd.to_numeric)
        frame = frame.sort_values('time')
        if (not np.array_equal(frame.time,np.arange(end-cfg['minutes']*60000,end,60000)) or
            not frame['confirm'].eq(1).all() or not np.isfinite(frame[prior.FIELDS]).all().all() or
            not frame.low.gt(0).all() or not frame.low.le(frame[['open','close']].min(axis=1)).all() or
            not frame.high.ge(frame[['open','close']].max(axis=1)).all()):
            raise ValueError(f'Incomplete or invalid official settlement observations for {index}')
        blocks.append(frame.assign(symbol=symbol,instId=index))
    data = pd.concat(blocks,ignore_index=True)
    data.to_csv(EXP/'settlement_index_minutes.csv',index=False)
    return data.groupby('symbol')[['low','close','high']].mean()


def main():
    if (EXP/'summary.json').is_file():
        raise FileExistsError('Completed expanded-roster retirement settlement study')
    cfg = json.loads((EXP/'config.json').read_text())
    end = pd.Timestamp(cfg['delivery_utc']).value//1_000_000
    official = index_data(cfg,end)
    before = pd.read_csv(prior.EXP/'funded_fee_only.csv.gz').set_index('event_id')
    added = pd.read_csv(prior.EXP/'funded_retired_legs.csv.gz',float_precision='round_trip')
    old = risk.hourly.tested.expiry.trend.thirds.okx_paths()
    old = old[old.event_id.isin(before.index)&~old.is_control]
    old,_ = risk.hourly.tested.expiry.time_in_force(old)
    rows = pd.concat([old,added[old.columns]],ignore_index=True)
    mask = rows.symbol.isin(cfg['symbols']) & rows.exit_reason.eq('series_tail_close') & rows.exit_bar_ms.eq(end-wallet.HOUR)
    ids = set(rows.loc[mask,'event_id'])
    expected = set(json.loads((prior.EXP/'summary.json').read_text())['retired_selected_source_boundary_events'])
    if (set(rows.event_id)!=set(before.index) or not rows.groupby('event_id').size().eq(2).all()
            or ids!=expected or len(ids)!=2 or set(rows.loc[mask,'symbol'])!=set(official.index) or mask.sum()!=4):
        raise ValueError('Newly funded exits differ from exact documented unresolved positions')

    @lru_cache(maxsize=None)
    def quotes(s):
        if s not in set(added.symbol):
            return wallet.quotes(s)
        path = prior.prices.CACHE/'series'/(s+'-USDT-SWAP')/'bars1h.csv.gz'
        x = pd.read_csv(path,float_precision='round_trip')
        return x.time.to_numpy(),x[prior.FIELDS].to_numpy()

    results = {}
    for label,column,slip in [('proxy','close',0.),('mean_low','low',0.),('mean_high','high',0.),
                               ('proxy_market_5bp','close',.0005)]:
        adjusted = rows.copy()
        adjusted.loc[mask,'exit_price'] = adjusted.loc[mask,'symbol'].map(official[column])
        adjusted.loc[mask,'exit_fee_rate'] = 0.
        adjusted.loc[mask,'exit_reason'] = 'no_fee_index_delivery'
        data = risk.split.pair_data(adjusted[adjusted.tp_r.eq(0)],adjusted[adjusted.tp_r.eq(2)],9,quote_loader=quotes)
        if slip:
            m,p,e,_ = risk.checked._trim_account_with_slippage(data,quotes,1.,.2,review_hours=1,market_slip=slip)
        else:
            m,p,e,_ = risk.hourly.tested.trim_account(data,quotes,1.,.2,review_hours=1)
        if set(p.index)!=set(before.index) or not np.isclose(1000+p.cash_pnl.sum(),e.nav.iat[-1],rtol=1.e-9,atol=.001):
            raise ValueError('Settlement scenario changed the funded roster or failed cash reconciliation')
        if label.startswith('proxy'):
            p.to_csv(EXP/f'funded_{label}.csv.gz')
            e.to_csv(EXP/f'hourly_{label}.csv.gz',index_label='hour_end_utc')
        results[label] = m
        del data
    prior.prices.safe_json(EXP/'summary.json',dict(metrics=results,delivery_fee=0.,event_ids=sorted(ids),
                official_price_hour=official.to_dict('index'),minutes_per_index=cfg['minutes'],
                scope='17 added names. The venue can adjust delivery; mean minute closes proxy the official 1h index average. Historical funding omitted.'))
    print({k:v['all']['final_usdt'] for k,v in results.items()},flush=True)


if __name__=='__main__':
    main()
