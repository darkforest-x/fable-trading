"""Explicit management of free-manual tickets through the existing locked worker.

Source: OKX V5 Trade (order/cancel-order/order-algo/amend-algos/cancel-algos),
https://www.okx.com/docs-v5/en/ (2026-10-24). Long/short closing sides only reduce.
A read-only preview binds account, original entry, position and protection.
Every exchange POST has a durable boundary; ambiguous writes are only GET-read.
"""
import json
import uuid
from pathlib import Path

from .client import APIError, TransportError
from .engine import one, TERMINAL
from .free_manual import Tickets, clean
from .manual_commands import Commands
from .manual_inventory import pages
from .risk import D, amount, quantize
from .store import identifier

REVISION = 'free-management-20261027-v2'


def confirmed_tickets(runtime):
    book = Tickets(runtime)
    with book.db() as db:
        rows = db.execute("SELECT state,value FROM tickets WHERE json_extract(value,'$.confirmed_ms') IS NOT NULL ORDER BY rowid DESC").fetchall()
    return [dict(json.loads(row[1]), state=row[0]) for row in rows]


def entry(engine, ticket):
    order = one(engine.client.get('/api/v5/trade/order', instId=ticket['symbol'], clOrdId=ticket['client_order_id']))
    body = ticket['order_body']
    if (any(order.get(k) != body[k] for k in ('instId', 'clOrdId', 'side', 'posSide', 'ordType', 'tdMode'))
            or D(order['sz']) != D(body['sz'])
            or 'px' in body and D(order.get('px') or '0') != D(body['px'])):
        raise ValueError('原委托归属不匹配，请核对账户')
    return order


def algo_client_id(ticket):
    if ticket.get('management_no_protection'):
        return None
    attached = ticket['order_body'].get('attachAlgoOrds', [])
    return ticket.get('management_algo_client_id') or (attached[0]['attachAlgoClOrdId'] if attached else None)


def protection(engine, ticket):
    identity = algo_client_id(ticket)
    if not identity:
        return None
    row = one(engine.client.get('/api/v5/trade/order-algo', algoClOrdId=identity))
    if (row.get('algoClOrdId') != identity or row.get('instId') != ticket['symbol']
            or row.get('posSide') != ticket['side'] or row.get('tdMode') != 'cross'
            or row.get('side') != ('sell' if ticket['side'] == 'long' else 'buy')):
        raise ValueError('保护单归属不匹配')
    return row


