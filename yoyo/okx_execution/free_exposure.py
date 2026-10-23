"""Read-only gross allowance accounting for free manual OKX entries.

Confirmed local intents reserve exposure until the exchange inventory represents
them. Unknown results stay reserved; protective/reduce orders do not open risk.
"""
from yoyo.contracts.manual_exposure import allowance, pool
from .risk import D
from .engine import one


def opening(row):
    if str(row.get('reduceOnly', '')).lower() == 'true':
        return False
    if row.get('posSide') not in ('long','short') or row.get('side') not in ('buy','sell'):
        raise ValueError('已有委托的开平方向暂不可用')
    return (row['posSide'], row['side']) in (('long', 'buy'), ('short', 'sell'))


def budget(engine, account, symbol, inventory, tickets, exclude=None):
    exposures, represented = [], set()
    specs = {}

    def spec(s):
        if s not in specs:
            specs[s] = one(engine.client.get('/api/v5/account/instruments', instType='SWAP', instId=s))
        value = specs[s]
        if value.get('instId') != s or value.get('ctValCcy') != s.split('-')[0]:
            raise ValueError('已有委托的合约面值暂不可用')
        if D(value['ctVal']) <= 0:
            raise ValueError('已有委托的合约面值暂不可用')
        return D(value['ctVal'])

    for p in account['positions']:
        s = p['instId']
        if D(p['pos']) and s.endswith('-USDT-SWAP') and pool(s) == pool(symbol):
            if D(p['markPx']) <= 0:
                raise ValueError('已有持仓的标记价格暂不可用')
            exposures.append((s, abs(D(p['pos']))*spec(s)*D(p['markPx'])))
    for row in inventory:
        s = row['instId']
        if not s.endswith('-USDT-SWAP') or pool(s) != pool(symbol) or not opening(row):
            continue
        ticker = one(engine.client.get('/api/v5/market/ticker', instId=s))
        if ticker.get('instId') != s or not -1000 <= engine.clock()-int(ticker['ts']) <= 5000:
            raise ValueError('已有委托的行情暂不可用')
        prices = [D(ticker['askPx']), D(ticker['bidPx'])]
        if min(prices) <= 0 or prices[0] < prices[1]:
            raise ValueError('已有委托的行情暂不可用')
        prices += [D(row[k]) for k in ('px', 'orderPx', 'triggerPx') if row.get(k) and D(row[k]) > 0]
        if D(row['sz']) <= 0:
            raise ValueError('已有开仓挂单的数量暂不可用')
        remaining = max(D(0), D(row['sz'])-D(row.get('accFillSz') or 0))
        exposures.append((s, remaining*spec(s)*max(prices)))
        represented.add(row.get('clOrdId'))
    reserved = []
    for t in tickets:
        if t['id'] == exclude or t['client_order_id'] in represented:
            continue
        # A sent/unknown ticket may already have become a position. Counting it
        # again is conservative until reconciliation, never permission to add.
        reserved.append((t['symbol'], D(t['notional'])))
    result = allowance(symbol, account['equity'], exposures+reserved)
    result['exchange_exposure_used'] = str(sum((D(v) for s,v in exposures if pool(s)==pool(symbol)), D(0)))
    result['represented_client_ids'] = [x for x in represented if x]
    return result
