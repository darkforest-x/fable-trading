"""USDT cross-margin simulation using OKX public mark prices and position tiers.

Sources: OKX docs-v5 public/mark-price, public/position-tiers and instruments;
https://www.okx.com/help/v-tiered-maintenance-margin-ratio-rules.
Both directions count toward the symbol tier. Conditional liquidation prices
hold other symbols fixed. The simulator closes all positions on breach rather
than reproducing OKX's staged reductions, insurance fund or ADL.
"""
from decimal import Decimal
import threading
import time

import httpx

from .paper_desk import TAKER_FEE, clock_ms, fresh, number, positive


class PublicRisk:
    def __init__(self, http=None):
        from .paper_desk_market import PublicHTTP
        self.http = http or PublicHTTP()
        self.lock = threading.Lock()
        self.background = False
        self.instruments = {}
        self.instrument_at = 0
        self.tiers = {}
        self.marks = {}
        self.mark_at = 0
        self.requested = {}
        self.wake = lambda: None

    def fetch(self, path, **params):
        return self.http.get('public/' + path, **params)

    def want(self, symbols):
        now = time.monotonic()
        with self.lock:
            self.requested = {s: at for s, at in self.requested.items() if now-at < 300}
            needed = any(s not in self.tiers or now-self.tiers[s][0]>=3600 for s in symbols)
            self.requested.update({s: now for s in symbols})
        if needed:
            self.wake()

    def refresh_instruments(self):
        if self.instruments and time.monotonic() - self.instrument_at < 3600:
            return
        rows = self.fetch('instruments', instType='SWAP')
        instruments = {r['instId']: r for r in rows
                       if r.get('ctType') == 'linear' and r.get('settleCcy') == 'USDT'}
        if not instruments:
            raise ValueError('OKX 合约信息为空')
        self.instruments = instruments
        self.instrument_at = time.monotonic()

    def refresh_marks(self):
        if self.marks and time.monotonic() - self.mark_at < 3:
            return
        rows = self.fetch('mark-price', instType='SWAP')
        self.marks = {r['instId']: r for r in rows}
        self.mark_at = time.monotonic()

    def refresh_tiers(self, symbols, stopped=lambda: False):
        instruments = self.instruments
        for symbol in symbols:
            if stopped():
                break
            instrument = instruments.get(symbol)
            cached = self.tiers.get(symbol)
            if not instrument or cached and time.monotonic() - cached[0] < 3600:
                continue
            rows = self.fetch('position-tiers', instType='SWAP', tdMode='cross',
                              instFamily=instrument['instFamily'])
            if rows:
                self.tiers = {**self.tiers, symbol: (time.monotonic(), rows, clock_ms())}

    def get(self, symbols):
        if not symbols:
            return {}
        if self.background:
            self.want(symbols)
        else:
            self.refresh_instruments()
            self.refresh_marks()
            self.refresh_tiers(symbols)
        instruments, marks, tiers = self.instruments, self.marks, self.tiers
        result = {}
        for symbol in symbols:
            instrument, mark, cached = instruments.get(symbol), marks.get(symbol), tiers.get(symbol)
            if (not instrument or not mark or not cached or
                    time.monotonic()-self.instrument_at >= 3600 or time.monotonic()-cached[0] >= 3600):
                continue
            result[symbol] = dict(mark_price=str(positive(mark['markPx'])), mark_source_ms=int(mark['ts']),
                                  risk=dict(contract_size=str(positive(instrument['ctVal']) * positive(instrument['ctMult'])),
                                            tiers=cached[1], fetched_ms=cached[2], instrument=dict(instrument)))
        return result


def tier_for(quote, quantity):
    """Round fractional simulated contract counts upward across tier boundaries."""
    risk = quote.get('risk')
    if not risk or not 0 <= clock_ms() - risk.get('fetched_ms', 0) <= 3600000:
        raise ValueError('维持保证金档位待更新')
    contracts = number(quantity) / positive(risk['contract_size'])
    tiers = sorted(risk['tiers'], key=lambda t: positive(t['maxSz']))
    for tier in tiers:
        if contracts <= positive(tier['maxSz']):
            rate = positive(tier['mmr'])
            if rate >= 1:
                raise ValueError('维持保证金率异常')
            return rate, positive(tier['maxLever'])
    raise ValueError('仓位超过 OKX 公开档位上限')


