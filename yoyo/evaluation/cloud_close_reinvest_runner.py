"""Fund one capped re-entry after the original 2R partial has closed.

The original OKX position remains a cash-sharing VectorBT two-leg position:
2/3 closes at its maker 2R limit and 1/3 keeps the original cloud runner.
An already-generated profit-confirm-entry path can buy back the 2/3 leg once,
only after its 2R order closed in an earlier H1 bar and while the runner is
still open. The add-on uses no more than the actually funded target-leg
notional or current VectorBT free cash, whichever is smaller.
"""
from __future__ import annotations

import argparse
import gc
import json
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
import vectorbt as vbt
from numba import njit
from vectorbt.portfolio import nb
from vectorbt.portfolio.enums import NoOrder

from yoyo.evaluation import cloud_close_eligibility_repair as repair
from yoyo.evaluation import cloud_close_profit_confirm_entry as confirmation
from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_thirds as thirds


EXP = wallet.ROOT / 'experiments/active/exp-cloud-close-reinvest-runner-20261109-v1'
BASELINE = repair.EXP / 'thirds' / 'funded_OKX.csv.gz'
OPPORTUNITIES = confirmation.EXP / 'opportunities.csv.gz'
HOUR_MS = wallet.HOUR
TAKER = wallet.TAKER
MAKER = 0.0002
BASE_WEIGHTS = np.array([2. / 3., 1. / 3.], dtype=np.float64)


def _phase_index(phase: str) -> int:
    if phase == 'open':
        return 1
    if phase in ('intrabar', 'close'):
        return 2
    raise ValueError(f'Unknown exit phase: {phase}')


def _phase_order(phase: str) -> int:
    return {'open': 0, 'intrabar': 1, 'close': 2}[phase]


def read_inputs():
    """Read only frozen original paths, corrected account ids, and prior opportunities."""
    paths = thirds.okx_paths()
    paths = paths[(~paths.is_control) & paths.tp_r.isin([0., 2.])].copy()
    if paths.duplicated(['event_id', 'tp_r']).any():
        raise ValueError('Duplicate original event/leg path')
    opportunities = pd.read_csv(OPPORTUNITIES, float_precision='round_trip',
                                dtype={'symbol': str}, keep_default_na=False)
    opportunities = opportunities[~opportunities.is_control].copy()
    if opportunities.event_id.duplicated().any():
        raise ValueError('More than one confirmation opportunity per original event')
    baseline = pd.read_csv(BASELINE, float_precision='round_trip', dtype={'symbol': str})
    if baseline.event_id.duplicated().any():
        raise ValueError('Duplicate frozen funded original event')
    return paths, opportunities, baseline


def _selected_originals(paths: pd.DataFrame, slots: int = 9) -> tuple[pd.DataFrame, dict]:
    """Use the same original runner admission/sort and assign each 2R pair one slot."""
    runner = paths[paths.tp_r.eq(0.)].copy()
    target = paths[paths.tp_r.eq(2.)].set_index('event_id', drop=False)
    selected, skips = wallet.admit(runner, slots)
    if selected.event_id.duplicated().any():
        raise ValueError('Original slot admission selected an event twice')
    if set(selected.event_id) - set(target.index):
        raise ValueError('Selected runner has no 2R partial path')
    out = selected.copy()
    out['slot'] = out.slot.astype('int64')
    # Preserve the parent's exact per-slot target/runner column ordering. Add-on
    # columns come after every original column so a same-open re-entry cannot
    # consume cash ahead of an unrelated original signal.
    out['target_col'] = out.slot * 2
    out['runner_col'] = out.slot * 2 + 1
    out['reinvest_col'] = slots * 2 + out.slot
    target_rows = target.loc[out.event_id].reset_index(drop=True)
    for col in ('entry_ms', 'entry_price', 'side'):
        if not np.allclose(pd.to_numeric(out[col]), pd.to_numeric(target_rows[col]),
                           rtol=1e-10, atol=1e-12):
            raise ValueError(f'Original target/runner entry mismatch: {col}')
    out = out.reset_index(drop=True)
    for col in ('target_exit_bar_ms', 'target_exit_price', 'target_exit_phase',
                'target_exit_fee_rate', 'target_exit_reason'):
        source = {'target_exit_bar_ms': 'exit_bar_ms', 'target_exit_price': 'exit_price',
                  'target_exit_phase': 'exit_phase', 'target_exit_fee_rate': 'exit_fee_rate',
                  'target_exit_reason': 'exit_reason'}[col]
        out[col] = out.event_id.map(target[source])
    return out, skips


