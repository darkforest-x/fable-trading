"""Virtual App lots over a shared OKX hedge position, from exact owned fills.

Sources: OKX v5 order, fills-history and order-algo. Exchange avgPx/realizedPnl
belong to the combined position and cannot price an App lot. Persist each fill
before using it, including terminal receipts, so old fills survive API retention.
Never infer an App close from an external reduction or an order-ID prefix.
"""
import copy
from decimal import Decimal
from .policy import Rejected, dec
from .okx_execution import ExchangeRejected, stable_id


def lot_totals(fills, contract_value, side, legacy_exit_cutoff=None):
    """Average-cost accounting in exchange execution order (not HTTP arrival)."""
    quantity = cost = pnl = fees = entered = Decimal(0)
    last = 0
    outside = Decimal(0)
    direction = 1 if side == 'long' else -1
    for fill in sorted(fills, key=lambda f: (int(f['fillTime']), int(f['tradeId']), int(f['billId']))):
        size, price, fee = dec(fill['fillSz'])*contract_value, dec(fill['fillPx']), dec(fill['fee'])
        if size <= 0 or price <= 0 or fill['feeCcy'] != 'USDT':
            raise Rejected('App 成交数量、价格或费用尚未核对')
        if fill['side'] == ('buy' if side == 'long' else 'sell'):
            cost += size*price
            quantity += size
            entered += size
        else:
            if size > quantity:
                if legacy_exit_cutoff is None or int(fill['fillTime']) > legacy_exit_cutoff:
                    raise Rejected('App 平仓成交超出自有数量，等待核对')
                # Pre-upgrade closes could repeatedly use a stale combined
                # position. Allocate only the remaining App lot and its fee
                # share; preserve the excess as historical evidence, not PnL.
                outside += size-quantity
                fee *= quantity/size
                size = quantity
                if not size:
                    continue
            basis = cost/quantity
            pnl += (price-basis)*size*direction
            cost -= basis*size
            quantity -= size
        pnl += fee
        fees -= fee
        last = max(last, int(fill['fillTime']))
    return dict(remaining=quantity, entry=cost/quantity if quantity else Decimal(0),
                pnl=pnl, fees=fees, entered=entered, closed=last, legacy_external_exit=outside)


