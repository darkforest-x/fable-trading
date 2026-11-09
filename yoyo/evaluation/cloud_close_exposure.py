"""Research position exposure tiers for the frozen 12H cloud-close account.

This consumes the reward study's frozen paths and uses its preparation/admission
and hourly marks. Long and short positions are represented by positive,
fully-collateralized direction-aware synthetic linear-perpetual shares. A
vectorized maintenance-boundary audit invalidates any breached tier; it never
invents liquidation fills. The existing 1x account artifact is reused.
"""
from __future__ import annotations

import copy
import gc
import json
from pathlib import Path

import numpy as np
import pandas as pd
import vectorbt as vbt
from numba import njit
from vectorbt.portfolio import nb
from vectorbt.portfolio.enums import NoOrder

from yoyo.evaluation import cloud_close_admission as admission
from yoyo.evaluation import cloud_close_reward_account as wallet

ROOT = wallet.ROOT
EXP = ROOT / 'experiments/active/exp-cloud-close-exposure-20261109-v1'
REWARD = wallet.EXP
ADMISSION = admission.EXP
SLOTS = 12
EXPOSURES = (0.5, 1.0, 1.5, 2.0, 3.0)
TAKER = wallet.TAKER
MAKER = 0.0002
MMR = 0.10
STRESS_SLIP = 0.0005
HOUR = wallet.HOUR


def effective_leverage(exposure: float) -> float:
    """Return notional per unit isolated margin, at least fully collateralized."""
    if exposure <= 0:
        raise ValueError('Exposure must be positive')
    return max(1.0, float(exposure))


def liquidation_threshold(side: int, exposure: float, slip: float = 0.0,
                          mmr: float = MMR, entry_fee: float = TAKER,
                          exit_fee: float = TAKER) -> float | None:
    """Raw spot/entry ratio at which isolated equity reaches maintenance + close cost.

    The entry and exit include adverse market slippage. A nonpositive long
    threshold means a positive spot price cannot reach liquidation.
    """
    lev = effective_leverage(exposure)
    if side > 0:
        den = lev * (1.0 - slip) * (1.0 - exit_fee - mmr)
        num = lev * (1.0 + slip) * (1.0 + entry_fee) - 1.0
    elif side < 0:
        den = lev * (1.0 + slip) * (1.0 + exit_fee + mmr)
        num = 1.0 + lev * (1.0 - slip) * (1.0 - entry_fee)
    else:
        raise ValueError('Side must be -1 or +1')
    if den <= 0:
        return None
    ratio = num / den
    return float(ratio) if ratio > 0 else None


def remaining_equity_per_margin(side: int, exposure: float, spot_ratio: float,
                                slip: float = 0.0, entry_fee: float = TAKER,
                                exit_fee: float = TAKER) -> float:
    """Equity after a conservative taker close, divided by initial isolated margin."""
    lev = effective_leverage(exposure)
    entry_exec = 1.0 + side * slip
    exit_exec = spot_ratio * (1.0 - side * slip)
    return float(1.0 + side * lev * (exit_exec - entry_exec)
                 - lev * entry_exec * entry_fee - lev * exit_exec * exit_fee)


