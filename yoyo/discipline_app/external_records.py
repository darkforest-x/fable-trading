"""Read-only observations of exchange activity outside this App's intent ledger.

Ownership comes from persisted exact client IDs, never an ID prefix. These
records are deliberately separate from positions/pending/closed used by policy.
Disappearance from a snapshot is not evidence of a fill, cancel or settlement.
Source: OKX v5 positions and pending ordinary/algo order response fields.
"""
import hashlib
import json
from decimal import Decimal, InvalidOperation


def number(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def observed_records(state, positions, orders, algos, commands):
    order_ids, algo_ids = set(), set()
    for intent in state['intents'].values():
        payload = intent['payload']
        order_ids.add(payload['clOrdId'])
        algo_ids.update(a['attachAlgoClOrdId'] for a in payload.get('attachAlgoOrds', [])
                        + intent.get('protection_specs', []) + intent.get('merging', {}).get('specs', []))
    for endpoint, payload in commands:
        if not isinstance(payload, dict):
            continue
        if endpoint == '/api/v5/trade/order' and payload.get('clOrdId'):
            order_ids.add(payload['clOrdId'])
        if endpoint == '/api/v5/trade/order-algo' and payload.get('algoClOrdId'):
            algo_ids.add(payload['algoClOrdId'])
    keys = {(p['symbol'], p['side'], 'cross') for p in state['positions']}
    keys.update((i['request']['symbol'], i['request']['side'], 'cross')
                for i in state['intents'].values() if i['phase'] not in ('cancelled', 'rejected', 'closed'))
    result = []
    for kind, rows in [('position', positions), ('order', orders), ('algo', algos)]:
        for row in rows:
            symbol, side = row.get('instId', ''), row.get('posSide', '')
            mixed = False
            if kind == 'position':
                size = number(row.get('pos'))
                if size is None or size == 0:
                    continue
                if (symbol, side, row.get('mgnMode', 'cross')) in keys:
                    managed = next((p for p in state['positions'] if p['symbol'] == symbol and p['side'] == side), None)
                    spec = state['instruments'].get(symbol, {})
                    expected = number(managed['remaining']) / number(spec['contract_value']) if managed and spec.get('contract_value') else None
                    if expected is None or size <= expected:
                        continue
                    mixed = True
            elif row.get('clOrdId') in order_ids or row.get('algoClOrdId') in algo_ids:
                continue
            identity = row.get('posId' if kind == 'position' else 'ordId' if kind == 'order' else 'algoId')
            identity = identity or '|'.join(str(row.get(k, '')) for k in ('instId', 'posSide', 'mgnMode', 'clOrdId', 'algoClOrdId', 'cTime'))
            key = hashlib.sha256(f'{kind}:{identity}'.encode()).hexdigest()[:24]
            instrument = state['instruments'].get(symbol, {})
            price = number(row.get('avgPx') if kind == 'position' else row.get('px'))
            notional = None
            value_currency = 'USDT'
            # Only linear USDT contracts can be converted with our catalogue.
            if symbol.endswith('-USDT-SWAP'):
                if kind == 'position':
                    mark = number(row.get('markPx')) or number(state['quotes'].get(symbol, {}).get('last'))
                    contract_value = number(instrument.get('contract_value'))
                    if mark is not None and contract_value is not None:
                        notional = abs(size) * contract_value * mark
                    else:
                        notional = number(row.get('notionalUsd'))
                        value_currency = 'USD'
                elif price is not None and instrument.get('contract_value'):
                    size, filled = number(row.get('sz')), number(row.get('accFillSz', '0'))
                    if size is not None and filled is not None:
                        notional = max(Decimal(0), size-filled) * Decimal(instrument['contract_value']) * price
            created = number(row.get('cTime'))
            result.append(dict(id=key, kind=kind, symbol=symbol, side=side or row.get('side', ''),
                margin_mode=row.get('mgnMode', row.get('tdMode')), mixed=mixed,
                price=str(price) if price is not None and price > 0 else None,
                notional=str(abs(notional)) if notional is not None else None, value_currency=value_currency,
                pnl=str(number(row.get('upl'))) if symbol.endswith('-USDT-SWAP') and number(row.get('upl')) is not None else None,
                created=float(created)/1000 if created is not None and created > 0 else None,
                quantity=row.get('pos', row.get('sz')), status='observed'))
    return result


def persist_observations(db, state, records, now, event):
    db.execute('CREATE TABLE IF NOT EXISTS external_observations (id TEXT PRIMARY KEY, body TEXT NOT NULL, active INTEGER NOT NULL, last_seen REAL NOT NULL)')
    db.execute('CREATE INDEX IF NOT EXISTS external_observations_active ON external_observations(active,last_seen)')
    active = {row['id'] for row in records}
    previous = {key: json.loads(body) for key, body in db.execute('SELECT id,body FROM external_observations WHERE active=1')}
    for key in active - previous.keys():
        prior = db.execute('SELECT body FROM external_observations WHERE id=?', (key,)).fetchone()
        if prior:
            previous[key] = json.loads(prior[0])
    for row in records:
        prior = previous.get(row['id'])
        row.update(first_seen=prior['first_seen'] if prior else now, last_seen=now)
        row.pop('absent_since', None)
        comparable = lambda r: {k: v for k, v in r.items() if k not in ('first_seen', 'last_seen', 'pnl', 'notional')}
        if not prior or comparable(prior) != comparable(row):
            event(db, 'external_activity_observed', row)
        previous[row['id']] = row
        db.execute('INSERT OR REPLACE INTO external_observations VALUES(?,?,1,?)', (row['id'], json.dumps(row), now))
    for key, row in previous.items():
        if key not in active and row['status'] == 'observed':
            row.update(status='absent', absent_since=now)
            event(db, 'external_activity_absent', row)
            db.execute('UPDATE external_observations SET body=?,active=0,last_seen=? WHERE id=?', (json.dumps(row), now, key))
    recent = [json.loads(row[0]) for row in db.execute('SELECT body FROM external_observations WHERE active=0 ORDER BY last_seen DESC LIMIT 20')]
    state['external_activity'] = dict(current=records, recent=recent, updated_at=now)
    state['foreign'] = bool(records)  # Informational compatibility field only.
