"""Compare native hourly-entry and 12H-confirmation fills on frozen ETH history.

The framework sees the entry/stop and the close-boundary failure event only.
Reference peaks, later matching labels and all post-fill bars are used solely
for attribution. Every opportunity in both frozen streams is executed.
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.cloud_eth_early_inputs import load_inputs
from yoyo.layers.l3_backtest.nautilus_cloud_hourly import run_cloud_hourly


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'experiments/active/exp-cloud-eth-hourly-early-execution-20261115-v1'


def enrich(frame, trades, risk_budget):
    """Measure excursions only while a filled position was open."""
    positions = {t: i for i, t in enumerate(frame.index.as_unit('ms').asi8)}
    highs, lows, opens = (frame[k].to_numpy() for k in ('high', 'low', 'open'))
    records = []
    for t in trades.to_dict('records'):
        i, j = positions[t['entry_ms']], positions[t['exit_hour_ms']]
        side, entry, qty = t['side'], t['entry_px'], t['quantity']
        distance = side * (entry - t['stop'])
        risk = qty * distance
        if i > j or abs(risk - risk_budget) > 0.01 or distance <= 0:
            raise ValueError(f"Invalid holding interval or initial risk: {t['trade_id']}")
        phase = t['exit_phase']
        if phase not in {'open', 'intrabar', 'close'}:
            raise ValueError(f'Unknown exit phase {phase}')
        favorable = highs if side == 1 else lows
        # The exit-hour open was observable regardless of how its OHLC unfolded.
        known = max(0, side * (opens[j] - entry), side * (t['exit_px'] - entry))
        last = j + int(phase == 'close')
        if last > i:
            known = max(known, np.max(side * (favorable[i:last] - entry)))
        possible = max(known, side * (favorable[j] - entry)) if phase == 'intrabar' else known
        fees = t['entry_fee'] + t['exit_fee']
        gross = side * (t['exit_px'] - entry) * qty
        records.append(dict(**t, risk_usdt=risk, gross_r=gross / risk_budget,
                            fee_r=fees / risk_budget, net_r=(gross - fees) / risk_budget,
                            peak_lower_r_actual=known / distance, peak_upper_r_actual=possible / distance))
    return pd.DataFrame(records)


def drawdown(frame, trades, budget):
    """Sum each fill's realized and open P&L at the observed hourly closes."""
    positions = {t: i for i, t in enumerate(frame.index.as_unit('ms').asi8)}
    closes = frame.close.to_numpy()
    changes = np.zeros(len(frame))
    for t in trades.itertuples():
        i, j = positions[t.entry_ms], positions[t.exit_hour_ms]
        # Independent risks overlap. Charge the entry fee at opening and the
        # exit fee when settled. Differences avoid carrying each trade to tail.
        marks = ((closes[i:j] - t.entry_px) * t.side * t.quantity - t.entry_fee) / budget
        changes[i:j + 1] += np.diff(np.r_[0., marks, t.net_r])
    pnl = np.r_[0., changes.cumsum()]
    if not np.isclose(pnl[-1], trades.net_r.sum()):
        raise ValueError('Closing marks do not reconcile with the fills')
    dd = np.maximum.accumulate(pnl) - pnl
    trough = int(dd.argmax())
    peak = int(pnl[:trough + 1].argmax())
    def close_time(index):
        return (frame.index[index - 1] + pd.Timedelta(hours=1)).isoformat() if index else None
    return dict(hourly_drawdown_r=float(dd[trough]), dd_peak_utc=close_time(peak),
                dd_trough_utc=close_time(trough))


def stats(rows):
    return dict(n=len(rows), wins=int((rows.net_r > 0).sum()),
                win_rate_pct=float(100 * (rows.net_r > 0).mean()),
                net_r=float(rows.net_r.sum()), mean_net_r=float(rows.net_r.mean()),
                gross_r=float(rows.gross_r.sum()), fees_r=float(rows.fee_r.sum()))


