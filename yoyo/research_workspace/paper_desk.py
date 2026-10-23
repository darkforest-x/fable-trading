"""Manual paper accounts requested by Notion task 54 (2026-10-15).

Independent linear long/short positions reserve entry notional / leverage. There
is no exchange order client, account credential, strategy trigger or reset.
Money uses Decimal; every fill and idempotency receipt commits atomically.
OKX regular-tier maker/taker fees are recorded per fill. Quotes are immutable public OKX
bid/ask observations, not executable depth or exchange-confirmed fills.
"""
from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal, InvalidOperation, ROUND_DOWN
import json
from pathlib import Path
import sqlite3
import time
import uuid

MAKER_FEE = Decimal("0.0002")
TAKER_FEE = Decimal("0.0005")
FEE = TAKER_FEE
QUOTE_TTL_MS = 15000
MAX_LEVERAGE = 125  # Local simulation limit, not an exchange instrument limit.
ENTRY_LEVERAGES = (1, 2, 5, 10, 15, 20, 30, 50, 100)
POSITION_MULTIPLES = ENTRY_LEVERAGES


def quick_quote(quote):
    return bool(quote and json.loads(quote.get('risk_json') or '{}').get('monday_quick'))


def symbol_leverage(symbol, value, quote=None):
    leverage_value(value)
    if quick_quote(quote):
        return value  # Actual instrument and quantity tier checked by check_open.
    cap = 100 if symbol in {'BTC-USDT-SWAP', 'ETH-USDT-SWAP'} else 20
    if value > cap:
        raise ValueError(f"{symbol.removesuffix('-USDT-SWAP')} 合约最高支持 {cap} 倍杠杆")
    if value not in ENTRY_LEVERAGES:
        raise ValueError('请选择支持的杠杆档位')
    return value


def limit_fee(side, quote, price, action='open'):
    buy = (action == 'open') == (side == 'long')
    crosses = positive(price) >= number(quote['ask']) if buy else positive(price) <= number(quote['bid'])
    return TAKER_FEE if crosses else MAKER_FEE


def leverage_value(value):
    if type(value) is not int or not 1 <= value <= MAX_LEVERAGE:
        raise ValueError(f"杠杆必须是 1 至 {MAX_LEVERAGE} 的整数")
    return value


def margin(quantity, entry_price, leverage):
    """Reserve collateral; quantity already denotes the complete base exposure."""
    return number(quantity) * number(entry_price) / leverage_value(leverage)


def clock_ms():
    return time.time_ns() // 1_000_000