def _validate_reinvestment_candidates(base: pd.DataFrame, opportunities: pd.DataFrame,
                                      quote_loader=None) -> tuple[pd.DataFrame, dict]:
    """Apply only the causal fill/runner-live gate and verify shadow exit is runner-bounded."""
    quote_loader = quote_loader or wallet.quotes
    runner = base.set_index('event_id', drop=False)
    counts = Counter()
    kept = []
    matched = opportunities[opportunities.event_id.isin(runner.index)].copy()
    counts['opportunities_on_funded_originals'] = len(matched)
    for row in matched.to_dict('records'):
        r = runner.loc[row['event_id']]
        if int(row['side']) != int(r.side) or row['symbol'] != r.symbol:
            counts['direction_or_symbol_mismatch'] += 1
            continue
        # The resting TP fill must be known from a strictly earlier H1 bar.
        if int(r.target_exit_bar_ms) >= int(row['entry_ms']):
            counts['partial_not_closed_in_prior_h1'] += 1
            continue
        # A runner stopped at this same open is already gone before an open fill.
        if (int(r.exit_bar_ms) < int(row['entry_ms'])
                or (int(r.exit_bar_ms) == int(row['entry_ms']) and r.exit_phase == 'open')):
            counts['runner_not_live_at_entry_open'] += 1
            continue
        if int(row['exit_bar_ms']) > int(r.exit_bar_ms):
            raise ValueError(f'Confirmation shadow extends beyond original runner: {row["event_id"]}')
        clock, hourly = quote_loader(row['symbol'])
        i = int(np.searchsorted(clock, int(row['entry_ms'])))
        if i >= len(clock) or int(clock[i]) != int(row['entry_ms']):
            counts['missing_reentry_open'] += 1
            continue
        if not np.isclose(hourly[i, 0], float(row['entry_price']), rtol=1e-9, atol=1e-12):
            raise ValueError(f'Re-entry price is not the causal H1 open: {row["event_id"]}')
        expected_stop = float(row['shadow_entry_price']) + int(row['side']) * .5 * float(row['shadow_risk_distance'])
        if not np.isclose(expected_stop, float(row['stop_price']), rtol=1e-9, atol=1e-12):
            raise ValueError(f'Re-entry stop differs from original-R +0.5 lock: {row["event_id"]}')

        runner_phase = str(r.exit_phase)
        add_phase = str(row['exit_phase'])
        sync = False
        same_bar_order = 'earlier_bar'
        if int(row['exit_bar_ms']) == int(r.exit_bar_ms):
            rp, ap = _phase_order(runner_phase), _phase_order(add_phase)
            if ap > rp:
                # At one timestamp, a known earlier runner phase closes the add-on there.
                row['source_addon_exit_phase'] = add_phase
                row['source_addon_exit_price'] = float(row['exit_price'])
                row['source_addon_exit_reason'] = str(row['exit_reason'])
                row['exit_phase'] = runner_phase
                row['exit_price'] = float(r.exit_price)
                row['exit_fee_rate'] = float(r.exit_fee_rate)
                row['exit_reason'] = 'runner_sync_close'
                sync = True
                same_bar_order = 'runner_phase_first_sync'
            elif ap == rp and np.isclose(float(row['exit_price']), float(r.exit_price),
                                         rtol=1e-9, atol=1e-12):
                same_bar_order = 'same_phase_same_fill'
            elif (ap == rp == 1 and add_phase == 'intrabar'
                  and row['exit_reason'] == 'profit_lock' and r.exit_reason == 'stop'
                  and np.isclose(float(row['exit_price']), float(row['stop_price']),
                                 rtol=1e-9, atol=1e-12)
                  and np.isclose(float(r.exit_price), float(r.stop_price),
                                 rtol=1e-9, atol=1e-12)):
                # Both are adverse stops. Verify the add-on stop lies between this
                # bar's open and the runner stop, so the add-on necessarily fills first.
                j = int(np.searchsorted(clock, int(row['exit_bar_ms'])) )
                if j >= len(clock) or int(clock[j]) != int(row['exit_bar_ms']):
                    raise ValueError(f'Missing same-bar stop ordering quote: {row["event_id"]}')
                op, hi, lo, _ = hourly[j]
                side = int(row['side'])
                add_stop, runner_stop = float(row['exit_price']), float(r.exit_price)
                ordered = (op > add_stop > runner_stop and lo <= runner_stop) if side > 0 else (
                    op < add_stop < runner_stop and hi >= runner_stop)
                if not ordered:
                    raise ValueError(f'Cannot order same-H1 protective exits: {row["event_id"]}')
                same_bar_order = 'addon_stop_between_open_and_runner_stop'
            else:
                raise ValueError(f'Unresolved same-H1 runner/add-on exit order: {row["event_id"]}')
        row['sync_to_runner'] = sync
        row['same_bar_exit_order'] = same_bar_order
        row['target_exit_ms'] = int(r.target_exit_bar_ms)
        row['target_exit_price'] = float(r.target_exit_price)
        row['target_exit_phase'] = str(r.target_exit_phase)
        row['target_exit_reason'] = str(r.target_exit_reason)
        row['runner_exit_ms'] = int(r.exit_bar_ms)
        row['runner_exit_price'] = float(r.exit_price)
        row['runner_exit_phase'] = runner_phase
        row['runner_exit_reason'] = str(r.exit_reason)
        row['target_entry_price'] = float(r.entry_price)
        kept.append(row)
        counts['eligible_reentry'] += 1
        if sync:
            counts['runner_phase_sync_close'] += 1
        if same_bar_order == 'addon_stop_between_open_and_runner_stop':
            counts['proven_addon_stop_first_same_bar'] += 1
    return pd.DataFrame(kept), dict(counts)


