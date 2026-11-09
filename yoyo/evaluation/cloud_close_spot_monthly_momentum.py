"""Rank early native spot markets once monthly using strictly completed V1.7 12H cloud observations."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_launch_spot_wallet as spot
from yoyo.evaluation import cloud_close_major_rotation as rotation

EXP = spot.EXP.parent / 'exp-cloud-close-spot-monthly-momentum-20261109-v1'
STEP = spot.clock.STEP
LOOKBACK = 56


def market_panel(symbols):
    """Read original H1 trades, exact UTC 12H closes, and trailing 28-day return on each monthly clock."""
    grid = pd.Index(np.arange(spot.START, spot.END, spot.H), name='time')
    decision = grid[grid % STEP == 0]
    dates = pd.to_datetime(decision, unit='ms', utc=True)
    month = decision[(dates.day == 1) & (dates.hour == 0)]
    loaded, scores, executable = {}, {}, {}
    for s in symbols:
        ts, px = spot.quotes(s+'-USDT')
        native = pd.DataFrame(px, index=pd.Index(ts, name='time'), columns=['open','high','low','close'])
        bars = spot.clock.bars_for(native, 0)
        close = bars.close
        close.index = close.index + STEP
        trend = rotation.bullish(close)
        momentum = close.div(close.reindex(close.index-LOOKBACK*STEP).to_numpy())-1.
        scores[s] = momentum.where(trend).reindex(month)
        executable[s] = pd.Series(decision.isin(native.index), index=decision)
        # Forward carry is a valuation convention only. Never fill at these marks.
        last = native.close.reindex(grid).ffill()
        if native.index[-1] < grid[-1]:
            last.loc[last.index > native.index[-1]] = native.close.iloc[-1]*1e-12
        marked = native.reindex(grid).apply(lambda x: x.fillna(last)).fillna(1.)
        loaded[s] = {'bars1':marked, 'actual':pd.Series(grid.isin(native.index), index=grid)}
    return loaded, pd.DataFrame(scores), pd.DataFrame(executable)


def targets(scores, executable, symbols):
    actual = executable.to_numpy()
    result = pd.DataFrame(index=executable.index)
    for arm in ['leader', 'laggard', 'alphabetic', 'passive_btc']:
        active = target = -1
        holdings = []
        for i,t in enumerate(executable.index):
            if t in scores.index:
                values = scores.loc[t].to_numpy()
                eligible = np.flatnonzero(np.isfinite(values))
                if arm == 'passive_btc':
                    if target == -1 and np.isfinite(values[symbols.index('BTC')]):
                        target = symbols.index('BTC')
                elif not len(eligible):
                    target = -1
                elif arm == 'alphabetic':
                    target = eligible[0]
                else:
                    ranked = values[eligible]
                    best = (ranked.max() if arm == 'leader' else ranked.min())
                    tied = eligible[ranked == best]
                    target = active if active in tied else tied[0]
            if target != active:
                if active >= 0 and actual[i,active]:
                    active = -1
                if active == -1 and target >= 0 and actual[i,target]:
                    active = target
            holdings.append(active)
        result[arm] = holdings
    return result


def main():
    if (EXP/'summary.json').is_file():
        raise FileExistsError('Completed monthly spot ranking')
    symbols = sorted(x[:-4] for x in json.loads((spot.source.EXP/'summary.json').read_text())['symbols'])
    loaded, scores, executable = market_panel(symbols)
    choices = targets(scores, executable, symbols)
    scores.to_csv(EXP/'monthly_scores.csv.gz', index_label='monthly_decision_ms')
    choices.to_csv(EXP/'chosen.csv.gz', index_label='next_native_open_ms')
    results = {}
    for arm in choices:
        hours = loaded['BTC']['bars1'].index
        holding = choices[arm].reindex(hours, method='ffill')
        missing = {s:int((holding.eq(i) & ~loaded[s]['actual']).sum()) for i,s in enumerate(symbols)}
        last = holding.iloc[-1]
        written = last >= 0 and not loaded[symbols[last]]['actual'].iloc[-1]
        for slip in (0., .0005):
            key = arm+('_stress' if slip else '')
            result = rotation.run(loaded, choices, arm, slip, symbols=symbols,
                                  directory=EXP, fee_rate=spot.FEE)
            result.update(missing_held_hours=missing, terminal_writeoff=written)
            results[key] = result
            print(key, result['metrics']['all'], flush=True)
    rotation.source._write_json(EXP/'summary.json',dict(symbols=symbols, arms=results))


if __name__ == '__main__':
    main()