def read_plan(engine, ticket, action, stop=None, percentage='100', take_profit=None):
    latest = next((t for t in confirmed_tickets(engine.store.path.parent) if t['symbol'] == ticket['symbol']), None)
    if not latest or latest['id'] != ticket['id'] or ticket.get('management_closed_ms'):
        raise ValueError('该记录不是当前持仓对应的最新委托')
    account = engine.account()
    if account['uid'] != ticket['account_uid'] or account['uid'] != engine.store.meta('account_uid'):
        raise ValueError('执行账户已变化')
    if any(t['symbol'] == ticket['symbol'] and t['state'] not in TERMINAL for t in engine.store.trades()):
        raise ValueError('该合约包含策略持仓，请从原分笔管理')
    order = entry(engine, ticket)
    plan = dict(lot_id=ticket['id'], action=action, account_uid=account['uid'], symbol=ticket['symbol'],
                side=ticket['side'], entry_id=order['ordId'], client_order_id=ticket['client_order_id'])
    if action == 'cancel':
        if order['state'] not in ('live', 'partially_filled'):
            raise ValueError('原委托已结束，请刷新列表')
        return dict(plan, size=order['sz'], filled_size=order.get('accFillSz') or '0')
    if action not in ('close', 'stop', 'protection') or order['state'] not in ('filled', 'canceled'):
        raise ValueError('请先撤销未成交委托，再管理已成交持仓')
    if ticket['state'] not in ('filled', 'canceled'):
        raise ValueError('原委托或保护回执仍在核对，请稍后刷新')
    rows = [p for p in account['positions'] if p['instId'] == ticket['symbol'] and D(p['pos']) != 0]
    if (len(rows) != 1 or rows[0].get('posSide') != ticket['side'] or rows[0].get('mgnMode') != 'cross'
            or D(rows[0]['pos']) != D(order.get('accFillSz') or '0')-D(ticket.get('management_closed_size') or '0') or D(rows[0]['pos']) <= 0):
        raise ValueError('当前持仓与原委托数量不一致，请核对交易所持仓')
    position = rows[0]
    if position.get('avgPx') and order.get('avgPx') and D(position['avgPx']) != D(order['avgPx']):
        raise ValueError('当前持仓均价与原委托不一致，请核对归属')
    algo = protection(engine, ticket)
    if algo and (algo['state'] != 'live' or D(algo['sz']) != D(position['pos'])):
        raise ValueError('保护正在执行或数量已变化，请刷新后核对')
    ordinary = pages(engine.client, '/api/v5/trade/orders-pending', 'ordId', instId=ticket['symbol'])
    if any(r['instId'] == ticket['symbol'] for r in ordinary):
        raise ValueError('该合约仍有其他委托，请先处理委托')
    for kind in ('conditional', 'oco', 'trigger', 'move_order_stop'):
        for r in pages(engine.client, '/api/v5/trade/orders-algo-pending', 'algoId', ordType=kind, instId=ticket['symbol']):
            if r['instId'] == ticket['symbol'] and (not algo or r['algoId'] != algo['algoId']):
                raise ValueError('该合约存在其他保护单，请先核对')
    plan.update(size=position['pos'], position_size=position['pos'], position_id=position.get('posId'),
                algo_id=algo['algoId'] if algo else None, algo_client_id=algo_client_id(ticket),
                old_stop=(algo.get('slTriggerPx') or None) if algo else None,
                take_profit=(algo.get('tpTriggerPx') or None) if algo else None,
                old_take_profit=(algo.get('tpTriggerPx') or None) if algo else None,
                stop_type=(algo.get('slTriggerPxType') or 'last') if algo else 'last',
                tp_type=(algo.get('tpTriggerPxType') or 'last') if algo else 'last')
    if action in ('stop', 'protection', 'close'):
        instrument = one(engine.client.get('/api/v5/account/instruments', instType='SWAP', instId=ticket['symbol']))
        if instrument.get('instId') != ticket['symbol']:
            raise ValueError('合约规格暂不可用')
    if action == 'close':
        pct = D(percentage)
        if not pct.is_finite() or not 0 < pct <= 100:
            raise ValueError('平仓比例须大于 0 且不超过 100%')
        size = quantize(D(position['pos'])*pct/100, D(instrument['lotSz']))
        if size < D(instrument['minSz']) or size > D(instrument['maxMktSz']):
            raise ValueError('本次平仓数量不符合交易所最小数量或市价单上限')
        plan.update(size=amount(size), percentage=amount(pct), remaining_size=amount(D(position['pos'])-size),
                    closed_size_before=ticket.get('management_closed_size') or '0')
    if action in ('stop', 'protection'):
        ticker = one(engine.client.get('/api/v5/market/ticker', instId=ticket['symbol']))
        if instrument.get('instId') != ticket['symbol'] or ticker.get('instId') != ticket['symbol'] or not 0 <= engine.clock()-int(ticker['ts']) < 5000:
            raise ValueError('合约规格或报价暂不可用')
        quote = D(ticker['bidPx' if ticket['side'] == 'long' else 'askPx'])
        if action == 'stop' and stop is None:
            raise ValueError('请输入止损价格')
        desired_tp = plan['take_profit'] if action == 'stop' else take_profit
        for value, kind in ((stop, '止损'), (desired_tp, '止盈')):
            if value is None:
                continue
            px = D(value)
            if not px.is_finite() or px <= 0 or px % D(instrument['tickSz']):
                raise ValueError(kind+'必须大于零并符合最小报价单位')
            profitable = px > quote if ticket['side']=='long' else px < quote
            if (kind == '止损' and profitable) or (kind == '止盈' and not profitable) or px == quote:
                raise ValueError(kind+'已越过当前价格，请使用平仓操作')
        plan.update(stop=amount(D(stop)) if stop is not None else None,
                    take_profit=amount(D(desired_tp)) if desired_tp is not None else None)
    return plan