def account_risk(account, positions, marks, at):
    active = [p for p in positions if number(p['remaining']) > 0]
    if not active:
        return dict(risk_complete=True, risk_equity=account['cash'], maintenance='0',
                    liquidation_fee_reserve='0', margin_ratio=None, liquidation_prices={}, breached=False)
    grouped = {}
    try:
        for p in active:
            symbol = p['symbol']
            q = marks.get(symbol)
            if (not q or q.get('symbol') != symbol or not fresh(q, at)
                    or not -5000 <= at - q.get('mark_source_ms', 0) <= 15000):
                raise ValueError('标记价格待更新')
            group = grouped.setdefault(symbol, dict(quantity=Decimal(0), net=Decimal(0), entry=Decimal(0),
                                                     mark=positive(q['mark_price']), quote=q))
            qty = number(p['remaining'])
            signed = qty * (1 if p['side'] == 'long' else -1)
            group['quantity'] += qty
            group['net'] += signed
            group['entry'] += signed * number(p['entry_price'])
        equity, required, maintenance = number(account['cash']), Decimal(0), Decimal(0)
        for group in grouped.values():
            group['mmr'], _ = tier_for(group['quote'], group['quantity'])
            group['upl'] = group['net'] * group['mark'] - group['entry']
            group['requirement'] = group['quantity'] * group['mark'] * (group['mmr'] + TAKER_FEE)
            equity += group['upl']
            required += group['requirement']
            maintenance += group['quantity'] * group['mark'] * group['mmr']
        prices = {}
        for symbol, group in grouped.items():
            # Equity minus maintenance and closing fees is affine in this mark.
            slope = group['net'] - group['quantity'] * (group['mmr'] + TAKER_FEE)
            threshold = group['mark'] - (equity - required) / slope if slope else None
            prices[symbol] = str(threshold) if threshold is not None and threshold > 0 else None
        return dict(risk_complete=True, risk_equity=str(equity), maintenance=str(maintenance),
                    liquidation_fee_reserve=str(required - maintenance), margin_ratio=str(equity / required),
                    liquidation_prices=prices, breached=equity <= required)
    except (ValueError, KeyError, TypeError):
        return dict(risk_complete=False, risk_equity=None, maintenance=None,
                    liquidation_fee_reserve=None, margin_ratio=None, liquidation_prices={}, breached=False)


def liquidate_accounts(desk, db, marks, at):
    """Atomic full-account liquidation, with a durable input/settlement audit."""
    for row in db.execute('SELECT * FROM accounts').fetchall():
        account = dict(row)
        positions = [dict(p) for p in db.execute('SELECT * FROM positions WHERE account_id=? AND CAST(remaining AS NUMERIC)>0', (account['id'],))
                     if number(p['remaining']) > 0]
        risk = account_risk(account, positions, marks, at)
        if not risk['risk_complete'] or not risk['breached']:
            continue
        # Neither stale pre-entry marks nor a missing executable price can close a book.
        if any(min(marks[p['symbol']]['source_ms'], marks[p['symbol']]['received_ms'], marks[p['symbol']]['mark_source_ms'])
               <= (p.get('exposure_updated_ms') or p['opened_ms']) for p in positions):
            continue
        db.execute("UPDATE limit_orders SET status='cancelled',updated_ms=?,reason='全仓强平，撤销账户全部挂单' "
                   "WHERE account_id=? AND status='pending'", (at, account['id']))
        fills = []
        for p in positions:
            q = desk.record_quote(db, marks[p['symbol']])
            group_qty = sum(number(x['remaining']) for x in positions if x['symbol'] == p['symbol'])
            mmr, _ = tier_for(marks[p['symbol']], group_qty)
            clearance = number(p['remaining']) * number(q['mark_price']) * mmr
            result = desk.close_fill(db, p, number(p['remaining']), q, at, exit_reason='liquidation', clearance_fee=clearance)
            fills.append(result['fill']['id'])
        from .paper_desk import encoded
        db.execute('INSERT INTO liquidation_events(account_id,created_ms,evidence) VALUES(?,?,?)',
                   (account['id'], at, encoded(dict(risk=risk, fill_ids=fills))))
