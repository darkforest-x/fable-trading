"""Test one causal BTC daily regime across the new strong-pullback and weak-rally paths."""
import gc
import json
from functools import lru_cache

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_weak_rally_branch as rally

wallet = rally.wallet
EXP = rally.EXP.parent/'exp-cloud-close-rally-regime-20261109-v1'


def market_regime():
    """Use only the previous fully completed UTC day and preceding 199 consecutive complete daily closes."""
    clock,data = wallet.quotes('BTC')
    h = pd.DataFrame({'close':data[:,3]},index=pd.to_datetime(clock,unit='ms',utc=True))
    grouped = h.resample('1D',origin='epoch').close
    close = grouped.last().where(grouped.count().eq(24))
    average = close.rolling(200,min_periods=200).mean()
    sign = np.sign(close-average)
    sign.index = sign.index.as_unit('ms').asi8+24*wallet.HOUR
    return sign


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed BTC regime on the mirrored market-wide paths')
    folders = [rally.EXP,rally.EXP.parent/'exp-cloud-close-strong-pullback-branch-20261109-v1']
    chunks = []
    for side,folder in zip([-1,1],folders):
        x = pd.read_csv(folder/'native_paths.csv.gz',dtype={'symbol':str},float_precision='round_trip')
        if not x.side.eq(side).all():
            raise ValueError('Wrong polarity in a frozen native path')
        x['event_id'] = x.event_id+'|'+str(side)
        x['parent_id'] = x.parent_id+'|'+str(side)
        chunks.append(x)
    rows = pd.concat(chunks,ignore_index=True)
    if not rows.event_id.is_unique:
        raise ValueError('Duplicated native events across pullback directions')
    daily = market_regime()
    rows['btc_regime'] = (rows.entry_ms//(24*wallet.HOUR)*(24*wallet.HOUR)).map(daily)
    raw = rows[~rows.is_control & rows.btc_regime.notna()]
    aligned = rows[rows.btc_regime.eq(rows.side)]
    confirming = aligned[~aligned.is_control]
    control = aligned[aligned.is_control & aligned.parent_id.isin(confirming.parent_id)]
    matched = confirming[confirming.parent_id.isin(control.parent_id)]
    opposite = raw[raw.btc_regime.eq(-raw.side)]
    sources = rally.sources()
    @lru_cache(maxsize=None)
    def quotes(symbol):
        f,_,end = sources[symbol]
        d = pd.read_csv(f,usecols=['time',*rally.breadth.archive.FIELDS],float_precision='round_trip')
        d = d[d.time<end]
        return d.time.to_numpy(),d[rally.breadth.archive.FIELDS].to_numpy()

    results = {}
    for name,sample in [('unrestricted',raw),('aligned',confirming),('opposite',opposite),
                         ('matched',matched),('later',control)]:
        data = wallet.prepare(sample,9,quote_loader=quotes)
        for slip in ([0.,.0005] if name=='aligned' else [0.]):
            label = 'aligned_stress' if slip else name
            funded,equity = wallet.account(data,slip=slip)
            if not np.isclose(1000+funded.cash_pnl.sum(),equity.nav.iat[-1],atol=.005):
                raise ValueError('Account does not reconcile to actual closed position cash')
            funded.to_csv(EXP/f'funded_{label}.csv.gz',index=False)
            if name=='aligned':
                equity.to_csv(EXP/f'hourly_{label}.csv.gz',index_label='hour_end_utc')
            results[label] = dict(metrics={p:wallet.summarize(funded,equity,*dt)
                        for p,dt in rally.breadth.pair.periods.PERIODS.items()},candidates=len(sample),
                        unquoted_holding_hours=data['gap_marks'],funded_longs=int(funded.side.eq(1).sum()),
                        pre_end_tail_exits=int((funded.exit_reason.eq('series_tail_close')&
                                          funded.exit_bar_ms.lt(wallet.END-wallet.HOUR)).sum()))
            print(label,results[label]['metrics']['all'],flush=True)
        del data
        gc.collect()
    (EXP/'summary.json').write_text(json.dumps(dict(results=results,no_regime_or_warmup=int(rows.btc_regime.isna().sum()),
           scope='Native historical 360-contract research with gap/end marks and taker fees before funding.'),indent=2)+'\n')


if __name__=='__main__':
    main()
