"""Fund V1.7 12H entries only when current within-contract ATR percentage is expanding."""
import gc
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_binance_gap_repair as binance
from yoyo.evaluation import cloud_close_eligibility_repair as repair
from yoyo.evaluation import cloud_close_reward_paths as engine
from yoyo.evaluation import cloud_close_same_time_control as matched
from yoyo.evaluation import cloud_close_scaleout as split
from yoyo.evaluation import cloud_close_second_reclaim as second
from yoyo.evaluation import cloud_close_thirds as thirds

wallet = thirds.wallet
EXP = wallet.ROOT / 'experiments/active/exp-cloud-close-volatility-expansion-20261109-v1'


def expansion(bars):
    """At open(t)+12h divide Wilder ATR14/close through t by its last 96-bar mean through t."""
    atr = pd.DataFrame(engine.atr_points(bars.reset_index().to_dict('records')))['atr14']
    relative = pd.Series(atr.to_numpy(), index=bars.index) / bars.close
    mean = relative.rolling(96, min_periods=96).mean()
    index = relative.index.to_series()
    consecutive = index.diff(95).eq(95 * engine.STEP_12H)
    ratio = relative.div(mean.where(mean.gt(0))).where(consecutive)
    ratio.index += engine.STEP_12H
    return ratio


def market_bars(venue, symbol):
    if venue == 'OKX':
        file = wallet.SOURCE / 'inputs' / symbol / 'bars12h.csv.gz'
        if not file.is_file():
            file = wallet.TOP / 'inputs' / symbol / 'bars12h.csv.gz'
        return engine._load_bars(file, '12h')
    clock, prices = binance.quotes(symbol)
    hourly = pd.DataFrame(prices, index=clock, columns=['open','high','low','close'])
    group = hourly.groupby(hourly.index // engine.STEP_12H)
    bars = group.agg({'open':'first','high':'max','low':'min','close':'last'})[group.size().eq(12)]
    bars.index = pd.Index(bars.index * engine.STEP_12H, name='time')
    return bars


def screen(venue, paths):
    events = paths.loc[paths.tp_r.eq(0), ['event_id','symbol','entry_ms','is_control','parent_id']].copy()
    ratios = pd.Series(np.nan, index=events.index)
    for symbol, group in events.groupby('symbol'):
        indicator = expansion(market_bars(venue, symbol))
        ratios.loc[group.index] = indicator.reindex(group.entry_ms).to_numpy()
    events['ratio'] = ratios
    events.to_csv(EXP / f'ratios_{venue}.csv.gz', index=False)
    selected = paths[paths.event_id.isin(events.loc[ratios.ge(1), 'event_id'])]
    real = selected[~selected.is_control]
    ctl = selected[selected.is_control & selected.parent_id.isin(real.parent_id)]
    counts = dict(real=len(real)//2, both_expand_pairs=len(ctl)//2,
                  missing_real=int(events[~events.is_control].ratio.isna().sum()))
    return real, ctl, counts


def fund(venue, paths, slip):
    if venue == 'OKX':
        return second.funded(paths, slip)[:3]
    metrics, trades, equity = split.simulate(binance.prepare(paths), 2/3, slip)
    if not np.isclose(1000+trades.cash_pnl.sum(), equity.nav.iloc[-1], rtol=1e-8, atol=.005):
        raise ValueError('Binance cash flow mismatch')
    if not slip:
        independent = matched.returns(paths, 2/3).loc[trades.parent_id, 'net']
        if not np.allclose(independent, trades.net_return, rtol=1e-8, atol=1e-10):
            raise ValueError('Per-position net returns do not reconcile')
    return metrics, trades, equity


def main():
    if (EXP/'summary.json').is_file():
        raise FileExistsError('Frozen experiment already evaluated')
    original = pd.read_csv(repair.EXP / 'thirds' / 'account_metrics.csv')
    rows = original[original.scenario.eq('actual')].assign(policy='unfiltered').to_dict('records')
    summary = {}
    for venue in ['OKX', 'Binance']:
        if venue == 'OKX':
            paths = thirds.okx_paths()
            repair.check_parent_sets(paths)
        else:
            paths = binance.assembled(set(binance.patch.affected_tickers()))
        real, controls, counts = screen(venue, paths)
        metrics, trades, equity = fund(venue, real, 0.)
        trades.to_csv(EXP / f'funded_{venue}.csv.gz')
        equity.to_csv(EXP / f'hourly_{venue}.csv.gz', index_label='hour_end_utc')
        rows.extend(dict(policy='expanded',venue=venue,scenario='actual',period=p,**m) for p,m in metrics.items())
        dev, full = second.meets(metrics['dev']), second.meets(metrics['all'])
        summary[venue] = dict(**counts, dev_pass=dev, full_pass=full)
        if dev:
            paired = real[real.parent_id.isin(controls.parent_id)]
            for label, sample, slip in [('paired',paired,0.),('control',controls,0.),('stress',real,.0005)]:
                verified = fund(venue, sample, slip)[0]
                rows.extend(dict(policy='expanded',venue=venue,scenario=label,period=p,**m) for p,m in verified.items())
                gc.collect()
        if not (dev and full):
            break
    table = pd.DataFrame(rows)
    table.to_csv(EXP/'account_metrics.csv', index=False)
    (EXP/'summary.json').write_text(json.dumps(summary, indent=2, allow_nan=False)+'\n')
    print(table[table.period.eq('all')].to_string(index=False))


if __name__ == '__main__':
    main()
