"""Shared read-only OKX USDT perpetual catalog and quotes.

Source: OKX v5 public instruments and tickers. Only active linear, USDT-settled
SWAP instruments with base-denominated contract values enter the sizing engine.
Metadata refreshes every ten minutes; no fixed coin allowlist is maintained.
"""
import json
import threading
import time
import urllib.parse
import urllib.request
from .policy import dec, positive, Rejected


def today_change(row):
    """OKX sodUtc8 is calendar-day open; never substitute rolling open24h."""
    try:
        return str((positive(row['last']) / positive(row['sodUtc8']) - 1) * 100)
    except (KeyError, ValueError, Rejected):
        return None


def estimated_turnover(row):
    """Estimate USDT turnover from OKX SWAP base volume, never contract lots.

    OKX v5 Get tickers documents volCcy24h in base currency for derivatives;
    multiplying by last is an estimate, not the sum of each trade's notional.
    Source: https://www.okx.com/docs-v5/en/#order-book-trading-market-data-get-tickers
    """
    try:
        volume = dec(row['volCcy24h'])
        return str(volume * positive(row['last'])) if volume >= 0 else None
    except (KeyError, ValueError, Rejected):
        return None


def parse_instruments(rows):
    instruments = {}
    for row in rows:
        symbol = row.get('instId', '')
        if (row.get('state') != 'live' or row.get('instType', 'SWAP') != 'SWAP'
                or row.get('ctType') != 'linear' or row.get('settleCcy') != 'USDT'
                or not symbol.endswith('-USDT-SWAP')):
            continue
        coin = symbol.removesuffix('-USDT-SWAP')
        if not coin or row.get('ctValCcy') != coin:
            continue
        try:
            base = positive(row['ctVal']) * positive(row.get('ctMult') or '1')
            leverage = int(positive(row['lever']))
            if leverage < 1:
                continue
            instruments[symbol] = dict(symbol=symbol, coin=coin, tradable=True,
                step=str(base * positive(row['lotSz'])), minimum=str(base * positive(row['minSz'])),
                tick=str(positive(row['tickSz'])), max_leverage=leverage, contract_value=str(base),
                fee_group=row.get('groupId'), source='OKX public instruments',
                logo_url='https://static.okx.com/cdn/oksupport/asset/currency/icon/'
                         + urllib.parse.quote(coin.lower(), safe='') + '.png')
        except (KeyError, ValueError, Rejected):
            continue  # A malformed instrument must not stop the other contracts.
    if not instruments:
        raise ValueError('OKX 没有返回有效的 USDT 永续合约')
    return instruments


class PublicMarket:
    def __init__(self, book):
        self.book = book
        self.stop_event = threading.Event()
        self.thread = None
        self.instruments = {}
        self.metadata_at = 0

    def get(self, endpoint, params):
        request = urllib.request.Request('https://www.okx.com/api/v5/' + endpoint + '?' + urllib.parse.urlencode(params), headers={'User-Agent': 'SpikeDiscipline/0.6'})
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.load(response)
        if data.get('code') != '0':
            raise ValueError('OKX 公共行情返回错误')
        return data['data']

    def tick(self):
        if time.time() - self.metadata_at > 600:
            self.instruments = parse_instruments(self.get('public/instruments', {'instType': 'SWAP'}))
            self.metadata_at = time.time()
        quotes = {}
        for row in self.get('market/tickers', {'instType': 'SWAP'}):
            if row.get('instId') not in self.instruments:
                continue
            try:
                if any(positive(row[k]) <= 0 for k in ('last', 'bidPx', 'askPx')) or dec(row['bidPx']) > dec(row['askPx']):
                    continue
                quotes[row['instId']] = dict(last=row['last'], bid=row['bidPx'], ask=row['askPx'],
                    time=int(row['ts']) / 1000, source='OKX public ticker', today_change=today_change(row),
                    volume24h_usdt=estimated_turnover(row))
            except (KeyError, ValueError, Rejected):
                continue
        self.book.update_market(self.instruments, quotes)

    def run(self):
        while not self.stop_event.is_set():
            try:
                self.tick()
            except Exception as exc:
                self.book.market_failed(f'公共行情暂不可用：{type(exc).__name__}')
            self.stop_event.wait(2)

    def start(self):
        self.thread = threading.Thread(target=self.run, name='discipline-public-market', daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=12)