class Management(Commands):
    def __init__(self, runtime):
        self.path = Path(runtime)/'free-management.sqlite3'
        if self.path.is_symlink():
            raise ValueError('unsafe management path')
        with self.db() as db:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('CREATE TABLE IF NOT EXISTS commands(id TEXT PRIMARY KEY,lot TEXT,state TEXT,value TEXT)')
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_pending_lot ON commands(lot) WHERE state IN ('confirmed','processing')")
            db.commit()
        self.path.chmod(0o600)

    def preview(self, engine, ticket_id, action, stop=None, percentage='100', take_profit=None):
        ticket = Tickets(self.path.parent).get(ticket_id)
        if not ticket.get('confirmed_ms'):
            raise ValueError('只能管理已确认的原委托')
        plan = read_plan(engine, ticket, action, stop, percentage, take_profit)
        now = engine.clock()
        plan.update(id=uuid.uuid4().hex, created_ms=now, expires_ms=now+60000)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM commands WHERE lot=? AND state IN ('confirmed','processing')", (ticket_id,)).fetchone():
                raise ValueError('该委托已有待核对操作，请等待回执')
            db.execute("UPDATE commands SET state='superseded' WHERE lot=? AND state='preview'", (ticket_id,))
            db.execute('INSERT INTO commands VALUES(?,?,?,?)', (plan['id'], ticket_id, 'preview', json.dumps(plan)))
            db.commit()
        return dict(plan, state='preview')


def public(command):
    return dict(clean(command), account_scope='yolo_manual')


def post_once(engine, commands, command, phase, path, body):
    value = commands.save(dict(command, phase=phase, sent_ms=engine.clock(), reason='正在核对交易所回执'), 'processing')
    try:
        engine.client.post(path, body)
    except APIError as error:
        # OKX explicitly states 50004 is ambiguous, not a rejection.
        # https://www.okx.com/en-us/help/api-faq (2026-10-24).
        if error.code not in ('50004', 'malformed'):
            commands.save(dict(value, reason=f'交易所拒绝操作（{error.code}），请刷新核对'),
                          'processing' if phase in ('resize_protection', 'replacement_cancel') else 'rejected')
    return commands.get(command['id'])


def same_plan(current, original):
    keys = ('account_uid', 'symbol', 'side', 'entry_id', 'client_order_id', 'size', 'position_size', 'position_id', 'algo_id', 'algo_client_id', 'old_stop', 'old_take_profit', 'take_profit', 'stop_type', 'tp_type', 'stop', 'remaining_size')
    return all(current.get(k) == original.get(k) for k in keys)


