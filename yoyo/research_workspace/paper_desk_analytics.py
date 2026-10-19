"""Account history from immutable fills and observed equity, never synthetic P&L.

Balance history includes every fill (opening fees on their own day). Equity is
sampled by the independent worker once a minute; missing quotes produce gaps.
Daily settlement uses Asia/Shanghai, matching the owner's trading workspace.
"""
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from .paper_desk import clock_ms, number

ZONE = ZoneInfo('Asia/Shanghai')


def trade_statistics(account, positions, fills):
    """Lifetime metrics from complete account fills and fully closed lifecycles.

    Additions and partial exits share one position ID. Only a fully flat
    lifecycle enters win/loss metrics, with every entry/exit fee deducted.
    Balance drawdown uses every settlement event, before chart compression;
    it deliberately excludes unobserved intratrade equity drawdown.
    """
    zero = Decimal(0)
    initial = number(account['initial_cash'])
    balance = peak = initial
    drawdown = fees = turnover = zero
    nets = {}
    for fill in fills:
        fee = number(fill['fee'])
        net = number(fill['gross_pnl']) - fee
        nets[fill['position_id']] = nets.get(fill['position_id'], zero) + net
        fees += fee
        turnover += number(fill['quantity']) * number(fill['price'])
        balance += net
        peak = max(peak, balance)
        if peak > 0:
            drawdown = max(drawdown, (peak - balance) / peak * 100)
    closed = [p for p in positions if number(p['remaining']) == 0 and p['id'] in nets]
    outcomes = [nets[p['id']] for p in closed]
    wins = [n for n in outcomes if n > 0]
    losses = [n for n in outcomes if n < 0]
    average_win = sum(wins, zero) / len(wins) if wins else None
    average_loss = sum(losses, zero) / len(losses) if losses else None
    durations = [max(0, p['closed_ms'] - p['opened_ms']) for p in closed if p['closed_ms'] is not None]
    def value(n):
        return str(n) if n is not None else None
    return dict(
        closed_positions=len(closed), wins=len(wins), losses=len(losses),
        breakeven=sum(n == 0 for n in outcomes),
        win_rate=value(Decimal(len(wins)) / len(closed) * 100 if closed else None),
        closed_net=str(sum(outcomes, zero)), total_net=str(balance - initial),
        total_fees=str(fees), turnover=str(turnover), return_percent=value((balance - initial) / initial * 100 if initial > 0 else None),
        average_win=value(average_win), average_loss=value(average_loss),
        payoff_ratio=value(average_win / abs(average_loss) if wins and losses else None),
        profit_factor=value(sum(wins, zero) / abs(sum(losses, zero)) if losses else None),
        best_trade=value(max(outcomes) if outcomes else None), worst_trade=value(min(outcomes) if outcomes else None),
        max_balance_drawdown_percent=str(drawdown),
        average_holding_ms=sum(durations) // len(durations) if durations else None)


def sample_equity(desk, marks):
    at = clock_ms()
    with desk.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        for row in db.execute('SELECT * FROM accounts').fetchall():
            account = dict(row)
            positions = [dict(p) for p in db.execute('SELECT * FROM positions WHERE account_id=? AND CAST(remaining AS NUMERIC)>0', (account['id'],))]
            totals = desk.totals(account, positions, marks, at)
            db.execute('INSERT OR IGNORE INTO equity_samples(account_id,bucket_ms,observed_ms,equity,balance) VALUES(?,?,?,?,?)',
                       (account['id'], at // 60000 * 60000, at, totals['equity'], totals['balance']))


def _ledger(account, positions, fills):
    balance = number(account['initial_cash'])
    curve = [dict(at=account['created_ms'], balance=str(balance))]
    days = {}
    for fill in fills:
        net = number(fill['gross_pnl']) - number(fill['fee'])
        balance += net
        curve.append(dict(at=fill['created_ms'], balance=str(balance)))
        day = datetime.fromtimestamp(fill['created_ms'] / 1000, ZONE).strftime('%Y-%m-%d')
        d = days.setdefault(day, dict(net=Decimal(0), fees=Decimal(0), closes=0, fills=0))
        d['net'] += net
        d['fees'] += number(fill['fee'])
        d['closes'] += fill['action'] == 'close'
        d['fills'] += 1
    # Keep the API bounded while retaining endpoints and extrema per time bin.
    def compress(rows, key):
        if len(rows) <= 1200:
            return rows
        stride = (len(rows) + 299) // 300
        out = []
        for start in range(0, len(rows), stride):
            block = rows[start:start + stride]
            valid = [i for i, r in enumerate(block) if r[key] is not None]
            indices = {0, len(block) - 1}
            if valid:
                indices |= {min(valid, key=lambda i: number(block[i][key])), max(valid, key=lambda i: number(block[i][key]))}
            else:
                indices.add(0)
            # An outage must remain visible even inside a compressed bin.
            if len(valid) != len(block):
                indices.add(next(i for i, r in enumerate(block) if r[key] is None))
            out.extend(block[i] for i in sorted(indices))
        return out
    return dict(curve=compress(curve, 'balance'), days=days, balance=balance,
                total_net=str(balance-number(account['initial_cash'])), fill_count=len(fills),
                statistics=trade_statistics(account, positions, fills))


def history(desk, account_id, month=None):
    at = clock_ms()
    month = month or datetime.fromtimestamp(at / 1000, ZONE).strftime('%Y-%m')
    try:
        parsed = datetime.strptime(month, '%Y-%m')
        if parsed.strftime('%Y-%m') != month:
            raise ValueError()
    except ValueError:
        raise ValueError('月份格式须为 YYYY-MM') from None
    from .paper_history_cache import read
    ledger, samples, first = read(desk, account_id, _ledger)
    daily = [dict(date=day, net=str(d['net']), fees=str(d['fees']), closes=d['closes'], fills=d['fills'])
             for day, d in sorted(ledger['days'].items()) if day.startswith(month + '-')]
    monthly = sum((number(d['net']) for d in daily), Decimal(0))
    return dict(account_id=account_id, month=month, timezone='Asia/Shanghai', now_ms=at,
                balance_curve=ledger['curve'] + [dict(at=at, balance=str(ledger['balance']))], equity_curve=samples,
                days=daily, month_net=str(monthly), profitable_days=sum(number(d['net']) > 0 for d in daily),
                losing_days=sum(number(d['net']) < 0 for d in daily), total_net=ledger['total_net'],
                sampled_since=first, fill_count=ledger['fill_count'], statistics=dict(ledger['statistics']))

def closed_position_summaries(db, position_ids=None):
    """Rebuild closed-position display from ALL fills, never the 500-row UI slice.

    Quantity/notional are summed by fill; average prices are volume weighted.
    Return on cumulative posted entry margin is explicitly named, not mislabeled
    as exchange ROI or inferred from the final remaining-position entry price.
    """
    from decimal import Decimal
    from .paper_desk import number
    groups = {}
    if position_ids == []:
        return {}
    selection = '' if position_ids is None else ' AND p.id IN (' + ','.join('?' for _ in position_ids) + ')'
    rows = db.execute('SELECT f.*,p.leverage FROM fills f JOIN positions p ON p.id=f.position_id '
                      'WHERE CAST(p.remaining AS NUMERIC)=0' + selection + ' ORDER BY f.created_ms,f.rowid',
                      () if position_ids is None else tuple(position_ids))
    for row in rows:
        f = dict(row)
        item = groups.setdefault(f['position_id'], dict(open_qty=Decimal(0), close_qty=Decimal(0),
            open_notional=Decimal(0), close_notional=Decimal(0), entry_margin=Decimal(0), fills=[]))
        prefix = 'open' if f['action'] == 'open' else 'close'
        quantity, price = number(f['quantity']), number(f['price'])
        item[prefix + '_qty'] += quantity
        item[prefix + '_notional'] += quantity * price
        if prefix == 'open':
            item['entry_margin'] += quantity * price / f['leverage']
        item['fills'].append(f)
    result = {}
    for position_id, item in groups.items():
        result[position_id] = dict(
            average_open_price=str(item['open_notional']/item['open_qty']) if item['open_qty'] else None,
            average_close_price=str(item['close_notional']/item['close_qty']) if item['close_qty'] else None,
            opened_notional=str(item['open_notional']), closed_notional=str(item['close_notional']),
            cumulative_entry_margin=str(item['entry_margin']),
            closed_quantity=str(item['close_qty']),
            closing_reason=item['fills'][-1].get('exit_reason') if item['fills'] else None,
            related_fills=item['fills'])
    return result
