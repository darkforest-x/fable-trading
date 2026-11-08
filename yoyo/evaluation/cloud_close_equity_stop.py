"""Close-triggered equity stops on the frozen nine-slot cloud V1.7 wallet.

The only changed rule is a portfolio-level high-water mark stop. The existing
VectorBT-generated 2R profit-lock fills remain the native exit for each event.
All entries, native exits, forced exits, sizing, fees, and equity marks are
processed in one cash-sharing VectorBT ``from_order_func`` portfolio.
"""
from __future__ import annotations

import argparse
import gc
import json
import zlib

import numpy as np
import pandas as pd
import vectorbt as vbt
from numba import njit
from vectorbt.portfolio import nb
from vectorbt.portfolio.enums import NoOrder

from yoyo.evaluation import cloud_close_lock_profit as lock
from yoyo.evaluation import cloud_close_reward_account as wallet


ROOT = wallet.ROOT
EXP = ROOT / 'experiments/active/exp-cloud-close-equity-stop-20261109-v1'
HOUR = wallet.HOUR
TAKER = wallet.TAKER
MAKER = 0.0002
SLOTS = 9
THRESHOLDS = (0.0, 0.15, 0.25, 0.35)
STATUS = {
    0: 'not_considered',
    1: 'admitted',
    2: 'same_contract_busy',
    3: 'all_slots_busy',
    4: 'equity_stop_pending',
    5: 'unfundable',
}
REASON_EQUITY_STOP = 1


def _sort_events(events: pd.DataFrame) -> pd.DataFrame:
    out = events.copy()
    out['priority'] = [zlib.crc32(str(s).encode()) for s in out.symbol]
    return out.sort_values(
        ['entry_ms', 'entry_order', 'priority', 'symbol', 'event_id'],
        kind='mergesort',
    ).reset_index(drop=True)


def read_inputs() -> tuple[pd.DataFrame, dict]:
    """Read the frozen 2R lock-profit paths and enforce the declared universe."""
    path = lock.EXP / 'profit_lock_paths.csv.gz'
    frame = pd.read_csv(path, dtype={'symbol': str}, keep_default_na=False,
                        float_precision='round_trip')
    required = {
        'event_id', 'parent_id', 'source_row_number', 'symbol', 'side', 'tp_r',
        'is_control', 'entry_ms', 'exit_bar_ms', 'entry_price', 'exit_price',
        'stop_price', 'exit_fee_rate', 'exit_reason', 'exit_phase',
        'same_bar_ambiguous', 'gap_flag', 'entry_status', 'entry_order', 'arm_at_r',
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f'Frozen 2R path input missing columns: {missing}')
    frame = frame.loc[frame.arm_at_r.eq(2.0) & frame.entry_status.eq('executed')].copy()
    for name in ('entry_ms', 'exit_bar_ms', 'source_row_number'):
        frame[name] = pd.to_numeric(frame[name], errors='raise').astype('int64')
    for name in ('side', 'tp_r', 'entry_price', 'exit_price', 'stop_price',
                 'exit_fee_rate', 'entry_order'):
        frame[name] = pd.to_numeric(frame[name], errors='raise')
    frame['is_control'] = frame.is_control.astype(bool)
    if frame.duplicated(['event_id']).any() or frame.event_id.isna().any():
        raise ValueError('2R paths must have unique event IDs')
    if not frame.side.isin([-1, 1]).all() or not frame.exit_phase.isin(['open', 'intrabar', 'close']).all():
        raise ValueError('Invalid direction or native exit phase')
    if (frame.exit_bar_ms < frame.entry_ms).any():
        raise ValueError('Native exit precedes entry')
    if not np.isclose(frame.exit_fee_rate, TAKER).all():
        raise ValueError('The frozen TP=0 lock-profit paths must use taker exits')
    real = frame.loc[~frame.is_control]
    controls = frame.loc[frame.is_control]
    if len(real) != 11_780 or real.parent_id.nunique() != 11_780:
        raise ValueError(f'Frozen 90d real universe changed: rows={len(real)}')
    if len(controls) != 10_816 or controls.parent_id.nunique() != 10_816:
        raise ValueError(f'Frozen matched-control universe changed: rows={len(controls)}')
    if not controls.parent_id.isin(real.parent_id).all():
        raise ValueError('A control has no real parent in the frozen universe')
    if not real.parent_id.is_unique or not controls.parent_id.is_unique:
        raise ValueError('The frozen real/control parent key must be unique')
    for col in ('entry_ms', 'exit_bar_ms'):
        if ((frame[col] - wallet.START) % HOUR != 0).any():
            raise ValueError(f'{col} is not aligned to the UTC H1 open grid')
    if (frame.entry_ms < wallet.START).any() or (frame.exit_bar_ms >= wallet.END).any():
        raise ValueError('Frozen path lies outside the account grid')
    frame = _sort_events(frame)
    counts = {
        'source_path': str(path.relative_to(ROOT)),
        'lock_profit_arm_at_r': 2.0,
        'opportunities_real': int(len(real)),
        'opportunities_matched_control': int(len(controls)),
        'unique_real_parent_id': int(real.parent_id.nunique()),
        'unique_control_parent_id': int(controls.parent_id.nunique()),
        'unique_symbols': int(frame.symbol.nunique()),
        'first_entry_ms': int(frame.entry_ms.min()),
        'last_entry_ms': int(frame.entry_ms.max()),
        'last_native_exit_ms': int(frame.exit_bar_ms.max()),
        'entry_fee_rate': TAKER,
        'native_exit_fee_rate_counts': frame.exit_fee_rate.value_counts().to_dict(),
    }
    return frame, counts