def prepare(base: pd.DataFrame, opportunities: pd.DataFrame, slots: int = 9, *,
            start: int = wallet.START, end: int = wallet.END, reinvest: bool = True,
            quote_loader=None) -> dict:
    """Build three VectorBT columns per base slot: target, runner, and optional re-entry."""
    quote_loader = quote_loader or wallet.quotes
    candidates, candidate_counts = _validate_reinvestment_candidates(base, opportunities, quote_loader)
    selected = base.reset_index(drop=True).copy()
    if not selected.slot.between(0, slots - 1).all():
        raise ValueError('Selected original slot outside configured capacity')

    # Reuse the established H1 mark/gap preparation for both original legs and
    # add-ons. Base target/runner columns keep the exact existing callback order;
    # all add-on columns follow them so same-open original signals keep priority.
    target = selected.copy()
    target['slot'] = selected.slot * 2
    target['exit_bar_ms'] = selected.target_exit_bar_ms
    target['exit_price'] = selected.target_exit_price
    target['exit_phase'] = selected.target_exit_phase
    target['exit_fee_rate'] = selected.target_exit_fee_rate
    target['exit_reason'] = selected.target_exit_reason
    runner = selected.copy()
    runner['slot'] = selected.slot * 2 + 1
    original_paths = pd.concat([target, runner], ignore_index=True)
    original_data = wallet.prepare(None, slots * 2, start, end,
                                   assigned=(original_paths, {}), quote_loader=quote_loader)

    candidates_frame = candidates.copy()
    if reinvest and len(candidates_frame):
        slots_by_event = selected.set_index('event_id').slot
        candidates_frame['slot'] = candidates_frame.event_id.map(slots_by_event).astype('int64')
        addon_paths = candidates_frame
    else:
        addon_paths = candidates_frame.iloc[0:0].copy()
    addon_data = wallet.prepare(None, slots, start, end,
                                assigned=(addon_paths, {}), quote_loader=quote_loader)

    def combine(key):
        original = original_data[key].reshape(-1, 3, slots * 2)
        addon = addon_data[key].reshape(-1, 3, slots).copy()
        if key == 'actions':
            addon[np.abs(addon) == 1] *= 3
        return np.concatenate([original, addon], axis=2).reshape(-1, slots * 3)

    # Re-entry columns are reused by later parents in the same slot. Preserve
    # the original target-entry row for each add-on so runtime TP caps remain
    # event-specific after the slot has been reused.
    target_entry_rows = np.full((len(addon_data['actions']), slots), -1,
                                dtype=np.int64)
    if len(addon_paths):
        entry_row_by_event = {
            str(row.event_id): _stage(int(row.entry_ms), 'entry', start)
            for row in selected.itertuples(index=False)
        }
        for row in addon_paths.itertuples(index=False):
            reentry_row = _stage(int(row.entry_ms), 'entry', start)
            slot = int(row.slot)
            if not (0 <= reentry_row < len(target_entry_rows)):
                raise ValueError(f'Re-entry time outside prepared data: {row.event_id}')
            if target_entry_rows[reentry_row, slot] >= 0:
                raise ValueError('Two re-entry parents map to one slot at the same H1 open')
            target_entry_rows[reentry_row, slot] = entry_row_by_event[str(row.event_id)]

    return dict(selected=selected, candidates=addon_paths.reset_index(drop=True),
                candidate_counts=candidate_counts, skips=original_data['skips'],
                gap_marks=original_data['gap_marks'] + addon_data['gap_marks'],
                marks=combine('marks'), prices=combine('prices'), fees=combine('fees'),
                actions=combine('actions'),
                high=np.concatenate([original_data['high'], addon_data['high']], axis=1),
                low=np.concatenate([original_data['low'], addon_data['low']], axis=1),
                target_entry_rows=target_entry_rows,
                start=start, slots=slots, reinvest=reinvest)


@njit(cache=True)
def _pre_segment(c):
    for col in range(c.from_col, c.to_col):
        c.last_val_price[col] = c.close[c.i, col]
    return (nb.get_group_value_ctx_nb(c),)


