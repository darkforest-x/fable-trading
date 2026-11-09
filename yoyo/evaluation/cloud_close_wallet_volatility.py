"""Size new confirmed 12H positions by the recent volatility of the independent original wallet."""
import json
from contextlib import nullcontext

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_delivery_control as delivery
from yoyo.evaluation import cloud_close_tenth_risk as reference

EXP = reference.study.EXP.parent/'exp-cloud-close-wallet-volatility-20261109-v1'
HOUR = delivery.HOUR


def weights(curve, data):
    """Use the reference NAV at UTC 12H boundaries, ending at the key open before each entry."""
    nav = curve.nav.loc[(curve.index.hour%12==0) & (curve.index.minute==0)]
    if not nav.index.is_unique or not nav.index.to_series().diff().iloc[1:].eq(pd.Timedelta(hours=12)).all():
        raise ValueError('Reference strategy needs a consecutive native clock')
    returns = np.log(nav).diff()
    slow = returns.rolling(180,min_periods=180).std(ddof=1)
    fast = returns.rolling(40,min_periods=40).std(ddof=1)
    ratio = (slow.where(slow>0)/fast.where(fast>0)).clip(.25,2.)
    ratio.index = ratio.index+pd.Timedelta(hours=12)
    positions = data['selected'][data['selected'].tp_r.eq(0)].copy()
    entries = pd.to_datetime(positions.entry_ms,unit='ms',utc=True)
    positions['shadow_risk_weight'] = ratio.reindex(entries).to_numpy()
    factor = positions.shadow_risk_weight.fillna(1.)
    nhours, nslots = data['actions'].shape[0]//3, data['actions'].shape[1]//2
    matrix = np.ones((nhours,nslots))
    offsets = (positions.entry_ms-data['start'])//HOUR
    matrix[offsets.to_numpy(),:] = factor.to_numpy()[:,None]
    return matrix, positions.set_index('event_id').shadow_risk_weight


def evaluate(venue,extra):
    tenth = reference.study
    source, suffix = (tenth.EXP,'OKX') if venue=='OKX' else (delivery.study.EXP,'tenth')
    original = pd.read_csv(source/f'funded_{suffix}.csv.gz').set_index('event_id')
    shadow = pd.read_csv(source/f'hourly_{suffix}.csv.gz',index_col='hour_end_utc',parse_dates=True)
    paths = tenth.trend.thirds.okx_paths() if venue=='OKX' else delivery.archive.pool(extra)
    paths = paths[~paths.is_control & paths.event_id.isin(original.index)]
    _, legs = tenth.trend.inputs(venue,paths)
    data = (tenth.split.pair_data(legs[legs.tp_r.eq(0)],legs[legs.tp_r.eq(2)],9)
            if venue=='OKX' else delivery.archive.native.prepare(legs))
    matrix, factor = weights(shadow,data)
    metrics, parents, eq = tenth.split.simulate(data,2/3,slot_weights=matrix,exposure=.9)
    if set(parents.index)!=set(original.index) or not np.allclose(parents.net_return,
            original.loc[parents.index,'net_return'],rtol=1.e-8,atol=1.e-10):
        raise ValueError('Dynamic budget changed native trade membership or per-unit returns')
    if not np.isclose(1000+parents.cash_pnl.sum(),eq.nav.iloc[-1],rtol=1e-8,atol=.001):
        raise ValueError('Compounded native wallet must reconcile to realized money')
    parents['shadow_risk_weight'] = factor
    parents.to_csv(EXP/f'funded_{venue}.csv.gz')
    eq.to_csv(EXP/f'hourly_{venue}.csv.gz',index_label='hour_end_utc')
    legs = legs.copy()
    legs['signed_size'] = legs.event_id.map(parents.notional)*legs.tp_r.map(reference.risk.LEG_FRACTIONS)*legs.side/legs.entry_price
    envelope,_ = reference.risk._fee_adjusted_envelope(eq,legs)
    dev = envelope[envelope.index<=reference.risk.SPLIT]
    return dict(metrics=metrics,original_nav=float(shadow.nav.iloc[-1]),
                full_drawdown_upper_pct=reference.risk._max_drawdown_upper(envelope),
                dev_drawdown_upper_pct=reference.risk._max_drawdown_upper(dev),
                all_positions=len(parents),warmup_or_flat_fallback=int(factor.isna().sum()),
                below_base=int(factor.lt(1.-1.e-10).sum()),above_base=int(factor.gt(1.+1.e-10).sum()),
                terminal_reconciled=True)


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed original-wallet volatility strategy')
    extra = set(json.loads((delivery.archive.EXP/'summary.json').read_text())['archived_additions'])
    extra.update(json.loads((delivery.study.EXP/'summary.json').read_text())['new_archive_cohorts']['priced'])
    output = {}
    for venue in ('OKX','Binance'):
        with (delivery.archive.market_quotes(extra) if venue=='Binance' else nullcontext()):
            output[venue] = evaluate(venue,extra)
    (EXP/'summary.json').write_text(json.dumps(output,indent=2,allow_nan=False)+'\n')
    print(json.dumps(output,indent=2),flush=True)


if __name__=='__main__':
    main()