def _margin_audit(selected: pd.DataFrame, exposure: float, slip: float,
                  mmr: float = MMR, *, quote_loader=None) -> tuple[pd.DataFrame, dict]:
    """Vectorized per-position boundary audit; never creates liquidation fills."""
    loader = wallet.quotes if quote_loader is None else quote_loader
    out = selected.copy()
    lev = effective_leverage(exposure)
    out['liquidation_threshold_ratio'] = [
        liquidation_threshold(int(r.side), exposure, slip, mmr) for r in out.itertuples()]
    out['min_remaining_equity_per_margin'] = np.nan
    out['min_maintenance_buffer_per_margin'] = np.nan
    out['maintenance_boundary_hit'] = False
    totals = dict(positions_audited=len(out), maintenance_boundary_hits=0,
                  negative_equity_positions=0,
                  unquoted_position_hours=0, worst_margin_buffer_per_margin=float('inf'),
                  worst_remaining_equity_per_margin=float('inf'))
    for idx, row in out.iterrows():
        clock, bars = loader(row.symbol)
        first = int(np.searchsorted(clock, int(row.entry_ms)))
        last = int(np.searchsorted(clock, int(row.exit_bar_ms)))
        if first >= len(clock) or last >= len(clock) or int(clock[first]) != int(row.entry_ms) or int(clock[last]) != int(row.exit_bar_ms):
            raise ValueError(f'Entry or exit has no native OHLC: {row.event_id}')
        times = clock[first:last+1]
        observed = bars[first:last+1]
        full_times = np.arange(int(row.entry_ms), int(row.exit_bar_ms)+HOUR, HOUR, dtype=np.int64)
        loc = ((times - int(row.entry_ms)) // HOUR).astype(np.int64)
        if len(times) != len(full_times):
            totals['unquoted_position_hours'] += len(full_times)-len(times)
            prev = np.searchsorted(loc, np.arange(len(full_times)), side='right')-1
            full_bars = np.empty((len(full_times), 4), dtype=float)
            full_bars[:] = observed[-1, 3]
            valid = prev >= 0
            full_bars[valid] = observed[prev[valid], 3, None]
            full_bars[loc] = observed
        else:
            full_bars = observed

        side = int(row.side)
        entry = float(row.entry_price)
        # Adverse OHLC is a single vector. A planned open exit sees only its open;
        # an intrabar exit conservatively sees the whole exit bar.
        adverse = (full_bars[:, 2] if side > 0 else full_bars[:, 1]).astype(float)
        if str(row.exit_phase) == 'open':
            adverse[-1] = float(full_bars[-1, 0])
        ratios = adverse / entry
        exit_exec = ratios * (1.0 - side * slip)
        entry_exec = 1.0 + side * slip
        remaining = (1.0 + side * lev * (exit_exec-entry_exec)
                     - lev * entry_exec * TAKER - lev * exit_exec * TAKER)
        maintenance = lev * ratios * mmr
        buffers = remaining - maintenance
        min_i = int(np.argmin(buffers))
        min_buffer = float(buffers[min_i])
        min_equity = float(remaining[min_i])
        hit = bool(min_buffer <= 1e-12 or min_equity <= 1e-12)
        out.at[idx, 'min_remaining_equity_per_margin'] = min_equity
        out.at[idx, 'min_maintenance_buffer_per_margin'] = min_buffer
        out.at[idx, 'maintenance_boundary_hit'] = hit
        totals['maintenance_boundary_hits'] += int(hit)
        totals['negative_equity_positions'] += int(min_equity < 0.)
        totals['worst_margin_buffer_per_margin'] = min(totals['worst_margin_buffer_per_margin'], min_buffer)
        totals['worst_remaining_equity_per_margin'] = min(totals['worst_remaining_equity_per_margin'], min_equity)
    if not len(out):
        totals['worst_margin_buffer_per_margin'] = None
        totals['worst_remaining_equity_per_margin'] = None
    totals['valid'] = totals['maintenance_boundary_hits'] == 0
    return out, totals


def _syntheticize(data: dict, exposure: float, slip: float) -> tuple[dict, float]:
    """Convert source spot marks/fills to leveraged, fee-adjusted linear shares."""
    out = data
    selected = out['selected']
    leverage = effective_leverage(exposure)
    marks = out['marks'].copy()
    prices = out['prices'].copy()
    fees = out['fees'].copy()
    high = out['high'].copy()
    low = out['low'].copy()
    actions = out['actions']
    start = int(out['start'])

    for row in selected.itertuples():
        first = int((int(row.entry_ms) - start) // HOUR)
        last = int((int(row.exit_bar_ms) - start) // HOUR)
        entry = float(row.entry_price)
        section = slice(first * 3, (last + 1) * 3)
        side = int(row.side)
        marks[section, row.slot] = 1.0 + leverage * side * (marks[section, row.slot] / entry - 1.0)
        raw_high = high[first:last+1, row.slot].copy()
        raw_low = low[first:last+1, row.slot].copy()
        if side > 0:
            high[first:last+1, row.slot] = leverage * raw_high / entry
            low[first:last+1, row.slot] = leverage * raw_low / entry
        else:
            high[first:last+1, row.slot] = -leverage * raw_low / entry
            low[first:last+1, row.slot] = -leverage * raw_high / entry

        entry_stage = first * 3
        exit_stage = last * 3 + (1 if row.exit_phase == 'open' else 2)
        entry_ratio = 1.0 + side * slip
        entry_synthetic = 1.0 + leverage * side * (entry_ratio - 1.0)
        prices[entry_stage, row.slot] = entry_synthetic
        entry_fee_rate = float(fees[entry_stage, row.slot])
        fees[entry_stage, row.slot] = leverage * entry_ratio * entry_fee_rate / entry_synthetic

        exit_ratio = float(row.exit_price / entry)
        if float(row.exit_fee_rate) >= TAKER:
            exit_ratio *= (1.0 - side * slip)
        exit_synthetic = 1.0 + leverage * side * (exit_ratio - 1.0)
        if exit_synthetic <= 0:
            raise ValueError(f'Nonpositive synthetic fill price: {row.event_id}')
        prices[exit_stage, row.slot] = exit_synthetic
        fee_rate = float(row.exit_fee_rate)
        fees[exit_stage, row.slot] = leverage * exit_ratio * fee_rate / exit_synthetic
        # Every synthetic collateral share is positive; direction lives in its
        # price transform rather than in a negative-position cash lock.
        out['actions'][entry_stage, row.slot] = 1
        if row.exit_phase == 'open':
            # The position is flat after stage 1; do not carry a stale, possibly
            # nonpositive transformed close as an unheld mark.
            marks[last * 3 + 2, row.slot] = 1.0
        marks[exit_stage, row.slot] = 1.0
        if np.any(marks[first*3:exit_stage, row.slot] <= 0):
            raise ValueError(f'Active synthetic mark became nonpositive without an audited breach: {row.event_id}')

    if np.any(prices[actions != 0] <= 0):
        raise ValueError('Nonpositive synthetic fill remains after the maintenance audit')
    out['marks'] = marks
    out['prices'] = prices
    out['fees'] = fees
    out['high'] = high
    out['low'] = low
    return out, leverage


@njit(cache=True)
def pre_segment(c):
    for col in range(c.from_col, c.to_col):
        c.last_val_price[col] = c.close[c.i, col]
    return (nb.get_group_value_ctx_nb(c),)


@njit(cache=True)
def exposure_order(c, nav, actions, prices, fees, exposure, leverage, slip, reservation):
    action = actions[c.i, c.col]
    if action == 0:
        return NoOrder
    fee = fees[c.i, c.col]
    price = prices[c.i, c.col]
    if action == 2:
        if c.position_now == 0.:
            return NoOrder
        return nb.order_nb(size=-c.position_now, price=price, fees=fee,
                           slippage=0., lock_cash=True, allow_partial=False,
                           raise_reject=True)
    if c.position_now != 0.:
        raise ValueError('Position collision')
    capacity = c.group_len
    flat_slots = 0
    for child in range(c.from_col, c.to_col):
        if c.last_position[child] == 0.:
            flat_slots += 1
    margin_fraction = 1.0 if exposure >= 1.0 else exposure
    reservation[0] = min(nav * margin_fraction / capacity,
                         c.free_cash_now / max(1, flat_slots))
    if reservation[0] < 1.e-8:
        return NoOrder
    # Conservative sizing also covers the source wallet's 1x market-slippage
    # convention. The synthetic index already embeds the actual adverse price.
    size = action * (reservation[0] / ((1. + leverage * slip) * (1. + leverage * 0.0005)))
    size *= (1. - 1.e-10)
    return nb.order_nb(size=size, price=price, fees=fee, slippage=0.,
                       lock_cash=True, allow_partial=False, raise_reject=True)


def _portfolio(data: dict, exposure: float, leverage: float, slip: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    actions = data['actions']
    pf = vbt.Portfolio.from_order_func(
        data['marks'], exposure_order, actions, data['prices'], data['fees'],
        exposure, leverage, slip, np.zeros(1), pre_segment_func_nb=pre_segment,
        cash_sharing=True, group_by=True, init_cash=1000.,
        max_orders=int(np.count_nonzero(actions)) + 1, fill_pos_record=False)
    order_records = pf.orders.records
    roster = data['selected'].copy()
    start = int(data['start'])
    entry_stage = (roster.entry_ms - start) // HOUR * 3
    exit_stage = ((roster.exit_bar_ms - start) // HOUR * 3
                  + np.where(roster.exit_phase.eq('open'), 1, 2))
    keyed = order_records.set_index(['idx', 'col'])
    entries = keyed.reindex(pd.MultiIndex.from_arrays([entry_stage, roster.slot]))
    exits = keyed.reindex(pd.MultiIndex.from_arrays([exit_stage, roster.slot]))
    filled = entries['size'].notna().to_numpy()
    if not np.array_equal(filled, exits['size'].notna().to_numpy()):
        raise ValueError('Exposure account has an unclosed position')
    entries, exits = entries[filled], exits[filled]
    roster = roster.iloc[np.flatnonzero(filled)].copy()
    if not np.allclose(entries['size'], exits['size'], rtol=1e-9, atol=1e-12):
        raise ValueError('Exposure account entry/exit quantity mismatch')
    quantity = entries['size'].to_numpy()
    entry_price_s = entries.price.to_numpy()
    exit_price_s = exits.price.to_numpy()
    entry_fee = entries.fees.to_numpy()
    exit_fee = exits.fees.to_numpy()
    entry_ratio = 1. + roster.side.to_numpy() * slip
    roster['margin_usdt'] = quantity
    roster['quantity'] = quantity
    roster['entry_notional'] = quantity * leverage * entry_ratio
    roster['notional'] = roster.entry_notional
    roster['cash_pnl'] = (exit_price_s-entry_price_s) * quantity - entry_fee - exit_fee
    roster['net_return'] = roster.cash_pnl / roster.notional
    roster['min_isolated_remaining_usdt'] = quantity * roster.min_remaining_equity_per_margin
    roster['min_maintenance_surplus_usdt'] = quantity * roster.min_maintenance_buffer_per_margin

    nav = np.asarray(pf.value()).reshape(-1, 3)
    if np.any(~np.isfinite(nav)) or float(nav.min()) < -1e-8:
        raise ValueError('Portfolio equity became negative or nonfinite')
    if not np.isclose(nav[-1, -1], 1000. + roster.cash_pnl.sum(), rtol=1e-8, atol=1e-7):
        raise ValueError('Final NAV does not reconcile to funded trade cash PnL')
    qty = np.asarray(pf.assets())[1::3]
    favorable = data['high']
    adverse = data['low']
    upper = np.maximum(nav.max(axis=1), nav[:, 1] + (qty * favorable).sum(axis=1))
    lower = np.minimum(nav.min(axis=1), nav[:, 1] + (qty * adverse).sum(axis=1))
    index = pd.to_datetime(start + HOUR * (np.arange(len(nav)) + 1), unit='ms', utc=True)
    equity = pd.DataFrame(dict(nav=nav[:, 2], open_nav=nav[:, 0],
                               open_exit_nav=nav[:, 1], lower_nav=lower,
                               upper_nav=upper), index=index)
    return roster, equity


def _periods(roster: pd.DataFrame, equity: pd.DataFrame) -> dict:
    limits = {'full_history': (None, None), 'development': (None, '2025-01-01T00:00Z'),
              '2025': ('2025-01-01T00:00Z', '2026-01-01T00:00Z'),
              '2026_ytd': ('2026-01-01T00:00Z', None)}
    return {name: wallet.summarize(roster, equity, *lim)
            for name, lim in limits.items()}


def _read_frozen_pick() -> tuple[pd.DataFrame, str, int, dict]:
    reward_summary_path = REWARD / 'account_summary.json'
    admission_summary_path = ADMISSION / 'summary.json'
    for path in (reward_summary_path, admission_summary_path,
                 REWARD / 'account_metrics.csv', ADMISSION / 'account_metrics.csv'):
        if not path.is_file():
            raise FileNotFoundError(f'Parent development pick is not ready: {path}')
    reward_summary = json.loads(reward_summary_path.read_text())
    admission_summary = json.loads(admission_summary_path.read_text())
    tp_r = int(reward_summary['pick'])
    rule = str(admission_summary['pick'])
    if int(admission_summary['parent_tp_r']) != tp_r:
        raise ValueError('Admission result does not match frozen reward pick')
    if rule != 'all' and rule not in admission_summary.get('eligible', []):
        raise ValueError('Admission pick is not among its development-eligible rules')
    if rule == 'all' and tp_r != 0 and not reward_summary.get('development_eligible'):
        raise ValueError('No qualifying exit exists but fallback is not the original cloud runner')

    paths = wallet.read_paths()
    paths = paths[paths.tp_r.eq(tp_r) & ~paths.is_control].copy()
    if rule != 'all':
        src = pd.read_csv(wallet.SOURCE / 'signals.csv', float_precision='round_trip')
        src.index = pd.RangeIndex(1, len(src)+1, name='source_row_number')
        rules = admission.gates(src)
        if rule not in rules:
            raise ValueError(f'Unknown admission development pick: {rule}')
        admitted_rows = src.index[rules[rule].fillna(False)]
        paths = paths[paths.source_row_number.isin(admitted_rows)].copy()
    if paths.empty:
        raise ValueError('Frozen development pick has no signal paths')
    identity = dict(tp_r=tp_r, admission_rule=rule,
                    reward_summary=str(reward_summary_path.relative_to(ROOT)),
                    admission_summary=str(admission_summary_path.relative_to(ROOT)),
                    selected_signal_paths=len(paths))
    return paths, rule, tp_r, identity


def _baseline_artifacts(rule: str, tp_r: int) -> tuple[Path, Path, Path, str]:
    if rule == 'all':
        trades = REWARD / f'funded_tp{tp_r}.csv.gz'
        curve = REWARD / f'hourly_tp{tp_r}.csv.gz'
        metrics = REWARD / 'account_metrics.csv'
        label = 'reward'
    else:
        trades = ADMISSION / f'funded_{rule}.csv.gz'
        curve = ADMISSION / f'hourly_{rule}.csv.gz'
        metrics = ADMISSION / 'account_metrics.csv'
        label = 'admission'
    for path in (trades, curve, metrics):
        if not path.is_file():
            raise FileNotFoundError(f'Frozen parent account artifact is not ready: {path}')
    return trades, curve, metrics, label


def _load_one_x(rule: str, tp_r: int) -> tuple[pd.DataFrame, pd.DataFrame, dict, dict]:
    trades_path, curve_path, metrics_path, label = _baseline_artifacts(rule, tp_r)
    trades = pd.read_csv(trades_path, float_precision='round_trip')
    curve = pd.read_csv(curve_path, index_col='hour_end_utc', parse_dates=True,
                        float_precision='round_trip')
    if curve.index.tz is None:
        curve.index = curve.index.tz_localize('UTC')
    metrics_frame = pd.read_csv(metrics_path)
    if label == 'reward':
        ref = metrics_frame[metrics_frame.tp_r.eq(tp_r)]
        key_name, key_value = 'tp_r', tp_r
    else:
        ref = metrics_frame[metrics_frame.rule.eq(rule)]
        key_name, key_value = 'rule', rule
    if len(ref) != 4 or set(ref.period) != {'all', 'dev', '2025', '2026'}:
        raise ValueError(f'Unexpected parent 1x metric rows for {key_name}={key_value}')
    roster = trades.copy()
    if 'net_return' not in roster:
        roster['net_return'] = roster.cash_pnl / roster.notional
    computed = _periods(roster, curve)
    source_all = ref[ref.period.eq('all')].iloc[0]
    recomputed_all = computed['full_history']
    checks = dict(source=label, trades=len(roster), computed_closed_trades=int(recomputed_all['trades']),
                  source_terminal_usdt=float(source_all.final_usdt),
                  recomputed_terminal_usdt=float(recomputed_all['final_usdt']),
                  final_nav_matches=bool(np.isclose(source_all.final_usdt, recomputed_all['final_usdt'], rtol=1e-9, atol=1e-6)),
                  cash_pnl_sum=float(roster.cash_pnl.sum()),
                  cash_to_curve_matches=bool(np.isclose(1000.+roster.cash_pnl.sum(), curve.nav.iloc[-1], rtol=1e-8, atol=1e-6)),
                  trade_count_matches=bool(len(roster) == int(recomputed_all['trades'])),
                  trade_identity_unique=bool(roster.event_id.is_unique),
                  trades_file=str(trades_path.relative_to(ROOT)),
                  curve_file=str(curve_path.relative_to(ROOT)),
                  metrics_file=str(metrics_path.relative_to(ROOT)))
    if not checks['final_nav_matches'] or not checks['cash_to_curve_matches'] or not checks['trade_count_matches']:
        raise ValueError(f'Existing 1x account does not reconcile: {checks}')
    if 'slot' in roster and 'event_id' in roster and roster.event_id.duplicated().any():
        raise ValueError('Frozen unsplit 1x baseline has duplicate event IDs')
    return roster, curve, computed, checks


def _scenario(events: pd.DataFrame, exposure: float, slip: float) -> tuple[dict, dict, pd.DataFrame, pd.DataFrame]:
    # Preserve frozen admissions/order; never synthesize liquidation fills.
    selected, skips = wallet.admit(events, SLOTS)
    selected, audit = _margin_audit(selected, exposure, slip)
    if not audit['valid']:
        metadata = dict(invalid_reason='At least one admitted position reaches the assumed isolated maintenance boundary; this exposure scenario is not simulated.',
                       exposure=float(exposure), slip=float(slip),
                       admitted=len(selected), **audit, **skips)
        return {}, metadata, pd.DataFrame(), pd.DataFrame()
    raw = wallet.prepare(None, SLOTS, assigned=(selected, skips))
    data, leverage = _syntheticize(raw, exposure, slip)
    roster, equity = _portfolio(data, exposure, leverage, slip)
    metrics = _periods(roster, equity)
    metadata = dict(exposure=float(exposure), effective_isolated_leverage=leverage,
                    slip=float(slip), admitted=len(selected), funded=len(roster),
                    gap_marks=int(data['gap_marks']),
                    minimum_isolated_remaining_usdt=float(roster.min_isolated_remaining_usdt.min()) if len(roster) else None,
                    minimum_maintenance_surplus_usdt=float(roster.min_maintenance_surplus_usdt.min()) if len(roster) else None,
                    **audit, **skips)
    if (roster.min_isolated_remaining_usdt < -1e-8).any() or (roster.min_maintenance_surplus_usdt < -1e-8).any():
        raise ValueError(f'Isolated-margin audit disagrees with no-breach roster: {metadata}')
    return metrics, metadata, roster, equity


def _metric_rows(exposure: float, scenario: str, metrics: dict, metadata: dict) -> list[dict]:
    rows = []
    for period, values in metrics.items():
        rows.append(dict(exposure_multiple=float(exposure), scenario=scenario,
                         valid=bool(metadata.get('valid', True)), period=period,
                         positions_audited=metadata.get('positions_audited'),
                         maintenance_boundary_hits=metadata.get('maintenance_boundary_hits', 0),
                         negative_equity_positions=metadata.get('negative_equity_positions', 0),
                         worst_margin_buffer_per_margin=metadata.get('worst_margin_buffer_per_margin'),
                         worst_remaining_equity_per_margin=metadata.get('worst_remaining_equity_per_margin'),
                         unquoted_position_hours=metadata.get('unquoted_position_hours', 0),
                         min_isolated_remaining_usdt=metadata.get('minimum_isolated_remaining_usdt'),
                         min_maintenance_surplus_usdt=metadata.get('minimum_maintenance_surplus_usdt'),
                         **values))
    if not metrics:
        for period in ('full_history', 'development', '2025', '2026_ytd'):
            rows.append(dict(exposure_multiple=float(exposure), scenario=scenario,
                             valid=False, period=period,
                             positions_audited=metadata.get('positions_audited'),
                             maintenance_boundary_hits=metadata.get('maintenance_boundary_hits', 0),
                             negative_equity_positions=metadata.get('negative_equity_positions', 0),
                             worst_margin_buffer_per_margin=metadata.get('worst_margin_buffer_per_margin'),
                             worst_remaining_equity_per_margin=metadata.get('worst_remaining_equity_per_margin'),
                             unquoted_position_hours=metadata.get('unquoted_position_hours', 0),
                             invalid_reason=metadata.get('invalid_reason')))
    return rows


def _baseline_stress(rule: str, tp_r: int) -> dict | None:
    """Use an already-computed parent 5bp scenario when available."""
    if rule == 'all':
        summary = json.loads((REWARD / 'account_summary.json').read_text())
        return summary.get('checks', {}).get(str(tp_r), {}).get('slippage_5bp', {}).get('metrics')
    summary = json.loads((ADMISSION / 'summary.json').read_text())
    return summary.get('checks', {}).get(rule, {}).get('slippage_5bp', {}).get('metrics')


def _normalize_parent_metrics(metrics: dict) -> dict:
    names = {'all': 'full_history', 'dev': 'development', '2025': '2025', '2026': '2026_ytd'}
    return {names.get(period, period): values for period, values in metrics.items()}


def main() -> None:
    cfg = json.loads((EXP / 'config.json').read_text())
    if tuple(float(x) for x in cfg['account']['exposure_multiples']) != EXPOSURES:
        raise ValueError('Exposure tiers differ from the frozen config')
    if int(cfg['account']['slots']) != SLOTS:
        raise ValueError('Slot count differs from the frozen config')
    paths, rule, tp_r, pick_identity = _read_frozen_pick()
    base_roster, base_curve, base_metrics, reconciliation = _load_one_x(rule, tp_r)
    rows: list[dict] = []
    metadata: dict = {}
    outputs: dict = {}

    one_x_meta = dict(valid=True, exposure=1.0, effective_isolated_leverage=1.0,
                      source='reused_parent_1x_artifact',
                      minimum_isolated_remaining_usdt=None,
                      minimum_maintenance_surplus_usdt=None)
    _, base_margin_audit = _margin_audit(base_roster, 1.0, 0.0)
    one_x_meta.update(base_margin_audit)
    one_x_meta['valid'] = bool(base_margin_audit['valid'])
    if not one_x_meta['valid']:
        one_x_meta['invalid_reason'] = 'The frozen 1x account has at least one position crossing the assumed maintenance boundary; its no-liquidation curve is descriptive only.'
    rows.extend(_metric_rows(1.0, 'base_reused_parent', base_metrics, one_x_meta))
    metadata['1.0/base_reused_parent'] = one_x_meta
    outputs['1.0/base_reused_parent'] = (base_roster, base_curve)

    tasks = [(x, scenario, slip) for x in EXPOSURES if x != 1.0
             for scenario, slip in (('base', 0.0), ('stress_5bp', STRESS_SLIP))]
    # The 1x stress row is reused from the frozen parent account's existing
    # scenario when available; otherwise run only this distinct stress case.
    one_x_stress = _baseline_stress(rule, tp_r)
    if one_x_stress:
        _, stress_margin_audit = _margin_audit(base_roster, 1.0, STRESS_SLIP)
        one_x_stress = _normalize_parent_metrics(one_x_stress)
        stress_meta = dict(exposure=1.0, effective_isolated_leverage=1.0,
                           source='reused_parent_5bp_stress', **stress_margin_audit)
        if not stress_meta['valid']:
            stress_meta['invalid_reason'] = 'The frozen 1x stress account has at least one position crossing the assumed maintenance boundary.'
        rows.extend(_metric_rows(1.0, 'stress_5bp_reused_parent', one_x_stress, stress_meta))
        metadata['1.0/stress_5bp_reused_parent'] = stress_meta
    else:
        tasks.insert(0, (1.0, 'stress_5bp', STRESS_SLIP))

    for exposure, scenario, slip in tasks:
        metrics, meta, trades, equity = _scenario(paths, exposure, slip)
        key = f'{exposure:.1f}/{scenario}'
        metadata[key] = meta
        rows.extend(_metric_rows(exposure, scenario, metrics, meta))
        if meta.get('valid'):
            outputs[key] = (trades, equity)
        del metrics, trades, equity
        gc.collect()

    table = pd.DataFrame(rows)
    table.to_csv(EXP / 'account_metrics.csv', index=False)
    chosen_keys = []
    # Keep complete per-trade and hourly records for the selected exposure and
    # stress comparison. The study is descriptive: no post-result sizing pick.
    for x in EXPOSURES:
        for scenario in ('base', 'stress_5bp'):
            key = f'{x:.1f}/{scenario}'
            if key in outputs:
                trades, equity = outputs[key]
                tag = f'{x:g}x_{scenario}'
                trades.to_csv(EXP / f'funded_{tag}.csv.gz', index=False)
                equity.to_csv(EXP / f'hourly_{tag}.csv.gz', index_label='hour_end_utc')
                chosen_keys.append(key)

    summary = dict(engine=vbt.__version__, selection=pick_identity,
                   exposure_multiples=list(EXPOSURES), slots=SLOTS,
                   maintenance_margin_rate_assumption=MMR,
                   costs=dict(taker=TAKER, maker=MAKER, extra_market_slippage=STRESS_SLIP),
                   periods=['development', '2025', '2026_ytd', 'full_history'],
                   one_x_reconciliation=reconciliation,
                   one_x_full_account_rerun=False,
                   existing_one_x_stress_reused=bool(one_x_stress),
                   scenario_audits=metadata,
                   saved_candidate_curves=chosen_keys,
                   limitations=[
                       'Funding is not modeled; sustained positions can incur material perpetual funding.',
                       'Maintenance margin is a conservative 10% assumption, not a verified contract/tier schedule; actual OKX liquidation bands, fees and bankruptcy mechanics can differ.',
                       'No order-book depth, capacity, lot rounding, market impact beyond the explicit 5bp stress, or fee-tier change is modeled.',
                       'The parent wallet carries the previous observed close through unquoted hours; the count is reported, and unobserved intrahour paths remain unknown.',
                       'Any assumed maintenance-boundary touch invalidates the entire exposure/scenario; this builder does not create synthetic liquidation fills.',
                       'Same-hour OHLC maintenance/strategy-exit ordering is unresolved and is treated as a boundary breach.'
                   ])
    (EXP / 'summary.json').write_text(json.dumps(summary, indent=2, allow_nan=False) + '\n')
    print(table.to_string(index=False), flush=True)


def _toy_prepared() -> dict:
    """Three-bar long/short fixture for PnL and shared-cash equivalence."""
    rows, slots = 9, SLOTS
    marks = np.ones((rows, slots))
    actions = np.zeros((rows, slots), dtype=np.int8)
    prices = np.ones((rows, slots))
    fees = np.zeros((rows, slots))
    high = np.zeros((3, slots))
    low = np.zeros((3, slots))
    marks[0:3, 0] = [100., 100., 101.]
    marks[3:6, 0] = [101., 101., 110.]
    marks[0:3, 1] = [100., 100., 100.]
    marks[3:6, 1] = [100., 100., 90.]
    marks[6:9, 2] = [120., 120., 115.]
    actions[0, 0] = 1
    prices[0, 0] = 100.
    fees[0, 0] = TAKER
    actions[5, 0] = 2
    prices[5, 0] = 110.
    fees[5, 0] = TAKER
    actions[0, 1] = -1
    prices[0, 1] = 100.
    fees[0, 1] = TAKER
    actions[5, 1] = 2
    prices[5, 1] = 90.
    fees[5, 1] = TAKER
    actions[6, 2] = 1
    prices[6, 2] = 120.
    fees[6, 2] = TAKER
    actions[8, 2] = 2
    prices[8, 2] = 115.
    fees[8, 2] = TAKER
    high[:, 0] = [2., 11., 0.]
    low[:, 0] = [-1., -1., 0.]
    high[:, 1] = [1., 1., 0.]
    low[:, 1] = [-1., -10., 0.]
    high[:, 2] = [0., 0., 1.]
    low[:, 2] = [0., 0., -10.]
    selected = pd.DataFrame([
        dict(event_id='toy-long', parent_id='toy-long', symbol='TOYL', side=1,
             entry_ms=wallet.START, exit_bar_ms=wallet.START+HOUR,
             exit_phase='intrabar', entry_price=100., exit_price=110.,
             exit_fee_rate=TAKER, exit_reason='toy_exit', slot=0,
             min_remaining_equity_per_margin=1.,
             min_maintenance_buffer_per_margin=1., liquidated=False),
        dict(event_id='toy-short', parent_id='toy-short', symbol='TOYS', side=-1,
             entry_ms=wallet.START, exit_bar_ms=wallet.START+HOUR,
             exit_phase='intrabar', entry_price=100., exit_price=90.,
             exit_fee_rate=TAKER, exit_reason='toy_exit', slot=1,
             min_remaining_equity_per_margin=1.,
             min_maintenance_buffer_per_margin=1., liquidated=False),
        dict(event_id='toy-next', parent_id='toy-next', symbol='TOYN', side=1,
             entry_ms=wallet.START+2*HOUR, exit_bar_ms=wallet.START+2*HOUR,
             exit_phase='intrabar', entry_price=120., exit_price=115.,
             exit_fee_rate=TAKER, exit_reason='toy_exit', slot=2,
             min_remaining_equity_per_margin=1.,
             min_maintenance_buffer_per_margin=1., liquidated=False),
    ], index=[0, 1, 2])
    return dict(selected=selected, skips={'same_contract': 0, 'all_slots_busy': 0},
                gap_marks=0, marks=marks, prices=prices, fees=fees, actions=actions,
                high=high, low=low, start=wallet.START)


def self_check() -> dict:
    """Bounded VectorBT example: long/short cash parity and 3x boundaries."""
    toy = _toy_prepared()
    base_roster, base_equity = wallet.account(copy.deepcopy(toy), exposure=1.0)
    synthetic, lev = _syntheticize(copy.deepcopy(toy), exposure=1.0, slip=0.0)
    new_roster, new_equity = _portfolio(synthetic, exposure=1.0, leverage=lev, slip=0.0)
    base_roster = base_roster.set_index('event_id').sort_index()
    new_roster = new_roster.set_index('event_id').sort_index()
    if not np.allclose(base_roster.cash_pnl, new_roster.cash_pnl, rtol=1e-10, atol=1e-9):
        raise AssertionError('Toy per-trade cash PnL differs at 1x')
    if not np.allclose(base_roster.notional, new_roster.notional, rtol=1e-10, atol=1e-9):
        raise AssertionError('Toy per-trade entry notional differs at 1x')
    if not np.isclose(base_equity.nav.iloc[-1], new_equity.nav.iloc[-1], rtol=1e-10, atol=1e-9):
        raise AssertionError('Toy terminal NAV differs at 1x')
    base_stress, base_stress_equity = wallet.account(copy.deepcopy(toy), slip=STRESS_SLIP, exposure=1.0)
    synthetic_stress, lev_stress = _syntheticize(copy.deepcopy(toy), exposure=1.0, slip=STRESS_SLIP)
    new_stress, new_stress_equity = _portfolio(synthetic_stress, exposure=1.0,
                                                leverage=lev_stress, slip=STRESS_SLIP)
    base_stress = base_stress.set_index('event_id').sort_index()
    new_stress = new_stress.set_index('event_id').sort_index()
    stress_trade_match = bool(
        np.allclose(base_stress.cash_pnl, new_stress.cash_pnl, rtol=1e-9, atol=1e-8)
        and np.allclose(base_stress.notional, new_stress.notional, rtol=1e-9, atol=1e-8))
    stress_nav_match = bool(np.isclose(base_stress_equity.nav.iloc[-1],
                                       new_stress_equity.nav.iloc[-1], rtol=1e-9, atol=1e-8))
    threshold_long = liquidation_threshold(1, 3.0)
    threshold_short = liquidation_threshold(-1, 3.0)
    if not (0 < threshold_long < 1 < threshold_short):
        raise AssertionError('3x liquidation thresholds are not finite and directional')
    threshold_buffers = {
        'long': remaining_equity_per_margin(1, 3.0, threshold_long)-3.*threshold_long*MMR,
        'short': remaining_equity_per_margin(-1, 3.0, threshold_short)-3.*threshold_short*MMR,
    }
    if any(abs(value) > 1e-12 for value in threshold_buffers.values()):
        raise AssertionError('Fee-inclusive maintenance boundaries do not balance')
    result = dict(toy_long_cash_pnl=float(new_roster.loc['toy-long', 'cash_pnl']),
                  toy_short_cash_pnl=float(new_roster.loc['toy-short', 'cash_pnl']),
                  toy_long_notional=float(new_roster.loc['toy-long', 'notional']),
                  toy_short_notional=float(new_roster.loc['toy-short', 'notional']),
                  toy_later_entry_notional=float(new_roster.loc['toy-next', 'notional']),
                  toy_terminal_usdt=float(new_equity.nav.iloc[-1]),
                  parent_terminal_usdt=float(base_equity.nav.iloc[-1]),
                  one_x_long_short_pnl_notional_match=True,
                  one_x_shared_cash_terminal_nav_match=True,
                  one_x_stress_trade_match=stress_trade_match,
                  one_x_stress_terminal_nav_match=stress_nav_match,
                  long_3x_liquidation_ratio=threshold_long,
                  short_3x_liquidation_ratio=threshold_short,
                  liquidation_threshold_buffer_per_margin=threshold_buffers)
    if not stress_trade_match or not stress_nav_match:
        raise AssertionError('1x stress toy does not match short/long shared-cash accounting')
    print(json.dumps(result, indent=2), flush=True)
    return result


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--self-check', action='store_true')
    args = parser.parse_args()
    if args.self_check:
        self_check()
    else:
        main()
