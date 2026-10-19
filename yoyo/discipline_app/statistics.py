"""Paper-account statistics from the full durable ledger, never UI history slices.

Closed-trade metrics use whole-trade net PnL and immutable entry R. Fees use
execution time; the curve contains closed-trade cumulative PnL, not a historical
mark-to-market equity series. No historical equity drawdown is inferred.
"""
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo
from .policy import dec

ZERO = Decimal(0)


def trade_summary(trades):
    pnls = [dec(t['pnl']) for t in trades]
    wins = [v for v in pnls if v > 0]
    losses = [v for v in pnls if v < 0]
    count = len(pnls)
    average_win = sum(wins, ZERO) / len(wins) if wins else None
    average_loss = -sum(losses, ZERO) / len(losses) if losses else None
    ratio = average_win / average_loss if average_win is not None and average_loss else None
    valid_r = count and all(dec(t['risk']) > 0 for t in trades)
    avg_r = sum((dec(t['pnl']) / dec(t['risk']) for t in trades), ZERO) / count if valid_r else None
    return dict(count=count, wins=len(wins), losses=len(losses), flat=count-len(wins)-len(losses),
                net_pnl=str(sum(pnls, ZERO)), win_rate=str(Decimal(len(wins)) / count) if count else None,
                payoff_ratio=str(ratio) if ratio is not None else None,
                average_net_r=str(avg_r) if avg_r is not None else None)


def summarize(state, equity, fee_events, now, days):
    start = now - days * 86400 if days else None
    closed = sorted((t for t in state['closed'] if (start is None or t['closed'] >= start) and t['closed'] <= now),
                    key=lambda t: (t['closed'], t['id']))
    realized = sum((dec(t['pnl']) for t in state['closed'] + state['positions']), ZERO)
    initial = dec(state['cash']) - realized
    unrealized = equity - dec(state['cash'])
    account = dict(equity=str(equity), initial_equity=str(initial), realized_pnl=str(realized),
                   unrealized_pnl=str(unrealized), net_pnl=str(equity-initial),
                   return_fraction=str((equity-initial)/initial) if initial > 0 else None,
                   open_positions=len(state['positions']), pending_orders=len(state['pending']))
    fees = sum((dec(fee) for timestamp, fee in fee_events
                if (start is None or timestamp >= start) and timestamp <= now), ZERO)
    daily = defaultdict(lambda: ZERO)
    for trade in closed:
        date = datetime.fromtimestamp(trade['closed'], ZoneInfo('Asia/Shanghai')).date().isoformat()
        daily[date] += dec(trade['pnl'])
    total, curve = ZERO, []
    for date, value in sorted(daily.items()):
        total += value
        curve.append(dict(date=date, pnl=str(total)))
    return dict(mode='paper', server_time=now, period_days=days, period_start=start,
                account=account, closed=trade_summary(closed), fees=str(fees), curve=curve,
                symbols=[dict(symbol=s, **trade_summary([t for t in closed if t['symbol'] == s]))
                         for s in sorted({t['symbol'] for t in closed})],
                sides=[dict(side=s, **trade_summary([t for t in closed if t['side'] == s]))
                       for s in ('long', 'short')],
                market_fresh=not state['positions'] or all(
                    p['symbol'] in state['quotes'] and 0 <= now-state['quotes'][p['symbol']]['time'] <= 15
                    for p in state['positions']))