@njit(cache=True)
def _order_func(c, nav, actions, prices, fees, market_slip, reservation,
                target_notional, add_budget, add_cash_before,
                target_entry_rows, slots):
    action = actions[c.i, c.col]
    if action == 0:
        return NoOrder
    fee = fees[c.i, c.col]
    slip = market_slip if fee == TAKER else 0.
    price = prices[c.i, c.col]
    if c.col < slots * 2:
        slot = c.col // 2
        leg = c.col % 2
    else:
        slot = c.col - slots * 2
        leg = 2
    if action == 2:
        if c.position_now == 0.:
            return NoOrder
        return nb.order_nb(size=-c.position_now, price=price, fees=fee,
                           slippage=slip, lock_cash=True, allow_partial=False,
                           raise_reject=True)
    if abs(action) == 1:
        if c.position_now != 0. or leg > 1:
            raise ValueError('Original position collision')
        if leg == 0:
            free_slots = 0
            for base_slot in range(slots):
                first = base_slot * 2
                addon = slots * 2 + base_slot
                occupied = (c.last_position[first] != 0. or
                            c.last_position[first+1] != 0. or
                            c.last_position[addon] != 0.)
                free_slots += int(not occupied)
            reservation[slot] = min(nav / slots,
                                    c.free_cash_now / max(1, free_slots))
            target_notional[c.i, slot] = 0.
        budget = reservation[slot] * BASE_WEIGHTS[leg]
        if budget < 1.e-8:
            return NoOrder
        size = action * (budget * (1. - 1.e-10) /
                         (price * (1. + slip) * (1. + fee)))
        if leg == 0:
            fill_price = price * (1. + slip if action > 0 else 1. - slip)
            target_notional[c.i, slot] = abs(size) * fill_price
        return nb.order_nb(size=size, price=price, fees=fee, slippage=slip,
                           lock_cash=True, allow_partial=False, raise_reject=True)
    if abs(action) == 3:
        if leg != 2 or c.position_now != 0.:
            raise ValueError('Re-entry position collision')
        if c.last_position[slot * 2] != 0.:
            raise ValueError('Re-entry target leg must be flat before the add-on')
        if c.last_position[slot * 2 + 1] == 0.:
            add_cash_before[c.i, slot] = max(0., c.free_cash_now)
            add_budget[c.i, slot] = 0.
            return NoOrder
        cash = max(0., c.free_cash_now)
        target_entry_row = target_entry_rows[c.i, slot]
        if target_entry_row < 0:
            raise ValueError('Re-entry lacks its parent target-entry row')
        budget = min(target_notional[target_entry_row, slot], cash)
        add_cash_before[c.i, slot] = cash
        add_budget[c.i, slot] = budget
        if budget < 1.e-8:
            return NoOrder
        size = (1 if action > 0 else -1) * (budget * (1. - 1.e-10) /
                    (price * (1. + slip) * (1. + fee)))
        return nb.order_nb(size=size, price=price, fees=fee, slippage=slip,
                           lock_cash=True, allow_partial=False, raise_reject=True)
    raise ValueError('Unknown account action')


