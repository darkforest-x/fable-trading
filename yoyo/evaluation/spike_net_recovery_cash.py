"""Finite recovery cash with no loss-count cap or debt-forgiving reset.

Only already-admitted realized outcomes determine the next price-risk budget.
The caller supplies net_r after the frozen 20bp cost; fee reserve below checks
capacity and is not a second fee. Positions rejected for margin never occupy
a shadow slot, and rejection never forgives debt. No exchange liquidation or
intrabar marked equity is modeled.
"""
import math

from yoyo.evaluation.spike_recovery_cash import eligible

COST = .002
EPS = 1e-9


def simulate_recovery(opportunities, *, initial_balance=1000., base_risk=1.,
                      schedule="double", leverage_cap=10.):
    """Double after each net loss; keep risk on BE/unrecovered profit.

    Only cumulative realized cycle PnL >= 0 resets risk. If price risk alone
    exhausts cash, halt permanently. A margin/fee capacity rejection preserves
    debt and tries the next independent signal, whose initial risk fraction
    may permit a different notional. leverage_cap=None is a price-risk-only
    capacity diagnostic, not a realizable unlimited-leverage account.
    """
    if schedule not in {"fixed", "double"}:
        raise ValueError("schedule must be fixed or double")
    if not math.isfinite(initial_balance) or initial_balance <= 0 or base_risk <= 0:
        raise ValueError("invalid initial cash/risk")
    if leverage_cap is not None and (not math.isfinite(leverage_cap) or leverage_cap <= 0):
        raise ValueError("invalid leverage cap")
    rows = sorted(opportunities, key=lambda r: (r["signal_i"], r["side"]))
    balance = peak = float(initial_balance)
    cycle_net = 0.
    level = cycle_id = cycle_n = 0
    previous = None
    ledger, cycles = [], []
    net_run = initial_run = gross_run = 0
    s = dict(n_candidates=len(rows), n_accepted=0, n_natural=0, n_blocked=0,
             n_capacity_rejected=0, n_profit=0, n_net_be=0, n_other_zero=0,
             n_net_loss=0, n_strict_positive=0, n_full_initial_stop=0,
             n_tp=0, max_net_loss_streak=0, max_initial_stop_streak=0,
             max_gross_loss_streak=0, max_unrecovered_loss_events=0,
             max_risk=0., max_attempted_risk=0., max_effective_leverage=0.,
             max_realized_drawdown=0., recovered_cycles=0, n_boundary=0,
             boundary_pnl=0., natural_net_r=0., natural_gross_r=0.,
             positive_cash=0., negative_cash=0., halt_reason=None,
             halt_time=None, halt_risk=None, halt_equity=None)

    def finish(reason):
        nonlocal cycle_id, cycle_net, level, cycle_n
        if cycle_n:
            recovered = reason == "recovered"
            cycles.append(dict(cycle_id=cycle_id, n_trades=cycle_n,
                               loss_events=level, net_pnl=cycle_net,
                               finish_reason=reason, recovered=recovered,
                               balance_after=balance))
            s["recovered_cycles"] += int(recovered)
        cycle_id += 1
        cycle_net = 0.
        level = cycle_n = 0

    for trade in rows:
        if not eligible(trade, previous):
            s["n_blocked"] += 1
            continue
        risk = base_risk * (2. ** level if schedule == "double" else 1.)
        frac = float(trade["initial_risk_frac"])
        if not math.isfinite(frac) or frac <= 0:
            raise ValueError("invalid initial risk fraction")
        notional = risk / frac
        reserve = COST * notional
        required = risk + reserve
        if leverage_cap is not None:
            required = max(required, notional / leverage_cap + reserve)
        s["max_attempted_risk"] = max(s["max_attempted_risk"], risk)
        before, old_net, old_level = balance, cycle_net, level
        common = dict(signal_i=trade["signal_i"], side=trade["side"],
                      entry_time=str(trade["entry_time"]), risk=risk,
                      notional=notional, fee_reserve=reserve, required_cash=required,
                      equity_before=balance, cycle_id=cycle_id,
                      cycle_net_before=cycle_net, level_before=level)
        if required > balance or balance <= 0:
            s["n_capacity_rejected"] += 1
            permanent = risk >= balance - EPS
            reason = "price_risk_exceeds_cash" if permanent else "capacity_wait"
            ledger.append(dict(common, accepted=False, reason=reason, pnl=0.,
                               equity_after=balance, cycle_net_after=cycle_net,
                               level_after=level, cycle_end=None))
            if permanent:
                s.update(halt_reason=reason, halt_time=str(trade["entry_time"]),
                         halt_risk=risk, halt_equity=balance)
                break
            continue
        net_r = float(trade["net_r"])
        gross_r = float(trade["gross_r"])
        if not math.isfinite(net_r) or not math.isfinite(gross_r):
            raise ValueError("unresolved outcome cannot disappear")
        raw_pnl = risk * net_r
        balance = max(0., before + raw_pnl)
        pnl = balance - before
        cycle_net += pnl
        cycle_n += 1
        s["n_accepted"] += 1
        s["max_risk"] = max(s["max_risk"], risk)
        s["max_effective_leverage"] = max(s["max_effective_leverage"], notional / before)
        peak = max(peak, balance)
        s["max_realized_drawdown"] = max(s["max_realized_drawdown"], 1 - balance / peak)
        censored = bool(trade.get("censored", False))
        reason = str(trade["exit_reason"])
        net_be = reason.startswith("cost_be") and net_r >= -EPS
        full_stop = bool(trade.get("full_initial_stop", reason.startswith("initial_stop")))
        end_reason = None
        if censored:
            s["n_boundary"] += 1
            s["boundary_pnl"] += pnl
            outcome = "boundary"
        else:
            lost = net_r < -EPS
            profit = net_r > EPS and not net_be
            outcome = "loss" if lost else "net_be" if net_be else "profit" if profit else "zero"
            s["n_natural"] += 1
            s["n_net_loss"] += int(lost)
            s["n_profit"] += int(profit)
            s["n_net_be"] += int(net_be)
            s["n_other_zero"] += int(outcome == "zero")
            s["n_strict_positive"] += int(net_r > EPS)
            s["n_full_initial_stop"] += int(full_stop)
            s["n_tp"] += int(reason.startswith("take_profit"))
            s["natural_net_r"] += net_r
            s["natural_gross_r"] += gross_r
            s["positive_cash"] += max(0., pnl)
            s["negative_cash"] += min(0., pnl)
            net_run = net_run + 1 if lost else 0
            gross_run = gross_run + 1 if gross_r < -EPS else 0
            initial_run = initial_run + 1 if full_stop else 0
            s["max_net_loss_streak"] = max(s["max_net_loss_streak"], net_run)
            s["max_gross_loss_streak"] = max(s["max_gross_loss_streak"], gross_run)
            s["max_initial_stop_streak"] = max(s["max_initial_stop_streak"], initial_run)
            if lost:
                level += 1
            s["max_unrecovered_loss_events"] = max(s["max_unrecovered_loss_events"], level)
            if cycle_net >= -EPS:
                end_reason = "recovered"
        ledger.append(dict(common, accepted=True, entry_i=trade["entry_i"],
                           exit_i=trade["exit_i"], exit_time=str(trade["exit_time"]),
                           reason=reason, net_r=net_r, gross_r=gross_r,
                           cost_r=trade.get("cost_r", COST / frac),
                           full_initial_stop=full_stop, outcome=outcome,
                           censored=censored, pnl=pnl, raw_pnl=raw_pnl,
                           equity_after=balance, cycle_net_after=cycle_net,
                           level_after=level, cycle_end=end_reason))
        previous = trade
        if end_reason:
            finish(end_reason)
    residual_debt = max(0., -cycle_net)
    finish("capacity_halt" if s["halt_reason"] else "unfinished")
    n = s["n_natural"]
    s.update(initial_balance=initial_balance, final_balance=balance,
             residual_debt=residual_debt, profit=balance-initial_balance,
             profit_rate=s["n_profit"] / n if n else None,
             be_rate=s["n_net_be"] / n if n else None,
             loss_rate=s["n_net_loss"] / n if n else None,
             mean_net_r=s["natural_net_r"] / n if n else None,
             mean_gross_r=s["natural_gross_r"] / n if n else None,
             cash_pf=s["positive_cash"] / -s["negative_cash"] if s["negative_cash"] < 0 else None)
    assert abs(sum(r["pnl"] for r in ledger) + initial_balance - balance) < 1e-7
    assert abs(sum(r["net_pnl"] for r in cycles) + initial_balance - balance) < 1e-7
    assert s["n_profit"] + s["n_net_be"] + s["n_other_zero"] + s["n_net_loss"] == n
    return dict(summary=s, ledger=ledger, cycles=cycles)