def summarize(frame, trades, config):
    result = {}
    formal = trades[trades.cohort == 'confirmed'].set_index('week')
    for cohort, group in trades.groupby('cohort'):
        actual = group if cohort == 'confirmed' else group[group.matched]
        by_level = {}
        for level in config['thresholds_r']:
            weeks = formal.index[formal.peak_lower_r >= level]
            same = actual[actual.week.isin(weeks)]
            executable = formal.index[formal.peak_lower_r_actual >= level]
            executable_same = actual[actual.week.isin(executable)]
            by_level[level] = dict(
                confirmed_execution_keys=len(executable),
                confirmed_execution_keys_recaptured=int((executable_same.peak_lower_r_actual >= level).sum()),
                reference_keys=len(weeks), same_key_trades=len(same),
                same_key_reached=int((same.peak_lower_r_actual >= level).sum()),
                same_key_possible=int((same.peak_upper_r_actual >= level).sum()),
                all_reached=int((group.peak_lower_r_actual >= level).sum()),
                all_possible=int((group.peak_upper_r_actual >= level).sum()),
                realized_net_at_least=int((group.net_r >= level).sum()))
        years = pd.to_datetime(group.entry_ms, unit='ms', utc=True).dt.year
        result[cohort] = dict(**stats(group), **drawdown(frame, group, config['risk_usdt']),
            by_entry_year={int(key): stats(rows) for key, rows in group.groupby(years)},
            by_matched={str(key): stats(rows) for key, rows in group.groupby('matched')},
            by_reason={key: stats(rows) for key, rows in group.groupby('exit_reason')},
            peaks=by_level)
    return result


def check_archived_baseline(trades):
    """Reconcile with the existing 97 ETH trades, stored at unrounded barrier prices."""
    path = OUT.parent / 'exp-cloud-close-reward-20261109-v1/paths.csv.gz'
    fields = ['symbol', 'is_control', 'tp_r', 'week', 'entry_ms', 'side', 'exit_bar_ms',
              'entry_price', 'exit_price', 'net_r']
    historical = pd.read_csv(path, usecols=fields)
    historical = historical[(historical.symbol == 'ETH') & ~historical.is_control & (historical.tp_r == 0)]
    formal = trades[trades.cohort == 'confirmed']
    reconciled = formal.merge(historical, on=['week', 'side', 'entry_ms'], validate='one_to_one',
                              suffixes=('', '_archived'))
    if len(reconciled) != len(historical) or len(reconciled) != len(formal):
        raise ValueError('Formal signals differ from the previously saved execution baseline')
    return dict(source=str(path.relative_to(ROOT)), opportunities=len(reconciled),
                same_entry_price=int(np.isclose(reconciled.entry_px, reconciled.entry_price_archived,
                                                rtol=0., atol=1e-7).sum()),
                same_exit_hour=int((reconciled.exit_hour_ms == reconciled.exit_bar_ms).sum()),
                max_exit_price_difference=float((reconciled.exit_px - reconciled.exit_price).abs().max()),
                old_total_net_r=float(reconciled.net_r_archived.sum()),
                difference_net_r=float((reconciled.net_r - reconciled.net_r_archived).sum()))


