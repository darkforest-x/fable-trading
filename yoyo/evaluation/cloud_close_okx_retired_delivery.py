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


def index_data(cfg,end,*,directory=EXP):
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
    if directory is not None:
        data.to_csv(directory/'settlement_index_minutes.csv',index=False)
    return data


def main(directory=EXP,parent=prior.EXP):
    if (directory/'summary.json').is_file():
        raise FileExistsError('Completed expanded-roster retirement settlement study')
    cfg = json.loads((directory/'config.json').read_text())
    if 'deliveries' in cfg:
        info = cfg['deliveries']
        old = pd.read_csv(EXP/'settlement_index_minutes.csv')
        parts = []
        for symbol,spec in info.items():
            end = pd.Timestamp(spec['delivery_utc']).value//1_000_000
            cached = old[(old.symbol==symbol)&old.time.between(end-60*60000,end-1)]
            if len(cached)==60:
                parts.append(cached)
            else:
                parts.append(index_data(dict(cfg,symbols=[symbol]),end,directory=None))
        minutes = pd.concat(parts,ignore_index=True)
        minutes.to_csv(directory/'settlement_index_minutes.csv',index=False)
    else:
        end = pd.Timestamp(cfg['delivery_utc']).value//1_000_000
        minutes = index_data(cfg,end)
        info = {s:dict(delivery_utc=cfg['delivery_utc'],fee_confirmed_zero=True) for s in cfg['symbols']}
    official = minutes.groupby('symbol')[['low','close','high']].mean()
    ends = {s:pd.Timestamp(n['delivery_utc']).value//1_000_000 for s,n in info.items()}
    before = pd.read_csv(parent/'funded_fee_only.csv.gz').set_index('event_id')
    added = pd.read_csv(parent/'funded_retired_legs.csv.gz',float_precision='round_trip')
    old = risk.hourly.tested.expiry.trend.thirds.okx_paths()
    old = old[old.event_id.isin(before.index)&~old.is_control]
    old,_ = risk.hourly.tested.expiry.time_in_force(old)
    rows = pd.concat([old,added[old.columns]],ignore_index=True)
    mask = rows.symbol.isin(info) & rows.exit_reason.eq('series_tail_close') & rows.exit_bar_ms.eq(rows.symbol.map(ends)-wallet.HOUR)
    ids = set(rows.loc[mask,'event_id'])
    expected = set(added.loc[added.exit_reason.eq('series_tail_close'),'event_id'])
    gap_events = sorted(added.loc[added.gap_flag | added.exit_reason.isin(['data_gap_markout','stale_12h_markout']),'event_id'].unique())
    if (set(rows.event_id)!=set(before.index) or not rows.groupby('event_id').size().eq(2).all()
            or ids!=expected or len(ids)!=len(info) or set(rows.loc[mask,'symbol'])!=set(official.index)):
        raise ValueError('Newly funded exits differ from exact documented unresolved positions')

    @lru_cache(maxsize=None)
    def quotes(s):
        if s not in set(added.symbol):
            return wallet.quotes(s)
        path = prior.prices.CACHE/'series'/(s+'-USDT-SWAP')/'bars1h.csv.gz'
        x = pd.read_csv(path,float_precision='round_trip')
        return x.time.to_numpy(),x[prior.FIELDS].to_numpy()

    results = {}
    scenarios = [('proxy','close',0.),('mean_low','low',0.),('mean_high','high',0.),
                 ('proxy_market_5bp','close',.0005)]
    if any(not n['fee_confirmed_zero'] for n in info.values()):
        scenarios.append(('proxy_delivery_5bp','close',0.))
    for label,column,slip in scenarios:
        adjusted = rows.copy()
        adjusted.loc[mask,'exit_price'] = adjusted.loc[mask,'symbol'].map(official[column])
        adjusted.loc[mask,'exit_fee_rate'] = 0.
        adjusted.loc[mask,'exit_reason'] = 'index_delivery'
        if label=='proxy_delivery_5bp':
            charged = {s for s,n in info.items() if not n['fee_confirmed_zero']}
            adjusted.loc[mask & adjusted.symbol.isin(charged),'exit_fee_rate'] = wallet.TAKER
        data = risk.split.pair_data(adjusted[adjusted.tp_r.eq(0)],adjusted[adjusted.tp_r.eq(2)],9,quote_loader=quotes)
        if slip:
            m,p,e,_ = risk.checked._trim_account_with_slippage(data,quotes,1.,.2,review_hours=1,market_slip=slip)
        else:
            m,p,e,_ = risk.hourly.tested.trim_account(data,quotes,1.,.2,review_hours=1)
        if set(p.index)!=set(before.index) or not np.isclose(1000+p.cash_pnl.sum(),e.nav.iat[-1],rtol=1.e-9,atol=.001):
            raise ValueError('Settlement scenario changed the funded roster or failed cash reconciliation')
        if label in ('proxy','proxy_market_5bp'):
            p.to_csv(directory/f'funded_{label}.csv.gz')
            e.to_csv(directory/f'hourly_{label}.csv.gz',index_label='hour_end_utc')
        results[label] = m
        del data
    prior.prices.safe_json(directory/'summary.json',dict(metrics=results,primary_delivery_fee=0.,event_ids=sorted(ids),remaining_source_gap_events=gap_events,
                official_price_hour=official.to_dict('index'),minutes_per_index=cfg['minutes'],
                scope='Fixed expanded roster. The venue can adjust delivery; mean minute closes proxy the official 1h index average. Historical funding omitted.'))
    print({k:v['all']['final_usdt'] for k,v in results.items()},flush=True)


if __name__=='__main__':
    import argparse
    cli = argparse.ArgumentParser()
    cli.add_argument('--directory-census',action='store_true')
    if cli.parse_args().directory_census:
        main(EXP.parent/'exp-cloud-close-okx-directory-delivery-20261109-v1',
             EXP.parent/'exp-cloud-close-okx-directory-account-20261109-v1')
    else:
        main()
