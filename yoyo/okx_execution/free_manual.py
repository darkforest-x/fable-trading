"""Owner-requested discretionary tickets (2026-10-23), under the manual worker lock.

OKX V5 Trade / Place order and Account / Set leverage:
https://www.okx.com/docs-v5/en/#order-book-trading-trade-post-place-order
User chooses direction, notional, leverage, limit and optional fixed exits.
Previews only GET. A durable confirmation binds exact contracts and prices.
Exchange-native orders remain owner-managed, separate from YOLO strategy lots.
Unknown POSTs are reconciled by GET and never resubmitted, including on restart.
"""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import uuid

from .client import APIError, TransportError
from .engine import one, TERMINAL
from .manual_inventory import policy, pages
from .risk import D, amount, quantize
from .store import identifier

REVISION = 'free-manual-20261023-v2'
PENDING = ('confirmed', 'leverage_pending', 'sending', 'live', 'partially_filled', 'protection_pending')


class Tickets:
    def __init__(self, runtime):
        self.path = Path(runtime) / 'free-manual.sqlite3'
        if self.path.is_symlink():
            raise ValueError('unsafe ticket path')
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS tickets(id TEXT PRIMARY KEY, symbol TEXT, state TEXT, value TEXT);
                CREATE UNIQUE INDEX IF NOT EXISTS pending_symbol ON tickets(symbol)
                WHERE state IN ('confirmed','leverage_pending','sending','live','partially_filled','protection_pending','unknown');
            """)
        self.path.chmod(0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.execute('PRAGMA synchronous=FULL')
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, identity):
        with self.db() as db:
            row = db.execute('SELECT state,value FROM tickets WHERE id=?', (identity,)).fetchone()
        if not row:
            raise ValueError('订单预览不存在')
        return dict(json.loads(row[1]), state=row[0])

    def preview(self, value):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE tickets SET state='superseded' WHERE state='preview' AND json_extract(value,'$.ticket_id')=?", (value['ticket_id'],))
            db.execute('INSERT INTO tickets VALUES(?,?,?,?)', (value['id'], value['symbol'], 'preview', json.dumps(value)))
        return self.get(value['id'])

    def save(self, value, state):
        with self.db() as db:
            db.execute('UPDATE tickets SET state=?,value=? WHERE id=?', (state, json.dumps(value), value['id']))
        return self.get(value['id'])

    def confirm(self, identity, now, uid):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT state,value FROM tickets WHERE id=?', (identity,)).fetchone()
            if not row:
                raise ValueError('订单预览不存在')
            value = json.loads(row[1])
            if value['account_uid'] != uid:
                raise ValueError('执行账户已变化')
            if value.get('confirmed_ms'):
                return dict(value, state=row[0])
            if row[0] != 'preview' or not value['created_ms'] <= now < value['expires_ms']:
                raise ValueError('订单预览已过期或已编辑，请重新预览')
            if 'exposure_cap' in value:
                from yoyo.contracts.manual_exposure import pool
                used = D(value['exchange_exposure_used'])
                represented = value.get('represented_client_ids', [])
                for state, encoded in db.execute("SELECT state,value FROM tickets WHERE state IN ('confirmed','leverage_pending','sending','live','partially_filled','protection_pending','unknown')"):
                    other = json.loads(encoded)
                    if pool(other['symbol']) == pool(value['symbol']) and other['client_order_id'] not in represented:
                        used += D(other['notional'])
                if used + D(value['notional']) > D(value['exposure_cap']):
                    raise ValueError('仓位额度已被其他委托占用，请重新预览')
            value['confirmed_ms'] = now
            try:
                db.execute("UPDATE tickets SET state='confirmed',value=? WHERE id=?", (json.dumps(value), identity))
            except sqlite3.IntegrityError:
                raise ValueError('这个币种已有待核对的手动订单，请先查看原订单') from None
        return dict(value, state='confirmed')

    def pending(self):
        with self.db() as db:
            rows = db.execute('SELECT id FROM tickets WHERE state IN ('+','.join('?' for _ in PENDING)+') ORDER BY rowid', PENDING).fetchall()
        return [self.get(r[0]) for r in rows]

    def reserved(self):
        with self.db() as db:
            rows = db.execute("SELECT state,value FROM tickets WHERE state IN ('confirmed','leverage_pending','sending','live','partially_filled','protection_pending','unknown')").fetchall()
        return [dict(json.loads(r[1]), state=r[0]) for r in rows]

    def recent(self):
        with self.db() as db:
            rows = db.execute("SELECT id FROM tickets WHERE json_extract(value,'$.confirmed_ms') IS NOT NULL ORDER BY rowid DESC LIMIT 30").fetchall()
        return [self.get(r[0]) for r in rows]


def clean(value):
    return {k: v for k, v in value.items() if k not in ('account_uid', 'order_body', 'ticket_id', 'represented_client_ids')}


def vacant(engine, account, symbol):
    """Changing leverage cannot affect an existing position or strategy lot."""
    rules = policy(engine.store)
    if not rules or account['uid'] != rules['account_uid'] or account['uid'] != engine.store.meta('account_uid'):
        raise ValueError('手动账户归属未核对')
    if any(t['symbol'] == symbol and t['state'] not in TERMINAL for t in engine.store.trades()):
        raise ValueError('这个币种由均线策略管理，不能混入自由手动订单')
    if any(p['instId'] == symbol and D(p['pos']) != 0 for p in account['positions']):
        raise ValueError('这个币种已有持仓，请先在账户页核对；本入口不改已有仓位的杠杆')
    inventory = []
    for path, identity, queries in (
        ('/api/v5/trade/orders-pending', 'ordId', [{}]),
        ('/api/v5/trade/orders-algo-pending', 'algoId', [dict(ordType=k) for k in ('conditional', 'oco', 'trigger', 'move_order_stop')]),
    ):
        for query in queries:
            rows = pages(engine.client, path, identity, **query)
            inventory.extend(rows)
            if any(r['instId'] == symbol for r in rows):
                raise ValueError('这个币种已有挂单或保护单，请先在账户页核对')
    return inventory


def make_plan(engine, terms, *, approved=None, defaults_only=False):
    """Read actual specifications/fees; never resize or change a reviewed order."""
    symbol, side = terms['symbol'], terms['side']
    if side not in ('long', 'short') or terms['order_type'] not in ('market', 'limit'):
        raise ValueError('请选择方向和委托类型')
    account = engine.account()
    inventory = vacant(engine, account, symbol)
    from .free_exposure import budget
    from yoyo.contracts.manual_exposure import target
    limits = budget(engine, account, symbol, inventory,
                    Tickets(engine.store.path.parent).reserved(), approved['id'] if approved else None)
    instrument = one(engine.client.get('/api/v5/account/instruments', instType='SWAP', instId=symbol))
    if (instrument.get('instId') != symbol or instrument.get('instType') != 'SWAP'
            or instrument.get('ctType') != 'linear' or instrument.get('settleCcy') != 'USDT'
            or instrument.get('state') != 'live' or instrument.get('ctValCcy') != symbol.split('-')[0]):
        raise ValueError('仅支持当前可交易的 USDT 线性永续')
    ticker = one(engine.client.get('/api/v5/market/ticker', instId=symbol))
    if ticker.get('instId') != symbol or not -1000 <= engine.clock()-int(ticker['ts']) <= 5000:
        raise ValueError('行情已过期，请重新预览')
    tick, lot, ctval = (D(instrument[k]) for k in ('tickSz', 'lotSz', 'ctVal'))
    if min(tick, lot, ctval) <= 0:
        raise ValueError('合约规格暂不可用')
    price = D(terms['limit_price'] if terms['order_type'] == 'limit' else ticker['askPx' if side == 'long' else 'bidPx'])
    requested = D(terms['notional_usdt']) if terms.get('notional_usdt') is not None else target(limits, terms.get('allocation_percent', '100'))
    leverage = D(terms['leverage']) if terms.get('leverage') is not None else D(1)
    if requested > D(limits['exposure_remaining']):
        raise ValueError('超过总权益仓位额度，请刷新账户或降低额度')
    if min(price, leverage, requested) <= 0 or leverage != leverage.to_integral_value():
        raise ValueError('金额和价格必须大于 0，杠杆必须是正整数')
    if terms['order_type'] == 'limit' and price % tick:
        raise ValueError(f'限价须符合最小报价单位 {tick}')
    size = D(approved['size']) if approved else quantize(requested/(price*ctval), lot)
    max_size = D(instrument['maxMktSz' if terms['order_type'] == 'market' else 'maxLmtSz'])
    if size < D(instrument['minSz']) or size % lot or max_size <= 0 or size > max_size:
        raise ValueError('委托数量不符合该合约的最小数量、步长或单笔上限')
    tiers = engine.client.get('/api/v5/public/position-tiers', instType='SWAP', tdMode='cross', instFamily=instrument['instFamily'])
    matching = [t for t in tiers if D(t['minSz']) <= size <= D(t['maxSz'])]
    if not matching:
        raise ValueError('委托数量不在交易所支持的持仓档位内')
    max_leverage = min(D(instrument['lever']), *(D(t['maxLever']) for t in matching))
    if terms.get('leverage') is None:
        leverage = max_leverage.to_integral_value(rounding='ROUND_FLOOR')
    if leverage > max_leverage:
        raise ValueError(f'该币种在此数量下最高支持 {amount(max_leverage)} 倍杠杆，请调整后重新预览')
    limits.update(max_leverage=amount(max_leverage), leverage=amount(leverage),
                  price=amount(price), notional_usdt=amount(requested),
                  notional=amount(size*ctval*price), size=amount(size),
                  available=account['available'], tick_size=amount(tick))
    if defaults_only:
        return limits
    fee = one(engine.client.get('/api/v5/account/trade-fee', instType='SWAP', instFamily=instrument['instFamily']))
    # Current V5 uses feeGroup; top-level maker/taker are deprecated and are
    # empty for USDT swaps. Match the instrument's group, never guess a fee.
    groups = [g for g in fee.get('feeGroup', []) if g.get('groupId') == instrument.get('groupId')]
    if not instrument.get('groupId') or fee.get('instType') != 'SWAP' or len(groups) != 1:
        raise ValueError('未能核对该合约的实际手续费组，请稍后重试')
    maker, taker = -D(groups[0]['maker']), -D(groups[0]['taker'])
    if max(abs(maker), abs(taker)) > D('.01'):
        raise ValueError('手续费返回异常，请稍后重新预览')
    notional = size*ctval*price
    margin, fee_reserve = notional/leverage, notional*max(D('0'), maker, taker)
    if margin+fee_reserve > D(account['available']):
        raise ValueError('可用资金不足以支付本单保证金和开仓手续费')
    if approved and (notional > D(approved['notional']) or margin+fee_reserve > D(approved['required'])):
        raise ValueError('市场价格或费用已变化并超出确认金额，请重新预览')
    exits = {}
    for field, prefix, direction in (('take_profit', 'tp', 1), ('stop_loss', 'sl', -1)):
        raw = terms.get(field)
        if raw is None:
            continue
        value = D(raw)
        if value <= 0 or value % tick or (1 if side == 'long' else -1)*direction*(value-price) <= 0:
            raise ValueError('止盈止损须在入场价的正确一侧，并符合最小报价单位')
        exits.update({prefix+'TriggerPx': amount(value), prefix+'OrdPx': '-1', prefix+'TriggerPxType': 'last'})
    now, identity = engine.clock(), approved['id'] if approved else uuid.uuid4().hex
    body = dict(instId=symbol, tdMode='cross', posSide=side, side='buy' if side == 'long' else 'sell',
                ordType=terms['order_type'], sz=amount(size), clOrdId=identifier(identity, 'free-entry'))
    if terms['order_type'] == 'limit':
        body['px'] = amount(price)
    if exits:
        body['attachAlgoOrds'] = [dict(exits, attachAlgoClOrdId=identifier(identity, 'free-exits'))]
    return dict({**terms, **limits}, id=identity, account_uid=account['uid'], account_scope='yolo_manual',
                size=amount(size), base_quantity=amount(size*ctval), price=amount(price),
                notional=amount(notional), margin=amount(margin), fee_reserve=amount(fee_reserve),
                required=amount(margin+fee_reserve), available=account['available'],
                maker_fee=amount(maker), taker_fee=amount(taker), fee_read_ms=now,
                created_ms=now, expires_ms=now+60000, quote_ms=int(ticker['ts']), order_body=body,
                client_order_id=body['clOrdId'], protection='pending' if exits else 'none')


def reconcile(engine, book, ticket):
    """GET only after the durable send boundary; an absent order never permits retry."""
    try:
        order = one(engine.client.get('/api/v5/trade/order', instId=ticket['symbol'], clOrdId=ticket['client_order_id']))
    except APIError as error:
        if error.code == '51603':
            # Eventual consistency is possible. Persist uncertainty, never replay.
            book.save(dict(ticket, reason='交易所暂未查到原订单，正在核对；不会重复提交'), 'sending')
            return
        raise
    body = ticket['order_body']
    if (any(order.get(k) != body[k] for k in ('instId', 'clOrdId', 'side', 'posSide', 'ordType', 'tdMode'))
            or D(order['sz']) != D(body['sz'])
            or (body['ordType'] == 'limit' and D(order.get('px') or '0') != D(body['px']))):
        book.save(dict(ticket, reason='订单回执与确认内容不一致，请在交易所核对'), 'unknown')
        return
    state = order.get('state')
    if state not in ('live', 'partially_filled', 'filled', 'canceled'):
        raise ValueError('订单状态暂不可用')
    ticket = dict(ticket, order_id=order['ordId'], filled_size=order.get('accFillSz'),
                  average_price=order.get('avgPx'), updated_ms=engine.clock(), reason=None)
    if body.get('attachAlgoOrds') and D(order.get('accFillSz') or '0') > 0:
        if state in ('filled', 'canceled'):
            algo_id = body['attachAlgoOrds'][0]['attachAlgoClOrdId']
            # Pending list is authoritative for active protection. Filled/canceled
            # protection is available through its exact client ID in details.
            try:
                algo = one(engine.client.get('/api/v5/trade/order-algo', algoClOrdId=algo_id))
            except APIError as error:
                if error.code != '51603':
                    raise
                algo = {}
            attached = body['attachAlgoOrds'][0]
            valid = (algo.get('instId') == ticket['symbol'] and algo.get('algoClOrdId') == algo_id
                     and algo.get('tdMode') == 'cross' and algo.get('posSide') == body['posSide']
                     and algo.get('side') == ('sell' if body['side'] == 'buy' else 'buy')
                     and D(algo.get('sz') or '0') == D(order['accFillSz'])
                     and all((D(algo.get(k) or '0') == D(v) if k.endswith('Px') else algo.get(k) == v)
                             for k, v in attached.items() if k != 'attachAlgoClOrdId'))
            ticket['protection'] = 'verified' if valid and algo.get('state') in ('live', 'effective') else 'unverified'
            if ticket['protection'] != 'verified':
                ticket['reason'] = '已有成交，附带止盈止损尚未核验，请立即在交易所核对'
                state = 'protection_pending'
        else:
            ticket['protection'] = 'waiting_full_fill'
    book.save(ticket, state)


def consume(engine, book, *, entries):
    pending = book.pending()
    # One ticket per heartbeat bounds exchange reads and prioritizes existing
    # strategy protection. Round-robin includes old limit orders and new intents.
    index = getattr(engine, '_free_ticket_cursor', 0)
    engine._free_ticket_cursor = index+1
    for ticket in ([pending[index % len(pending)]] if pending else []):
        state = ticket['state']
        try:
            if state in ('sending', 'live', 'partially_filled', 'protection_pending'):
                reconcile(engine, book, ticket)
                continue
            if not entries:
                continue
            if not ticket.get('confirmed_ms') or not ticket['created_ms'] <= ticket['confirmed_ms'] <= engine.clock() < ticket['expires_ms']:
                book.save(dict(ticket, reason='确认报价已过期，请重新预览'), 'expired')
                continue
            plan = make_plan(engine, ticket, approved=ticket)
            if plan['account_uid'] != ticket['account_uid'] or plan['order_body'] != ticket['order_body']:
                raise ValueError('账户或合约规格已变化，请重新预览')
            if state == 'confirmed':
                ticket = book.save(dict(ticket, leverage_sent_ms=engine.clock()), 'leverage_pending')
                try:
                    engine.client.post('/api/v5/account/set-leverage', dict(instId=ticket['symbol'], mgnMode='cross', lever=ticket['leverage']))
                except APIError:
                    book.save(dict(ticket, reason='交易所拒绝所选杠杆，本次未下单'), 'rejected')
                    continue
            rows = engine.client.get('/api/v5/account/leverage-info', instId=ticket['symbol'], mgnMode='cross')
            if not rows or any(r.get('instId') != ticket['symbol'] or r.get('mgnMode') != 'cross' or D(r['lever']) != D(ticket['leverage']) for r in rows):
                if engine.clock()-ticket['leverage_sent_ms'] >= 10000:
                    book.save(dict(ticket, reason='杠杆未核验，本次未下单'), 'rejected')
                continue
            # Fresh reads after leverage; no existing account position is altered.
            make_plan(engine, ticket, approved=ticket)
            if engine.clock() >= ticket['expires_ms']:
                raise ValueError('确认报价已过期，本次未下单')
            book.save(ticket, 'sending')  # crash boundary BEFORE the single POST
            try:
                engine.client.post('/api/v5/trade/order', ticket['order_body'])
            except APIError as error:
                book.save(dict(ticket, reason=f'交易所拒绝委托（{error.code}）'), 'rejected')
                continue
            reconcile(engine, book, ticket)
        except (TransportError, APIError):
            # Preserve the persisted phase, never replay an ambiguous mutation.
            continue
        except (ValueError, KeyError, TypeError):
            saved = book.get(ticket['id'])
            if saved['state'] in ('confirmed', 'leverage_pending'):
                book.save(dict(saved, reason='账户、行情或订单条件变化，请重新预览'), 'rejected')
            # Once sending, GET reconciliation retains ownership of the outcome.