def consume(engine, commands):
    pending = commands.pending()
    if not pending:
        return
    index = getattr(engine, '_free_manage_cursor', 0)
    engine._free_manage_cursor = index+1
    cmd = pending[index % len(pending)]
    book = Tickets(commands.path.parent)
    try:
        ticket = book.get(cmd['lot_id'])
        if engine.store.meta('account_uid') != cmd['account_uid'] or ticket['account_uid'] != cmd['account_uid']:
            raise ValueError('执行账户已变化')
        if cmd['state'] == 'confirmed':
            if not cmd.get('confirmed_ms') or not cmd['created_ms'] <= cmd['confirmed_ms'] <= engine.clock() < cmd['expires_ms']:
                raise ValueError('操作预览已过期，请重新核对')
            current = read_plan(engine, ticket, cmd['action'], cmd.get('stop'), cmd.get('percentage','100'), cmd.get('take_profit'))
            if not same_plan(current, cmd):
                raise ValueError('持仓或保护已经变化，请重新预览')
            if cmd['action'] == 'cancel':
                cmd = post_once(engine, commands, cmd, 'cancel', '/api/v5/trade/cancel-order', dict(instId=cmd['symbol'], ordId=cmd['entry_id']))
            elif cmd['action'] == 'close':
                body = dict(instId=cmd['symbol'], tdMode='cross', posSide=cmd['side'], side='sell' if cmd['side']=='long' else 'buy', ordType='market', sz=cmd['size'], clOrdId=identifier(cmd['id'], 'free-close'))
                cmd = post_once(engine, commands, cmd, 'close', '/api/v5/trade/order', body)
            elif cmd['action'] == 'protection':
                cmd = update_protection(engine, commands, cmd)
            elif cmd['algo_id']:
                cmd = post_once(engine, commands, cmd, 'stop', '/api/v5/trade/amend-algos', dict(instId=cmd['symbol'], algoId=cmd['algo_id'], reqId=identifier(cmd['id'], 'free-amend'), newSlTriggerPx=cmd['stop'], newSlOrdPx='-1', newSlTriggerPxType=cmd['stop_type'], cxlOnFail=False))
            else:
                body = dict(instId=cmd['symbol'], tdMode='cross', posSide=cmd['side'], side='sell' if cmd['side']=='long' else 'buy', ordType='conditional', sz=cmd['size'], algoClOrdId=identifier(cmd['id'], 'free-stop'), slTriggerPx=cmd['stop'], slOrdPx='-1', slTriggerPxType='last')
                cmd = post_once(engine, commands, cmd, 'new_stop', '/api/v5/trade/order-algo', body)
        if cmd['state'] != 'processing':
            return
        if cmd['phase'] in ('protection', 'replace_protection', 'remove_protection', 'replacement_created', 'replacement_cancel'):
            reconcile_protection(engine, commands, book, ticket, cmd)
            return
        if cmd['phase'] == 'cancel':
            row = entry(engine, ticket)
            if row['state'] in ('canceled', 'filled'):
                commands.save(dict(cmd, reason='原委托已结束'), 'done')
            return
        if cmd['phase'] in ('stop', 'new_stop'):
            identity = cmd['algo_client_id'] if cmd['phase']=='stop' else identifier(cmd['id'], 'free-stop')
            algo = one(engine.client.get('/api/v5/trade/order-algo', algoClOrdId=identity))
            valid = (algo.get('algoClOrdId') == identity and algo.get('instId') == cmd['symbol'] and algo.get('posSide') == cmd['side'] and algo.get('tdMode') == 'cross' and algo.get('side') == ('sell' if cmd['side']=='long' else 'buy') and D(algo.get('sz') or '0') == D(cmd['size']) and D(algo.get('slTriggerPx') or '0') == D(cmd['stop']) and algo.get('slOrdPx') == '-1' and D(algo.get('tpTriggerPx') or '0') == D(cmd['take_profit'] or '0'))
            valid = valid and algo.get('slTriggerPxType') == cmd['stop_type']
            if valid and algo['state'] in ('live', 'effective'):
                book.save(dict(ticket, stop_loss=cmd['stop'], management_algo_client_id=identity, management_no_protection=False), ticket['state'])
                commands.save(dict(cmd, reason='止损已核对'), 'done')
            return
        if cmd['phase'] == 'close':
            row = one(engine.client.get('/api/v5/trade/order', instId=cmd['symbol'], clOrdId=identifier(cmd['id'], 'free-close')))
            if (row.get('clOrdId') != identifier(cmd['id'], 'free-close') or row.get('instId') != cmd['symbol'] or row.get('posSide') != cmd['side'] or row.get('side') != ('sell' if cmd['side']=='long' else 'buy') or row.get('tdMode') != 'cross' or row.get('ordType') != 'market' or D(row['sz']) != D(cmd['size'])):
                return
            if row['state'] != 'filled' or D(row.get('accFillSz') or '0') != D(cmd['size']):
                return
            if D(cmd.get('remaining_size','0')) > 0:
                if not remaining_matches(engine, cmd):
                    return
                # Persist the accepted fill once, before the separate resize boundary.
                book.save(dict(ticket, management_closed_size=amount(D(cmd['closed_size_before'])+D(cmd['size']))), ticket['state'])
                if not cmd['algo_id']:
                    commands.save(dict(cmd, reason='部分平仓成交已核对'), 'done')
                    return
                algo = protection(engine, ticket)
                if algo['state']=='live' and algo['algoId']==cmd['algo_id'] and exits_match(algo, cmd, old=True):
                    if D(algo['sz']) == D(cmd['remaining_size']):
                        commands.save(dict(cmd, reason='部分平仓与剩余保护已核对'), 'done')
                    elif D(algo['sz']) == D(cmd['position_size']):
                        cmd = post_once(engine, commands, cmd, 'resize_protection', '/api/v5/trade/amend-algos',
                                        dict(instId=cmd['symbol'], algoId=cmd['algo_id'], newSz=cmd['remaining_size'],
                                             reqId=identifier(cmd['id'],'free-resize'), cxlOnFail=False))
                return
            if not cmd['algo_id']:
                book.save(dict(ticket, management_closed_ms=engine.clock()), ticket['state'])
                commands.save(dict(cmd, reason='平仓成交已核对'), 'done')
                return
            algo = protection(engine, ticket)
            if algo['state'] in ('canceled', 'effective', 'order_failed'):
                book.save(dict(ticket, management_closed_ms=engine.clock()), ticket['state'])
                commands.save(dict(cmd, reason='平仓与原保护单已核对'), 'done')
                return
            if algo['state'] == 'live' and algo['algoId'] == cmd['algo_id']:
                cmd = post_once(engine, commands, cmd, 'cancel_protection', '/api/v5/trade/cancel-algos', [dict(instId=cmd['symbol'], algoId=cmd['algo_id'])])
        if cmd['state'] == 'processing' and cmd['phase'] == 'cancel_protection':
            algo = protection(engine, ticket)
            if algo['state'] in ('canceled', 'effective', 'order_failed'):
                book.save(dict(ticket, management_closed_ms=engine.clock()), ticket['state'])
                commands.save(dict(cmd, reason='平仓与原保护单已核对'), 'done')
        if cmd['state']=='processing' and cmd['phase']=='resize_protection':
            algo = protection(engine, ticket)
            if (remaining_matches(engine, cmd) and algo['state']=='live' and algo['algoId']==cmd['algo_id']
                    and D(algo['sz'])==D(cmd['remaining_size']) and exits_match(algo, cmd, old=True)):
                commands.save(dict(cmd, reason='部分平仓与剩余保护已核对'), 'done')
    except (TransportError, APIError):
        return  # Never replay an exchange mutation, including after restart.
    except (ValueError, KeyError, TypeError) as error:
        if commands.get(cmd['id'])['state'] == 'confirmed':
            commands.save(dict(cmd, reason=str(error)), 'rejected')