def number(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("金额或数量无效") from None
    if not result.is_finite():
        raise ValueError("金额或数量必须是有限值")
    return result


def positive(value):
    result = number(value)
    if result <= 0 or result > Decimal("1e12") or result < Decimal("1e-12"):
        raise ValueError("金额或数量需在 0.000000000001 至 1 万亿之间")
    return result


def optional_price(value):
    return None if value is None else str(positive(value))


def stop_prices(side, entry_price, take_profit=None, stop_loss=None):
    """Validate owner-entered exit prices relative to the actual entry price."""
    tp, sl = optional_price(take_profit), optional_price(stop_loss)
    entry = number(entry_price)
    if side not in {"long", "short"}:
        raise ValueError("方向无效")
    if side == "long":
        if tp is not None and number(tp) <= entry:
            raise ValueError("做多止盈价须高于开仓价")
        if sl is not None and number(sl) >= entry:
            raise ValueError("做多止损价须低于开仓价")
    else:
        if tp is not None and number(tp) >= entry:
            raise ValueError("做空止盈价须低于开仓价")
        if sl is not None and number(sl) <= entry:
            raise ValueError("做空止损价须高于开仓价")
    return dict(take_profit=tp, stop_loss=sl)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def fresh(quote, at):
    return -5000 <= at - quote["source_ms"] <= QUOTE_TTL_MS and 0 <= at - quote["received_ms"] <= QUOTE_TTL_MS


class PaperDesk:
    def __init__(self, runtime):
        self.path = Path(runtime) / "paper_desk.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS accounts (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, initial_cash TEXT NOT NULL,
              cash TEXT NOT NULL, created_ms INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS positions (
              id TEXT PRIMARY KEY, account_id TEXT NOT NULL, symbol TEXT NOT NULL,
              side TEXT NOT NULL, quantity TEXT NOT NULL, remaining TEXT NOT NULL,
              entry_price TEXT NOT NULL, realized TEXT NOT NULL, fees TEXT NOT NULL,
              opened_ms INTEGER NOT NULL, closed_ms INTEGER, note TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS fills (
              id TEXT PRIMARY KEY, position_id TEXT NOT NULL, account_id TEXT NOT NULL,
              action TEXT NOT NULL, quantity TEXT NOT NULL, price TEXT NOT NULL,
              fee TEXT NOT NULL, gross_pnl TEXT NOT NULL, quote_id TEXT NOT NULL,
              created_ms INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS quotes (
              id TEXT PRIMARY KEY, symbol TEXT NOT NULL, bid TEXT NOT NULL,
              ask TEXT NOT NULL, source_ms INTEGER NOT NULL, received_ms INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS receipts (
              request_id TEXT PRIMARY KEY, payload TEXT NOT NULL, response TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS position_journals (
              position_id TEXT PRIMARY KEY, entry_reason TEXT NOT NULL, analysis TEXT NOT NULL,
              plan TEXT NOT NULL, review TEXT NOT NULL, version INTEGER NOT NULL,
              created_ms INTEGER NOT NULL, updated_ms INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS journal_images (
              id TEXT PRIMARY KEY, position_id TEXT NOT NULL, name TEXT NOT NULL,
              mime TEXT NOT NULL, data BLOB NOT NULL, sha256 TEXT NOT NULL,
              width INTEGER NOT NULL, height INTEGER NOT NULL, created_ms INTEGER NOT NULL,
              deleted INTEGER NOT NULL DEFAULT 0, UNIQUE(position_id,sha256));
            CREATE TABLE IF NOT EXISTS limit_orders (
              id TEXT PRIMARY KEY, account_id TEXT NOT NULL, action TEXT NOT NULL,
              symbol TEXT NOT NULL, side TEXT NOT NULL, quantity TEXT NOT NULL,
              limit_price TEXT NOT NULL, leverage INTEGER NOT NULL, note TEXT NOT NULL,
              position_id TEXT, status TEXT NOT NULL, created_ms INTEGER NOT NULL,
              updated_ms INTEGER NOT NULL, quote_id TEXT NOT NULL,
              trigger_quote_id TEXT, fill_id TEXT UNIQUE, reason TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS limit_orders_pending ON limit_orders(status,account_id);
            CREATE INDEX IF NOT EXISTS positions_account ON positions(account_id);
            CREATE INDEX IF NOT EXISTS fills_account ON fills(account_id, created_ms);
            CREATE INDEX IF NOT EXISTS fills_position ON fills(position_id, created_ms);
            CREATE INDEX IF NOT EXISTS positions_open ON positions(account_id,opened_ms DESC,id) WHERE CAST(remaining AS NUMERIC)>0;
            CREATE INDEX IF NOT EXISTS positions_closed ON positions(account_id,closed_ms DESC,id) WHERE CAST(remaining AS NUMERIC)=0;
            CREATE TABLE IF NOT EXISTS equity_samples (
              account_id TEXT NOT NULL,bucket_ms INTEGER NOT NULL,observed_ms INTEGER NOT NULL,
              equity TEXT,balance TEXT NOT NULL,PRIMARY KEY(account_id,bucket_ms));
            CREATE TABLE IF NOT EXISTS liquidation_events (
              id INTEGER PRIMARY KEY,account_id TEXT NOT NULL,created_ms INTEGER NOT NULL,evidence TEXT NOT NULL);
            """)
            from .paper_history_cache import install as install_history_versions
            install_history_versions(db)
            # Serialize schema inspection and migration across service starts.
            # Historical positions were 1x; do not reinterpret their exposure.
            db.execute("BEGIN IMMEDIATE")
            if "leverage" not in {r[1] for r in db.execute("PRAGMA table_info(positions)")}:
                db.execute("ALTER TABLE positions ADD COLUMN leverage INTEGER NOT NULL DEFAULT 1")
            if "protection_updated_ms" not in {r[1] for r in db.execute("PRAGMA table_info(positions)")}:
                db.execute("ALTER TABLE positions ADD COLUMN protection_updated_ms INTEGER")
            for table, columns in (("positions", ("take_profit", "stop_loss")),
                                   ("limit_orders", ("take_profit", "stop_loss")),
                                   ("fills", ("exit_reason",))):
                existing = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
                for column in columns:
                    if column not in existing:
                        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")
            for table, columns in {
                'positions': {'exposure_updated_ms': 'INTEGER', 'entry_fees_remaining': 'TEXT'},
                'fills': {'fee_rate': 'TEXT', 'fee_role': 'TEXT', 'clearance_fee': "TEXT NOT NULL DEFAULT '0'"},
                'limit_orders': {'fee_rate': 'TEXT'},
                'quotes': {'mark_price': 'TEXT', 'mark_source_ms': 'INTEGER', 'risk_json': 'TEXT'},
            }.items():
                existing = {r[1] for r in db.execute(f'PRAGMA table_info({table})')}
                for column, definition in columns.items():
                    if column not in existing:
                        db.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')
            for p in db.execute('SELECT * FROM positions WHERE entry_fees_remaining IS NULL').fetchall():
                fees = sum((number(r[0]) for r in db.execute("SELECT fee FROM fills WHERE position_id=? AND action='open'", (p['id'],))), Decimal(0))
                db.execute('UPDATE positions SET entry_fees_remaining=? WHERE id=?',
                           (str(fees * number(p['remaining']) / number(p['quantity'])), p['id']))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def row(db, table, key):
        # Table identifiers are internal constants, never request values.
        row = db.execute(f"SELECT * FROM {table} WHERE id=?", (key,)).fetchone()
        if row is None:
            raise KeyError("账户、持仓或报价不存在")
        return dict(row)

    @staticmethod
    def replay(db, request_id, payload):
        row = db.execute("SELECT * FROM receipts WHERE request_id=?", (request_id,)).fetchone()
        if row:
            old_payload = json.loads(row["payload"])
            if old_payload.get("action") == "open":
                old_payload.setdefault("leverage", 1)
            if old_payload.get("action") in {"open", "limit_open"}:
                old_payload.setdefault("take_profit", None)
                old_payload.setdefault("stop_loss", None)
            if old_payload != payload:
                raise ValueError("请求标识已用于其他操作，请刷新后重试")
            response = json.loads(row["response"])
            if "position" in response:
                response["position"].setdefault("leverage", 1)
                response["fill"].setdefault("leverage", response["position"]["leverage"])
            return response
        return None

    @staticmethod
    def receipt(db, request_id, payload, response):
        db.execute("INSERT INTO receipts VALUES(?,?,?)", (request_id, encoded(payload), encoded(response)))
        return response

    def create_account(self, name, initial_cash, request_id):
        cash = positive(initial_cash)
        payload = dict(action="create", name=name, initial_cash=str(cash))
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = self.replay(db, request_id, payload)
            if previous:
                return previous
            account = dict(id=uuid.uuid4().hex, name=name, initial_cash=str(cash), cash=str(cash), created_ms=clock_ms())
            db.execute("INSERT INTO accounts VALUES(:id,:name,:initial_cash,:cash,:created_ms)", account)
            return self.receipt(db, request_id, payload, account)

    @staticmethod
    def record_quote(db, value):
        quote = dict(value, id=uuid.uuid4().hex)
        bid, ask = positive(quote["bid"]), positive(quote["ask"])
        if bid > ask or not fresh(quote, clock_ms()):
            raise ValueError("行情过期或买卖报价异常，请重新获取")
        quote.update(bid=str(bid), ask=str(ask))
        quote.update(mark_price=quote.get('mark_price'), mark_source_ms=quote.get('mark_source_ms'),
                     risk_json=encoded(quote['risk']) if quote.get('risk') else None)
        db.execute("INSERT INTO quotes(id,symbol,bid,ask,source_ms,received_ms,mark_price,mark_source_ms,risk_json) "
                   "VALUES(:id,:symbol,:bid,:ask,:source_ms,:received_ms,:mark_price,:mark_source_ms,:risk_json)", quote)
        return dict(quote, expires_ms=min(quote["source_ms"], quote["received_ms"]) + QUOTE_TTL_MS)

    def save_quote(self, value):
        with self.connect() as db:
            return self.record_quote(db, value)

    @staticmethod
    def pending_orders(db, account_id, exclude=None):
        return [dict(r) for r in db.execute(
            "SELECT * FROM limit_orders WHERE account_id=? AND status='pending' ORDER BY created_ms,id", (account_id,)) if r["id"] != exclude]

    @staticmethod
    def pending_close_quantity(db, position_id, exclude=None):
        return sum((number(r["quantity"]) for r in db.execute(
            "SELECT id,quantity FROM limit_orders WHERE position_id=? AND action='close' AND status='pending'", (position_id,))
                    if r["id"] != exclude), Decimal(0))

    def symbols(self, account_id=None):
        with self.connect() as db:
            rows = db.execute("SELECT symbol,remaining FROM positions WHERE CAST(remaining AS NUMERIC)>0" + (" AND account_id=?" if account_id else ""),
                              (account_id,) if account_id else ()).fetchall()
        return sorted({r["symbol"] for r in rows if number(r["remaining"]) > 0})

    @staticmethod
    def totals(account, positions, marks, at, orders=()):
        reserved, upl = Decimal(0), Decimal(0)
        complete = True
        missing = set()
        for p in positions:
            remaining, entry = number(p["remaining"]), number(p["entry_price"])
            if remaining == 0:
                continue
            reserved += margin(remaining, entry, p["leverage"])
            mark = marks.get(p["symbol"])
            if not mark or not fresh(mark, at):
                complete = False
                missing.add(p['symbol'])
                continue
            px = number(mark["bid"] if p["side"] == "long" else mark["ask"])
            upl += (px - entry) * remaining * (1 if p["side"] == "long" else -1)
        order_reserved = sum((margin(o["quantity"], o["limit_price"], o["leverage"])
                              + number(o["quantity"]) * number(o["limit_price"]) * number(o.get('fee_rate') or MAKER_FEE)
                              for o in orders if o["status"] == "pending" and o["action"] == "open"), Decimal(0))
        cash = number(account["cash"])
        equity = cash + upl if complete else None
        # Unrealized gains cannot fund new positions; losses reduce buying power.
        available = max(Decimal(0), min(cash, equity) - reserved - order_reserved) if complete else None
        return dict(balance=str(cash), reserved=str(reserved), order_reserved=str(order_reserved), unrealized=str(upl) if complete else None,
                    equity=str(equity) if complete else None, available=str(available) if complete else None,
                    net_realized=str(cash - number(account["initial_cash"])), marks_complete=complete,
                    missing_mark_symbols=sorted(missing))

    def open(self, account_id, side, quantity, quote_id, note, request_id, marks, leverage=1,
             take_profit=None, stop_loss=None):
        qty = positive(quantity)
        leverage = leverage_value(leverage)
        if side not in {"long", "short"}:
            raise ValueError("方向无效")
        payload = dict(action="open", account_id=account_id, side=side, quantity=str(qty), quote_id=quote_id, note=note, leverage=leverage,
                       take_profit=optional_price(take_profit), stop_loss=optional_price(stop_loss))
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = self.replay(db, request_id, payload)
            if previous:
                return previous
            at = clock_ms()
            account = self.row(db, "accounts", account_id)
            quote = self.row(db, "quotes", quote_id)
            symbol_leverage(quote['symbol'], leverage, quote)
            if not fresh(quote, at):
                raise ValueError("报价已过期，请刷新报价并重新确认")
            result = self.open_fill(db, account_id, side, qty, quote, note, at, marks, leverage,
                                    take_profit=take_profit, stop_loss=stop_loss)
            return self.receipt(db, request_id, payload, result)

    def size_all_in(self, account_id, side, quote_id, leverage, marks, limit_price=None,
                    take_profit=None, stop_loss=None):
        """Preserve the legacy all-available sizing contract for older clients."""
        return self._size_entry(account_id, side, quote_id, leverage, marks,
                                limit_price, take_profit, stop_loss)

    def size_by_equity(self, account_id, side, quote_id, leverage, position_multiple, marks,
                       limit_price=None, take_profit=None, stop_loss=None):
        """Owner correction (2026-10-15): size exposure independently of collateral.

        Only current equity from fresh bid/ask marks is used. Target notional is
        equity times position_multiple; leverage only determines margin. Reject
        insufficient available funds instead of silently reducing the position.
        """
        if type(position_multiple) is not int or position_multiple not in POSITION_MULTIPLES:
            raise ValueError("请选择支持的仓位倍数")
        return self._size_entry(account_id, side, quote_id, leverage, marks,
                                limit_price, take_profit, stop_loss, position_multiple)

    def size_by_notional(self, account_id, side, quote_id, leverage, notional_usdt, marks,
                         limit_price=None, take_profit=None, stop_loss=None):
        """Size a fixed USDT exposure using the same collateral and fee checks."""
        return self._size_entry(account_id, side, quote_id, leverage, marks,
                                limit_price, take_profit, stop_loss, notional_usdt=positive(notional_usdt))

    def _size_entry(self, account_id, side, quote_id, leverage, marks, limit_price=None,
                    take_profit=None, stop_loss=None, position_multiple=None, notional_usdt=None):
        """Calculate exposure and validate collateral including the entry fee.

        Only current persisted reservations and fresh bid/ask observations are
        used. Round base quantity down; confirmation rechecks affordability and
        never silently changes the quantity the owner reviewed.
        """
        if side not in {"long", "short"}:
            raise ValueError("方向无效")
        with self.connect() as db:
            db.execute("BEGIN")
            at = clock_ms()
            account = self.row(db, "accounts", account_id)
            quote = self.row(db, "quotes", quote_id)
            if not quick_quote(quote) and (type(leverage) is not int or leverage not in ENTRY_LEVERAGES):
                raise ValueError('请选择支持的杠杆档位')
            symbol_leverage(quote['symbol'], leverage, quote)
            if not fresh(quote, at):
                raise ValueError("报价已过期，请重新获取")
            positions = [dict(r) for r in db.execute("SELECT * FROM positions WHERE account_id=? AND CAST(remaining AS NUMERIC)>0", (account_id,))]
            totals = self.totals(account, positions, marks, at, self.pending_orders(db, account_id))
            if totals["available"] is None:
                raise ValueError("已有持仓的行情不完整，暂不能核算可用资金")
            budget = number(totals["available"])
            if budget <= 0:
                raise ValueError("没有可用资金，请先平仓或撤销开仓挂单")
            px = positive(limit_price) if limit_price is not None else number(quote["ask"] if side == "long" else quote["bid"])
            rate = limit_fee(side, quote, px) if limit_price is not None else TAKER_FEE
            if rate == TAKER_FEE:
                px = number(quote['ask'] if side == 'long' else quote['bid'])
            stops = stop_prices(side, px, take_profit, stop_loss)
            equity = number(totals["equity"])
            if equity <= 0:
                raise ValueError("账户权益不足，无法计算仓位")
            raw = (notional_usdt / px if notional_usdt is not None else equity * position_multiple / px if position_multiple is not None
                   else budget / (px * (Decimal(1) / leverage + rate)))
            if raw > Decimal("1e12"):
                raise ValueError("仓位数量超过模拟范围，请选择其他合约或账户")
            step = Decimal("1e-12")
            if quick_quote(quote):
                instrument = marks[quote['symbol']]['risk']['instrument']
                step = positive(instrument['ctVal'])*positive(instrument['lotSz'])
            qty = (raw/step).to_integral_value(rounding=ROUND_DOWN)*step
            if position_multiple is None and notional_usdt is None and margin(qty, px, leverage) + qty * px * rate > budget:
                qty -= step
            positive(qty)
            self.check_open(db, account_id, quote["symbol"], qty, px, leverage, marks, at, fee_rate=rate, entry_quote=quote)
            collateral, fee = margin(qty, px, leverage), qty * px * rate
            result = dict(quantity=format(qty, "f"), budget=str(budget),
                          margin=str(collateral), fee=str(fee), execution_price=str(px), fee_rate=str(rate), fee_role='maker' if rate == MAKER_FEE else 'taker', sizing="all_available", **stops)
            if position_multiple is not None:
                result.update(sizing="equity_multiple", position_multiple=position_multiple,
                              sizing_equity=str(equity), notional=str(qty * px),
                              available_after=str(budget - collateral - fee))
            if notional_usdt is not None:
                result.update(sizing='fixed_notional', requested_notional=str(notional_usdt),
                              sizing_equity=str(equity), notional=str(qty * px),
                              position_multiple=str(qty * px / equity), available_after=str(budget - collateral - fee))
            return result

    def check_open(self, db, account_id, symbol, qty, px, leverage, marks, at, exclude_order=None, fee_rate=TAKER_FEE, entry_quote=None):
        symbol_leverage(symbol, leverage, entry_quote)
        account = self.row(db, "accounts", account_id)
        positions = [dict(r) for r in db.execute("SELECT * FROM positions WHERE account_id=? AND CAST(remaining AS NUMERIC)>0", (account_id,))]
        active = [p for p in positions if number(p["remaining"]) > 0]
        orders = self.pending_orders(db, account_id, exclude_order)
        opening = [o for o in orders if o["action"] == "open"]
        if len({p['symbol'] for p in active} | {o['symbol'] for o in opening} | {symbol}) > 30:
            raise ValueError("每个账户最多同时交易 30 个合约")
        mark = marks.get(symbol)
        if mark and mark.get('risk'):
            from .paper_desk_risk import tier_for, account_risk
            risk = account_risk(account, active, marks, at)
            if not risk['risk_complete']:
                raise ValueError('持仓风险数据不完整，暂不能增加仓位')
            if risk['breached']:
                raise ValueError('账户已达到强平条件，暂不能增加仓位')
            total = qty + sum((number(p['remaining']) for p in active if p['symbol'] == symbol), Decimal(0))
            total += sum((number(o['quantity']) for o in opening if o['symbol'] == symbol), Decimal(0))
            _, max_leverage = tier_for(mark, total)
            if leverage > max_leverage:
                raise ValueError(f"加仓后超过 OKX 当前持仓档位的杠杆上限 {max_leverage} 倍")
        totals = self.totals(account, positions, marks, at, orders)
        if totals["available"] is None:
            raise ValueError("已有持仓的行情不完整，暂不能核算可用资金")
        if quick_quote(entry_quote):
            from .monday_ticket import exposure_budget
            limits = exposure_budget(symbol, totals, active, opening, marks)
            if qty*px > number(limits['exposure_remaining']):
                raise ValueError('超过总权益仓位额度，请重新预览')
            instrument = (mark or {}).get('risk', {}).get('instrument', {})
            if instrument.get('instId') != symbol:
                raise ValueError('交易所合约规格暂不可用')
            contracts = qty/positive(instrument['ctVal'])
            if leverage > positive(instrument['lever']):
                raise ValueError('超过交易所合约最大杠杆')
            if contracts < positive(instrument['minSz']) or contracts % positive(instrument['lotSz']):
                raise ValueError('数量不符合交易所最小数量或步长')
            if contracts > positive(instrument['maxLmtSz'] if json.loads(entry_quote.get('risk_json') or '{}').get('monday_order_type')=='limit' else instrument['maxMktSz']):
                raise ValueError('数量超过交易所单笔上限')
        if margin(qty, px, leverage) + qty * px * fee_rate > number(totals["available"]):
            raise ValueError("模拟可用资金不足（需覆盖保证金和开仓手续费）")
        return account

    def open_fill(self, db, account_id, side, qty, quote, note, at, marks, leverage, price=None, exclude_order=None,
                  take_profit=None, stop_loss=None, fee_rate=TAKER_FEE):
        """Commit a fill within the caller's transaction; price overrides are limit prices only."""
        px = number(quote["ask"] if side == "long" else quote["bid"]) if price is None else number(price)
        stops = stop_prices(side, px, take_profit, stop_loss)
        account = self.check_open(db, account_id, quote["symbol"], qty, px, leverage, marks, at, exclude_order, fee_rate, entry_quote=quote)
        fee = qty * px * fee_rate
        position = dict(id=uuid.uuid4().hex, account_id=account_id, symbol=quote["symbol"], side=side,
                        quantity=str(qty), remaining=str(qty), entry_price=str(px), realized="0", fees=str(fee),
                        opened_ms=at, closed_ms=None, note=note, leverage=leverage, exposure_updated_ms=at,
                        entry_fees_remaining=str(fee), protection_updated_ms=None, **stops)
        existing = next((dict(p) for p in db.execute('SELECT * FROM positions WHERE account_id=? AND symbol=? AND side=? AND leverage=? AND CAST(remaining AS NUMERIC)>0 ORDER BY opened_ms,id',
                                                   (account_id, quote['symbol'], side, leverage)) if number(p['remaining']) > 0), None)
        if existing:
            remaining = number(existing['remaining'])
            existing.update(entry_price=str((remaining * number(existing['entry_price']) + qty * px) / (remaining + qty)),
                            quantity=str(number(existing['quantity']) + qty), remaining=str(remaining + qty),
                            fees=str(number(existing['fees']) + fee), exposure_updated_ms=at,
                            entry_fees_remaining=str(number(existing['entry_fees_remaining']) + fee))
            if take_profit is not None or stop_loss is not None:
                # An explicitly supplied leg updates the merged position; blank retains it.
                existing.update({key: value for key, value in stops.items() if value is not None})
                existing['protection_updated_ms'] = at
            position = existing
            db.execute('UPDATE positions SET entry_price=:entry_price,quantity=:quantity,remaining=:remaining,fees=:fees,'
                       'exposure_updated_ms=:exposure_updated_ms,entry_fees_remaining=:entry_fees_remaining,'
                       'take_profit=:take_profit,stop_loss=:stop_loss,protection_updated_ms=:protection_updated_ms WHERE id=:id', position)
        else:
            db.execute("INSERT INTO positions(id,account_id,symbol,side,quantity,remaining,entry_price,realized,fees,opened_ms,closed_ms,note,leverage,take_profit,stop_loss,exposure_updated_ms,entry_fees_remaining) "
                       "VALUES(:id,:account_id,:symbol,:side,:quantity,:remaining,:entry_price,:realized,:fees,:opened_ms,:closed_ms,:note,:leverage,:take_profit,:stop_loss,:exposure_updated_ms,:entry_fees_remaining)", position)
        db.execute("UPDATE accounts SET cash=? WHERE id=?", (str(number(account["cash"]) - fee), account_id))
        fill = self.fill(db, position, "open", qty, px, fee, Decimal(0), quote["id"], at, fee_rate=fee_rate)
        return dict(position=position, fill=fill)

    @staticmethod
    def fill(db, position, action, qty, px, fee, pnl, quote_id, at, exit_reason=None, fee_rate=TAKER_FEE, clearance_fee=Decimal(0)):
        fill = dict(id=uuid.uuid4().hex, position_id=position["id"], account_id=position["account_id"],
                    action=action, quantity=str(qty), price=str(px), fee=str(fee), gross_pnl=str(pnl), quote_id=quote_id, created_ms=at,
                    leverage=position["leverage"], exit_reason=exit_reason, fee_rate=str(fee_rate),
                    fee_role='maker' if fee_rate == MAKER_FEE else 'taker', clearance_fee=str(clearance_fee))
        db.execute("INSERT INTO fills(id,position_id,account_id,action,quantity,price,fee,gross_pnl,quote_id,created_ms,exit_reason,fee_rate,fee_role,clearance_fee) "
                   "VALUES(:id,:position_id,:account_id,:action,:quantity,:price,:fee,:gross_pnl,:quote_id,:created_ms,:exit_reason,:fee_rate,:fee_role,:clearance_fee)", fill)
        return fill

    def close(self, position_id, quantity, quote_id, request_id):
        qty = positive(quantity)
        payload = dict(action="close", position_id=position_id, quantity=str(qty), quote_id=quote_id)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = self.replay(db, request_id, payload)
            if previous:
                return previous
            at = clock_ms()
            p = self.row(db, "positions", position_id)
            q = self.row(db, "quotes", quote_id)
            if q["symbol"] != p["symbol"] or not fresh(q, at):
                raise ValueError("报价与持仓不匹配或已过期，请重新获取")
            result = self.close_fill(db, p, qty, q, at)
            return self.receipt(db, request_id, payload, result)

    def size_close(self, position_id, percentage, quote_id):
        """Freeze a percentage of current remaining exposure, using Decimal.

        Existing close reservations are checked, never silently subtracted from
        the percentage basis. 100% preserves the exact stored remainder.
        """
        if type(percentage) is not int or not 1 <= percentage <= 100:
            raise ValueError("平仓比例须为 1 至 100 的整数")
        with self.connect() as db:
            db.execute("BEGIN")
            p, q = self.row(db, "positions", position_id), self.row(db, "quotes", quote_id)
            if q["symbol"] != p["symbol"] or not fresh(q, clock_ms()):
                raise ValueError("报价与持仓不匹配或已过期，请重新获取")
            remaining = number(p["remaining"])
            if remaining <= 0:
                raise ValueError("该持仓已平仓")
            qty = remaining if percentage == 100 else (remaining * percentage / 100).quantize(Decimal("1e-12"), rounding=ROUND_DOWN)
            positive(qty)
            self.check_close(db, p, qty)
            return dict(quantity=str(qty), close_percent=percentage, position_remaining=str(remaining),
                        available_to_close=str(remaining - self.pending_close_quantity(db, position_id)),
                        entry_price=p["entry_price"], side=p["side"], leverage=p["leverage"], sizing="position_percentage")

    def update_protection(self, position_id, take_profit, stop_loss, quote_id, request_id, *, stop_to_entry=False):
        """Amend the remaining position's exits atomically with close/matching.

        Validate relative to current executable bid/ask, allowing a profitable
        stop beyond entry. Clearing both legs needs no market data. Only quotes
        after this amendment may trigger the new settings. The entry shortcut
        resolves the current average and preserves TP under the same write lock;
        it never weakens an existing stop or includes fees in the entry price.
        """
        tp, sl = optional_price(take_profit), optional_price(stop_loss)
        payload = dict(action="update_protection", position_id=position_id,
                       take_profit=tp, stop_loss=sl, quote_id=quote_id)
        if stop_to_entry:
            if tp is not None or sl is not None:
                raise ValueError("移到开仓价不能同时指定其他止盈止损价格")
            payload["stop_to_entry"] = True
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = self.replay(db, request_id, payload)
            if previous:
                return previous
            p = self.row(db, "positions", position_id)
            if number(p["remaining"]) <= 0:
                raise ValueError("该持仓已平仓，无法修改止盈止损")
            if stop_to_entry:
                sign = 1 if p["side"] == "long" else -1
                if p["stop_loss"] is not None and (number(p["stop_loss"]) - number(p["entry_price"])) * sign >= 0:
                    return self.receipt(db, request_id, payload, dict(protection=p, stop_to_entry=True, unchanged=True))
                tp, sl = p["take_profit"], p["entry_price"]
            at = clock_ms()
            if tp is not None or sl is not None:
                if quote_id is None:
                    raise ValueError("设置止盈止损需要新鲜报价")
                q = self.row(db, "quotes", quote_id)
                if q["symbol"] != p["symbol"] or not fresh(q, at):
                    raise ValueError("报价与持仓不匹配或已过期，请重新获取")
                current = number(q["bid"] if p["side"] == "long" else q["ask"])
                sign = 1 if p["side"] == "long" else -1
                if tp is not None and (number(tp) - current) * sign <= 0:
                    raise ValueError("止盈价须" + ("高于" if sign == 1 else "低于") + "当前可平仓价")
                if sl is not None and (number(sl) - current) * sign >= 0:
                    raise ValueError("止损价须" + ("低于" if sign == 1 else "高于") + "当前可平仓价")
            p.update(take_profit=tp, stop_loss=sl, protection_updated_ms=at)
            db.execute("UPDATE positions SET take_profit=:take_profit,stop_loss=:stop_loss,"
                       "protection_updated_ms=:protection_updated_ms WHERE id=:id", p)
            result = dict(protection=p)
            if stop_to_entry:
                result.update(stop_to_entry=True, unchanged=False)
            return self.receipt(db, request_id, payload, result)

    def check_close(self, db, position, qty, exclude_order=None):
        remaining = number(position["remaining"])
        reserved = self.pending_close_quantity(db, position["id"], exclude_order)
        if qty > remaining - reserved:
            raise ValueError("平仓数量超过可用持仓（已有平仓挂单占用的数量需先撤单）")

    def close_fill(self, db, p, qty, q, at, price=None, exclude_order=None, exit_reason=None, fee_rate=TAKER_FEE, clearance_fee=Decimal(0)):
        self.check_close(db, p, qty, exclude_order)
        remaining = number(p["remaining"])
        px = number(q["bid"] if p["side"] == "long" else q["ask"]) if price is None else number(price)
        pnl = (px - number(p["entry_price"])) * qty * (1 if p["side"] == "long" else -1)
        fee = qty * px * fee_rate + clearance_fee
        account = self.row(db, "accounts", p["account_id"])
        p.update(remaining=str(remaining - qty), realized=str(number(p["realized"]) + pnl),
                 fees=str(number(p["fees"]) + fee), closed_ms=at if qty == remaining else None,
                 entry_fees_remaining=str(number(p['entry_fees_remaining']) * (remaining - qty) / remaining))
        db.execute("UPDATE positions SET remaining=:remaining,realized=:realized,fees=:fees,closed_ms=:closed_ms,entry_fees_remaining=:entry_fees_remaining WHERE id=:id", p)
        db.execute("UPDATE accounts SET cash=? WHERE id=?", (str(number(account["cash"]) + pnl - fee), account["id"]))
        fill = self.fill(db, p, "close", qty, px, fee, pnl, q["id"], at, exit_reason, fee_rate, clearance_fee)
        return dict(position=p, fill=fill)

    def risk_complete(self, marks):
        """Check active account risk without constructing the UI or reading history."""
        from .paper_desk_risk import account_risk
        at = clock_ms()
        with self.connect() as db:
            db.execute('BEGIN')
            accounts = [dict(r) for r in db.execute('SELECT * FROM accounts')]
            positions = [dict(r) for r in db.execute('SELECT * FROM positions WHERE CAST(remaining AS NUMERIC)>0')]
        return all(account_risk(a, [p for p in positions if p['account_id']==a['id']], marks, at)['risk_complete']
                   for a in accounts)

    def snapshot(self, marks, account_id=None, live=False):
        at = clock_ms()
        with self.connect() as db:
            db.execute("BEGIN")
            accounts = [dict(r) for r in db.execute("SELECT * FROM accounts ORDER BY created_ms,id")]
            if live:
                account_id = account_id or (accounts[0]['id'] if accounts else None)
                if account_id and not any(a['id'] == account_id for a in accounts):
                    raise KeyError('账户不存在')
                positions = [dict(r) for r in db.execute("SELECT * FROM positions WHERE account_id=? AND CAST(remaining AS NUMERIC)>0 ORDER BY opened_ms DESC,id", (account_id,))]
                orders = [dict(r) for r in db.execute("SELECT * FROM limit_orders WHERE account_id=? AND status='pending' ORDER BY created_ms DESC,id", (account_id,))]
                fills, summaries = [], {}
            else:
                positions = [dict(r) for r in db.execute("SELECT * FROM positions ORDER BY opened_ms DESC,id")]
                orders = [dict(r) for r in db.execute("SELECT * FROM limit_orders ORDER BY created_ms DESC,id")]
                fills = [dict(r) for r in db.execute("SELECT f.*,p.symbol,p.side,p.leverage,o.id AS order_id FROM fills f JOIN positions p ON p.id=f.position_id LEFT JOIN limit_orders o ON o.fill_id=f.id ORDER BY f.created_ms DESC,f.rowid DESC LIMIT 500")]
                from .paper_desk_analytics import closed_position_summaries
                summaries = closed_position_summaries(db)
        for p in positions:
            p.update(summaries.get(p['id'], {}))
            mark = marks.get(p["symbol"])
            valid = mark is not None and fresh(mark, at)
            px = number(mark["bid"] if p["side"] == "long" else mark["ask"]) if valid else None
            p["mark"] = str(px) if px is not None else None
            p["mark_ms"] = mark["source_ms"] if valid else None
            p["unrealized"] = str((px - number(p["entry_price"])) * number(p["remaining"]) * (1 if p["side"] == "long" else -1)) if valid else None
            p["net_realized"] = str(number(p["realized"]) - number(p["fees"]))
            p["pending_close_quantity"] = str(sum((number(o["quantity"]) for o in orders if o["status"] == "pending" and o["action"] == "close" and o["position_id"] == p["id"]), Decimal(0)))
            p["available_to_close"] = str(number(p["remaining"]) - number(p["pending_close_quantity"]))
            p["initial_margin"] = str(margin(p["quantity"], p["entry_price"], p["leverage"]))
            p["reserved_margin"] = str(margin(p["remaining"], p["entry_price"], p["leverage"]))
            p['notional_usdt'] = str(number(p['remaining']) * px) if px is not None else None
            p['available_usdt'] = str(number(p['available_to_close']) * px) if px is not None else None
        for a in accounts:
            if live and a["id"] != account_id:
                continue
            a.update(self.totals(a, [p for p in positions if p["account_id"] == a["id"]], marks, at, [o for o in orders if o["account_id"] == a["id"]]))
            from .paper_desk_risk import account_risk
            risk = account_risk(a, [p for p in positions if p['account_id'] == a['id']], marks, at)
            a.update(risk)
            for p in positions:
                if p['account_id'] == a['id']:
                    p['liquidation_price'] = risk['liquidation_prices'].get(p['symbol'])
                    p['risk_complete'] = risk['risk_complete']
                    p['mark_price'] = marks.get(p['symbol'], {}).get('mark_price') if risk['risk_complete'] else None
        return dict(accounts=accounts, positions=positions, fills=fills, orders=orders, now_ms=at, fee_rate=str(FEE),
                    active_account_id=account_id, view="live" if live else "full",
                    maker_fee_rate=str(MAKER_FEE), taker_fee_rate=str(TAKER_FEE), margin_mode='cross',
                    production_eligible=False, mode="local_paper", leverage_limits=dict(min=1, max=100, altcoin_max=20, default=100))

    def records(self, account_id, kind, limit=50, offset=0):
        """Page historical records while aggregating each complete position lifecycle."""
        if kind not in {'closed', 'fills'} or not 1 <= limit <= 100 or offset < 0:
            raise ValueError('记录分页参数无效')
        with self.connect() as db:
            db.execute('BEGIN')
            self.row(db, 'accounts', account_id)
            if kind == 'closed':
                predicate = 'account_id=? AND CAST(remaining AS NUMERIC)=0'
                total = db.execute('SELECT count(*) FROM positions WHERE '+predicate, (account_id,)).fetchone()[0]
                items = [dict(r) for r in db.execute('SELECT * FROM positions WHERE '+predicate+
                         ' ORDER BY closed_ms DESC,id LIMIT ? OFFSET ?', (account_id, limit, offset))]
                from .paper_desk_analytics import closed_position_summaries
                summaries = closed_position_summaries(db, [p['id'] for p in items])
                for p in items:
                    p.update(summaries.get(p['id'], {}))
                    p['net_realized'] = str(number(p['realized'])-number(p['fees']))
            else:
                total = db.execute('SELECT count(*) FROM fills WHERE account_id=?', (account_id,)).fetchone()[0]
                items = [dict(r) for r in db.execute('SELECT f.*,p.symbol,p.side,p.leverage,o.id AS order_id '
                         'FROM fills f JOIN positions p ON p.id=f.position_id LEFT JOIN limit_orders o ON o.fill_id=f.id '
                         'WHERE f.account_id=? ORDER BY f.created_ms DESC,f.rowid DESC LIMIT ? OFFSET ?',
                         (account_id, limit, offset))]
        return dict(account_id=account_id, kind=kind, items=items, total=total, offset=offset,
                    limit=limit, has_more=offset+len(items)<total)

    def export(self, account_id):
        with self.connect() as db:
            db.execute("BEGIN")
            account = self.row(db, "accounts", account_id)
            positions = [dict(r) for r in db.execute("SELECT * FROM positions WHERE account_id=?", (account_id,))]
            orders = [dict(r) for r in db.execute("SELECT * FROM limit_orders WHERE account_id=? ORDER BY created_ms,id", (account_id,))]
            fills = [dict(r) for r in db.execute("SELECT f.*,p.leverage,o.id AS order_id FROM fills f JOIN positions p ON p.id=f.position_id LEFT JOIN limit_orders o ON o.fill_id=f.id "
                                                "WHERE f.account_id=? ORDER BY f.created_ms,f.rowid", (account_id,))]
            quotes = [dict(r) for r in db.execute("SELECT * FROM quotes WHERE id IN (SELECT quote_id FROM fills WHERE account_id=? UNION SELECT quote_id FROM limit_orders WHERE account_id=? UNION SELECT trigger_quote_id FROM limit_orders WHERE account_id=?)", (account_id, account_id, account_id))]
            position_ids = {p["id"] for p in positions}
            updates = []
            for row in db.execute("SELECT * FROM receipts"):
                payload = json.loads(row["payload"])
                if payload.get("action") == "update_protection" and payload["position_id"] in position_ids:
                    response = json.loads(row["response"])
                    update = dict(payload, request_id=row["request_id"], updated_ms=response["protection"]["protection_updated_ms"])
                    if payload.get("stop_to_entry"):
                        update.update(take_profit=response["protection"]["take_profit"],
                                      stop_loss=response["protection"]["stop_loss"], unchanged=response.get("unchanged", False))
                    updates.append(update)
            recorded = {q["id"] for q in quotes}
            for update in updates:
                if update["quote_id"] and update["quote_id"] not in recorded:
                    quotes.append(self.row(db, "quotes", update["quote_id"]))
                    recorded.add(update["quote_id"])
            samples = [dict(r) for r in db.execute('SELECT * FROM equity_samples WHERE account_id=? ORDER BY bucket_ms', (account_id,))]
            liquidations = [dict(r) for r in db.execute('SELECT * FROM liquidation_events WHERE account_id=? ORDER BY created_ms,id', (account_id,))]
            journals = [dict(r) for r in db.execute('SELECT j.* FROM position_journals j JOIN positions p ON p.id=j.position_id '
                        'WHERE p.account_id=? ORDER BY p.opened_ms,p.id', (account_id,))]
            from .paper_desk_images import export_images
            journal_images = export_images(db, account_id)
        return dict(schema_version=8, mode="local_paper", production_eligible=False, fee_rate=str(FEE),
                    maker_fee_rate=str(MAKER_FEE), taker_fee_rate=str(TAKER_FEE), equity_samples=samples, liquidation_events=liquidations,
                    account=account, positions=positions, fills=fills, quotes=quotes, orders=orders,
                    protection_updates=updates, journals=journals, journal_images=journal_images,
                    fill_policy="confirmed-public-bid-ask-snapshot-15s-v1", leverage_policy="per_position_fixed_at_entry",
                    limit_fill_policy="immediate-cross-taker-or-post-submission-touch-maker-v2",
                    stop_fill_policy="post-entry-fresh-exit-bid-ask-full-remaining-close-v1",
                    liquidation_policy='cross-mark-equity-vs-tier-maintenance-plus-taker-full-account-v1',
                    exclusions=["funding", "depth_slippage", "staged_liquidation", "insurance_fund", "adl"])
