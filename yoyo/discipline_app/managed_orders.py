"""Recoverable limit fills and scale-ins, using durable protection generations.

Each new fill is merged into the remaining position. A replacement stop is
verified before prior protection is retired. Order state comes only from OKX
receipts; cancellation acknowledgements never prove zero execution.
Source: OKX v5 place order/algo, cancel order and position history contracts.
"""
import copy
from decimal import Decimal
from .policy import Policy, Rejected, dec, positive, floor_step
from .okx_execution import ExchangeRejected, ExchangeUncertain, protective_prices, stable_id


class ManagedOrders:
    def _cancel_entry(self, intent):
        identity = stable_id(intent['id'], 'cancel-entry')
        payload = dict(instId=intent['request']['symbol'], clOrdId=intent['payload']['clOrdId'])
        self.outbox.prepare(identity, '/api/v5/trade/cancel-order', payload)
        record = self.outbox.inspect(identity)
        if record['phase'] == 'attempted':
            rows = self.client.get('/api/v5/trade/order', **payload)
            if len(rows) != 1 or rows[0]['state'] not in ('filled', 'canceled'):
                raise ExchangeUncertain('撤单已提交，等待最终成交核对')
            self.outbox.resolve(identity, 'acknowledged', [dict(sCode='0', ordId=rows[0]['ordId'])])
        else:
            self.outbox.dispatch(identity, self.client)

    def _managed_order(self, intent):
        key = intent['id']
        record = self.outbox.inspect(key)
        if record['phase'] in ('prepared', 'rejected'):
            # Never start an unsent entry from a background recovery.
            with self.transaction() as (db, state):
                if record['phase'] == 'prepared':
                    db.execute("UPDATE exchange_commands SET phase='rejected',receipt=? WHERE id=?", ('{"code":"unsent"}', key))
                current = state['intents'][key]
                current['phase'] = 'rejected'
                self.refund_entry(db, state, current)
                state['pending'] = [x for x in state['pending'] if x['id'] != key]
            return
        rows = self.client.get('/api/v5/trade/order', instId=intent['request']['symbol'], clOrdId=intent['payload']['clOrdId'])
        if len(rows) != 1:
            raise Rejected('等待原订单编号的交易所回执')
        order = rows[0]
        if any(order.get(f) != intent['payload'][f] for f in ('instId', 'clOrdId', 'side', 'posSide')):
            raise Rejected('订单回执与原指令不匹配')
        if record['phase'] == 'attempted':
            self.outbox.resolve(key, 'acknowledged', [dict(sCode='0', ordId=order['ordId'])])
        filled = dec(order['accFillSz'])
        if filled < dec(intent.get('applied_size', '0')) or filled > dec(intent['payload']['sz']):
            raise Rejected('累计成交数量异常，等待核对')
        terminal = order['state'] in ('filled', 'canceled')
        if filled > 0 and order.get('feeCcy') != 'USDT':
            raise Rejected('成交费用币种不匹配，暂停开仓')
        if filled > dec(intent.get('applied_size', '0')) or intent.get('merging'):
            self._merge_fill(intent, order)
        with self.transaction() as (db, state):
            current = state['intents'][key]
            if terminal:
                state['pending'] = [x for x in state['pending'] if x['id'] != key]
                current['phase'] = 'filled' if filled else 'cancelled'
                current['error'] = None if filled else '订单未成交，已取消；本次不消耗开仓次数'
                if not filled:
                    self.refund_entry(db, state, current)
            else:
                current['phase'] = 'partially_filled' if filled else 'pending'
                current['error'] = None
                for pending in state['pending']:
                    if pending['id'] == key:
                        pending['filled_quantity'] = str(filled * dec(state['instruments'][order['instId']]['contract_value']))
                        ratio=(dec(current['payload']['sz'])-filled)/dec(current['payload']['sz'])
                        pending['plan']={**current['plan'],**{field:str(dec(current['plan'][field])*ratio) for field in ('quantity','notional','margin','risk','costs')}}
            # A parent that disappeared cannot silently become a new trade.
            parent = current.get('add_to')
            cancel = not terminal and parent and not any(x['id'] == parent for x in state['positions'])
            if not terminal:
                try:
                    q = self.quote(state, order['instId'])
                    cancel = cancel or (dec(q['last']) - dec(current['plan']['stop'])) * (1 if order['posSide'] == 'long' else -1) <= 0
                except Rejected:
                    cancel = True
        if cancel:
            self._cancel_entry(intent)

    def _merge_fill(self, intent, order):
        from .scaling import merged_position
        key = intent['id']
        # A position may already be closed by the time the receipt is read.
        # Persist execution evidence independently of any current position.
        with self.transaction() as (_, state):
            state['intents'][key]['observed_fill'] = {field: order[field] for field in
                ('ordId', 'accFillSz', 'avgPx', 'fee', 'feeCcy', 'state', 'cTime')}
        with self.read_transaction() as (_, state):
            current = copy.deepcopy(state['intents'][key])
            instrument = state['instruments'][order['instId']]
            root = current.get('add_to') or key
            parent = next((copy.deepcopy(p) for p in state['positions'] if p['id'] == root), None)
        merging = current.get('merging')
        if not merging:
            raw_rows = self.client.get('/api/v5/account/positions')
            raw = next((p for p in raw_rows if p['instId'] == order['instId'] and p['posSide'] == order['posSide'] and dec(p['pos']) > 0), None)
            if raw is None:
                self._recover_flat_fill(current, order, parent, instrument)
                return
            if raw.get('mgnMode') != 'cross':
                raise Rejected('成交后的持仓正在核对，暂停新开仓')
            total, previous = dec(order['accFillSz']), dec(current.get('applied_size', '0'))
            delta = total - previous
            quantity = delta * dec(instrument['contract_value'])
            price = (total * dec(order['avgPx']) - previous * dec(current.get('applied_avg', '0'))) / delta
            fee = -dec(order['fee']) - dec(current.get('applied_fee', '0'))
            policy = Policy.parse(current['policy'])
            actual = dec(raw['pos']) * dec(instrument['contract_value'])
            if not parent:
                if current.get('add_to') or previous:
                    raise Rejected('原持仓已变化，加仓成交等待人工核对')
                if actual != quantity:
                    raise Rejected('实际仓位与本次成交数量不一致')
                risk = quantity * ((price - dec(current['plan']['stop'])) * (1 if order['posSide']=='long' else -1) + price * (dec(policy.fee_rate) + dec(policy.slippage_rate))) + fee
                pos = dict(id=root, symbol=order['instId'], side=order['posSide'], direction=1 if order['posSide']=='long' else -1,
                    quantity=str(quantity), remaining=str(quantity), entry=str(price), stop=current['plan']['stop'], original_stop=current['plan']['stop'],
                    risk=str(risk), margin=str(quantity*price/current['request']['leverage']), leverage=current['request']['leverage'], margin_mode='cross',
                    entry_fee=str(fee), pnl=order['fee'], breakeven=False, stages=[], opened=int(order['cTime'])/1000,
                    policy=current['policy'], policy_revision=current['policy_revision'], ord_id=order['ordId'], pos_id=raw['posId'], protection='awaiting-exchange')
            else:
                # Reductions while a limit waits must not be added back to the lot.
                base_remaining = actual - quantity
                if not 0 < base_remaining <= dec(parent['remaining']) or raw['posId'] != parent.get('pos_id'):
                    raise Rejected('加仓期间原持仓数量变化尚未核对')
                parent['remaining'] = str(base_remaining)
                pos = dict(parent, **merged_position(parent, quantity, price, fee, policy))
                pos['pnl'] = raw['realizedPnl']
            if abs(dec(raw['avgPx'])-dec(pos['entry'])) > dec(instrument['tick']):
                raise Rejected('加仓均价与交易所回执不一致')
            pos['entry'] = raw['avgPx']
            pos['pos_id'] = raw['posId']
            # Reserve total cash risk, including the actual entry fee, at every fill.
            pos['risk'] = str(actual * ((dec(pos['entry'])-dec(pos['stop']))*pos['direction'] + dec(pos['entry'])*(dec(policy.fee_rate)+dec(policy.slippage_rate))) + dec(pos['entry_fee']))
            budget = min(dec(current['plan'].get('merged_budget', current['plan']['budget'])), dec(state['account_equity'])*dec(policy.risk_fraction))
            if dec(pos['risk']) <= 0 or dec(pos['risk']) > budget:
                self._emergency_close(pos, instrument)
                raise Rejected('合并后的实际风险超出计划，已提交减仓保护')
            liquidation = dec(raw.get('liqPx') or '0')
            if liquidation and (dec(pos['stop'])-liquidation)*pos['direction'] <= 0:
                self._emergency_close(pos,instrument)
                raise Rejected('合并止损超出强平边界，已提交减仓保护')
            generation = stable_id(key, 'fill-'+str(total.normalize()))
            prices = protective_prices(pos['entry'],pos['quantity'],pos['risk'],pos['entry_fee'],policy,pos['side'],instrument['tick'])
            first = floor_step(actual*dec(policy.take_profits[0][1]),dec(instrument['step']))
            specs = [dict(attachAlgoClOrdId=stable_id(generation,'stop'),sz=str(actual/dec(instrument['contract_value'])+dec(current['payload']['sz'])-total),slTriggerPx=pos['stop'],slOrdPx='-1',slTriggerPxType='last')]
            for i, amount in enumerate((first, actual-first)):
                specs.append(dict(attachAlgoClOrdId=stable_id(generation,'tp'+str(i)),sz=str(amount/dec(instrument['contract_value'])),tpTriggerPx=prices['targets'][i],tpOrdPx='-1',tpTriggerPxType='last',disabled=amount<dec(instrument['minimum'])))
            merging = dict(position=pos, specs=specs, generation=generation, created=self.clock(), filled=str(total), avg=order['avgPx'], fee=str(-dec(order['fee'])), delta=str(delta))
            with self.transaction() as (_, state):
                state['intents'][key]['merging'] = merging
                if not any(p['id']==root for p in state['positions']):
                    state['positions'].append(copy.deepcopy(pos))
                else:
                    next(p for p in state['positions'] if p['id']==root)['protection']='awaiting-exchange'
        pos, specs = merging['position'], merging['specs']
        current_positions=self.client.get('/api/v5/account/positions')
        if not any(p['instId']==pos['symbol'] and p['posSide']==pos['side'] and dec(p['pos'])>0 for p in current_positions):
            if order['state'] not in ('filled','canceled'):
                self._cancel_entry(current)
            self._commit_merge(key,root,merging)
            return
        # Place and read back the new stop first. A timeout never resends it.
        for spec in specs:
            if spec.get('disabled'):
                continue
            identity = spec['attachAlgoClOrdId']
            payload = {k:v for k,v in spec.items() if k not in ('attachAlgoClOrdId','disabled')}
            payload.update(instId=pos['symbol'], tdMode='cross',posSide=pos['side'],side='sell' if pos['side']=='long' else 'buy',ordType='conditional',algoClOrdId=identity)
            self.outbox.prepare(identity, '/api/v5/trade/order-algo',payload)
            record=self.outbox.inspect(identity)
            if record['phase']=='attempted':
                row=self._algo(identity,[])
                self.outbox.resolve(identity,'acknowledged',[dict(sCode='0',algoId=row['algoId'])])
            else:
                self.outbox.dispatch(identity,self.client)
            row=self._algo(identity,[])
            fields=('instId','posSide','side','sz') + (('slTriggerPx','slOrdPx','slTriggerPxType') if 'slTriggerPx' in spec else ('tpTriggerPx','tpOrdPx','tpTriggerPxType'))
            if row['state']!='live' or any(str(row.get(f))!=str(payload[f]) and (f not in ('sz','slTriggerPx','tpTriggerPx') or dec(row.get(f,'0'))!=dec(payload[f])) for f in fields):
                raise Rejected('新的整仓保护尚未核对，保留原保护')
        with self.read_transaction() as (_, state):
            root_intent=state['intents'][root]
            retiring=list(root_intent.get('protection_specs', root_intent['payload']['attachAlgoOrds']))
            retiring+=current['payload']['attachAlgoOrds']
        new_ids={s['attachAlgoClOrdId'] for s in specs}
        for spec in retiring:
            identity=spec['attachAlgoClOrdId']
            if identity in new_ids or spec.get('disabled'):
                continue
            try:
                row=self._algo(identity,[])
            except ExchangeRejected as exc:
                # Attached orders do not exist before the parent fully fills.
                if exc.code=='51603' and order['state']!='filled':
                    continue
                raise
            if row['state']=='live':
                cancel_id=stable_id(merging['generation'],'retire-'+identity)
                self.outbox.prepare(cancel_id,'/api/v5/trade/cancel-algos',[dict(instId=pos['symbol'],algoId=row['algoId'])])
                self.outbox.dispatch(cancel_id,self.client)
                if self._algo(identity,[])['state']=='live':
                    raise Rejected('新保护已生效，等待旧保护撤销核对')
        self._commit_merge(key, root, merging)

    def _recover_flat_fill(self, current, order, parent, instrument):
        """Recover an executed lot already closed before local fill discovery.

        Never infer a zero fill from a missing current position. Close history
        must match the owned lifecycle and cumulative entered contract count.
        """
        if order['state'] not in ('filled', 'canceled'):
            self._cancel_entry(current)
            raise Rejected('仓位已归零，等待剩余开仓委托撤销核对')
        if current.get('add_to') and parent is None:
            raise Rejected('原持仓已结束，加仓成交需核对原仓位生命周期')
        key = current['id']
        root = current.get('add_to') or key
        total, previous = dec(order['accFillSz']), dec(current.get('applied_size', '0'))
        delta = total - previous
        with self.read_transaction() as (_, state):
            root_intent = copy.deepcopy(state['intents'][root])
        prior = dec(root_intent.get('total_filled_contracts',
            root_intent['payload']['sz'] if root != key and not root_intent.get('execution_version') else '0'))
        entered = prior + delta
        rows = self.client.get('/api/v5/account/positions-history', instType='SWAP', instId=order['instId'], limit='100')
        matching = [row for row in rows if row.get('posSide') == order['posSide']
            and int(row['uTime']) / 1000 >= root_intent['created'] and row.get('type') in ('2', '3', '6')
            and dec(row.get('closeTotalPos', '0')) == entered
            and (not parent or not parent.get('pos_id') or row['posId'] == parent['pos_id'])]
        if len(matching) != 1:
            raise Rejected('成交仓位已归零，等待完整净盈亏结算，暂停新开仓')
        history = matching[0]
        quantity = delta * dec(instrument['contract_value'])
        price = (total * dec(order['avgPx']) - previous * dec(current.get('applied_avg', '0'))) / delta
        fee = -dec(order['fee']) - dec(current.get('applied_fee', '0'))
        if parent:
            pos = copy.deepcopy(parent)
            base = dec(parent['remaining'])
            pos.update(quantity=str(base + quantity),
                entry=str((base * dec(parent['entry']) + quantity * price) / (base + quantity)),
                entry_fee=str(dec(parent['entry_fee']) * base / dec(parent['quantity']) + fee))
        else:
            pos = dict(id=root, symbol=order['instId'], side=order['posSide'],
                direction=1 if order['posSide'] == 'long' else -1,
                quantity=str(quantity), entry=str(price), stop=current['plan']['stop'],
                original_stop=current['plan']['stop'], risk=current['plan']['risk'],
                margin=current['plan']['margin'], leverage=current['request']['leverage'],
                margin_mode='cross', entry_fee=str(-dec(order['fee'])), pnl=order['fee'],
                breakeven=False, stages=[], opened=int(order['cTime']) / 1000,
                policy=current['policy'], policy_revision=current['policy_revision'], ord_id=order['ordId'])
        if history.get('openAvgPx'):
            pos['entry'] = history['openAvgPx']
        policy = Policy.parse(current['policy'])
        pos['margin'] = str(dec(pos['quantity']) * dec(pos['entry']) / pos['leverage'])
        cash_risk = dec(pos['quantity']) * ((dec(pos['entry']) - dec(pos['stop'])) * pos['direction']
            + dec(pos['entry']) * (dec(policy.fee_rate) + dec(policy.slippage_rate))) + dec(pos['entry_fee'])
        # A non-positive risk basis cannot produce a meaningful net-R statistic.
        # Settlement still uses the complete exchange PnL, never a fabricated R.
        pos['risk'] = str(max(Decimal(0), cash_risk))
        pos.update(remaining='0', pos_id=history['posId'], protection='awaiting-exchange')
        # Keep every owned protection ID until _settle has verified cleanup.
        specs = {s['attachAlgoClOrdId']: s for s in
            root_intent['payload']['attachAlgoOrds'] + root_intent.get('protection_specs', []) + current['payload']['attachAlgoOrds']}
        with self.transaction() as (db, state):
            live = state['intents'][key]
            if dec(live.get('applied_size', '0')) >= total:
                return
            live.update(applied_size=str(total), applied_avg=order['avgPx'], applied_fee=str(-dec(order['fee'])))
            state['pending'] = [p for p in state['pending'] if p['id'] != key]
            state['intents'][root].update(aggregate=True, total_filled_contracts=str(entered), protection_specs=list(specs.values()))
            target = next((p for p in state['positions'] if p['id'] == root), None)
            if target is None:
                state['positions'].append(pos)
            else:
                target.update(pos)
            self.event(db, 'live_flat_fill_recovered', dict(id=root, order=key, filled=str(total)))

    def _commit_merge(self, key, root, merging):
        pos,specs=merging['position'],merging['specs']
        with self.transaction() as (db, state):
            current=state['intents'][key]
            if not current.get('merging'):
                return
            root_intent=state['intents'][root]
            root_intent['aggregate']=True
            root_intent['protection_specs']=specs
            root_intent['protection_generation']=merging['generation']
            root_intent['total_filled_contracts']=str(dec(root_intent.get('total_filled_contracts', root_intent['payload']['sz'] if root!=key and not root_intent.get('execution_version') else '0'))+dec(merging['delta']))
            root_intent['plan']['budget']=str(max(dec(root_intent['plan']['budget']),dec(current['plan'].get('merged_budget',current['plan']['budget']))))
            current.update(applied_size=merging['filled'],applied_avg=merging['avg'],applied_fee=merging['fee'])
            current.pop('merging',None)
            target=next(p for p in state['positions'] if p['id']==root)
            target.update(pos, protection='awaiting-exchange')
            self.event(db,'live_position_rebased',dict(id=root,order=key,quantity=pos['quantity'],entry=pos['entry'],stop=pos['stop'],risk=pos['risk']))

    def _aggregate_position(self, intent, raw_positions, algos):
        with self.read_transaction() as (_,state):
            pos=next((copy.deepcopy(x) for x in state['positions'] if x['id']==intent['id']),None)
            instrument=state['instruments'][intent['request']['symbol']]
        if not pos:
            return
        raw=next((p for p in raw_positions if p['instId']==pos['symbol'] and p['posSide']==pos['side'] and dec(p['pos'])>0),None)
        protected=copy.deepcopy(intent)
        protected['payload']['attachAlgoOrds']=intent['protection_specs']
        protected['payload']['sz']=intent['total_filled_contracts']
        if raw is None:
            with self.read_transaction() as (_,state):
                pending=[state['intents'][p['id']] for p in state['pending'] if p['symbol']==pos['symbol']]
            if pending:
                for pending_intent in pending:
                    self._cancel_entry(pending_intent)
                raise Rejected('仓位已归零，等待剩余开仓委托撤销')
            self._settle(protected,pos,algos)
            return
        remaining=dec(raw['pos'])*dec(instrument['contract_value'])
        if remaining>dec(pos['remaining']) or abs(dec(raw['avgPx'])-dec(pos['entry']))>dec(instrument['tick']) or raw.get('mgnMode')!='cross':
            raise Rejected('仓位与已核对成交不一致，暂停开仓')
        with self.transaction() as (_,state):
            target=next(x for x in state['positions'] if x['id']==pos['id'])
            target.update(remaining=str(remaining),pnl=raw['realizedPnl'])
            pos=copy.deepcopy(target)
        self._protect(protected,pos,algos,instrument)