def remaining_matches(engine, cmd):
    account = engine.account()
    rows = [p for p in account['positions'] if p['instId']==cmd['symbol'] and D(p['pos']) != 0]
    return (account['uid']==cmd['account_uid'] and len(rows)==1 and rows[0].get('posSide')==cmd['side']
            and rows[0].get('posId')==cmd['position_id'] and D(rows[0]['pos'])==D(cmd['remaining_size']))


def exits_match(algo, cmd, *, old=False):
    for prefix, key, basis in (('sl', 'old_stop' if old else 'stop', 'stop_type'),
                               ('tp', 'old_take_profit' if old else 'take_profit', 'tp_type')):
        value = cmd.get(key)
        if D(algo.get(prefix+'TriggerPx') or '0') != D(value or '0'):
            return False
        if value and (algo.get(prefix+'OrdPx')!='-1' or algo.get(prefix+'TriggerPxType','last')!=cmd[basis]):
            return False
    return True


def protection_body(cmd):
    body = dict(instId=cmd['symbol'],tdMode='cross',posSide=cmd['side'],side='sell' if cmd['side']=='long' else 'buy',
                sz=cmd['size'],ordType='oco' if cmd['stop'] and cmd['take_profit'] else 'conditional',
                algoClOrdId=identifier(cmd['id'],'free-protect'))
    for prefix,key,basis in (('sl','stop','stop_type'),('tp','take_profit','tp_type')):
        if cmd[key]:
            body.update({prefix+'TriggerPx':cmd[key],prefix+'OrdPx':'-1',prefix+'TriggerPxType':cmd[basis]})
    return body


