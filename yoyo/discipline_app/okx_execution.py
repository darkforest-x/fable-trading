"""Explicit OKX execution protocol; independent of all existing Spike modules.

Sources: OKX API v5 authentication, place order, attached split TP/SL, and
amend algo documentation at https://www.okx.com/docs-v5/en/ (2026-10-18).
This module does not activate execution by import. Live writes require the
separate service to construct a write-enabled client and a user order intent.
"""
import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from decimal import Decimal
import httpx
from .okx_demo import Credentials
from .policy import Rejected, dec, positive, floor_step, ceil_step, Policy


class ExchangeUncertain(RuntimeError):
    """A write may have reached OKX; reconcile its stable ID, never blind retry."""


class ExchangeRejected(Rejected):
    def __init__(self, code):
        self.code = str(code) if str(code).isdigit() else 'unknown'
        super().__init__(f'OKX 拒绝请求（{self.code}），请检查账户与委托条件。')


class OkxExecutionClient:
    READ = frozenset(('/api/v5/account/config', '/api/v5/account/balance',
        '/api/v5/account/positions', '/api/v5/account/positions-history',
        '/api/v5/account/leverage-info', '/api/v5/account/trade-fee',
        '/api/v5/trade/orders-pending', '/api/v5/trade/order',
        '/api/v5/trade/orders-algo-pending', '/api/v5/trade/orders-algo-history',
        '/api/v5/trade/order-algo', '/api/v5/trade/fills'))
    WRITE = frozenset(('/api/v5/account/set-leverage', '/api/v5/trade/order',
        '/api/v5/trade/cancel-order', '/api/v5/trade/order-algo', '/api/v5/trade/amend-algos', '/api/v5/trade/cancel-algos'))

    def __init__(self, credentials: Credentials, *, environment, write_enabled=False, transport=None):
        if environment not in ('okx_live', 'okx_demo'):
            raise ValueError('An explicit OKX environment is required')
        self.credentials, self.environment, self.write_enabled = credentials, environment, write_enabled
        # A new TLS session through the local proxy costs ~2.4s; httpx's 5s
        # default expiry dropped it between reconcile cycles. Keep a small
        # warm pool so parallel account reads reuse established sessions.
        self.client = httpx.Client(base_url='https://www.okx.com', timeout=10,
            follow_redirects=False, transport=transport,
            limits=httpx.Limits(max_connections=12, max_keepalive_connections=10, keepalive_expiry=60))
        self.offset, self.synchronized_at = 0.0, 0.0

    def close(self):
        self.client.close()

    def sync_time(self):
        start = time.time()
        try:
            response = self.client.get('/api/v5/public/time')
            data = self._response(response, write=False)
        except httpx.HTTPError:
            raise Rejected('无法同步交易所时间，暂停开仓') from None
        end = time.time()
        if end - start > 5:
            raise Rejected('OKX 连接延迟过高，暂停开仓')
        try:
            self.offset = int(data[0]['ts']) / 1000 - (start + end) / 2
        except (ValueError, KeyError, IndexError, TypeError):
            raise Rejected('交易所时间格式异常，暂停开仓') from None
        self.synchronized_at = end

    @staticmethod
    def _response(response, *, write):
        if response.status_code >= 500 or 300 <= response.status_code < 400:
            if write:
                raise ExchangeUncertain('交易所响应未确认，请等待订单核对')
            raise Rejected('交易所暂时不可用')
        try:
            result = response.json()
            code, data = str(result['code']), result.get('data', [])
            if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            if write:
                raise ExchangeUncertain('交易所返回格式异常，请等待订单核对') from None
            raise Rejected('交易所返回格式异常') from None
        if code != '0' or response.status_code != 200:
            raise ExchangeRejected(data[0].get('sCode', code) if data and isinstance(data[0], dict) else code)
        for row in data:
            if row.get('sCode', '0') != '0':
                raise ExchangeRejected(row['sCode'])
        return data

    def request(self, method, path, *, params=None, body=None):
        from urllib.parse import urlencode
        write = method == 'POST'
        if method not in ('GET', 'POST') or path not in (self.WRITE if write else self.READ):
            raise Rejected('该交易所操作不在此 App 的权限范围内')
        if write and not self.write_enabled:
            raise Rejected('实盘执行尚未启用')
        if time.time() - self.synchronized_at > 60:
            self.sync_time()
        request_path = path + ('?' + urlencode(params) if params else '')
        payload = json.dumps(body, separators=(',', ':'), ensure_ascii=False) if body is not None else ''
        now = time.time() + self.offset
        timestamp = datetime.fromtimestamp(now, timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
        signature = base64.b64encode(hmac.new(self.credentials.secret.encode(),
            (timestamp + method + request_path + payload).encode(), hashlib.sha256).digest()).decode()
        headers = {'OK-ACCESS-KEY': self.credentials.key, 'OK-ACCESS-SIGN': signature,
            'OK-ACCESS-PASSPHRASE': self.credentials.passphrase, 'OK-ACCESS-TIMESTAMP': timestamp,
            'x-simulated-trading': '0' if self.environment == 'okx_live' else '1',
            'Content-Type': 'application/json'}
        if write and path == '/api/v5/trade/order':
            headers['expTime'] = str(int(now * 1000) + 5000)
        try:
            response = self.client.request(method, request_path, headers=headers, content=payload or None)
        except httpx.HTTPError:
            if write:
                raise ExchangeUncertain('请求结果未确认，请等待交易所回执核对') from None
            raise Rejected('交易所连接中断，暂停开仓') from None
        return self._response(response, write=write)

    def get(self, path, **params):
        return self.request('GET', path, params=params)

    def post(self, path, body):
        return self.request('POST', path, body=body)


def stable_id(identity, purpose):
    return 'sd' + hashlib.sha256((identity + ':' + purpose).encode()).hexdigest()[:30]


def protective_prices(entry, quantity, risk, entry_fee, policy, side, tick):
    """Net-original-quantity R targets; fixed R includes the entry plan costs.

    Inputs are the actual entry price/fee and the immutable accepted risk. No
    future market data is read. Price rounding never brings TP earlier than R.
    """
    entry, quantity, risk, entry_fee, tick = map(dec, (entry, quantity, risk, entry_fee, tick))
    fee, slip = dec(policy.fee_rate), dec(policy.slippage_rate)
    direction = 1 if side == 'long' else -1
    per_fee = entry_fee / quantity
    breakeven = ((entry + per_fee) / ((1-slip)*(1-fee)) if direction == 1
        else (entry - per_fee) / ((1+slip)*(1+fee)))
    rounding = ceil_step if direction == 1 else floor_step
    targets = []
    for multiple, _ in policy.take_profits:
        raw = ((entry + per_fee + dec(multiple)*risk/quantity)/(1-fee) if direction == 1
            else (entry - per_fee - dec(multiple)*risk/quantity)/(1+fee))
        targets.append(str(rounding(positive(raw), tick)))
    return dict(breakeven=str(rounding(positive(breakeven), tick)), targets=targets)


def entry_payload(identity, ticket, sized, instrument, policy, position_mode):
    """Compile a fully-filled-or-cancelled entry with exchange-hosted SL + split TP.

    FOK avoids leaving an unprotected partial entry while OKX waits for a full
    fill before creating attached algos. Missing protection acknowledgements
    must still block the execution engine; this compiler is not such evidence.
    """
    if position_mode not in ('net_mode', 'long_short_mode'):
        raise Rejected('当前账户持仓模式不受支持')
    if policy.margin_mode != 'cross':
        raise Rejected('实盘接入当前仅支持已确认的全仓规则')
    if len(policy.take_profits) != 2:
        raise Rejected('实盘接入需要两档止盈规则')
    value, step = positive(instrument['contract_value']), positive(instrument['step'])
    quantity, tick = positive(sized['quantity']), positive(instrument['tick'])
    lots = quantity / value
    first = floor_step(quantity * dec(policy.take_profits[0][1]), step)
    last = quantity - first
    if min(first, last) < positive(instrument['minimum']):
        raise Rejected('当前仓位不足以按最小合约数量分两档止盈')
    entry = positive(sized['entry'])
    price = floor_step(entry, tick) if ticket['side'] == 'long' else ceil_step(entry, tick)
    # Tighten, never widen, the accepted worst execution bound.
    prices = protective_prices(entry, quantity, sized['risk'], entry*quantity*dec(policy.fee_rate),
        policy, ticket['side'], tick)
    algos = [dict(attachAlgoClOrdId=stable_id(identity, 'stop'),
        slTriggerPx=sized['stop'], slOrdPx='-1', slTriggerPxType='last')]
    for index, amount in enumerate((first, last)):
        algos.append(dict(attachAlgoClOrdId=stable_id(identity, f'tp{index}'),
            tpTriggerPx=prices['targets'][index], tpOrdPx='-1', tpTriggerPxType='last',
            sz=str(amount/value)))
    return dict(instId=ticket['symbol'], tdMode='cross', clOrdId=stable_id(identity, 'entry'),
        side='buy' if ticket['side']=='long' else 'sell',
        posSide=ticket['side'] if position_mode=='long_short_mode' else 'net',
        ordType='fok', px=str(price), sz=str(lots), attachAlgoOrds=algos)
