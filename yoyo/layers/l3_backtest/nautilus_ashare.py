"""A-share session adapter for native NautilusTrader 1.231.0 matching.

Reuse the established native OHLC segment adapter. This module creates Equity
orders; it never scans highs/lows to compute fills or trade PnL. T+1 protection
is armed only on the next observed trading date. Daily orders are reissued at
the next observed open, so overnight gaps use that open. Sell stop-limit orders
rest at the daily lower limit after triggering. Empty synthetic books block
limit-down sells / limit-up buys; unlike STOP_MARKET, native STOP_LIMIT does
not fall back to unconditional liquidity. Synthetic full liquidity away from
locked limits, zero slippage, no historical order queue or capacity claim.

Raw prices and frozen nominal targets; cash dividends are booked separately by
the caller. Unsupported share-count changes are administrative censoring, not
executed strategy exits. No funding constraints: results are independent lots.
Source: installed engine.pyx determine_limit_fills_with_simulation and
https://nautilustrader.io/docs/latest/concepts/backtesting/bar-execution/.
"""
from __future__ import annotations

from decimal import Decimal
import math

import pandas as pd
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import FeeModel, FillModel
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig, RiskEngineConfig, StrategyConfig
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.currencies import CNY
from nautilus_trader.model.data import BarType, BookOrder
from nautilus_trader.model.enums import AccountType, BookType, OmsType, OrderSide, OrderType
from nautilus_trader.model.identifiers import InstrumentId, PositionId, Symbol, Venue
from nautilus_trader.model.instruments import Equity
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.persistence.wranglers import BarDataWrangler
from nautilus_trader.trading.strategy import Strategy

from .nautilus_independent import ENGINE_VERSION, ohlc_path_frame


def cents(value):
    return float(Decimal(str(value)).quantize(Decimal('.01'), rounding='ROUND_HALF_UP'))


class StockFee(FeeModel):
    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg

    def get_commission(self, order, fill_qty, fill_px, instrument):
        n = float(fill_qty) * float(fill_px)
        fee = max(self.cfg['commission_min_cny'], n*self.cfg['commission_rate'])
        fee += n*self.cfg['transfer_rate_each_side']
        if order.side == OrderSide.SELL:
            fee += n*self.cfg['sell_stamp_rate']
        return Money(cents(fee), CNY)


class StockLiquidity(FillModel):
    def __init__(self):
        super().__init__()
        self.strategy = None
        self.blocked = set()

    def get_orderbook_for_fill_simulation(self, instrument, order, best_bid, best_ask):
        s = self.strategy
        ns = s.clock.timestamp_ns()
        i, phase = s.phases[ns]
        row = s.frame.iloc[i]
        buy = order.side == OrderSide.BUY
        quote = float(best_ask if buy else best_bid)
        book = OrderBook(instrument_id=instrument.id, book_type=BookType.L2_MBP)
        locked = quote >= row.limit_up-.004 if buy else quote <= row.limit_down+.004
        if locked or row.volume <= 0:
            self.blocked.add((str(order.client_order_id), i, phase))
            # Native MARKET ignores an empty book; such orders are never sent
            # while locked. STOP_LIMIT and LIMIT preserve this empty result.
            return book
        if order.order_type == OrderType.LIMIT:
            limit = float(order.price)
            _, kind = s.order_map[str(order.client_order_id)]
            price = quote if kind == 'stop' else min(limit, quote) if buy and phase == 0 else max(limit, quote) if not buy and phase == 0 else limit
        elif order.order_type == OrderType.STOP_LIMIT:
            trigger = float(order.trigger_price)
            triggered = s.triggered.get(str(order.client_order_id), ns)
            price = quote if triggered < ns else min(trigger, quote) if phase == 0 else trigger
        else:
            price = quote
        side = OrderSide.SELL if buy else OrderSide.BUY
        book.add(BookOrder(side=side, price=instrument.make_price(price),
                           size=order.quantity, order_id=1), 0, 0)
        return book