def update_protection(engine, commands, cmd):
    desired = bool(cmd['stop'])+bool(cmd['take_profit'])
    original = bool(cmd['old_stop'])+bool(cmd['old_take_profit'])
    if cmd['algo_id'] and not desired:
        return post_once(engine, commands, cmd, 'remove_protection',
                         '/api/v5/trade/cancel-algos',[dict(instId=cmd['symbol'],algoId=cmd['algo_id'])])
    if cmd['algo_id'] and (original!=desired or bool(cmd['stop'])!=bool(cmd['old_stop'])):
        # Keep the original protection until replacement protection is verified.
        # Both are closing sides in long/short mode and cannot reverse exposure.
        return post_once(engine, commands, cmd, 'replacement_created',
                         '/api/v5/trade/order-algo',protection_body(cmd))
    if not cmd['algo_id']:
        if not desired:
            return commands.save(dict(cmd, reason='当前未设置止盈止损'), 'done')
        return post_once(engine, commands, cmd, 'replace_protection','/api/v5/trade/order-algo',protection_body(cmd))
    body=dict(instId=cmd['symbol'],algoId=cmd['algo_id'],reqId=identifier(cmd['id'],'free-protect'),cxlOnFail=False)
    for prefix,key,basis in (('Sl','stop','stop_type'),('Tp','take_profit','tp_type')):
        if cmd[key] or cmd['old_stop' if prefix=='Sl' else 'old_take_profit']:
            body.update({'new'+prefix+'TriggerPx':cmd[key] or '0','new'+prefix+'OrdPx':'-1' if cmd[key] else '0',
                         'new'+prefix+'TriggerPxType':cmd[basis]})
    return post_once(engine, commands, cmd, 'protection','/api/v5/trade/amend-algos',body)


def reconcile_protection(engine, commands, book, ticket, cmd):
    if cmd['phase']=='remove_protection':
        algo=protection(engine,ticket)
        if algo['state']!='canceled':
            return
        book.save(dict(ticket,stop_loss=None,take_profit=None,management_algo_client_id='',management_no_protection=True),ticket['state'])
        commands.save(dict(cmd,reason='止盈止损已取消'),'done')
        return
    identity=cmd['algo_client_id'] if cmd['phase']=='protection' else identifier(cmd['id'],'free-protect')
    algo=one(engine.client.get('/api/v5/trade/order-algo',algoClOrdId=identity))
    if (algo.get('algoClOrdId')==identity and algo.get('instId')==cmd['symbol'] and algo.get('posSide')==cmd['side']
            and algo.get('side')==('sell' if cmd['side']=='long' else 'buy') and algo.get('tdMode')=='cross'
            and D(algo['sz'])==D(cmd['size']) and algo['state'] in ('live','effective') and exits_match(algo,cmd)):
        if cmd['phase']=='replacement_created':
            old=protection(engine,ticket)
            if old['state']=='live':
                cmd=post_once(engine,commands,cmd,'replacement_cancel','/api/v5/trade/cancel-algos',
                              [dict(instId=cmd['symbol'],algoId=cmd['algo_id'])])
            elif old['state'] not in ('canceled','effective','order_failed'):
                return
        if cmd['phase']=='replacement_cancel' and protection(engine,ticket)['state'] not in ('canceled','effective','order_failed'):
            return
        book.save(dict(ticket,stop_loss=cmd['stop'],take_profit=cmd['take_profit'],management_algo_client_id=identity,
                       management_no_protection=False),ticket['state'])
        commands.save(dict(cmd,reason='止盈止损已核对'),'done')