class OwnedLots:
    def _owned_order_fills(self, root, symbol, side, *, client_id=None, order_id=None, entry=False):
        cache_key = client_id or 'order:'+str(order_id)
        with self.read_transaction() as (_, state):
            cached = copy.deepcopy(state.get('owned_receipts', {}).get(root, {}).get(cache_key, {}))
        order = cached.get('order')
        if not order or order['state'] not in ('filled', 'canceled'):
            params = dict(instId=symbol, **({'clOrdId':client_id} if client_id else {'ordId':order_id}))
            rows = self.client.get('/api/v5/trade/order', **params)
            if len(rows) != 1:
                raise Rejected('等待 App 原始订单回执')
            order = rows[0]
        expected_side = ('buy' if side=='long' else 'sell') if entry else ('sell' if side=='long' else 'buy')
        if (order['instId'] != symbol or order['posSide'] != side or order['side'] != expected_side or
                order.get('tdMode','cross') != 'cross' or
                (client_id and order.get('clOrdId') != client_id) or
                (order_id and order['ordId'] != order_id)):
            raise Rejected('App 成交归属与原始订单不一致')
        fills = {f['billId']:f for f in cached.get('fills', [])}
        expected = dec(order.get('accFillSz') or '0')
        if sum((dec(f['fillSz']) for f in fills.values()), Decimal(0)) != expected:
            after = None
            for _ in range(100):
                params = dict(instType='SWAP', instId=symbol, ordId=order['ordId'], limit='100')
                if after:
                    params['after'] = after
                rows = self.client.get('/api/v5/trade/fills-history', **params)
                for row in rows:
                    if (row['ordId'] != order['ordId'] or row['instId'] != symbol or
                            row['posSide'] != side or row['side'] != order['side']):
                        raise Rejected('App 成交明细归属不一致')
                    old = fills.get(row['billId'])
                    if old and any(old[k] != row[k] for k in ('fillSz','fillPx','fee','feeCcy','fillTime','tradeId')):
                        raise Rejected('App 已保存成交明细发生变化')
                    fills[row['billId']] = row
                if len(rows) < 100 or sum((dec(f['fillSz']) for f in fills.values()), Decimal(0)) >= expected:
                    break
                cursor = rows[-1]['billId']
                if cursor == after:
                    raise Rejected('App 成交明细分页未推进')
                after = cursor
        # Save even partial evidence; an incomplete feed is retried, never guessed.
        with self.transaction() as (_, state):
            state.setdefault('owned_receipts', {}).setdefault(root, {})[cache_key] = dict(order=order, fills=list(fills.values()))
        if (sum((dec(f['fillSz']) for f in fills.values()), Decimal(0)) != expected or
                (expected and sum((dec(f['fee']) for f in fills.values()), Decimal(0)) != dec(order['fee']))):
            raise Rejected('App 成交明细尚未齐全，等待交易所核对')
        return list(fills.values())

    def _owned_view(self, intent, positions, instrument):
        """Return the owned projection only when sharing was observed/accepted."""
        root = intent.get('add_to') or intent['id']
        symbol, side = intent['request']['symbol'], intent['request']['side']
        raw = next((p for p in positions if p['instId']==symbol and p['posSide']==side
                    and p.get('mgnMode', 'cross')=='cross' and dec(p['pos'])>0), None)
        with self.read_transaction() as (db, state):
            owner = copy.deepcopy(state['intents'][root])
            pos = next((p for p in state['positions'] if p['id']==root), None)
            outstanding = sum((max(Decimal(0),dec(i.get('observed_fill',{}).get('accFillSz','0'))-dec(i.get('applied_size','0')))
                for i in state['intents'].values() if i['id']==root or i.get('add_to')==root),Decimal(0))
            expected = dec(pos['remaining'])+outstanding*dec(instrument['contract_value']) if pos else Decimal(0)
            mixed = bool(raw and pos and (dec(raw['pos'])*dec(instrument['contract_value'])>expected
                         or (not outstanding and abs(dec(raw['avgPx'])-dec(pos['entry']))>dec(instrument['tick']))))
            enabled = owner.get('owned_accounting') or intent.get('owned_accounting') or mixed
            if not enabled:
                return raw
            entries = [copy.deepcopy(i) for i in state['intents'].values() if i['id']==root or i.get('add_to')==root]
            commands = dict(state.get('close_commands', {}), **state.get('owned_exit_commands', {}))
        intent['owned_accounting'] = True
        with self.transaction() as (_, state):
            owner = state['intents'][root]
            if not owner.get('ownership_version'):
                owner.setdefault('legacy_exit_cutoff', int(self.clock()*1000))
            cutoff = owner.get('legacy_exit_cutoff')
        fills, algo_specs = {}, {}
        def collect(rows):
            for fill in rows:
                if fill['billId'] in fills and fills[fill['billId']] != fill:
                    raise Rejected('App 成交编号重复且内容不一致')
                fills[fill['billId']] = fill
        for entry in entries:
            command = self.outbox.inspect(entry['id'])
            if command['phase'] in ('prepared', 'rejected'):
                continue
            collect(self._owned_order_fills(root,symbol,side,client_id=entry['payload']['clOrdId'],entry=True))
            for spec in (entry['payload'].get('attachAlgoOrds', []) + entry.get('protection_specs', [])
                         + entry.get('protection_history', []) + entry.get('merging', {}).get('specs', [])):
                if not spec.get('disabled'):
                    algo_specs[spec['attachAlgoClOrdId']] = spec
        # Older emergency closes have a deterministic, exact root-scoped ID.
        commands.setdefault(stable_id(root,'missing-protection-close'), root)
        for key, target in commands.items():
            if target != root:
                continue
            try:
                command = self.outbox.inspect(key)
            except Rejected:
                continue
            if command['phase'] not in ('prepared','rejected'):
                collect(self._owned_order_fills(root,symbol,side,client_id=command['payload']['clOrdId']))
        for identity in algo_specs:
            try:
                attached = any(identity in {x['attachAlgoClOrdId'] for x in i['payload'].get('attachAlgoOrds', [])} for i in entries)
                if not attached:
                    try:
                        command = self.outbox.inspect(identity)
                    except Rejected:
                        continue
                    if command['phase'] in ('prepared','rejected'):
                        continue
                with self.read_transaction() as (_, state):
                    algo = state.get('owned_algo_receipts', {}).get(identity)
                if not algo:
                    algo = self._algo(identity, [])
                    if algo['state'] in ('effective','canceled','order_failed'):
                        with self.transaction() as (_, state):
                            state.setdefault('owned_algo_receipts', {})[identity] = algo
            except ExchangeRejected as exc:
                if exc.code == '51603':
                    # An attached protection is absent before its parent fully fills.
                    attached_pending = any(identity in {s['attachAlgoClOrdId'] for s in i['payload'].get('attachAlgoOrds', [])}
                        and i['phase'] in ('accepted','pending','partially_filled') for i in entries)
                    if attached_pending:
                        continue
                raise
            if algo.get('instId') != symbol or algo.get('posSide') != side or algo.get('side') != ('sell' if side=='long' else 'buy'):
                raise Rejected('App 保护单归属不一致')
            if algo['state'] == 'effective':
                ids = algo.get('ordIdList') or ([algo['ordId']] if algo.get('ordId') else [])
                if not ids:
                    raise Rejected('App 保护已触发，等待成交回执')
                for oid in ids:
                    collect(self._owned_order_fills(root,symbol,side,order_id=oid))
        totals = lot_totals(fills.values(), dec(instrument['contract_value']), side, cutoff)
        with self.transaction() as (db, state):
            state['intents'][root]['owned_accounting'] = True
            prior = state['intents'][root].get('owned_totals', {}).get('legacy_external_exit', '0')
            if totals['legacy_external_exit'] != dec(prior):
                self.event(db,'legacy_owned_exit_allocation',dict(id=root,external_quantity=str(totals['legacy_external_exit']),cutoff=cutoff))
            state['intents'][root]['owned_totals'] = {k:str(v) for k,v in totals.items()}
        if totals['remaining'] == 0:
            return None
        if raw is None or dec(raw['pos'])*dec(instrument['contract_value']) < totals['remaining']:
            raise Rejected('交易所剩余数量少于 App 自有成交，等待减仓归属核对')
        return dict(raw, pos=str(totals['remaining']/dec(instrument['contract_value'])),
                    avgPx=str(totals['entry']), realizedPnl=str(totals['pnl']))

    def _owned_history(self, intent):
        root = intent.get('add_to') or intent['id']
        with self.read_transaction() as (_, state):
            totals = state['intents'][root].get('owned_totals')
        if not totals or dec(totals['remaining']) != 0 or dec(totals['entered']) <= 0:
            raise Rejected('等待 App 自有仓位结算')
        return dict(realizedPnl=totals['pnl'], fee=str(-dec(totals['fees'])),
                    uTime=totals['closed'], posId='app-'+root)