class AShareStrategy(Strategy):
    def __init__(self, instrument, bar_type, frame, phases, plans, cfg, liquidity):
        super().__init__(StrategyConfig(oms_type='HEDGING', log_events=False, log_commands=False))
        self.instrument, self.bar_type, self.frame, self.phases = instrument, bar_type, frame, phases
        self.plans, self.cfg, self.liquidity = plans, cfg, liquidity
        self.pending, self.active, self.order_map = {}, {}, {}
        self.done, self.triggered = set(), {}
        self.rows, self.fills, self.errors = [], [], []
        self.day = None
        self.t1_hits = set()

    def on_start(self):
        self.subscribe_bars(self.bar_type)

    def submit(self, order, state, kind):
        self.order_map[str(order.client_order_id)] = (state, kind)
        state.setdefault('orders', []).append(order)
        self.submit_order(order, position_id=PositionId(state['trade_id']))

    def cancel(self, state):
        for order in state.get('orders', []):
            if not order.is_closed:
                self.cancel_order(order)
        state['orders'] = []

    def open_lot(self, plan, i, kind):
        p = self.frame.iloc[i]
        price = plan['entry'] if kind == 'limit' else p.close
        units = int(self.cfg['notional_cny'] / price / 100) * 100
        if units < 100:
            self.done.add(plan['trade_id'])
            return
        state = dict(plan, quantity=units, orders=[], fee=0., cash_dividend=0.)
        self.pending[plan['trade_id']] = state
        if kind == 'limit':
            order = self.order_factory.limit(self.instrument.id, OrderSide.BUY,
                self.instrument.make_qty(units), price=self.instrument.make_price(price))
        else:
            # A marketable limit at the observed close retains limit-up gating.
            order = self.order_factory.limit(self.instrument.id, OrderSide.BUY,
                self.instrument.make_qty(units), price=self.instrument.make_price(price))
        self.submit(order, state, 'entry')
        if kind == 'market' and plan['trade_id'] in self.pending:
            self.cancel(state)
            self.pending.pop(plan['trade_id'])
            self.done.add(plan['trade_id'])

    def protect(self, state, i):
        row = self.frame.iloc[i]
        if state['entry_date'] >= row.date:
            return
        qty = self.instrument.make_qty(state['quantity'])
        if state.get('stop_triggered'):
            stop = self.order_factory.limit(self.instrument.id, OrderSide.SELL, qty,
                price=self.instrument.make_price(row.limit_down), reduce_only=True)
        else:
            stop = self.order_factory.stop_limit(self.instrument.id, OrderSide.SELL, qty,
                trigger_price=self.instrument.make_price(state['stop']),
                price=self.instrument.make_price(row.limit_down), reduce_only=True)
        self.submit(stop, state, 'stop')
        if state['trade_id'] not in self.active:
            return
        target = self.order_factory.limit(self.instrument.id, OrderSide.SELL, qty,
            price=self.instrument.make_price(state['target']), reduce_only=True)
        self.submit(target, state, 'target')

    def on_order_triggered(self, event):
        self.triggered.setdefault(str(event.client_order_id), event.ts_event)
        state, kind = self.order_map[str(event.client_order_id)]
        if kind == 'stop':
            state['stop_triggered'] = True

    def on_order_filled(self, event):
        state, kind = self.order_map[str(event.client_order_id)]
        i, phase = self.phases[event.ts_event]
        row = self.frame.iloc[i]
        price, quantity = float(event.last_px), int(event.last_qty)
        if quantity != state['quantity']:
            raise RuntimeError('Unexpected partial fill')
        fee = float(event.commission)
        self.fills.append(dict(trade_id=state['trade_id'], kind=kind, bar_i=i,
            phase=phase, time=str(self.frame.index[i]), price=price, quantity=quantity, fee=fee))
        state['fee'] += fee
        if kind == 'entry':
            self.done.add(state['trade_id'])
            self.pending.pop(state['trade_id'])
            state.update(fill_price=price, entry_i=i, entry_date=row.date,
                entry_time=str(self.frame.index[i]), stop=cents(price*(1-self.cfg['stop_fraction'])),
                actual_bucket=int(row.get('vol_bucket', -999)),
                target=state['target'] if state['population']=='signal' else math.ceil(price*(1+state['target_return'])*100-1e-8)/100)
            self.active[state['trade_id']] = state
        else:
            if row.date <= state['entry_date']:
                raise RuntimeError('T+1 violation')
            self.finish(state, i, price, kind, True)

    def finish(self, state, i, price, reason, executed):
        p = self.frame.iloc[i]
        out = {k:v for k,v in state.items() if k != 'orders'}
        out.update(exit_i=i, exit_date=p.date, exit_time=str(self.frame.index[i]), exit_price=price,
                   reason=reason, executed=executed,
                   gross_pnl=(price-state['fill_price'])*state['quantity'],
                   holding_days=(pd.Timestamp(p.date)-pd.Timestamp(state['entry_date'])).days)
        self.rows.append(out)
        self.active.pop(state['trade_id'])
        # Native reduce-only cleanup owns sibling cancellation on executed exits.
        if not executed:
            self.cancel(state)

    def on_bar(self, bar):
        i, phase = self.phases[bar.ts_event]
        p = self.frame.iloc[i]
        if phase == 0 and p.date != self.day:
            self.day = p.date
            for state in list(self.active.values()):
                self.protect(state, i)
            for plan in self.plans:
                if (plan['population']=='signal' and plan['trade_id'] not in self.done
                        and plan['start_date'] <= p.date <= plan['expire_date']):
                    self.open_lot(plan, i, 'limit')
        if phase != 3:
            return
        for state in list(self.active.values()):
            if (state['entry_date'] == p.date and i > state['entry_i'] and p.high >= state['target']):
                self.t1_hits.add(state['trade_id'])
            if p.censor_after:
                self.finish(state, i, p.close, 'share_action_censored', False)
        for plan in self.plans:
            if plan['population']=='random' and plan['trade_id'] not in self.done and plan['submit_i']==i:
                self.open_lot(plan, i, 'market')
        if p.session_end:
            for state in list(self.pending.values()):
                self.cancel(state)
            self.pending.clear()
            for state in list(self.active.values()):
                self.cancel(state)
        if i == len(self.frame)-1:
            for state in list(self.active.values()):
                # Keep unrealized positions; do not invent terminal liquidation.
                self.finish(state, i, p.close, 'open_at_end', False)

    def on_order_rejected(self, event):
        self.errors.append(str(event))

    on_order_denied = on_order_rejected
    on_order_modify_rejected = on_order_rejected
    on_order_cancel_rejected = on_order_rejected