def _stage(ms: int, phase: str, start: int) -> int:
    return ((int(ms) - start) // HOUR_MS) * 3 + (0 if phase == 'entry' else _phase_index(phase))


def _leg_result(orders, entry_stage, exit_stage, col):
    entry = orders.get((entry_stage, col))
    exit_ = orders.get((exit_stage, col))
    if entry is None and exit_ is None:
        return None
    if entry is None or exit_ is None:
        raise ValueError('Position has one fill but no matching close')
    if entry['side'] == exit_['side'] or not np.isclose(
            entry['size'], exit_['size'], rtol=1e-9, atol=1e-12):
        raise ValueError('Entry and exit quantities do not reconcile')
    signed_size = float(entry['size']) if entry['side'] == 0 else -float(entry['size'])
    pnl = float(signed_size * (exit_['price'] - entry['price']) - entry['fees'] - exit_['fees'])
    return dict(entry=entry, exit=exit_, cash_pnl=pnl,
                notional=float(entry['size'] * entry['price']),
                quantity=float(entry['size']), entry_fee=float(entry['fees']),
                exit_fee=float(exit_['fees']))


def account(data: dict, *, slip: float = 0., initial_cash: float = 1000.):
    """Run the combined account in one cash-sharing VectorBT portfolio."""
    slots = int(data['slots'])
    reservation = np.zeros(slots, dtype=np.float64)
    target_notional = np.zeros((len(data['actions']), slots), dtype=np.float64)
    add_budget = np.zeros((len(data['actions']), slots), dtype=np.float64)
    add_cash_before = np.zeros((len(data['actions']), slots), dtype=np.float64)
    actions = data['actions']
    portfolio = vbt.Portfolio.from_order_func(
        data['marks'], _order_func, actions, data['prices'], data['fees'], slip,
        reservation, target_notional, add_budget, add_cash_before,
        data['target_entry_rows'], slots,
        pre_segment_func_nb=_pre_segment, cash_sharing=True, group_by=True,
        init_cash=initial_cash, max_orders=int(np.count_nonzero(actions)) + 1,
        fill_pos_record=False)
    order_records = pd.DataFrame(portfolio.orders.records)
    if order_records.empty:
        raise ValueError('VectorBT produced no account orders')
    order_map = {(int(r.idx), int(r.col)): r._asdict()
                 for r in order_records.itertuples(index=False)}
    rows = []
    reentry_rows = []
    start = int(data['start'])
    for row in data['selected'].itertuples(index=False):
        slot = int(row.slot)
        entry = _stage(int(row.entry_ms), 'entry', start)
        target_exit = _stage(int(row.target_exit_bar_ms), str(row.target_exit_phase), start)
        runner_exit = _stage(int(row.exit_bar_ms), str(row.exit_phase), start)
        target = _leg_result(order_map, entry, target_exit, slot * 2)
        runner = _leg_result(order_map, entry, runner_exit, slot * 2 + 1)
        if (target is None) != (runner is None):
            raise ValueError(f'Original two-leg funding was not atomic: {row.event_id}')
        if target is None:
            continue
        original = dict(event_id=row.event_id, position_id=row.event_id + ':original',
                        parent_id=row.parent_id, symbol=row.symbol, side=int(row.side),
                        position_type='original', entry_ms=int(row.entry_ms),
                        exit_bar_ms=int(row.exit_bar_ms),
                        cash_pnl=target['cash_pnl'] + runner['cash_pnl'],
                        notional=target['notional'] + runner['notional'],
                        base_cash_pnl=target['cash_pnl'] + runner['cash_pnl'],
                        base_notional=target['notional'] + runner['notional'],
                        reinvest_cash_pnl=0., reinvest_notional=0.,
                        reinvest_fees=0., total_fees=(target['entry_fee'] + target['exit_fee'] +
                                                     runner['entry_fee'] + runner['exit_fee']),
                        target_cash_pnl=target['cash_pnl'], runner_cash_pnl=runner['cash_pnl'],
                        target_notional=target['notional'], runner_notional=runner['notional'],
                        target_quantity=target['quantity'], target_entry_price=target['entry']['price'],
                        target_exit_price=target['exit']['price'],
                        target_entry_fee=target['entry_fee'], target_exit_fee=target['exit_fee'],
                        runner_quantity=runner['quantity'], runner_entry_price=runner['entry']['price'],
                        runner_exit_price=runner['exit']['price'],
                        runner_entry_fee=runner['entry_fee'], runner_exit_fee=runner['exit_fee'],
                        slot=slot, reinvest_status='not_offered')
        original['net_return'] = original['cash_pnl'] / original['notional']
        candidates = data['candidates']
        candidate = candidates[candidates.event_id.eq(row.event_id)] if len(candidates) else pd.DataFrame()
        if len(candidate):
            if len(candidate) != 1:
                raise ValueError(f'An original event has multiple re-entry candidates: {row.event_id}')
            c = candidate.iloc[0]
            addon_entry = _stage(int(c.entry_ms), 'entry', start)
            addon_exit = _stage(int(c.exit_bar_ms), str(c.exit_phase), start)
            addon = _leg_result(order_map, addon_entry, addon_exit, slots * 2 + slot)
            if addon is None:
                original['reinvest_status'] = 'no_free_cash'
            else:
                addon_row = int(addon_entry)
                available_cash = float(add_cash_before[addon_row, slot])
                requested_budget = float(add_budget[addon_row, slot])
                if addon['notional'] > target['notional'] + 1e-6:
                    raise ValueError(f'Re-entry exceeded released target-leg notional: {row.event_id}')
                if addon['notional'] > available_cash + 1e-6:
                    raise ValueError(f'Re-entry exceeded free cash at order time: {row.event_id}')
                original['reinvest_status'] = 'filled'
                original['reinvest_cash_pnl'] = addon['cash_pnl']
                original['reinvest_notional'] = addon['notional']
                original['reinvest_fees'] = addon['entry_fee'] + addon['exit_fee']
                original['total_fees'] += original['reinvest_fees']
                original['cash_pnl'] += addon['cash_pnl']
                original['notional'] += addon['notional']
                original['net_return'] = original['cash_pnl'] / original['notional']
                reentry = dict(event_id=row.event_id, position_id=row.event_id + ':reinvest',
                               parent_id=row.parent_id, symbol=row.symbol, side=int(row.side),
                               position_type='reinvest', entry_ms=int(c.entry_ms),
                               exit_bar_ms=int(c.exit_bar_ms), cash_pnl=addon['cash_pnl'],
                               notional=addon['notional'], net_return=addon['cash_pnl']/addon['notional'],
                               quantity=addon['quantity'], entry_price=addon['entry']['price'],
                               exit_price=addon['exit']['price'], entry_fee=addon['entry_fee'],
                               exit_fee=addon['exit_fee'], total_fees=addon['entry_fee']+addon['exit_fee'],
                               slot=slot, source_exit_reason=c.get('source_addon_exit_reason', c.get('exit_reason')),
                               source_exit_price=c.get('source_addon_exit_price', np.nan),
                               actual_exit_reason=c.exit_reason,
                               target_notional_cap=float(target['notional']),
                               available_cash_before=available_cash,
                               requested_budget=requested_budget,
                               sync_to_runner=bool(c.sync_to_runner),
                               same_bar_exit_order=c.same_bar_exit_order)
                reentry_rows.append(reentry)
        rows.append(original)
    if not rows:
        raise ValueError('No funded original positions')
    parent_ledger = pd.DataFrame(rows).sort_values(['entry_ms','event_id']).reset_index(drop=True)
    reentry_ledger = pd.DataFrame(reentry_rows)

    nav = np.asarray(portfolio.value()).reshape(-1, 3)
    quantity = np.asarray(portfolio.assets())[1::3]
    favorable = np.where(quantity >= 0., data['high'], data['low'])
    adverse = np.where(quantity >= 0., data['low'], data['high'])
    upper = np.maximum(nav.max(axis=1), nav[:, 1] + (quantity * favorable).sum(axis=1))
    lower = np.minimum(nav.min(axis=1), nav[:, 1] + (quantity * adverse).sum(axis=1))
    index = pd.to_datetime(start + HOUR_MS * (np.arange(len(nav)) + 1), unit='ms', utc=True)
    equity = pd.DataFrame(dict(nav=nav[:, 2], open_nav=nav[:, 0], open_exit_nav=nav[:, 1],
                               lower_nav=lower, upper_nav=upper), index=index)
    cash_from_orders = initial_cash + float((np.where(order_records.side.eq(0), -1., 1.) *
                               order_records['size'] * order_records.price - order_records.fees).sum())
    cash_from_positions = initial_cash + float(parent_ledger.cash_pnl.sum())
    if (not np.isclose(cash_from_orders, cash_from_positions, rtol=1e-10, atol=1e-5)
            or not np.isclose(nav[-1, -1], cash_from_orders, rtol=1e-10, atol=.005)):
        raise ValueError(f'NAV/cash reconciliation failed: {nav[-1,-1]} / '
                         f'{cash_from_orders} / {cash_from_positions}')
    meta = dict(original_selected=len(data['selected']), original_funded=len(parent_ledger),
                original_no_cash=len(data['selected']) - len(parent_ledger),
                reinvest_candidates=len(data['candidates']), reinvest_funded=len(reentry_rows),
                reinvest_no_cash=int(parent_ledger.reinvest_status.eq('no_free_cash').sum()),
                total_fees=float(parent_ledger.total_fees.sum()),
                missing_hour_marks=int(data['gap_marks']), cash_from_orders=cash_from_orders,
                cash_from_position_ledger=cash_from_positions, final_nav=float(nav[-1, -1]))
    return parent_ledger, reentry_ledger, equity, meta


def simulate(paths: pd.DataFrame, opportunities: pd.DataFrame, *, slots: int = 9,
             slip: float = 0., reinvest: bool = True, start: int = wallet.START,
             end: int = wallet.END, quote_loader=None):
    base, skips = _selected_originals(paths, slots)
    data = prepare(base, opportunities, slots, start=start, end=end,
                   reinvest=reinvest, quote_loader=quote_loader)
    parent_ledger, reentry_ledger, equity, meta = account(data, slip=slip)
    periods = {'all': (None, None), 'dev': (None, '2025-01-01T00:00Z'),
               '2025': ('2025-01-01T00:00Z', '2026-01-01T00:00Z'),
               '2026': ('2026-01-01T00:00Z', None)}
    metrics = {name: wallet.summarize(parent_ledger, equity, *bounds)
               for name, bounds in periods.items()}
    meta.update(candidate_counts=data['candidate_counts'], admission_skips=skips)
    return metrics, parent_ledger, reentry_ledger, equity, meta, base


def _baseline_reconciliation(originals: pd.DataFrame, frozen: pd.DataFrame) -> dict:
    left = (originals.set_index('event_id') if 'event_id' in originals.columns else originals).sort_index()
    right = (frozen.set_index('event_id') if 'event_id' in frozen.columns else frozen).sort_index()
    if set(left.index) != set(right.index):
        raise ValueError('No-reinvestment event set differs from frozen funded account')
    cash = left.cash_pnl - right.cash_pnl
    notional = left.notional - right.notional
    aligned = right.loc[left.index]
    identity_mismatches = {}
    for field in ('symbol', 'side', 'entry_ms', 'exit_bar_ms'):
        identity_mismatches[field] = int((left[field].to_numpy() != aligned[field].to_numpy()).sum())
    return dict(events=len(left), max_abs_cash_pnl_diff=float(cash.abs().max()),
                max_abs_notional_diff=float(notional.abs().max()),
                total_cash_pnl_diff=float(cash.sum()), total_notional_diff=float(notional.sum()),
                identity_mismatches=identity_mismatches)


def _hourly_reconciliation(equity: pd.DataFrame, frozen_path: Path) -> dict:
    frozen = pd.read_csv(frozen_path)
    frozen.index = pd.to_datetime(frozen.pop('hour_end_utc'), utc=True)
    if not frozen.index.is_unique or not frozen.index.is_monotonic_increasing:
        raise ValueError('Frozen hourly account index is not unique and ordered')
    if not equity.index.equals(frozen.index):
        raise ValueError('No-reinvestment hourly grid differs from frozen account')
    fields = ['nav', 'open_nav', 'open_exit_nav', 'lower_nav', 'upper_nav']
    differences = equity[fields].to_numpy() - frozen[fields].to_numpy()
    maxima = dict(zip(fields, np.max(np.abs(differences), axis=0).astype(float).tolist()))
    return dict(hours=len(equity), max_abs_by_field=maxima,
                hours_over_1e_6=int((np.abs(differences).max(axis=1) > 1e-6).sum()))


def _reentry_trade_metrics(reentries: pd.DataFrame) -> dict:
    if not len(reentries):
        return dict(trades=0, total_cash_pnl=0., win_pct=None, mean_win_usdt=None,
                    mean_loss_usdt=None, payoff_usdt=None)
    pnl = reentries.cash_pnl.to_numpy(dtype=float)
    win, loss = pnl[pnl > 0], pnl[pnl < 0]
    return dict(trades=len(pnl), total_cash_pnl=float(pnl.sum()),
                win_pct=float(100. * np.mean(pnl > 0)),
                mean_win_usdt=float(win.mean()) if len(win) else None,
                mean_loss_usdt=float(loss.mean()) if len(loss) else None,
                payoff_usdt=float(win.mean() / -loss.mean()) if len(win) and len(loss) else None)


def _metrics_rows(label, metrics):
    return [dict(scenario=label, period=period, **values)
            for period, values in metrics.items()]


def _smoke():
    """Small deterministic adapter check; never writes research outputs."""
    paths, opportunities, baseline = read_inputs()
    base_all, _ = _selected_originals(paths, 9)
    frozen = baseline.set_index('event_id')
    # The first funded event starts from the original 1000U account, so its
    # no-reinvest leg cash flow is directly comparable to the frozen ledger.
    first = base_all.sort_values(['entry_ms','entry_order','priority','symbol','event_id']).iloc[[0]].copy()
    event = str(first.event_id.iloc[0])
    start = int(first.entry_ms.iloc[0])
    end = int(first.exit_bar_ms.iloc[0]) + HOUR_MS
    mini_paths = paths[paths.event_id.eq(event)]
    selected, _ = _selected_originals(mini_paths, 9)
    # Keep the original slot id to match the full account's first admission.
    selected['slot'] = int(first.slot.iloc[0])
    data = prepare(selected, opportunities.iloc[0:0], 9, start=start, end=end,
                   reinvest=False)
    originals, _, equity, meta = account(data)
    expected = frozen.loc[event]
    baseline_check = _baseline_reconciliation(originals, frozen.loc[[event]])
    pnl_diff = float(originals.cash_pnl.iloc[0] - expected.cash_pnl)
    notional_diff = float(originals.notional.iloc[0] - expected.notional)
    if abs(pnl_diff) > .01 or abs(notional_diff) > .01:
        raise ValueError(f'First-event frozen cashflow mismatch: {pnl_diff}, {notional_diff}')

    # Also exercise one actual re-entry path, but only over its one source event.
    candidate_rows = opportunities[opportunities.event_id.eq('signal-row-00062:signal')]
    candidate_event = candidate_rows.event_id.iloc[0]
    candidate_base = base_all[base_all.event_id.eq(candidate_event)].copy()
    candidate_start = int(candidate_base.entry_ms.iloc[0])
    candidate_end = int(candidate_base.exit_bar_ms.iloc[0]) + HOUR_MS
    candidate_paths = paths[paths.event_id.eq(candidate_event)]
    candidate_base, _ = _selected_originals(candidate_paths, 9)
    candidate_data = prepare(candidate_base, candidate_rows, 9, start=candidate_start,
                             end=candidate_end, reinvest=True)
    _, add, _, candidate_meta = account(candidate_data)
    if len(add) != 1 or add.notional.iloc[0] > add.target_notional_cap.iloc[0] + 1e-6:
        raise ValueError('Re-entry smoke failed the one-fill / released-margin cap')
    return dict(adapter='VectorBT cash-sharing, local callback',
                no_reinvest_event=event, no_reinvest_frozen_cash_pnl_diff=pnl_diff,
                no_reinvest_frozen_notional_diff=notional_diff,
                no_reinvest_final_nav=float(equity.nav.iloc[-1]),
                reinvest_smoke_event=candidate_event,
                reinvest_notional=float(add.notional.iloc[0]),
                reinvest_cap=float(add.target_notional_cap.iloc[0]),
                reinvest_free_cash=float(add.available_cash_before.iloc[0]),
                reinvest_cash_pnl=float(add.cash_pnl.iloc[0]),
                reinvest_no_loan=bool(add.notional.iloc[0] <= add.available_cash_before.iloc[0] + 1e-6),
                no_reinvest_reconciliation=baseline_check,
                candidate_meta=candidate_meta)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--smoke', action='store_true', help='Run only the small adapter check; write nothing')
    args = parser.parse_args()
    if args.smoke:
        print(json.dumps(_smoke(), indent=2, allow_nan=False))
        return

    paths, opportunities, frozen = read_inputs()
    no_add_metrics, no_add_positions, _, no_add_equity, no_add_meta, no_add_selected = simulate(
        paths, opportunities.iloc[0:0], reinvest=False)
    if set(no_add_selected.event_id) != set(frozen.event_id):
        raise ValueError('Zero-add original slot event set differs from frozen funded ledger')
    baseline_check = _baseline_reconciliation(no_add_positions, frozen)
    hourly_check = _hourly_reconciliation(no_add_equity,
        repair.EXP / 'thirds' / 'hourly_OKX.csv.gz')
    if (baseline_check['max_abs_cash_pnl_diff'] > 1e-6
            or baseline_check['max_abs_notional_diff'] > 1e-6
            or any(baseline_check['identity_mismatches'].values())
            or max(hourly_check['max_abs_by_field'].values()) > 1e-6):
        raise ValueError(f'Zero-add account does not reproduce frozen cash/NAV: '
                         f'{baseline_check} {hourly_check}')
    rows = _metrics_rows('zero_add_replay', no_add_metrics)
    del no_add_positions, no_add_equity, no_add_selected
    gc.collect()

    metrics, positions, reentries, equity, meta, selected = simulate(paths, opportunities)
    selected_ids, frozen_ids = set(selected.event_id), set(frozen.event_id)
    if selected_ids != frozen_ids:
        raise ValueError('Original slot candidate set differs from frozen funded cash account')
    rows.extend(_metrics_rows('reinvested_actual', metrics))
    dev = metrics['dev']
    dev_pass = bool(wallet.qualifies(dev) and dev['payoff_usdt'] >= 2.5 and dev['trades'] >= 100)
    checks = {}
    if dev_pass:
        all_paths, all_opportunities, _ = read_inputs()
        control_paths = thirds.okx_paths()
        control_paths = control_paths[control_paths.is_control & control_paths.tp_r.isin([0.,2.])
                                      & control_paths.parent_id.isin(all_paths.parent_id)].copy()
        control_parent_ids = set(control_paths.loc[control_paths.tp_r.eq(0.), 'parent_id'])
        actual_paths = all_paths[all_paths.parent_id.isin(control_parent_ids)]
        raw_opps = pd.read_csv(OPPORTUNITIES, float_precision='round_trip', dtype={'symbol':str}, keep_default_na=False)
        raw_opps = raw_opps[raw_opps.is_control].copy()
        for label, event_paths, event_opps, slip_value in (
                ('paired_actual', actual_paths, all_opportunities, 0.),
                ('matched_control', control_paths, raw_opps, 0.),
                ('stress_5bp', all_paths, all_opportunities, .0005)):
            values, _, reentry_subset, _, check_meta, _ = simulate(
                event_paths, event_opps, slip=slip_value)
            checks[label] = dict(metrics=values, account=check_meta,
                                 reentry_trade_metrics=_reentry_trade_metrics(reentry_subset))
            rows.extend(_metrics_rows(label, values))
            gc.collect()
    EXP.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(EXP / 'account_metrics.csv', index=False)
    positions.to_csv(EXP / 'funded.csv.gz', index=False)
    reentries.to_csv(EXP / 'reinvested.csv.gz', index=False)
    equity.to_csv(EXP / 'hourly.csv.gz', index_label='hour_end_utc')
    summary = dict(experiment_id='exp-cloud-close-reinvest-runner-20261109-v1',
                   development_pass=dev_pass,
                   original_capacity_event_set_matches_frozen=len(selected_ids),
                   zero_add_baseline_reconciliation=baseline_check,
                   zero_add_hourly_reconciliation=hourly_check,
                   zero_add_account=no_add_meta,
                   account=meta,
                   parent_level_metrics=metrics,
                   reentry_trade_metrics=_reentry_trade_metrics(reentries),
                   checks=checks)
    (EXP / 'summary.json').write_text(json.dumps(summary, indent=2, allow_nan=False) + '\n')
    print(pd.DataFrame(rows)[lambda x: x.period.eq('all')].to_string(index=False), flush=True)


if __name__ == '__main__':
    main()