def paired_weeks(trades, block_weeks=4, repeats=10000):
    """Resample both arms together using consecutive four-week calendar blocks."""
    by_arm = []
    weeks = pd.to_datetime(trades.week)
    calendar = pd.date_range(weeks.min(), weeks.max(), freq='W-MON')
    for cohort in ('confirmed', 'early'):
        rows = trades[trades.cohort == cohort].set_index('week').copy()
        rows.index = pd.to_datetime(rows.index)
        if not rows.index.is_unique or not rows.index.isin(calendar).all():
            raise ValueError('Each policy may enter at most once per NY week')
        rows['wins'] = (rows.net_r > 0).astype(int)
        rows['count'] = 1
        by_arm.append(rows[['net_r', 'wins', 'count']].reindex(calendar, fill_value=0).to_numpy())
    weekly = np.stack(by_arm, axis=1)
    rng = np.random.default_rng(20261115)
    delta = []
    # Circular block bootstrap, preserving simultaneous signals in both policies.
    for _ in range(repeats // 100):
        start = rng.integers(len(calendar), size=(100, (len(calendar) + block_weeks - 1) // block_weeks))
        indices = (start[:, :, None] + np.arange(block_weeks)) % len(calendar)
        samples = weekly[indices.reshape(100, -1)[:, :len(calendar)]].sum(axis=1)
        rates = 100 * samples[:, :, 1] / samples[:, :, 2]
        delta.extend(zip(samples[:, 1, 0] - samples[:, 0, 0], rates[:, 1] - rates[:, 0]))
    limits = np.percentile(delta, [2.5, 97.5], axis=0)
    return dict(block_weeks=block_weeks, resamples=len(delta), calendar_weeks=len(calendar),
                total_net_r_difference_95=limits[:, 0].tolist(),
                win_rate_difference_pp_95=limits[:, 1].tolist())


def main(reuse_fills=False):
    config = json.loads((OUT / 'config.json').read_text())
    inputs = load_inputs(ROOT)
    frame = pd.DataFrame(inputs.bars1h)
    frame.index = pd.to_datetime(frame.pop('time_ms'), unit='ms', utc=True)
    opportunities = pd.DataFrame(inputs.opportunities).rename(columns={'opportunity_id': 'trade_id'})
    crosses = pd.DataFrame(inputs.cloud_crosses)
    stream = ['trade_id', 'side', 'entry_ms', 'entry_price', 'stop', 'fail_at_ms']
    raw = OUT / 'native_fills.json'
    if reuse_fills:
        saved = json.loads(raw.read_text())
        fills, audit = pd.DataFrame(saved['positions']), saved['audit']
    else:
        fills, audit = run_cloud_hourly(frame, opportunities[stream], crosses,
                                        tick=config['price_tick'], risk_usdt=config['risk_usdt'],
                                        maker=config['maker'], taker=config['taker'])
        raw.write_text(json.dumps(dict(audit=audit, positions=fills.to_dict('records')),
                                  allow_nan=False, indent=2) + '\n')
    if set(fills.trade_id) != set(opportunities.trade_id) or not fills.trade_id.is_unique:
        raise ValueError('Native fills do not cover every frozen opportunity exactly once')
    joined = opportunities.merge(fills, on='trade_id', validate='one_to_one', suffixes=('', '_native'))
    failed = joined[joined.fail_at_ms.notna()]
    if (failed.exit_hour_ms > failed.fail_at_ms).any() or (
        (failed.exit_hour_ms == failed.fail_at_ms) & (failed.exit_phase != 'open')
    ).any():
        raise ValueError('Unconfirmed positions must be flat at the boundary open')
    if 'entry_ms_native' in joined and not (joined.entry_ms == joined.entry_ms_native).all():
        raise ValueError('Actual entry time differs from requested entry open')
    if not np.allclose(joined.entry_price, joined.entry_px, rtol=0., atol=1e-7):
        raise ValueError('Actual entry prices differ from next available opens')
    actual_fees = joined.entry_fee + joined.exit_fee
    expected_fees = (joined.entry_px + joined.exit_px) * joined.quantity * config['taker']
    if not np.allclose(actual_fees, expected_fees, rtol=1e-7, atol=2e-8):
        raise ValueError('Actual commission differs from two taker fills')
    trades = enrich(frame, joined, config['risk_usdt']).sort_values(['entry_ms', 'cohort'])
    results = summarize(frame, trades, config)
    for col in ('entry_ms', 'exit_hour_ms'):
        trades[col.replace('_ms', '_bjt')] = pd.to_datetime(trades[col], unit='ms', utc=True).dt.tz_convert('Asia/Shanghai')
    trades.drop(columns=['entry_ms_native'], errors='ignore').to_csv(OUT / 'trades.csv', index=False)
    data = dict(cutoff_utc=config['cutoff_utc'], audit=audit, arms=results,
                archived_baseline=check_archived_baseline(trades), paired_weeks=paired_weeks(trades))
    (OUT / 'summary.json').write_text(json.dumps(data, indent=2, allow_nan=False) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--reuse-fills', action='store_true',
                        help='Recompute attribution and metrics from saved native execution records.')
    main(parser.parse_args().reuse_fills)
