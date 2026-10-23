"""OKX hedge-mode execution with durable intent and exchange-owned protection.

This standalone ledger reuses decimal sizing and policy gates, never paper fills.
The owner submits each entry/close intent from the App. Background writes are
limited to that intent's protective stops/TPs and cancellation of its own algos.
Sources: OKX API v5 order, position history, attached TP/SL and amend-algos docs.
"""
import copy
import hashlib
import json
import threading
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo
from decimal import Decimal
from .book import Book, encoded
from .execution_journal import ExecutionJournal
from .okx_execution import ExchangeRejected, ExchangeUncertain, entry_payload, protective_prices, stable_id
from .policy import Policy, Rejected, dec, positive, ceil_step, floor_step
from .closing import close_fraction, close_quantity
from .managed_orders import ManagedOrders
from .external_records import observed_records, persist_observations


class LiveBook(ManagedOrders, Book):
    mode = 'okx_live'

    def __init__(self, path, client, clock=time.time):
        if client.environment != self.mode:
            raise ValueError('Live ledger requires an explicitly live client')
        super().__init__(path, clock, initial_equity='1')
        self.client = client
        self.outbox = ExecutionJournal(path, clock)
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = None
        with self.transaction() as (_, state):
            state['mode'] = self.mode
            state.setdefault('account_equity', '0')
            state.setdefault('available', '0')
            state.setdefault('private_at', 0)
            state.setdefault('private_error', '正在核对 OKX 实盘账户')
            state.setdefault('intents', {})
            state.setdefault('previews', {})
            state.setdefault('account_reference', None)
            state.setdefault('foreign', True)
            state['account_synced'] = False
        with self.transaction() as (_, state):
            initialized = state['policy'] is not None
        if not initialized:
            self.configure(Policy())

    def equity(self, state):
        return dec(state.get('account_equity', '0'))

    def day(self, state):
        if self.equity(state) > 0 and state.get('private_at') and 0<=self.clock()-state['private_at']<=15:
            daily = state['daily']
            key = datetime.fromtimestamp(self.clock(), ZoneInfo('Asia/Shanghai')).date().isoformat()
            if daily and daily['key'] == key and 'app_start_pnl' not in daily:
                # Upgrade retains today's managed settlements and entry fees.
                # For legacy overnight positions no midnight mark was stored:
                # begin their floating-PnL measurement at this observed cutover.
                start = datetime.fromisoformat(key).replace(tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()
                prior = sum((dec(p['pnl']) for p in state['closed'] if p['closed'] < start), Decimal(0))
                carry = [p for p in state['positions'] if p.get('opened', self.clock()) < start]
                baseline = prior + sum((self.position_pnl(state, p) for p in carry), Decimal(0))
                daily.update(app_start_pnl=str(baseline), pnl_scope='app_managed', scope_started_at=self.clock(),
                             overnight_baseline='cutover' if carry else 'no_open_carry')
                if daily['locked_reason'] == '当日净值亏损停手' and self.daily_pnl(state) > -dec(daily['start_equity'])*dec(self.policy(state).daily_loss_fraction):
                    daily['locked_reason'] = None
            super().day(state)

    @staticmethod
    def position_pnl(state, pos):
        mark = dec(state['quotes'].get(pos['symbol'], {}).get('last', pos['entry']))
        return dec(pos['pnl']) + (mark-dec(pos['entry']))*dec(pos['remaining'])*pos['direction']

    def managed_pnl(self, state):
        return (sum((dec(p['pnl']) for p in state['closed']), Decimal(0))
                + sum((self.position_pnl(state, p) for p in state['positions']), Decimal(0)))

    def begin_day(self, state):
        state['daily'].update(app_start_pnl=str(self.managed_pnl(state)), pnl_scope='app_managed', scope_started_at=self.clock())

    def daily_pnl(self, state):
        return self.managed_pnl(state) - dec(state['daily'].get('app_start_pnl', '0'))

    def entry_guard(self, state, exclude_pending=None, add_to=None):
        if not self.client.write_enabled:
            raise Rejected('实盘执行尚未启用')
        if state.get('private_error') or not 0 <= self.clock()-state.get('private_at', 0) <= 15:
            raise Rejected(state.get('private_error') or '账户状态已过期，暂停开仓')
        if any(p['protection'] != 'exchange-verified' for p in state['positions']):
            raise Rejected('持仓保护尚未核对完成，暂停开仓')
        for pending in state['pending']:
            intent = state['intents'].get(pending['id'], {})
            # A verified resting limit is a reservation, not an unknown command.
            # Parent/side duplication and shared collateral remain Book gates.
            if (intent.get('execution_version') != 2
                    or intent.get('phase') not in ('pending', 'partially_filled')
                    or intent.get('payload', {}).get('ordType') != 'limit'
                    or self.outbox.inspect(pending['id'])['phase'] != 'acknowledged'):
                raise Rejected('上一笔委托结果尚未核对完成，暂停开仓')
        if self.outbox.unresolved():
            raise Rejected('交易所请求结果尚未核对，暂停开仓')
        return super().entry_guard(state, exclude_pending, add_to)

    def preview_in(self, state, request, reviewed_plan=None):
        # An exchange position/lever setting belongs to a contract, not an App.
        # Keep external activity on other contracts entirely outside policy;
        # do not merge into or change leverage on an externally occupied one.
        if any(row['symbol'] == request['symbol'] for row in state.get('external_activity', {}).get('current', [])):
            raise Rejected('该合约有外部交易，仅记录；请在 OKX 处理同币仓位，其他合约可正常开仓')
        result = super().preview_in(state, request, reviewed_plan)
        if dec(result['margin']) + dec(result['costs']) > dec(state['available']):
            raise Rejected('OKX 可用保证金不足')
        # Compile before accepting an intent, including minimum split sizes.
        entry_payload('preflight', request, result, state['instruments'][request['symbol']], self.policy(state), 'long_short_mode')
        return result

    def preview(self, request):
        with self.lock:
            self.reconcile()
            self.check_fee(request['symbol'])
            with self.transaction() as (_, state):
                sized = self.preview_in(state, request)
                identity = uuid.uuid4().hex
                state['previews'] = {k:v for k,v in state['previews'].items() if v['expires'] > self.clock()}
                if len(state['previews']) >= 20:
                    state['previews'].pop(next(iter(state['previews'])))
                state['previews'][identity] = dict(request=request, plan=sized, expires=self.clock()+45,
                    revision=state['policy_revision'])
                return dict(plan=sized, policy_revision=state['policy_revision'], mode=self.mode, preview_id=identity)

    def check_fee(self, symbol):
        with self.transaction() as (_, state):
            group=state['instruments'].get(symbol,{}).get('fee_group')
        if not group:
            raise Rejected('合约手续费分组缺失，暂停开仓')
        rows = self.client.get('/api/v5/account/trade-fee', instType='SWAP', instFamily=symbol.removesuffix('-SWAP'))
        fees=rows[0].get('feeGroup',[]) if len(rows)==1 else []
        matching=[row for row in fees if row.get('groupId')==group]
        if len(matching)!=1:
            raise Rejected('未读到合约对应手续费，暂停开仓')
        if max(Decimal(0), -dec(matching[0].get('taker'))) > dec(Policy().fee_rate):
            raise Rejected('交易所实际手续费高于当前成本预留，暂停开仓')

    def enter(self, key, request, revision, preview_id=None, confirmed_live=False):
        if not confirmed_live:
            raise Rejected('请在 App 明确确认本次实盘订单')
        if not isinstance(key, str) or not 16 <= len(key) <= 80 or not key.isalnum():
            raise Rejected('请求编号无效')
        fingerprint = hashlib.sha256(encoded([request, revision, preview_id]).encode()).hexdigest()
        with self.lock:
            self.reconcile()
            with self.transaction() as (db, state):
                intent = state['intents'].get(key)
                if intent:
                    if intent['fingerprint'] != fingerprint:
                        raise Rejected('同一请求编号不能用于不同指令')
                    return self.receipt(intent)
                saved = state['previews'].get(preview_id)
                if not saved or saved.get('consumed_by') or saved['expires'] < self.clock() or saved['request'] != request or saved['revision'] != revision or revision != state['policy_revision']:
                    raise Rejected('实盘预览已过期或参数变化，请重新确认')
                sized = self.preview_in(state, request, saved['plan'])
                # A changed quote never increases the user's reviewed exposure.
                if any(dec(sized[field]) > dec(saved['plan'][field]) for field in ('quantity','notional','risk','margin')):
                    raise Rejected('行情变化使本单超出已确认计划，请重新预览')
                payload = entry_payload(key, request, sized, state['instruments'][request['symbol']], self.policy(state), 'long_short_mode')
                if request['kind'] == 'limit':
                    payload['ordType'] = 'limit'
                intent = dict(id=key, fingerprint=fingerprint, request=request, plan=sized, payload=payload,
                    policy=self.policy(state).document(), policy_revision=revision, created=self.clock(),
                    phase='accepted', error=None, entry_day=state['daily']['key'])
                if request['kind']=='limit' or sized.get('add_to'):
                    intent.update(execution_version=2, add_to=sized.get('add_to'))
                state['intents'][key] = intent
                saved['consumed_by'] = key
                state['pending'].append(dict(id=key, **request, plan=sized, created=self.clock()))
                state['daily']['entries'] += 1
                self.outbox.prepare(key, '/api/v5/trade/order', payload, db)
                lever_id = stable_id(key, 'leverage')
                self.outbox.prepare(lever_id, '/api/v5/account/set-leverage',
                    dict(instId=request['symbol'], mgnMode='cross', lever=str(request['leverage'])), db)
                self.event(db, 'live_intent_accepted', dict(id=key, plan=sized, request=request))
            # Committed reservation precedes either external write.
            try:
                self.outbox.dispatch(lever_id, self.client)
                lever = self.client.get('/api/v5/account/leverage-info', instId=request['symbol'], mgnMode='cross')
                if not lever or any(dec(row['lever']) != request['leverage'] for row in lever):
                    raise Rejected('交易所杠杆回读不一致，未发送开仓委托')
                with self.transaction() as (_,state):
                    self.quote(state,request['symbol'])
                    if not 0<=self.clock()-state['private_at']<=15 or self.clock()>saved['expires']:
                        raise Rejected('账户核验或确认计划已过期，未发送开仓委托')
                self.outbox.dispatch(key, self.client)
            except ExchangeRejected as exc:
                self._intent_error(key, str(exc))
            except (ExchangeUncertain, Rejected) as exc:
                self._intent_error(key, str(exc))
            self.reconcile()
            with self.transaction() as (_, state):
                return self.receipt(state['intents'][key])

    @staticmethod
    def receipt(intent):
        return dict(id=intent['id'], status=intent['phase'], mode='okx_live',
                    plan=intent['plan'], message=intent.get('error'))

    def _intent_error(self, identity, error):
        with self.transaction() as (_, state):
            state['intents'][identity]['error'] = error

    def update_market(self, instruments, quotes):
        # No simulated fills, balance changes or R-stage execution here.
        with self.transaction() as (_, state):
            # Retain metadata for managed exits, but never open a delisted contract.
            owned = {p['symbol'] for p in state['positions']} | {p['symbol'] for p in state['pending']}
            retained = {k:dict(v, tradable=False) for k,v in state['instruments'].items() if k in owned and k not in instruments}
            state['instruments'] = dict(retained, **instruments)
            accepted = 0
            for symbol, quote in quotes.items():
                if symbol not in instruments or not 0 <= self.clock()-float(quote['time']) <= 15:
                    continue
                if not all(positive(quote[k]) > 0 for k in ('last','bid','ask')) or dec(quote['bid']) > dec(quote['ask']):
                    continue
                if quote['time'] < state['quotes'].get(symbol, {}).get('time',0):
                    continue
                state['instruments'][symbol] = instruments[symbol]
                state['quotes'][symbol] = quote
                accepted += 1
            state['heartbeat'] = self.clock()
            state['quote_error'] = None if accepted else '未收到新鲜有效行情'

    def _fetch(self):
        config = self.client.get('/api/v5/account/config')[0]
        if config.get('acctLv') != '2' or config.get('posMode') != 'long_short_mode':
            raise Rejected('此版本实盘只支持合约账户与双向持仓模式；不会自动改账户设置')
        permissions = set(config.get('perm','').split(','))
        if 'trade' not in permissions or 'withdraw' in permissions:
            raise Rejected('API 必须具有读取和交易权限，且不能具有提币权限')
        balance = self.client.get('/api/v5/account/balance', ccy='USDT')[0]
        usdt = next((row for row in balance['details'] if row['ccy']=='USDT'), None)
        if not usdt or dec(usdt['eq']) <= 0:
            raise Rejected('OKX 交易账户没有可用 USDT 净值')
        positions = self.client.get('/api/v5/account/positions')
        orders = self.client.get('/api/v5/trade/orders-pending')
        algos = []
        for kind in ('conditional','oco','trigger','move_order_stop'):
            rows = self.client.get('/api/v5/trade/orders-algo-pending', ordType=kind)
            if len(rows) >= 100:
                raise Rejected('账户委托超出单次核对范围，暂停开仓')
            algos.extend(rows)
        if len(orders) >= 100 or len(positions) >= 100:
            raise Rejected('账户记录超出单次核对范围，暂停开仓')
        return config, usdt, positions, orders, algos

    def reconcile(self):
        with self.lock:
            with self.transaction() as (_,state):
                state['account_synced']=False
            try:
                config, usdt, raw_positions, raw_orders, algos = self._fetch()
                self._resolve_commands()
                reference = hashlib.sha256(config['uid'].encode()).hexdigest()[:16]
                with self.transaction() as (_, state):
                    if state['account_reference'] not in (None, reference):
                        raise Rejected('账户身份变化，禁止沿用原账户风控记录')
                    state['account_reference'] = reference
                    state['account_equity'] = str(dec(usdt['eq']))
                    state['cash'] = str(dec(usdt['cashBal']))
                    state['available'] = str(max(Decimal(0), dec(usdt['availEq'])))
                    state['private_at'] = self.clock()
                    state['private_error'] = None
                    state['account_synced'] = True
                    intents = copy.deepcopy(state['intents'])
                    self.day(state)
                errors=[]
                failed_roots=set()
                # Read cumulative fills before reconciling their aggregate position.
                for intent in intents.values():
                    if intent.get('execution_version') == 2 and intent['phase'] not in ('cancelled','rejected','closed'):
                        try:
                            self._managed_order(intent)
                        except (Rejected, ExchangeUncertain) as exc:
                            errors.append(str(exc))
                            failed_roots.add(intent.get('add_to') or intent['id'])
                            with self.read_transaction() as (_,state):
                                merging=copy.deepcopy(state['intents'][intent['id']].get('merging'))
                                instrument=state['instruments'][intent['request']['symbol']]
                            if merging and self.clock()-merging['created']>20:
                                # If the replacement stop is still unverified, stop
                                # further fills and close only the owned aggregate.
                                try:
                                    stop=self._algo(merging['specs'][0]['attachAlgoClOrdId'],[])
                                    covered=stop.get('state')=='live' and dec(stop.get('sz','0'))>=dec(merging['specs'][0]['sz'])
                                except (Rejected,ExchangeUncertain):
                                    covered=False
                                if not covered:
                                    try:
                                        self._cancel_entry(intent, reason='protection_unverified')
                                        self._emergency_close(dict(merging['position'],id=merging['generation']),instrument)
                                    except (Rejected,ExchangeUncertain):
                                        pass  # Durable requests are reconciled next pass.
                with self.transaction() as (db, state):
                    for intent in state['intents'].values():
                        if intent['phase'] in ('cancelled','rejected') and not dec(intent.get('applied_size','0')):
                            self.refund_entry(db,state,intent)
                    intents=copy.deepcopy(state['intents'])
                if any(x.get('execution_version') == 2 and x['phase'] not in ('closed','cancelled','rejected') for x in intents.values()):
                    raw_positions=self.client.get('/api/v5/account/positions')
                    algos=[]
                    for kind in ('conditional','oco','trigger','move_order_stop'):
                        algos.extend(self.client.get('/api/v5/trade/orders-algo-pending',ordType=kind))
                for intent in intents.values():
                    if intent.get('add_to') or intent['id'] in failed_roots or intent['phase'] in ('cancelled','rejected','closed'):
                        continue
                    try:
                        if intent.get('aggregate'):
                            self._aggregate_position(intent,raw_positions,algos)
                        elif not intent.get('execution_version'):
                            self._reconcile_intent(intent,raw_positions,algos)
                    except (Rejected, ExchangeUncertain) as exc:
                        errors.append(str(exc))
                        failed_roots.add(intent['id'])
                if failed_roots:
                    with self.transaction() as (_,state):
                        for pos in state['positions']:
                            if pos['id'] in failed_roots:
                                pos['protection']='awaiting-exchange'
                with self.transaction() as (db, state):
                    commands = [(endpoint, json.loads(body)) for endpoint, body in db.execute('SELECT endpoint,payload FROM exchange_commands')]
                    records = observed_records(state, raw_positions, raw_orders, algos, commands)
                    persist_observations(db, state, records, self.clock(), self.event)
                    self.day(state)
                    if errors:
                        state['private_error']=errors[0]
                if errors:
                    return False
            except Exception as exc:
                message = str(exc) if isinstance(exc, (Rejected, ExchangeUncertain)) else '交易所回执格式或连接异常，暂停开仓'
                with self.transaction() as (_, state):
                    state['private_error'] = message
                    for pos in state['positions']:
                        pos['protection'] = 'awaiting-exchange'
                return False
            return True

    def _resolve_commands(self):
        for record in self.outbox.unresolved():
            path, payload = record['endpoint'], record['payload']
            try:
                if path == '/api/v5/trade/order':
                    rows = self.client.get(path, instId=payload['instId'], clOrdId=payload['clOrdId'])
                    if len(rows) == 1:
                        self.outbox.resolve(record['id'],'acknowledged',[dict(sCode='0',ordId=rows[0]['ordId'])])
                elif path == '/api/v5/trade/order-algo':
                    rows=self.client.get(path,algoClOrdId=payload['algoClOrdId'])
                    if len(rows)==1:
                        self.outbox.resolve(record['id'],'acknowledged',[dict(sCode='0',algoId=rows[0]['algoId'])])
                elif path == '/api/v5/trade/cancel-order':
                    rows=self.client.get('/api/v5/trade/order',**payload)
                    if len(rows)==1 and rows[0]['state'] in ('filled','canceled'):
                        self.outbox.resolve(record['id'],'acknowledged',[dict(sCode='0',ordId=rows[0]['ordId'])])
                elif path == '/api/v5/account/set-leverage':
                    rows = self.client.get('/api/v5/account/leverage-info',instId=payload['instId'],mgnMode='cross')
                    if rows and all(dec(row['lever'])==dec(payload['lever']) for row in rows):
                        self.outbox.resolve(record['id'],'acknowledged',rows)
                elif path == '/api/v5/trade/amend-algos':
                    rows = self.client.get('/api/v5/trade/order-algo',algoId=payload['algoId'])
                    fields = {'newSlTriggerPx':'slTriggerPx','newTpTriggerPx':'tpTriggerPx'}
                    if len(rows)==1 and all(dec(rows[0].get(old,'0'))==dec(payload[new]) for new,old in fields.items() if new in payload):
                        self.outbox.resolve(record['id'],'acknowledged',[dict(sCode='0',algoId=payload['algoId'])])
                elif path == '/api/v5/trade/cancel-algos':
                    rows = [self.client.get('/api/v5/trade/order-algo',algoId=item['algoId'])[0] for item in payload]
                    if all(row['state'] in ('canceled','effective','order_failed') for row in rows):
                        self.outbox.resolve(record['id'],'acknowledged',[dict(sCode='0',algoId=row['algoId']) for row in rows])
            except ExchangeRejected:
                # Not-found is not proof of no order. Keep the unresolved record.
                continue

    def _reconcile_intent(self, intent, positions, algos):
        key, request = intent['id'], intent['request']
        record = self.outbox.inspect(key)
        if record['phase'] == 'prepared':
            # An unsent entry is not resumed in the background after a crash.
            with self.transaction() as (db, state):
                db.execute("UPDATE exchange_commands SET phase='rejected',receipt=? WHERE id=? AND phase='prepared'",
                           (encoded({'code':'unsent'}),key))
                state['intents'][key]['phase'] = 'rejected'
                self.refund_entry(db,state,state['intents'][key])
                state['pending'] = [x for x in state['pending'] if x['id'] != key]
            return
        if record['phase'] == 'rejected':
            with self.transaction() as (db, state):
                self.refund_entry(db,state,state['intents'][key])
                state['intents'][key]['phase'] = 'rejected'
                self.refund_entry(db,state,state['intents'][key])
                state['pending'] = [x for x in state['pending'] if x['id'] != key]
            return
        rows = self.client.get('/api/v5/trade/order', instId=request['symbol'], clOrdId=intent['payload']['clOrdId'])
        if len(rows) != 1:
            raise Rejected('等待原订单编号的完整交易所回执')
        order = rows[0]
        if any(order.get(field)!=intent['payload'][field] for field in ('instId','clOrdId','side','posSide')):
            raise Rejected('交易所订单回执与原始意图不匹配')
        if record['phase'] == 'attempted':
            self.outbox.resolve(key, 'acknowledged', [dict(ordId=order['ordId'], sCode='0')])
        filled = dec(order['accFillSz'])
        if order['state'] == 'canceled' and filled == 0:
            with self.transaction() as (db, state):
                self.refund_entry(db,state,state['intents'][key])
                state['intents'][key]['phase'] = 'cancelled'
                state['intents'][key]['error'] = '未能全部成交，交易所已取消；本次不消耗开仓次数'
                state['pending'] = [x for x in state['pending'] if x['id'] != key]
            return
        if order['state'] != 'filled' or filled != dec(intent['payload']['sz']):
            raise Rejected('全量成交回执尚未确认，暂停开仓')
        if order['feeCcy'] != 'USDT':
            raise Rejected('成交费用币种不匹配，暂停开仓')
        raw = next((x for x in positions if x['instId']==request['symbol'] and x['posSide']==request['side'] and dec(x['pos'])>0), None)
        with self.transaction() as (db, state):
            pos = next((x for x in state['positions'] if x['id']==key), None)
            if pos is None:
                instrument = state['instruments'][request['symbol']]
                quantity = filled * dec(instrument['contract_value'])
                pos = dict(id=key, symbol=request['symbol'], side=request['side'], direction=1 if request['side']=='long' else -1,
                    quantity=str(quantity), remaining=str(quantity), entry=order['avgPx'], original_stop=intent['plan']['stop'],
                    stop=intent['plan']['stop'], risk=intent['plan']['risk'], margin=intent['plan']['margin'], leverage=request['leverage'],
                    margin_mode='cross', entry_fee=str(-dec(order['fee'])), pnl=order['fee'], breakeven=False, stages=[],
                    opened=int(order['cTime'])/1000, policy=intent['policy'], policy_revision=intent['policy_revision'],
                    protection='awaiting-exchange', ord_id=order['ordId'], pos_id=raw['posId'] if raw else None)
                state['positions'].append(pos)
                self.event(db,'live_fill',dict(id=key,entry_fee=pos['entry_fee']))
            state['pending'] = [x for x in state['pending'] if x['id'] != key]
            state['intents'][key]['phase'] = 'filled'
            pos = copy.deepcopy(pos)
        if raw is None:
            self._settle(intent, pos, algos)
            return
        with self.transaction() as (_, state):
            instrument = state['instruments'][pos['symbol']]
            target = next(x for x in state['positions'] if x['id']==key)
            remaining = dec(raw['pos']) * dec(instrument['contract_value'])
            if remaining > dec(pos['quantity']) or raw.get('mgnMode') != 'cross' or dec(raw['avgPx']) != dec(pos['entry']):
                raise Rejected('检测到 App 外加仓或保证金模式变化，暂停开仓')
            target['remaining'] = str(remaining)
            target['pos_id'] = raw['posId']
            target['pnl'] = str(dec(raw['realizedPnl']))
            pos = copy.deepcopy(target)
        policy=Policy.parse(pos['policy'])
        actual_risk=dec(pos['quantity'])*((dec(pos['entry'])-dec(pos['original_stop']))*pos['direction']+
            dec(pos['entry'])*(dec(policy.fee_rate)+dec(policy.slippage_rate)))+dec(pos['entry_fee'])
        invalid_risk=((dec(pos['entry'])-dec(pos['original_stop']))*pos['direction']<=0 or actual_risk>dec(intent['plan']['budget']))
        liquidation=dec(raw.get('liqPx') or '0')
        invalid_liquidation=liquidation>0 and (dec(pos['stop'])-liquidation)*pos['direction']<=0
        if invalid_risk or invalid_liquidation:
            self._emergency_close(pos,instrument)
            raise Rejected('实际成交风险或强平距离不符合计划，已提交减仓保护')
        self._protect(intent, pos, algos, instrument)

    def _algo(self, identity, current):
        found = next((x for x in current if x.get('algoClOrdId') == identity), None)
        if found:
            return found
        rows = self.client.get('/api/v5/trade/order-algo', algoClOrdId=identity)
        if len(rows) != 1:
            raise Rejected('交易所保护单尚未确认')
        return rows[0]

    def _amend(self, identity, algo, fields):
        body = dict(instId=algo['instId'], algoId=algo['algoId'], reqId=stable_id(identity,'amend'), cxlOnFail=False, **fields)
        self.outbox.prepare(identity,'/api/v5/trade/amend-algos',body)
        record = self.outbox.inspect(identity)
        if record['phase'] == 'attempted':
            row = self._algo(algo['algoClOrdId'], [])
            mapping = {'newSlTriggerPx':'slTriggerPx','newTpTriggerPx':'tpTriggerPx'}
            if all(dec(row.get(mapping[k], '0')) == dec(v) for k,v in fields.items() if k in mapping):
                self.outbox.resolve(identity,'acknowledged',[{'sCode':'0','algoId':row['algoId']}])
            else:
                raise ExchangeUncertain('保护单修改结果尚未确认')
        else:
            self.outbox.dispatch(identity,self.client)
        # Ack means accepted, not that the exchange has applied it.
        verified = self._algo(algo['algoClOrdId'], [])
        for key, value in fields.items():
            field = {'newSlTriggerPx':'slTriggerPx','newTpTriggerPx':'tpTriggerPx'}.get(key)
            if field and dec(verified.get(field,'0')) != dec(value):
                raise Rejected('保护单修改等待交易所回读确认')
        return verified

    def _protect(self, intent, pos, algos, instrument):
        policy = Policy.parse(pos['policy'])
        prices = protective_prices(pos['entry'],pos['quantity'],pos['risk'],pos['entry_fee'],policy,pos['side'],instrument['tick'])
        try:
            rows = [None if x.get('disabled') else self._algo(x['attachAlgoClOrdId'],algos) for x in intent['payload']['attachAlgoOrds']]
        except (Rejected, ExchangeUncertain):
            if self.clock()-pos['opened'] > 20:
                self._emergency_close(pos,instrument)
            raise
        stop = rows[0]
        expected_side = 'sell' if pos['side']=='long' else 'buy'
        if any(x.get('instId') != pos['symbol'] or x.get('posSide') != pos['side'] or x.get('side') != expected_side for x in rows if x):
            raise Rejected('保护单与持仓方向不匹配，暂停开仓')
        if stop.get('state') != 'live' or stop.get('slOrdPx') != '-1' or stop.get('slTriggerPxType') != 'last':
            if self.clock()-pos['opened'] > 20:
                self._emergency_close(pos,instrument)
            raise Rejected('交易所止损保护待核对，请查看 OKX 持仓')
        if dec(stop.get('sz') or '0') < dec(pos['remaining'])/dec(instrument['contract_value']) and stop.get('closeFraction') != '1':
            self._emergency_close(pos,instrument)
            raise Rejected('止损保护数量不足，已提交减仓保护')
        current_stop = dec(stop['slTriggerPx'])
        if (current_stop-dec(pos['stop']))*pos['direction'] < 0:
            raise Rejected('交易所止损被放宽，暂停开仓')
        stages = []
        for index, algo in enumerate(rows[1:]):
            if algo is None:
                stages.append(index)
                continue
            if algo['state'] == 'effective':
                # Triggering alone is not a fill. Confirm the corresponding order.
                order_ids = algo.get('ordIdList') or ([algo['ordId']] if algo.get('ordId') else [])
                if not order_ids:
                    raise Rejected('止盈已触发，等待实际成交核对')
                filled = sum((dec(self.client.get('/api/v5/trade/order',instId=pos['symbol'],ordId=oid)[0]['accFillSz']) for oid in order_ids),Decimal(0))
                if filled < dec(intent['payload']['attachAlgoOrds'][index+1]['sz']):
                    raise Rejected('止盈尚未全部成交，暂停开仓')
                stages.append(index)
            elif algo['state'] == 'live':
                if algo.get('tpOrdPx') != '-1' or dec(algo['sz']) != dec(intent['payload']['attachAlgoOrds'][index+1]['sz']):
                    raise Rejected('分批止盈数量或委托类型不匹配')
                if dec(algo['tpTriggerPx']) != dec(prices['targets'][index]):
                    self._amend(stable_id(intent.get('protection_generation',pos['id']),f'target-{index}'),algo,
                                dict(newTpTriggerPx=prices['targets'][index],newTpOrdPx='-1'))
            else:
                raise Rejected('分批止盈保护待核对，请查看 OKX 持仓')
        with self.transaction() as (_, state):
            quote = self.quote(state, pos['symbol'])
        mark, quantity = dec(quote['last']), dec(pos['quantity'])
        net = (mark-dec(pos['entry']))*quantity*pos['direction']-dec(pos['entry_fee'])-mark*quantity*dec(policy.fee_rate)
        current_r = net/dec(pos['risk'])
        if not pos['breakeven'] and current_r >= dec(policy.breakeven_r):
            target = dec(prices['breakeven'])
            if (mark-target)*pos['direction'] <= 0:
                raise Rejected('保本价已被行情穿过，请查看 OKX 持仓')
            if (target-current_stop)*pos['direction'] > 0:
                stop = self._amend(stable_id(intent.get('protection_generation',pos['id']),'breakeven'),stop,dict(newSlTriggerPx=str(target),newSlOrdPx='-1'))
                current_stop = dec(stop['slTriggerPx'])
        with self.transaction() as (_, state):
            target = next(x for x in state['positions'] if x['id']==pos['id'])
            target.update(stop=str(current_stop), current_r=str(current_r), stages=stages,
                breakeven=(current_stop-dec(prices['breakeven']))*pos['direction']>=0,
                protection='exchange-verified')

    def _emergency_close(self,pos,instrument):
        identity=stable_id(pos['id'],'missing-protection-close')
        try:
            self.outbox.inspect(identity)
        except Rejected:
            self.outbox.prepare(identity,'/api/v5/trade/order',dict(instId=pos['symbol'],tdMode='cross',
                posSide=pos['side'],side='sell' if pos['side']=='long' else 'buy',ordType='market',
                sz=str(dec(pos['remaining'])/dec(instrument['contract_value'])),clOrdId=stable_id(identity,'close')))
        self.outbox.dispatch(identity,self.client)

    def _settle(self, intent, pos, algos):
        rows = self.client.get('/api/v5/account/positions-history', instType='SWAP', instId=pos['symbol'], limit='100')
        matching = [row for row in rows if row.get('posSide')==pos['side'] and int(row['uTime'])/1000>=intent['created']
            and (not pos.get('pos_id') or row['posId']==pos['pos_id']) and row.get('type') in ('2','3','6')
            and dec(row.get('closeTotalPos','0'))==dec(intent['payload']['sz'])]
        if len(matching) != 1:
            raise Rejected('仓位已归零，等待完整净盈亏结算，暂停新开仓')
        history = matching[0]
        # Never allow an old owned exit to affect a subsequent position.
        for attached in intent['payload']['attachAlgoOrds']:
            algo = next((row for row in algos if row.get('algoClOrdId')==attached['attachAlgoClOrdId']),None)
            if algo:
                identity = stable_id(pos['id'], 'cleanup-'+algo['algoClOrdId'])
                self.outbox.prepare(identity,'/api/v5/trade/cancel-algos',[dict(instId=pos['symbol'],algoId=algo['algoId'])])
                self.outbox.dispatch(identity,self.client)
                raise Rejected('已平仓，正在核对剩余保护单撤销结果')
        pnl = dec(history['realizedPnl'])
        with self.transaction() as (db,state):
            actual = next(x for x in state['positions'] if x['id']==pos['id'])
            actual.update(remaining='0', pnl=str(pnl), closed=int(history['uTime'])/1000, close_reason='exchange', protection='closed', total_fee=str(-dec(history['fee'])))
            state['closed'].append(copy.deepcopy(actual))
            state['positions'].remove(actual)
            state['intents'][pos['id']]['phase']='closed'
            for child in state['intents'].values():
                if child.get('add_to')==pos['id']:
                    child['phase']='closed'
            self.settle_discipline(state, pnl, actual['closed'])
            self.event(db,'live_closed',dict(id=pos['id'],pnl=str(pnl),fee=str(-dec(history['fee']))))
            self.day(state)

    def close(self, key, identity, fraction='1'):
        fraction = close_fraction(fraction)
        with self.lock:
            with self.read_transaction() as (_, state):
                if key in state.get('close_abandoned', []):
                    raise Rejected('这笔未送达的平仓请求已结束，请重新选择平仓比例')
            self.reconcile()
            try:
                prior=self.outbox.inspect(key)
            except Rejected:
                prior=None
            if prior:
                if prior['endpoint']!='/api/v5/trade/order' or prior['payload'].get('clOrdId')!=stable_id(key,'close'):
                    raise Rejected('请求编号已用于其他交易指令')
                with self.transaction() as (_,state):
                    if state.get('close_commands',{}).get(key)!=identity:
                        raise Rejected('同一平仓编号不能用于不同持仓')
                    if dec(state.get('close_fractions',{}).get(key,'1')) != fraction:
                        raise Rejected('同一平仓编号不能修改平仓比例')
                self.outbox.dispatch(key,self.client)
                return dict(id=identity,status='close_submitted',mode=self.mode)
            # A second tap must not create another market close while the first
            # close's outcome remains unknown, even with a new mobile request ID.
            with self.transaction() as (_, state):
                closing = [command for command,position in state.get('close_commands',{}).items() if position==identity]
            for command in closing:
                prior_close = self.outbox.inspect(command)
                if prior_close['phase'] == 'rejected':
                    continue
                rows = self.client.get('/api/v5/trade/order', instId=prior_close['payload']['instId'],clOrdId=prior_close['payload']['clOrdId'])
                if len(rows)!=1 or rows[0].get('state') not in ('filled','canceled'):
                    raise Rejected('上一笔平仓尚未完成，请等待交易所核对')
            with self.transaction() as (db, state):
                pos = next((x for x in state['positions'] if x['id']==identity),None)
                if not pos:
                    raise Rejected('持仓不存在或已平仓')
                if not state.get('account_synced') or not 0 <= self.clock()-state.get('private_at',0) <= 15:
                    raise Rejected('账户状态已过期，不能核定平仓数量')
                instrument=state['instruments'][pos['symbol']]
                quantity=close_quantity(pos['remaining'],instrument,fraction)
                size=quantity/dec(instrument['contract_value'])
                payload=dict(instId=pos['symbol'],tdMode='cross',posSide=pos['side'],side='sell' if pos['side']=='long' else 'buy',
                    ordType='market',sz=str(size),clOrdId=stable_id(key,'close'))
                state.setdefault('close_commands',{})[key]=identity
                state.setdefault('close_fractions',{})[key]=str(fraction)
                self.outbox.prepare(key,'/api/v5/trade/order',payload,db)
            # Long/short mode opposite side+posSide is a close-only operation.
            self.outbox.dispatch(key,self.client)
            self.reconcile()
            return dict(id=identity,status='close_submitted',mode=self.mode)

    def close_status(self, key):
        """Read the original close receipt; never reconcile or dispatch on GET."""
        with self.read_transaction() as (_, state):
            position = state.get('close_commands', {}).get(key)
            abandoned = key in state.get('close_abandoned', [])
        if not position:
            return dict(id=key, status='not_received' if abandoned else 'not_found')
        command = self.outbox.inspect(key)
        result = dict(id=key, position_id=position, status='checking')
        if command['phase'] == 'rejected':
            return dict(result, status='rejected')
        if command['phase'] == 'prepared':
            return dict(result, status='ready')
        payload = command['payload']
        try:
            rows = self.client.get('/api/v5/trade/order', instId=payload['instId'], clOrdId=payload['clOrdId'])
        except (ExchangeRejected, ExchangeUncertain):
            return result
        if len(rows) == 1:
            phase = rows[0].get('state')
            if phase in ('filled', 'canceled'):
                return dict(result, status='filled' if phase == 'filled' else 'cancelled',
                            partially_filled=dec(rows[0].get('accFillSz') or '0') > 0)
        return result

    def abandon_unreceived_close(self, key):
        """Fence a missing mobile request while holding the same close lock.

        A delayed POST either owns a durable outbox or is rejected by this
        tombstone. No accepted exchange instruction is cancelled here.
        """
        with self.lock:
            with self.transaction() as (_, state):
                if key not in state.get('close_commands', {}):
                    abandoned = state.setdefault('close_abandoned', [])
                    if key not in abandoned:
                        abandoned.append(key)
        return self.close_status(key)

    def cancel(self, key, identity):
        with self.lock:
            with self.read_transaction() as (_,state):
                intent=copy.deepcopy(state['intents'].get(identity))
            if not intent or intent['request']['kind']!='limit' or intent.get('execution_version')!=2:
                raise Rejected('可撤销挂单不存在')
            self._cancel_entry(intent, reason='manual')
            self.reconcile()
            with self.read_transaction() as (_,state):
                result=state['intents'][identity]
                return dict(id=identity,status=result['phase'],mode=self.mode)


    def tighten(self, key, identity, stop):
        raise Rejected('此版本由服务端按已确认规则移动保本止损')

    def entry_stop(self, key, identity):
        """User-triggered, idempotent entry-price stop; never change TP or size.

        The action identity belongs to the immutable position, so retries with
        new phone request IDs cannot submit a second amendment. Uncertain
        amendments are resolved by the existing read-back reconciliation.
        """
        with self.lock:
            self.reconcile()
            with self.read_transaction() as (_, state):
                pos = next((x for x in state['positions'] if x['id'] == identity), None)
                if not pos:
                    raise Rejected('持仓不存在或已平仓')
                if not self.client.write_enabled or state.get('private_error') or not state.get('account_synced') or not 0 <= self.clock() - state['private_at'] <= 15:
                    raise Rejected('账户尚未完成核对，请稍后再试')
                if pos['protection'] != 'exchange-verified':
                    raise Rejected('保护单尚未核对完成')
                tick = dec(state['instruments'][pos['symbol']]['tick'])
                value = (ceil_step if pos['direction'] == 1 else floor_step)(dec(pos['entry']), tick)
                if (dec(pos['stop']) - value) * pos['direction'] >= 0:
                    return dict(id=identity, stop=pos['stop'], status='already_protected')
                mark = dec(self.quote(state, pos['symbol'])['last'])
                if (mark - value) * pos['direction'] <= 0:
                    raise Rejected('现价尚未越过开仓价，暂不能移动止损')
                owner=state['intents'][identity]
                attached = owner.get('protection_specs',owner['payload']['attachAlgoOrds'])[0]['attachAlgoClOrdId']
                generation=owner.get('protection_generation',identity)
            algo = self._algo(attached, [])
            if (dec(algo['slTriggerPx']) - value) * pos['direction'] >= 0:
                return dict(id=identity, stop=algo['slTriggerPx'], status='already_protected')
            verified = self._amend(stable_id(generation, 'user-entry-stop'), algo,
                dict(newSlTriggerPx=str(value), newSlOrdPx='-1'))
            with self.transaction() as (db, state):
                target = next(x for x in state['positions'] if x['id'] == identity)
                target['stop'] = verified['slTriggerPx']
                self.event(db, 'entry_stop_verified', dict(id=identity, stop=target['stop']))
            return dict(id=identity, stop=verified['slTriggerPx'], status='verified')

    def statistics(self, days=30):
        from .statistics import summarize
        from .statistics_basis import confirmed_amount
        if type(days) is not int or days not in (0,7,30):
            raise Rejected('统计区间无效')
        with self.read_transaction() as (db,state):
            result=summarize(state,self.equity(state),[],self.clock(),days)
            result['mode']=self.mode
            result['scope']='app_managed_trades'
            result['account_updated_at']=state['private_at']
            # Only a separately confirmed fixed basis can supply the denominator.
            # Current equity/cash and daily risk baselines must never substitute.
            net = self.managed_pnl(state)
            basis = confirmed_amount(state)
            result['account'].update(
                initial_equity=str(basis) if basis is not None else None,
                net_pnl=str(net),
                return_fraction=str(net/basis) if basis is not None else None,
                return_method='app_managed_fixed_capital' if basis is not None else None)
            result['account']['unrealized_pnl'] = str(sum((self.position_pnl(state, p)-dec(p['pnl']) for p in state['positions']), Decimal(0)))
            result['market_fresh']=not state['private_error'] and 0<=self.clock()-state['private_at']<=15
            start=self.clock()-days*86400 if days else 0
            # Fees are complete-lifecycle fees at settlement; no double-counted entry.
            fees=[json.loads(body)['fee'] for timestamp,body in db.execute("SELECT time,body FROM journal WHERE kind='live_closed'") if timestamp>=start]
            result['fees']=str(sum((dec(fee) for fee in fees),Decimal(0)))
            return result

    def snapshot(self):
        # The worker persists daily gates. Display reads must not contend for
        # its write lock or create a new ledger revision on every phone poll.
        with self.read_transaction() as (_, state):
            result=self.render_snapshot(state)
        for field in ('intents','previews','account_reference','close_commands','close_fractions','close_abandoned','statistics_return_basis'):
            result.pop(field,None)
        return result

    def run(self):
        while not self.stop_event.is_set():
            self.reconcile()
            self.stop_event.wait(5)

    def start(self):
        self.thread=threading.Thread(target=self.run,name='discipline-live-reconcile',daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=15)