def build_quote_grid(events: pd.DataFrame, *, start: int = wallet.START,
                     end: int = wallet.END, quote_loader=None) -> dict:
    """Align source H1 OHLC to the UTC account grid without inventing quotes.

    Missing hours retain the last observed close for NAV marks, exactly like
    ``cloud_close_reward_account.prepare``. ``present`` remains false so neither
    native nor forced executions can use that carried mark as an open.
    """
    if quote_loader is None:
        quote_loader = wallet.quotes
    if (end - start) % HOUR:
        raise ValueError('Account end must lie on the UTC H1 grid')
    count = (end - start) // HOUR
    symbols = sorted(events.symbol.astype(str).unique())
    sym_to_ix = {s: i for i, s in enumerate(symbols)}
    bars = np.full((count, len(symbols), 4), np.nan, dtype=np.float64)
    present = np.zeros((count, len(symbols)), dtype=np.bool_)
    source_rows = 0
    for sym, j in sym_to_ix.items():
        clock, raw = quote_loader(sym)
        clock = np.asarray(clock, dtype=np.int64)
        raw = np.asarray(raw, dtype=np.float64)
        if raw.shape != (len(clock), 4) or not np.isfinite(raw).all() or (raw <= 0).any():
            raise ValueError(f'Invalid H1 OHLC source for {sym}')
        if not len(clock) or not np.all(np.diff(clock) > 0):
            raise ValueError(f'Unordered/empty H1 source for {sym}')
        in_grid = (clock >= start) & (clock < end)
        times = clock[in_grid]
        rows = raw[in_grid]
        if ((times - start) % HOUR != 0).any():
            raise ValueError(f'Non-UTC-H1 source timestamps for {sym}')
        idx = ((times - start) // HOUR).astype(np.int64)
        if len(np.unique(idx)) != len(idx):
            raise ValueError(f'Duplicate H1 quote in aligned grid for {sym}')
        bars[idx, j] = rows
        present[idx, j] = True
        source_rows += len(idx)
        if not len(idx):
            raise ValueError(f'No H1 quote inside the account grid for {sym}')
        last_observed = np.maximum.accumulate(np.where(present[:, j], np.arange(count), -1))
        valid = last_observed >= 0
        carried = ~present[:, j] & valid
        carry_ix = last_observed[carried]
        close = bars[carry_ix, j, 3]
        bars[carried, j, :] = close[:, None]
    event_symbols = events.symbol.astype(str).map(sym_to_ix).to_numpy(dtype=np.int64)
    entry_idx = ((events.entry_ms.to_numpy(dtype=np.int64) - start) // HOUR).astype(np.int64)
    exit_idx = ((events.exit_bar_ms.to_numpy(dtype=np.int64) - start) // HOUR).astype(np.int64)
    if not present[entry_idx, event_symbols].all():
        raise ValueError('Frozen entry has no original H1 open')
    if not present[exit_idx, event_symbols].all():
        raise ValueError('Frozen native exit has no original H1 bar')
    source_open = bars[entry_idx, event_symbols, 0]
    if not np.allclose(source_open, events.entry_price.to_numpy(), rtol=1e-9, atol=1e-14):
        bad = int(np.flatnonzero(~np.isclose(source_open, events.entry_price.to_numpy(),
                                              rtol=1e-9, atol=1e-14))[0])
        raise ValueError(f'Frozen entry differs from native open: {events.iloc[bad].event_id}')
    return dict(start=int(start), end=int(end), count=int(count), symbols=symbols,
                sym_to_ix=sym_to_ix, bars=np.ascontiguousarray(bars),
                present=np.ascontiguousarray(present), event_symbols=event_symbols,
                entry_idx=entry_idx, exit_idx=exit_idx, source_rows=int(source_rows))


@njit(cache=True)
def _pre_segment(c, bars, marks, event_symbols, slot_event, queue_offsets, queue_events, entry_cursor,
                 exit_cursor, bar_symbol, start, pending, candidate_status,
                 blocked_opportunities):
    hour = c.i // 3
    phase = c.i % 3
    if phase == 0:
        entry_cursor[0] = queue_offsets[hour]
    else:
        entry_cursor[0] = queue_offsets[hour + 1]
    exit_cursor[0] = c.from_col
    for col in range(c.from_col, c.to_col):
        c.last_val_price[col] = 1.0
        event = slot_event[col]
        if event >= 0 and c.last_position[col] != 0.0:
            sym = event_symbols[event]
            price_col = 3 if phase == 2 else 0
            price = bars[hour, sym, price_col]
            if not np.isfinite(price) or price <= 0.0:
                raise ValueError('Open position has no causal mark')
            c.last_val_price[col] = price
            marks[c.i, col] = price
            if phase == 0:
                bar_symbol[hour, col] = sym
    if phase == 0 and pending[0] != 0:
        begin = queue_offsets[hour]
        finish = queue_offsets[hour + 1]
        for pos in range(begin, finish):
            candidate_status[queue_events[pos]] = 4
        blocked_opportunities[0] += finish - begin
        entry_cursor[0] = finish
    nav = nb.get_group_value_ctx_nb(c)
    return (nav,)


@njit(cache=True)
def _order_func(c, nav, bars, present, queue_offsets, queue_events, event_symbols,
                event_side, event_entry_price, event_exit_price, event_exit_fee,
                event_exit_hour, event_exit_stage, event_exit_reason, slot_event,
                pending, cycle_id, trigger_open_hour, entry_cursor, exit_cursor,
                candidate_status, entry_event_at, exit_event_at, bar_symbol,
                event_slot, event_cycle, final_reason, final_phase, forced_exit_ms,
                forced_delay_hours, native_exit_suppressed, blocked_opportunities,
                same_contract_rejects, slot_rejects, unfundable, marks, start, exposure,
                market_slippage, full_slots):
    hour = c.i // 3
    phase = c.i % 3
    if phase == 0:
        if pending[0] != 0:
            return -1, NoOrder
        finish = queue_offsets[hour + 1]
        while entry_cursor[0] < finish:
            event = queue_events[entry_cursor[0]]
            entry_cursor[0] += 1
            if candidate_status[event] != 0:
                continue
            same_symbol = False
            free_slot = -1
            free_slots = 0
            for col in range(c.from_col, c.to_col):
                if c.last_position[col] == 0.0:
                    free_slots += 1
                    if free_slot < 0:
                        free_slot = col
                else:
                    active = slot_event[col]
                    if active >= 0 and event_symbols[active] == event_symbols[event]:
                        same_symbol = True
            if same_symbol:
                candidate_status[event] = 2
                same_contract_rejects[0] += 1
                continue
            if free_slot < 0:
                candidate_status[event] = 3
                slot_rejects[0] += 1
                continue
            price = event_entry_price[event]
            fee = TAKER
            budget = min(nav * exposure / full_slots,
                         c.last_free_cash[c.group] / max(1, free_slots))
            if budget < 1.e-8 or price <= 0.0:
                candidate_status[event] = 5
                unfundable[0] += 1
                continue
            size = event_side[event] * (budget * (1.0 - 1.e-10)
                    / (price * (1.0 + market_slippage) * (1.0 + fee)))
            candidate_status[event] = 1
            slot_event[free_slot] = event
            event_slot[event] = free_slot
            event_cycle[event] = cycle_id[0]
            entry_event_at[c.i, free_slot] = event
            bar_symbol[hour, free_slot] = event_symbols[event]
            c.last_val_price[free_slot] = price
            marks[c.i, free_slot] = price
            return free_slot, nb.order_nb(size=size, price=price, fees=fee,
                slippage=market_slippage, lock_cash=True, allow_partial=False,
                raise_reject=True)
        return -1, NoOrder

    while exit_cursor[0] < c.to_col:
        col = exit_cursor[0]
        exit_cursor[0] += 1
        event = slot_event[col]
        if event < 0 or c.last_position[col] == 0.0:
            continue
        sym = event_symbols[event]
        native_exit = (event_exit_hour[event] == hour and
                       ((phase == 1 and event_exit_stage[event] == 1) or
                        (phase == 2 and event_exit_stage[event] == 2)))
        # An already scheduled native open exit owns that opening auction.
        # The portfolio stop closes only the remaining positions at this open.
        native_open_now = (phase == 1 and event_exit_hour[event] == hour
                           and event_exit_stage[event] == 1)
        force = (pending[0] != 0 and phase == 1 and present[hour, sym]
                 and not native_open_now)
        if not force and not native_exit:
            continue
        if native_exit and not present[hour, sym]:
            raise ValueError('Frozen native exit has no observed quote')
        if force:
            price = bars[hour, sym, 0]
            fee = TAKER
            final_reason[event] = REASON_EQUITY_STOP
            final_phase[event] = 1
            forced_exit_ms[event] = start + hour * HOUR
            forced_delay_hours[event] = hour - trigger_open_hour[0]
            native_exit_suppressed[event] = int(event_exit_hour[event] == hour)
        else:
            price = event_exit_price[event]
            fee = event_exit_fee[event]
            final_reason[event] = event_exit_reason[event]
            final_phase[event] = event_exit_stage[event]
        exit_event_at[c.i, col] = event
        slot_event[col] = -1
        slip = market_slippage if fee >= TAKER - 1.e-12 else 0.0
        return col, nb.order_nb(size=-c.last_position[col], price=price, fees=fee,
            slippage=slip, lock_cash=True, allow_partial=False, raise_reject=True)
    return -1, NoOrder


@njit(cache=True)
def _post_segment(c, threshold, bars, marks, event_symbols, slot_event, pending, cycle_hwm, lifetime_hwm,
                  trigger_open_hour, cycle_id, trigger_count, reset_count,
                  trigger_hour_log, trigger_nav_log, trigger_hwm_log,
                  trigger_position_log, reset_hour_log, reset_nav_log,
                  hwm_trace, lifetime_trace, pending_trace, cycle_trace,
                  start, hour_count):
    hour = c.i // 3
    phase = c.i % 3
    # The simulation context's close can be a read-only broadcast view. Reapply
    # the live asset marks here before computing risk NAV from VectorBT cash and
    # positions; this also protects post-fill marks from stale placeholder data.
    for col in range(c.from_col, c.to_col):
        c.last_val_price[col] = 1.0
        event = slot_event[col]
        if event >= 0 and c.last_position[col] != 0.0:
            sym = event_symbols[event]
            price = bars[hour, sym, 3 if phase == 2 else 0]
            if not np.isfinite(price) or price <= 0.0:
                raise ValueError('Open position has no causal post-segment mark')
            c.last_val_price[col] = price
            marks[c.i, col] = price
    nav = nb.get_group_value_ctx_nb(c)
    if phase == 1 and pending[0] != 0:
        active_count = 0
        for col in range(c.from_col, c.to_col):
            active_count += int(slot_event[col] >= 0)
        if active_count == 0:
            pending[0] = 0
            cycle_hwm[0] = nav
            cycle_id[0] += 1
            reset_hour_log[reset_count[0]] = hour
            reset_nav_log[reset_count[0]] = nav
            reset_count[0] += 1
    if phase == 2:
        if nav > lifetime_hwm[0]:
            lifetime_hwm[0] = nav
        if nav > cycle_hwm[0]:
            cycle_hwm[0] = nav
        if threshold > 0.0 and pending[0] == 0 and nav <= cycle_hwm[0] * (1.0 - threshold):
            active_count = 0
            for col in range(c.from_col, c.to_col):
                active_count += int(slot_event[col] >= 0)
            if active_count > 0:
                idx = trigger_count[0]
                trigger_hour_log[idx] = hour
                trigger_nav_log[idx] = nav
                trigger_hwm_log[idx] = cycle_hwm[0]
                trigger_position_log[idx] = active_count
                trigger_count[0] += 1
                trigger_open_hour[0] = hour + 1
                pending[0] = 1
        hwm_trace[hour] = cycle_hwm[0]
        lifetime_trace[hour] = lifetime_hwm[0]
        pending_trace[hour] = pending[0]
        cycle_trace[hour] = cycle_id[0]


def _arrays(events: pd.DataFrame, grid: dict) -> dict:
    count = int(grid['count'])
    symbols = grid['sym_to_ix']
    ordered = _sort_events(events)
    if ordered.event_id.duplicated().any():
        raise ValueError('Simulation input repeats an event')
    event_symbols = ordered.symbol.astype(str).map(symbols).to_numpy(dtype=np.int64)
    entry_hour = ((ordered.entry_ms.to_numpy(dtype=np.int64) - grid['start']) // HOUR).astype(np.int64)
    exit_hour = ((ordered.exit_bar_ms.to_numpy(dtype=np.int64) - grid['start']) // HOUR).astype(np.int64)
    if ((entry_hour < 0) | (entry_hour >= count) | (exit_hour < entry_hour) | (exit_hour >= count)).any():
        raise ValueError('Candidate entry/exit lies outside aligned hourly grid')
    counts = np.bincount(entry_hour, minlength=count)
    offsets = np.zeros(count + 1, dtype=np.int64)
    offsets[1:] = np.cumsum(counts)
    exit_stage = np.where(ordered.exit_phase.eq('open'), 1, 2).astype(np.int8)
    phase_check = ((exit_stage == 1) == ordered.exit_phase.eq('open').to_numpy())
    if not phase_check.all():
        raise ValueError('Invalid exit phase encoding')
    return dict(events=ordered, event_symbols=np.ascontiguousarray(event_symbols),
                entry_hour=np.ascontiguousarray(entry_hour),
                exit_hour=np.ascontiguousarray(exit_hour),
                queue_offsets=np.ascontiguousarray(offsets),
                queue_events=np.arange(len(ordered), dtype=np.int64),
                side=np.ascontiguousarray(ordered.side.to_numpy(dtype=np.int8)),
                entry_price=np.ascontiguousarray(ordered.entry_price.to_numpy(dtype=np.float64)),
                exit_price=np.ascontiguousarray(ordered.exit_price.to_numpy(dtype=np.float64)),
                exit_fee=np.ascontiguousarray(ordered.exit_fee_rate.to_numpy(dtype=np.float64)),
                exit_stage=np.ascontiguousarray(exit_stage),
                exit_reason=np.ascontiguousarray(ordered.exit_reason.astype(str).factorize()[0].astype(np.int16)),
                reason_labels=ordered.exit_reason.astype(str).drop_duplicates().tolist())


def simulate(events: pd.DataFrame, drawdown: float, *, grid: dict,
             slots: int = SLOTS, exposure: float = 1.0,
             market_slippage: float = 0.0) -> dict:
    """Run one threshold in a shared-cash VectorBT portfolio.

    The close-NAV breach schedules exits at the next account-grid open. If a
    held symbol has no quote then, it stays live and is closed at its first
    observed open. The account remains entry-locked until every forced close is
    recorded. That liquidation open cannot also admit a new event.
    """
    if not 0 <= drawdown < 1:
        raise ValueError(f'Invalid portfolio drawdown threshold: {drawdown}')
    if slots < 1 or exposure <= 0.0 or market_slippage < 0.0:
        raise ValueError('Invalid wallet parameters')
    arrays = _arrays(events, grid)
    evt = arrays['events']
    n_events = len(evt)
    count = int(grid['count'])
    n_rows = count * 3
    slot_event = np.full(slots, -1, dtype=np.int64)
    candidate_status = np.zeros(n_events, dtype=np.int8)
    entry_event_at = np.full((n_rows, slots), -1, dtype=np.int32)
    exit_event_at = np.full((n_rows, slots), -1, dtype=np.int32)
    bar_symbol = np.full((count, slots), -1, dtype=np.int16)
    event_slot = np.full(n_events, -1, dtype=np.int16)
    event_cycle = np.full(n_events, -1, dtype=np.int32)
    final_reason = np.full(n_events, -1, dtype=np.int16)
    final_phase = np.full(n_events, -1, dtype=np.int8)
    forced_exit_ms = np.full(n_events, -1, dtype=np.int64)
    forced_delay_hours = np.full(n_events, -1, dtype=np.int32)
    native_exit_suppressed = np.zeros(n_events, dtype=np.bool_)
    entry_cursor = np.zeros(1, dtype=np.int64)
    exit_cursor = np.zeros(1, dtype=np.int64)
    pending = np.zeros(1, dtype=np.int8)
    cycle_hwm = np.array([1000.0], dtype=np.float64)
    lifetime_hwm = np.array([1000.0], dtype=np.float64)
    trigger_open_hour = np.array([-1], dtype=np.int64)
    cycle_id = np.zeros(1, dtype=np.int32)
    trigger_count = np.zeros(1, dtype=np.int32)
    reset_count = np.zeros(1, dtype=np.int32)
    trigger_hour_log = np.full(count, -1, dtype=np.int64)
    trigger_nav_log = np.full(count, np.nan, dtype=np.float64)
    trigger_hwm_log = np.full(count, np.nan, dtype=np.float64)
    trigger_position_log = np.full(count, -1, dtype=np.int16)
    reset_hour_log = np.full(count, -1, dtype=np.int64)
    reset_nav_log = np.full(count, np.nan, dtype=np.float64)
    hwm_trace = np.full(count, np.nan, dtype=np.float64)
    lifetime_trace = np.full(count, np.nan, dtype=np.float64)
    pending_trace = np.full(count, -1, dtype=np.int8)
    cycle_trace = np.full(count, -1, dtype=np.int32)
    blocked_opportunities = np.zeros(1, dtype=np.int64)
    same_contract_rejects = np.zeros(1, dtype=np.int64)
    slot_rejects = np.zeros(1, dtype=np.int64)
    unfundable = np.zeros(1, dtype=np.int64)
    marks = np.ones((n_rows, slots), dtype=np.float64)
    pf = vbt.Portfolio.from_order_func(
        marks, _order_func,
        grid['bars'], grid['present'], arrays['queue_offsets'], arrays['queue_events'],
        arrays['event_symbols'], arrays['side'], arrays['entry_price'], arrays['exit_price'],
        arrays['exit_fee'], arrays['exit_hour'], arrays['exit_stage'], arrays['exit_reason'],
        slot_event, pending, cycle_id, trigger_open_hour, entry_cursor, exit_cursor,
        candidate_status, entry_event_at, exit_event_at, bar_symbol, event_slot, event_cycle,
        final_reason, final_phase, forced_exit_ms, forced_delay_hours,
        native_exit_suppressed, blocked_opportunities, same_contract_rejects,
        slot_rejects, unfundable, marks, grid['start'], exposure, market_slippage, slots,
        flexible=True, cash_sharing=True, group_by=True, init_cash=1000.0,
        pre_segment_func_nb=_pre_segment,
        pre_segment_args=(grid['bars'], marks, arrays['event_symbols'], slot_event,
                          arrays['queue_offsets'], arrays['queue_events'], entry_cursor, exit_cursor,
                          bar_symbol, grid['start'], pending, candidate_status,
                          blocked_opportunities),
        post_segment_func_nb=_post_segment,
        post_segment_args=(drawdown, grid['bars'], marks, arrays['event_symbols'],
                           slot_event, pending, cycle_hwm, lifetime_hwm,
                           trigger_open_hour, cycle_id, trigger_count, reset_count,
                           trigger_hour_log, trigger_nav_log, trigger_hwm_log,
                           trigger_position_log, reset_hour_log, reset_nav_log,
                           hwm_trace, lifetime_trace, pending_trace, cycle_trace,
                           grid['start'], count),
        call_pre_segment=True, call_post_segment=True,
        max_orders=2*n_events+1, fill_pos_record=False,
    )
    # VectorBT may broadcast the input close into an internal read-only copy.
    # Reattach the callback-updated marks for post-run values; order accounting
    # and trigger decisions already used the live last_val_price inside the engine.
    pf = pf.replace(close=marks)
    if pending[0] != 0 or np.any(slot_event >= 0):
        raise ValueError('Account ended with pending liquidation or active path')
    orders = pf.orders.records.copy()
    if len(orders):
        order_event = []
        order_kind = []
        for row in orders.itertuples():
            ent_ix = int(entry_event_at[int(row.idx), int(row.col)])
            ext_ix = int(exit_event_at[int(row.idx), int(row.col)])
            if (ent_ix >= 0) == (ext_ix >= 0):
                raise ValueError('VectorBT fill is not mapped to exactly one entry/exit stage')
            order_event.append(ent_ix if ent_ix >= 0 else ext_ix)
            order_kind.append('entry' if ent_ix >= 0 else 'exit')
        orders['event_ix'] = np.asarray(order_event, dtype=np.int64)
        orders['fill_kind'] = order_kind
        entries = orders.loc[orders.fill_kind.eq('entry')].copy()
        exits = orders.loc[orders.fill_kind.eq('exit')].copy()
    else:
        entries = exits = pd.DataFrame(columns=['idx','col','size','price','fees','event_ix'])
    if (entries.event_ix < 0).any() or (exits.event_ix < 0).any():
        raise ValueError('VectorBT order lacks its event identity')
    if entries.event_ix.duplicated().any() or exits.event_ix.duplicated().any():
        raise ValueError('An event received duplicate entry or exit fills')
    if set(entries.event_ix.astype(int)) != set(exits.event_ix.astype(int)):
        raise ValueError('Funded entries and exits do not reconcile one-to-one')
    entries = entries.set_index('event_ix').sort_index()
    exits = exits.set_index('event_ix').sort_index()
    funded_ix = entries.index.to_numpy(dtype=np.int64)
    if not np.array_equal(funded_ix, exits.index.to_numpy(dtype=np.int64)):
        raise ValueError('Entry and exit event ordering differs')
    if not np.allclose(entries['size'].to_numpy(), exits['size'].to_numpy(), rtol=1e-9, atol=1e-12):
        raise ValueError('Entry and exit quantity mismatch')
    funded = evt.iloc[funded_ix].copy().reset_index(drop=True)
    ent = entries.reset_index(drop=True)
    ext = exits.reset_index(drop=True)
    if not np.array_equal(event_slot[funded_ix], ent.col.to_numpy(dtype=np.int16)):
        raise ValueError('Position slot attribution differs from VectorBT fills')
    quantity = ent['size'].to_numpy(dtype=np.float64)
    direction = funded.side.to_numpy(dtype=np.float64)
    entry_px = ent.price.to_numpy(dtype=np.float64)
    exit_px = ext.price.to_numpy(dtype=np.float64)
    entry_fee = ent.fees.to_numpy(dtype=np.float64)
    exit_fee = ext.fees.to_numpy(dtype=np.float64)
    funded['entry_exec_price'] = entry_px
    funded['exit_exec_price'] = exit_px
    funded['entry_fee_usdt'] = entry_fee
    funded['exit_fee_usdt'] = exit_fee
    funded['entry_fee_rate'] = TAKER
    funded['entry_fee_class'] = 'taker'
    funded['exit_fee_class'] = np.where(funded.exit_fee_rate.to_numpy() < TAKER, 'maker', 'taker')
    funded['quantity'] = quantity
    funded['entry_notional'] = quantity * entry_px
    funded['cash_pnl'] = direction * (exit_px-entry_px) * quantity - entry_fee - exit_fee
    funded['net_return'] = funded.cash_pnl / funded.entry_notional
    funded['slot'] = event_slot[funded_ix]
    funded['risk_cycle_id'] = event_cycle[funded_ix]
    funded['forced_equity_exit'] = forced_exit_ms[funded_ix] >= 0
    funded['trigger_to_exit_delay_hours'] = forced_delay_hours[funded_ix]
    funded['native_exit_suppressed_same_hour'] = native_exit_suppressed[funded_ix]
    native_reason = funded.exit_reason.astype(str).to_numpy()
    native_phase = funded.exit_phase.astype(str).to_numpy()
    forced = funded.forced_equity_exit.to_numpy()
    funded['native_exit_bar_ms'] = funded.exit_bar_ms.to_numpy(dtype=np.int64)
    funded['native_exit_phase'] = native_phase
    funded['native_exit_reason'] = native_reason
    funded.loc[forced, 'exit_bar_ms'] = forced_exit_ms[funded_ix][forced]
    funded.loc[forced, 'exit_phase'] = 'open'
    funded.loc[forced, 'exit_reason'] = f'equity_stop_{int(round(drawdown*100))}pct'
    funded.loc[~forced, 'exit_phase'] = [
        'open' if x == 1 else ('intrabar' if native_phase[i] == 'intrabar' else 'close')
        for i, x in enumerate(final_phase[funded_ix]) if not forced[i]
    ]
    # Keep one unambiguous final phase assignment for both forced and native rows.
    funded['final_exit_phase_code'] = final_phase[funded_ix]
    funded['exit_fee_rate'] = exit_fee / np.maximum(exit_px * quantity, 1e-300)
    # All order fees are proportional; retain their actual schedule separately.
    funded['exit_fee_rate'] = np.where(funded.exit_fee_class.eq('maker'), MAKER, TAKER)
    funded['forced_exit_ms'] = forced_exit_ms[funded_ix]
    if (funded.exit_bar_ms < funded.entry_ms).any():
        raise ValueError('A forced exit predates its entry')

    nav = np.asarray(pf.value(), dtype=np.float64).reshape(count, 3)
    if not np.isfinite(nav).all() or (nav < -1e-8).any():
        raise ValueError('NAV became non-finite or negative')
    cash_from_orders = 1000.0 + float((np.where(orders.side.eq(0), -1.0, 1.0)
        * orders['size'] * orders.price - orders.fees).sum())
    final_from_positions = 1000.0 + float(funded.cash_pnl.sum())
    if (not np.isclose(cash_from_orders, final_from_positions, rtol=1e-10, atol=1e-5)
            or not np.isclose(nav[-1, -1], cash_from_orders, rtol=1e-10, atol=.005)):
        raise ValueError(f'Final NAV {nav[-1,-1]} vs orders {cash_from_orders} and PnL {final_from_positions}')

    qty = np.asarray(pf.assets(), dtype=np.float64).reshape(count, 3, slots)[:, 1, :]
    high = np.zeros((count, slots), dtype=np.float64)
    low = np.zeros_like(high)
    rows, cols = np.where(bar_symbol >= 0)
    sym_ix = bar_symbol[rows, cols].astype(np.int64)
    op = grid['bars'][rows, sym_ix, 0]
    hi = grid['bars'][rows, sym_ix, 1]
    lo = grid['bars'][rows, sym_ix, 2]
    high[rows, cols] = hi-op
    low[rows, cols] = lo-op
    # Match wallet.prepare's conservative-bound treatment of native intrabar
    # fills: once the stop/target is reached, do not count the unreachable tail
    # of that H1 bar in the drawdown envelope.
    for k, event in enumerate(funded_ix):
        if (not forced[k] and final_phase[event] == 2
                and funded.iloc[k].native_exit_phase == 'intrabar'):
            h = int(arrays['exit_hour'][event])
            col = int(event_slot[event])
            sym = int(arrays['event_symbols'][event])
            cap = exit_px[k] - grid['bars'][h, sym, 0]
            if funded.iloc[k].exit_fee_rate < TAKER:
                if direction[k] > 0:
                    high[h, col] = cap
                else:
                    low[h, col] = cap
            elif direction[k] > 0:
                low[h, col] = cap
            else:
                high[h, col] = cap
    favorable = np.where(qty >= 0.0, high, low)
    adverse = np.where(qty >= 0.0, low, high)
    upper = np.maximum(nav.max(axis=1), nav[:, 1] + (qty*favorable).sum(axis=1))
    lower = np.minimum(nav.min(axis=1), nav[:, 1] + (qty*adverse).sum(axis=1))
    index = pd.to_datetime(grid['start'] + HOUR*(np.arange(count)+1), unit='ms', utc=True)
    equity = pd.DataFrame(dict(nav=nav[:, 2], open_nav=nav[:, 0],
        open_exit_nav=nav[:, 1], lower_nav=lower, upper_nav=upper,
        risk_cycle_hwm=hwm_trace, lifetime_hwm=lifetime_trace,
        liquidation_pending=pending_trace, risk_cycle_id=cycle_trace), index=index)

    # Use the exact per-event OHLC coverage metric used by the original wallet.
    missing_hours = 0
    if len(funded):
        for row in funded.itertuples():
            sym_ix = grid['sym_to_ix'][str(row.symbol)]
            first = int((int(row.entry_ms) - grid['start']) // HOUR)
            last = int((int(row.exit_bar_ms) - grid['start']) // HOUR)
            missing_hours += int((~grid['present'][first:last+1, sym_ix]).sum())

    trigger_n = int(trigger_count[0])
    reset_n = int(reset_count[0])
    trigger_rows = []
    for i in range(trigger_n):
        h = int(trigger_hour_log[i])
        trigger_rows.append(dict(trigger_bar_open_ms=grid['start']+h*HOUR,
            trigger_close_ms=grid['start']+(h+1)*HOUR,
            scheduled_liquidation_open_ms=grid['start']+(h+1)*HOUR,
            trigger_nav=float(trigger_nav_log[i]), trigger_cycle_hwm=float(trigger_hwm_log[i]),
            positions_at_trigger=int(trigger_position_log[i]), cycle_id=i))
    reset_rows = []
    for i in range(reset_n):
        h = int(reset_hour_log[i])
        reset_rows.append(dict(reset_open_ms=grid['start']+h*HOUR,
                               post_liquidation_nav=float(reset_nav_log[i]), cycle_id=i+1))
    status_frame = evt[['event_id','parent_id','source_row_number','symbol','side','entry_ms',
                        'exit_bar_ms','is_control','entry_order']].copy()
    status_frame['admission_status'] = [STATUS[int(s)] for s in candidate_status]
    status_frame['slot'] = event_slot
    status_frame['risk_cycle_id'] = np.where(event_cycle >= 0, event_cycle, -1)
    status_frame['forced_exit_ms'] = forced_exit_ms
    status_frame['forced_delay_hours'] = forced_delay_hours
    status_frame['native_exit_suppressed_same_hour'] = native_exit_suppressed
    status_frame['final_exit_reason'] = [
        (f'equity_stop_{int(round(drawdown*100))}pct' if forced_exit_ms[i] >= 0
         else str(evt.iloc[i].exit_reason)) if candidate_status[i] == 1 else ''
        for i, code in enumerate(final_reason)
    ]
    static, static_reject = wallet.admit(evt, slots)
    dynamic_ids = set(evt.loc[candidate_status == 1, 'event_id'])
    static_ids = set(static.event_id)
    static_slots = dict(zip(static.event_id, static.slot))
    dynamic_slot_map = dict(zip(evt.event_id, event_slot))
    static_order_match = dynamic_ids == static_ids
    static_slot_match = static_order_match and all(
        int(dynamic_slot_map[event_id]) == int(slot)
        for event_id, slot in static_slots.items())
    if drawdown == 0.0 and (not static_order_match or not static_slot_match):
        raise ValueError('Zero-trigger dynamic admission does not reproduce static wallet roster')
    forced_delays = funded.loc[funded.forced_equity_exit, 'trigger_to_exit_delay_hours']
    audit = dict(
        drawdown_threshold=drawdown,
        candidates=len(evt), admitted=int((candidate_status == 1).sum()),
        same_contract_rejected=int(same_contract_rejects[0]),
        all_slots_rejected=int(slot_rejects[0]),
        equity_stop_blocked=int(blocked_opportunities[0]),
        unfundable=int(unfundable[0]),
        triggers=trigger_n, completed_liquidation_cycles=reset_n,
        forced_exits=int(funded.forced_equity_exit.sum()),
        delayed_forced_exits=int((funded.trigger_to_exit_delay_hours > 0).sum()),
        maximum_forced_exit_delay_hours=(int(forced_delays.max()) if len(forced_delays) else 0),
        native_exit_suppressed_same_hour=int(funded.native_exit_suppressed_same_hour.sum()),
        unquoted_position_hours=missing_hours,
        static_admit_count=len(static), static_admit_rejections=static_reject,
        static_roster_match=static_order_match, static_slot_assignment_match=static_slot_match,
        new_dynamic_admissions=len(dynamic_ids-static_ids),
        static_admissions_missing_dynamically=len(static_ids-dynamic_ids),
        vectorbt_version=vbt.__version__,
        orders=len(orders),
        cash_from_orders=float(cash_from_orders),
        final_cash_pnl=float(final_from_positions),
        input_quote_rows=int(grid['source_rows']),
    )
    return dict(events=evt, roster=funded, equity=equity,
                candidate_audit=status_frame, triggers=pd.DataFrame(trigger_rows),
                resets=pd.DataFrame(reset_rows), audit=audit, order_records=orders)


def _period_rows(sim: dict) -> dict[str, dict]:
    definitions = {'all': (None, None), 'dev': (None, '2025-01-01T00:00Z'),
                   '2025': ('2025-01-01T00:00Z', '2026-01-01T00:00Z'),
                   '2026': ('2026-01-01T00:00Z', None)}
    roster, equity = sim['roster'], sim['equity']
    out = {}
    for period, bounds in definitions.items():
        metrics = wallet.summarize(roster, equity, *bounds)
        lo = pd.Timestamp(bounds[0], tz='UTC') if bounds[0] else equity.index[0]-pd.Timedelta(hours=1)
        hi = pd.Timestamp(bounds[1], tz='UTC') if bounds[1] else equity.index[-1]+pd.Timedelta(hours=1)
        lms, hms = lo.value//1_000_000, hi.value//1_000_000
        fills_entry = roster[(roster.entry_ms >= lms) & (roster.entry_ms < hms)]
        fills_exit = roster[(roster.exit_bar_ms >= lms) & (roster.exit_bar_ms < hms)]
        candidates = sim['candidate_audit']
        c_period = candidates[(candidates.entry_ms >= lms) & (candidates.entry_ms < hms)]
        triggers = sim['triggers']
        tr_period = triggers[(triggers.trigger_close_ms >= lms) & (triggers.trigger_close_ms < hms)] if len(triggers) else triggers
        metrics.update(dict(
            trigger_count=int(len(tr_period)),
            forced_exit_count=int(fills_exit.forced_equity_exit.sum()),
            delayed_forced_exit_count=int((fills_exit.trigger_to_exit_delay_hours > 0).sum()),
            forced_delay_hours=int(fills_exit.loc[fills_exit.forced_equity_exit,
                                                   'trigger_to_exit_delay_hours'].sum()),
            entry_fills=int(len(fills_entry)), exit_fills=int(len(fills_exit)),
            entry_fees_usdt=float(fills_entry.entry_fee_usdt.sum()),
            exit_fees_usdt=float(fills_exit.exit_fee_usdt.sum()),
            fees_usdt=float(fills_entry.entry_fee_usdt.sum()+fills_exit.exit_fee_usdt.sum()),
            opportunities=int(len(c_period)),
            funded_entries=int(c_period.admission_status.eq('admitted').sum()),
            risk_blocked_entries=int(c_period.admission_status.eq('equity_stop_pending').sum()),
            same_contract_rejected=int(c_period.admission_status.eq('same_contract_busy').sum()),
            all_slots_rejected=int(c_period.admission_status.eq('all_slots_busy').sum()),
        ))
        out[period] = metrics
    return out


def _metric_rows(t: float, sim: dict) -> list[dict]:
    return [dict(drawdown_stop_pct=100*t, period=period, **metrics)
            for period, metrics in _period_rows(sim).items()]


def _check_zero_against_artifact(metrics: dict, audit: dict) -> dict:
    baseline_path = lock.EXP / 'account_metrics.csv'
    base = pd.read_csv(baseline_path)
    base = base.loc[base.arm_at_r.eq(2.0)].set_index('period')
    checks = {}
    for period, actual in metrics.items():
        expected = base.loc[period]
        checks[period] = {}
        for key in ('final_usdt', 'multiple', 'dd_mark_pct', 'dd_bound_pct', 'trades',
                    'win_pct', 'payoff', 'payoff_usdt'):
            got = actual[key]
            exp = float(expected[key])
            atol = .005 if key == 'final_usdt' else (1e-6 if key in ('trades',) else 2e-6)
            ok = bool(np.isclose(got, exp, rtol=0.0, atol=atol, equal_nan=True))
            checks[period][key] = dict(actual=float(got), artifact=exp, passed=ok, atol=atol)
            if not ok:
                raise ValueError(f'Zero threshold differs from frozen lock-profit account: {period}.{key}: {got} vs {exp}')
    if not audit['static_roster_match'] or not audit['static_slot_assignment_match']:
        raise ValueError('Zero threshold roster differs from frozen static admission')
    return checks


def _pass_gate(dev: dict) -> bool:
    return (dev['trades'] >= 100 and dev['win_pct'] is not None and dev['win_pct'] >= 30.0
            and (dev['payoff'] or 0.0) >= 2.5 and (dev['payoff_usdt'] or 0.0) >= 2.5
            and dev['dd_bound_pct'] <= 35.0)


def _write_sim(t: float, sim: dict, output_dir=EXP) -> None:
    tag = int(round(t*100))
    sim['roster'].to_csv(output_dir/f'funded_dd{tag}.csv.gz', index=False, compression='gzip')
    sim['equity'].to_csv(output_dir/f'hourly_dd{tag}.csv.gz', index_label='hour_end_utc', compression='gzip')
    sim['candidate_audit'].to_csv(output_dir/f'roster_dd{tag}.csv.gz', index=False, compression='gzip')
    sim['triggers'].to_csv(output_dir/f'triggers_dd{tag}.csv', index=False)
    sim['resets'].to_csv(output_dir/f'resets_dd{tag}.csv', index=False)


def main() -> None:
    EXP.mkdir(parents=True, exist_ok=True)
    cfg = json.loads((EXP/'config.json').read_text())
    if cfg.get('thresholds') != list(THRESHOLDS):
        raise ValueError('Experiment config thresholds differ from source constants')
    all_events, input_audit = read_inputs()
    real = all_events.loc[~all_events.is_control].copy().reset_index(drop=True)
    controls = all_events.loc[all_events.is_control].copy().reset_index(drop=True)
    grid = build_quote_grid(all_events)
    static_real, static_reject = wallet.admit(real, SLOTS)
    input_audit.update(dict(
        account_start_ms=wallet.START, account_end_ms=wallet.END,
        account_start_utc=pd.to_datetime(wallet.START, unit='ms', utc=True).isoformat(),
        account_end_utc=pd.to_datetime(wallet.END, unit='ms', utc=True).isoformat(),
        account_slots=SLOTS, static_admitted_real=len(static_real),
        static_admission_rejections=static_reject,
        source_h1_rows=grid['source_rows'], source_symbols=len(grid['symbols']),
        missing_open_audit='No fill is permitted on carried marks; forced close waits for the same symbol first observed open.',
        vectorbt_version=vbt.__version__,
        comparison_artifact=str((lock.EXP/'account_metrics.csv').relative_to(ROOT)),
    ))
    (EXP/'input_audit.json').write_text(json.dumps(input_audit, indent=2, allow_nan=False)+'\n')

    rows = []
    sims: dict[float, dict] = {}
    zero_parity = None
    for t in THRESHOLDS:
        print(f'Running shared-wallet equity stop {int(t*100)}% on {len(real)} opportunities', flush=True)
        sim = simulate(real, t, grid=grid)
        metrics = _period_rows(sim)
        if t == 0.0:
            zero_parity = _check_zero_against_artifact(metrics, sim['audit'])
        rows.extend(_metric_rows(t, sim))
        sim['metrics'] = metrics
        _write_sim(t, sim)
        sims[t] = sim
        gc.collect()
    metric_frame = pd.DataFrame(rows)
    metric_frame.to_csv(EXP/'account_metrics.csv', index=False)
    dev = metric_frame.loc[metric_frame.period.eq('dev')].copy()
    dev['gate_pass'] = [_pass_gate(r._asdict()) for r in dev.itertuples(index=False)]
    eligible = dev.loc[dev.gate_pass & dev.drawdown_stop_pct.gt(0.0)].sort_values(
        ['multiple','dd_bound_pct'], ascending=[False,True])
    pick = None if eligible.empty else float(eligible.iloc[0].drawdown_stop_pct)/100.0
    selected_for_checks = [] if pick is None else [pick]
    checks = {}
    for t in selected_for_checks:
        real_pool = real[real.parent_id.isin(controls.parent_id)].copy()
        control_pool = controls.copy()
        key = str(int(round(t*100)))
        checks[key] = {}
        for label, pool, slip in [('paired_signals', real_pool, 0.0),
                                  ('matched_controls', control_pool, 0.0),
                                  ('market_slippage_5bp', real, 0.0005)]:
            check_sim = simulate(pool, t, grid=grid, market_slippage=slip)
            checks[key][label] = dict(
                metrics=_period_rows(check_sim), audit=check_sim['audit'])
            if label == 'paired_signals':
                check_sim['roster'].to_csv(EXP/f'funded_paired_dd{int(t*100)}.csv.gz',
                                           index=False, compression='gzip')
                check_sim['equity'].to_csv(EXP/f'hourly_paired_dd{int(t*100)}.csv.gz',
                                           index_label='hour_end_utc', compression='gzip')
            gc.collect()
    summary = dict(
        experiment=cfg['id'], engine=f'VectorBT {vbt.__version__} Portfolio.from_order_func',
        pick_threshold=pick, development_eligible=[float(x)/100 for x in eligible.drawdown_stop_pct],
        development_gates=dict(min_trades=100,min_win_pct=30.0,min_equal_notional_payoff=2.5,
                               min_cash_payoff=2.5,max_conservative_dd_pct=35.0),
        zero_threshold_exact_parity=zero_parity,
        static_zero_roster=dict(count=len(static_real),rejections=static_reject),
        checks=checks,
        audits={str(int(t*100)):sims[t]['audit'] for t in THRESHOLDS},
        note=('Paired controls and additional 5bp market-slippage account run only if a nonzero '
              'threshold passes all preregistered development gates.'),
    )
    (EXP/'summary.json').write_text(json.dumps(summary, indent=2, allow_nan=False)+'\n')
    print(metric_frame[metric_frame.period.eq('all')].to_string(index=False), flush=True)


def _mini_events(rows: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    for name in ('event_id','parent_id','symbol','exit_reason','exit_phase'):
        frame[name] = frame[name].astype(str)
    frame['entry_order'] = np.arange(len(frame), dtype=float)
    frame['source_row_number'] = np.arange(len(frame), dtype=np.int64)+1
    frame['tp_r'] = 0.0
    frame['is_control'] = False
    frame['same_bar_ambiguous'] = False
    frame['gap_flag'] = False
    frame['entry_status'] = 'executed'
    frame['exit_fee_rate'] = TAKER
    return _sort_events(frame)


def mini_check() -> None:
    """Small synthetic checks for no-trigger parity, precedence and open gaps."""
    start = wallet.START
    hours = 8
    clock = start + np.arange(hours, dtype=np.int64)*HOUR

    def qdata(opens: list[float], closes: list[float], missing: set[int] | None = None):
        o = np.asarray(opens, dtype=float)
        c = np.asarray(closes, dtype=float)
        h = np.maximum(o, c)*1.001
        l = np.minimum(o, c)*.999
        bars = np.column_stack([o,h,l,c])
        present = np.ones(hours, dtype=bool)
        for ix in (missing or set()):
            present[ix] = False
        return clock[present], bars[present]

    source = {
        'A': qdata([100,100,100,100,100,100,100,100], [112,110,102,102,100,100,100,100]),
        'B': qdata([100,100,100,100,100,100,100,100], [92,94,100,100,100,100,100,100]),
        'C': qdata([100,100,100,100,100,100,100,100], [100,100,100,100,101,100,100,100]),
    }

    def loader(symbol):
        return source[symbol]

    def row(event, symbol, entry_h, exit_h, side=1, exit_phase='close', exit_px=100.0):
        ep = float(source[symbol][1][np.searchsorted(source[symbol][0], clock[entry_h]),0])
        if (clock[exit_h] not in source[symbol][0]):
            raise ValueError('Mini native exit must be on observed bar')
        return dict(event_id=event,parent_id=event,symbol=symbol,side=side,
            entry_ms=int(clock[entry_h]),exit_bar_ms=int(clock[exit_h]),entry_price=ep,
            exit_price=float(exit_px),stop_price=ep-10*side,exit_reason='native_exit',
            exit_phase=exit_phase)

    # Zero threshold must match the established wallet on a small roster,
    # including same-contract rejection and slot release chronology.
    baseline = _mini_events([row('a','A',0,2,exit_px=102), row('b','B',0,2,side=-1,exit_px=99),
                             row('a_busy','A',1,4,exit_px=101), row('c_full','C',1,4,exit_px=101),
                             row('d_after','C',3,5,exit_px=101)])
    grid0 = build_quote_grid(baseline, start=start, end=start+hours*HOUR, quote_loader=loader)
    sim0 = simulate(baseline,0.,grid=grid0,slots=2)
    base_data = wallet.prepare(baseline,2,start=start,end=start+hours*HOUR,quote_loader=loader)
    base_roster, base_equity = wallet.account(base_data)
    expected_ids = set(base_roster.event_id)
    actual_ids = set(sim0['roster'].event_id)
    if expected_ids != actual_ids or not sim0['audit']['static_roster_match']:
        raise AssertionError(f'Mini no-trigger roster mismatch: {expected_ids} / {actual_ids}')
    if not np.isclose(float(sim0['equity'].nav.iloc[-1]),float(base_equity.nav.iloc[-1]),rtol=1e-10,atol=.005):
        raise AssertionError('Mini no-trigger terminal NAV differs from wallet')
    if not np.allclose(sim0['equity'][['nav','open_nav','open_exit_nav','lower_nav','upper_nav']].to_numpy(),
                       base_equity[['nav','open_nav','open_exit_nav','lower_nav','upper_nav']].to_numpy(),
                       rtol=1e-9,atol=.005):
        raise AssertionError('Mini no-trigger NAV/mark bounds differ from wallet')

    # Native open exit takes precedence over the group liquidation at the same
    # open. The new signal at that open stays blocked; the native exit is charged once.
    cut_source = {
        'A': qdata([100,82,84,85,100,100,100,100], [84,82,84,85,100,100,100,100]),
        'B': qdata([100]*hours,[100]*hours),
        'C': qdata([100]*hours,[100]*hours),
    }

    def cut_loader(symbol):
        return cut_source[symbol]

    def cut_row(event,symbol,entry_h,exit_h,phase='close',exit_px=100.0):
        ep=float(cut_source[symbol][1][np.searchsorted(cut_source[symbol][0],clock[entry_h]),0])
        return dict(event_id=event,parent_id=event,symbol=symbol,side=1,entry_ms=int(clock[entry_h]),
            exit_bar_ms=int(clock[exit_h]),entry_price=ep,exit_price=exit_px,stop_price=ep-10,
            exit_reason='native_exit',exit_phase=phase)
    cut_events = _mini_events([cut_row('native_open_first','A',0,1,'open',82),
                               cut_row('same_open_entry','B',1,4),
                               cut_row('after_reset','C',2,4)])
    cut_grid = build_quote_grid(cut_events,start=start,end=start+hours*HOUR,quote_loader=cut_loader)
    cut = simulate(cut_events,.15,grid=cut_grid,slots=1)
    native_first = cut['roster'].loc[cut['roster'].event_id.eq('native_open_first')]
    if len(native_first)!=1 or native_first.iloc[0].exit_reason!='native_exit' or native_first.iloc[0].forced_equity_exit:
        raise AssertionError('Same-open native exit did not take precedence exactly once')
    status = cut['candidate_audit'].set_index('event_id').admission_status.to_dict()
    if status['same_open_entry']!='equity_stop_pending' or status['after_reset']!='admitted':
        raise AssertionError(f'Entry lock/restart ordering failed: {status}')
    if cut['audit']['triggers']!=1 or cut['audit']['completed_liquidation_cycles']!=1:
        raise AssertionError(f"One breach should create one reset: triggers={cut['audit']['triggers']} resets={cut['audit']['completed_liquidation_cycles']} trigger_rows={cut['triggers'].to_dict('records')} reset_rows={cut['resets'].to_dict('records')} curve={cut['equity'][['nav','risk_cycle_hwm','risk_cycle_id','liquidation_pending']].iloc[:6].to_dict('records')}")

    # A missing next-hour open is a mark only. The group remains locked until
    # the affected coin's first later observed open; its delay is recorded.
    gap_source = {
        'A': qdata([100,100,80,82,84,86,100,100],[84,100,80,82,84,86,100,100],{1}),
        'B': qdata([100]*hours,[100]*hours),
        'C': qdata([100]*hours,[100]*hours),
    }

    def gap_loader(symbol):
        return gap_source[symbol]

    def gap_row(event,symbol,entry_h,exit_h):
        loc=np.searchsorted(gap_source[symbol][0],clock[entry_h])
        ep=float(gap_source[symbol][1][loc,0])
        return dict(event_id=event,parent_id=event,symbol=symbol,side=1,entry_ms=int(clock[entry_h]),
            exit_bar_ms=int(clock[exit_h]),entry_price=ep,exit_price=100.0,stop_price=ep-10,
            exit_reason='native_exit',exit_phase='close')
    gap_events = _mini_events([gap_row('gap_force','A',0,5),gap_row('during_gap','B',1,5),
                               gap_row('still_locked','C',2,5),gap_row('after_gap','C',3,4)])
    gap_events.loc[gap_events.event_id.eq('after_gap'),'exit_price']=85.0
    gap_grid=build_quote_grid(gap_events,start=start,end=start+hours*HOUR,quote_loader=gap_loader)
    gap=simulate(gap_events,.15,grid=gap_grid,slots=1)
    forced_gap=gap['roster'].loc[gap['roster'].event_id.eq('gap_force')].iloc[0]
    gap_status=gap['candidate_audit'].set_index('event_id').admission_status.to_dict()
    if forced_gap.exit_bar_ms != int(clock[2]) or forced_gap.trigger_to_exit_delay_hours != 1:
        raise AssertionError('Missing-open close did not wait for next observed open and record delay')
    if gap_status['during_gap']!='equity_stop_pending' or gap_status['still_locked']!='equity_stop_pending' or gap_status['after_gap']!='admitted':
        raise AssertionError(f'Group remained/admitted at wrong time around quote gap: {gap_status}')
    if gap['audit']['triggers'] != 1:
        raise AssertionError('An empty account below its old HWM generated a false repeated trigger')
    print('mini checks passed: zero-path VectorBT parity, same-open precedence, gap delay, restart lock')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--mini-check', action='store_true', help='run bounded synthetic VectorBT checks only')
    args = parser.parse_args()
    mini_check() if args.mini_check else main()