def run_native(frame, plans, cfg, symbol):
    """Run the frozen native engine once for a symbol and all independent lots."""
    import nautilus_trader
    if nautilus_trader.__version__ != ENGINE_VERSION:
        raise RuntimeError('Unexpected Nautilus version')
    venue = Venue('ASHARE')
    instrument = Equity(InstrumentId(Symbol(symbol), venue), Symbol(symbol), CNY,
        price_precision=2, price_increment=Price(.01, 2), lot_size=Quantity.from_int(100),
        ts_event=0, ts_init=0)
    bar_type = BarType.from_str(f'{instrument.id}-1-HOUR-LAST-EXTERNAL')
    path, phases = ohlc_path_frame(frame, 'OHLC')
    path['volume'] = 1e12
    liquidity = StockLiquidity()
    engine = BacktestEngine(BacktestEngineConfig(logging=LoggingConfig(bypass_logging=True),
        risk_engine=RiskEngineConfig(bypass=True), run_analysis=False))
    try:
        engine.add_venue(venue, OmsType.HEDGING, AccountType.CASH,
            [Money(1e9, CNY)], base_currency=CNY, frozen_account=True,
            fee_model=StockFee(cfg), fill_model=liquidity, use_message_queue=False,
            reject_stop_orders=False, bar_execution=True, bar_adaptive_high_low_ordering=True,
            liquidity_consumption=False)
        engine.add_instrument(instrument)
        strategy = AShareStrategy(instrument, bar_type, frame, phases, plans, cfg, liquidity)
        liquidity.strategy = strategy
        engine.add_strategy(strategy)
        engine.add_data(BarDataWrangler(bar_type, instrument).process(path))
        engine.run()
        if strategy.errors:
            raise RuntimeError(strategy.errors[:5])
        positions = list(engine.cache.positions())
        for pos in positions:
            if pos.is_closed:
                fills = [r for r in strategy.fills if r['trade_id']==str(pos.id)]
                expected = sum((1 if r['kind']!='entry' else -1)*r['price']*r['quantity']-r['fee'] for r in fills)
                if abs(float(pos.realized_pnl)-expected) > .011:
                    raise RuntimeError('Native PnL reconciliation failed')
        return pd.DataFrame(strategy.rows), pd.DataFrame(strategy.fills), dict(
            engine=ENGINE_VERSION, native_positions=len(positions), native_fills=len(strategy.fills),
            locked_fill_attempts=len(liquidity.blocked), t1_target_touch_lots=len(strategy.t1_hits),
            errors=strategy.errors, path='OHLC monotone segments')
    finally:
        engine.dispose()
