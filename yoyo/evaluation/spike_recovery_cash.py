"""Finite-cash, causal admission of independent V8 entry outcomes.

All entry outcomes may be precomputed, but only an accepted trade's outcome
updates account state. Rejected candidates never occupy a shadow position.
The 0.2% fixed round-trip cost is already in net_r; the entry reserve is only
a margin check. No mark-price liquidation or intrabar equity is modeled.
"""
import math

COST = .002
EPS = 1e-9


def eligible(trade, previous):
    """Mirror the frozen engine's same-bar reverse and stopped-side rules."""
    if previous is None:
        return True
    signal, entry, exit_i = int(trade['signal_i']), int(trade['entry_i']), int(previous['exit_i'])
    opposite = int(trade['side']) != int(previous['side'])
    return (signal > exit_i or (signal == exit_i and opposite)
            or (entry == exit_i and previous['exit_at_open'] and opposite))


def risk_for(schedule, level, debt, factor, base):
    if schedule == 'fixed':
        return base
    if schedule == 'double':
        return base * factor ** level
    if schedule == 'debt':
        return max(base, factor * max(0., debt)) if debt > EPS else base
    if schedule == 'factorial':
        return base * math.factorial(level + 1)
    raise ValueError('unknown schedule')


def simulate(opportunities, *, initial_balance=1000., base_risk=1., schedule='double', factor=2.,
             reset_mode='recovery', max_losses=6, leverage_cap=10., record_ledger=True,
             loss_trigger='any_net_loss'):
    """Cash replay; finite cycle limits never reset account loss streaks.

On a capacity failure at an escalated level the cycle is abandoned, its
realized loss remains in cash, and the next signal restarts at base risk.
    Six triggering losses likewise terminate the cycle. Neither implies that
the next trade wins. Debt sizing uses realized net cash loss, including fees.
"""
    if reset_mode not in {'recovery', 'net_win', 'be_or_win'}:
        raise ValueError('unknown reset mode')
    if loss_trigger not in {'any_net_loss', 'losing_stop'}:
        raise ValueError('unknown loss trigger')
    if not 1 <= max_losses <= 6 or initial_balance < 0 or base_risk <= 0 or leverage_cap <= 0 or factor < 1:
        raise ValueError('invalid settings')
    risk_for(schedule, 0, 0, factor, base_risk)
    ordered = sorted(opportunities, key=lambda r: (r['signal_i'], r['side']))
    balance = peak = float(initial_balance)
    level = cycle_id = 0
    cycle_net = 0.
    cycle_n = 0
    previous = None
    ledger, cycles = [], []
    stats = dict(n_candidates=len(ordered), n_accepted=0, n_natural=0, n_blocked=0,
                 n_rejected_capacity=0, n_rejected_ruined=0, net_wins=0, net_losses=0,
                 max_consecutive_net_loss=0, max_consecutive_losing_stop=0,
                 max_risk=0., max_effective_leverage=0., max_realized_drawdown=0.,
                 recovered_cycles=0, abandoned_cycles=0, capacity_failed_cycles=0,
                 capped_cycles=0, natural_sum_net_r=0., natural_positive_cash=0., natural_negative_cash=0.)
    stats.update(n_boundary_marks=0, boundary_mark_pnl=0.)
    net_run = stop_run = 0
    active_months = set()

    def finish(reason):
        nonlocal level, cycle_id, cycle_net, cycle_n
        if cycle_n:
            recovered = cycle_net >= -EPS and reason not in {'boundary', 'unfinished'}
            stats['recovered_cycles'] += int(recovered)
            stats['abandoned_cycles'] += int(not recovered and reason not in {'boundary', 'unfinished'})
            stats['capacity_failed_cycles'] += int(reason == 'capacity')
            stats['capped_cycles'] += int(reason == 'loss_cap')
            if record_ledger:
                cycles.append(dict(cycle_id=cycle_id, trades=cycle_n, net_pnl=cycle_net,
                                   finish_reason=reason, recovered=recovered, balance_after=balance))
        level, cycle_net, cycle_n = 0, 0., 0
        cycle_id += 1

    for trade in ordered:
        if not eligible(trade, previous):
            stats['n_blocked'] += 1
            continue
        # Only entry-time facts and previously accepted realized cash are used.
        risk = risk_for(schedule, level, -cycle_net, factor, base_risk)
        risk_frac = float(trade['initial_risk_frac'])
        if not math.isfinite(risk_frac) or risk_frac <= 0:
            raise ValueError('invalid entry risk fraction')
        notional = risk / risk_frac
        fee_reserve = COST * notional
        required = max(notional / leverage_cap + fee_reserve, risk + fee_reserve)
        before, cycle_before, level_before, id_before = balance, cycle_net, level, cycle_id
        if balance <= 0 or required > balance:
            reason = 'ruined' if balance <= 0 else 'insufficient_balance'
            stats['n_rejected_ruined' if balance <= 0 else 'n_rejected_capacity'] += 1
            if record_ledger:
                ledger.append(dict(signal_i=trade['signal_i'], entry_time=str(trade['entry_time']), accepted=False,
                                   reason=reason, risk=risk, notional=notional, equity_before=before,
                                   equity_after=balance, cycle_id=cycle_id, level_before=level, pnl=0.))
            if balance > 0 and cycle_n:
                finish('capacity')
            continue
        net_r = float(trade['net_r'])
        if not math.isfinite(net_r):
            raise ValueError('accepted outcome must be finite; unresolved gaps cannot be erased')
        pnl = risk * net_r
        # An absorbing zero floor is a research cash convention, not liquidation.
        balance = max(0., before + pnl)
        booked_pnl = balance - before
        cycle_net += booked_pnl
        cycle_n += 1
        stats['n_accepted'] += 1
        stats['max_risk'] = max(stats['max_risk'], risk)
        stats['max_effective_leverage'] = max(stats['max_effective_leverage'], notional / before)
        peak = max(peak, balance)
        stats['max_realized_drawdown'] = max(stats['max_realized_drawdown'], 1 - balance / peak if peak else 0.)
        active_months.add(str(trade['entry_time'])[:7])
        previous = trade
        censored = bool(trade.get('censored', False))
        terminal_reason = None
        if not censored:
            stats['n_natural'] += 1
            won, lost = pnl > EPS, pnl < -EPS
            stopped = 'stop' in str(trade['exit_reason'])
            stats['net_wins'] += int(won)
            stats['net_losses'] += int(lost)
            stats['natural_sum_net_r'] += net_r
            stats['natural_positive_cash'] += max(0., booked_pnl)
            stats['natural_negative_cash'] += min(0., booked_pnl)
            net_run = net_run + 1 if lost else 0
            stop_run = stop_run + 1 if stopped and lost else 0
            stats['max_consecutive_net_loss'] = max(stats['max_consecutive_net_loss'], net_run)
            stats['max_consecutive_losing_stop'] = max(stats['max_consecutive_losing_stop'], stop_run)
            if lost and (stopped or loss_trigger == 'any_net_loss'):
                level += 1
            reset = (cycle_net >= -EPS if reset_mode == 'recovery' else won)
            if reset_mode == 'be_or_win':
                reset = reset or (bool(trade.get('be_armed', False)) and stopped and float(trade['gross_r']) >= -EPS)
            if reset:
                terminal_reason = 'reset_' + reset_mode
            elif level >= max_losses:
                terminal_reason = 'loss_cap'
        else:
            stats['n_boundary_marks'] += 1
            stats['boundary_mark_pnl'] += booked_pnl
            terminal_reason = 'boundary'
        if record_ledger:
            ledger.append(dict(signal_i=trade['signal_i'], entry_i=trade['entry_i'], exit_i=trade['exit_i'],
                               entry_time=str(trade['entry_time']), exit_time=str(trade['exit_time']), side=trade['side'],
                               accepted=True, reason=trade['exit_reason'], risk=risk, notional=notional,
                               equity_before=before, equity_after=balance, pnl=booked_pnl, raw_pnl=pnl,
                               gross_r=trade['gross_r'], net_r=net_r, censored=censored,
                               cycle_id=id_before, level_before=level_before, level_after=level,
                               cycle_net_before=cycle_before, cycle_net_after=cycle_net,
                               cycle_end=terminal_reason))
        if terminal_reason:
            finish(terminal_reason)
    finish('unfinished')
    stats.update(initial_balance=initial_balance, final_balance=balance, profit=balance-initial_balance,
                 final_cash_excluding_boundary=balance-stats['boundary_mark_pnl'],
                 win_rate=stats['net_wins']/stats['n_natural'] if stats['n_natural'] else 0.,
                 active_months=len(active_months), ruined=balance <= 0,
                 cash_pf=stats['natural_positive_cash'] / -stats['natural_negative_cash'] if stats['natural_negative_cash'] < 0 else None)
    return dict(summary=stats, ledger=ledger, cycles=cycles)
