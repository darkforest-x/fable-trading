"""Durable single-account paper ledger with SQLite BEGIN IMMEDIATE serialization.

The transaction contains risk checks, reservations, fills, R-stage advancement,
cash accounting and idempotency receipts. There is deliberately no exchange
write adapter: a paper receipt must never masquerade as a live protection order.
"""
import copy
import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo
from .policy import (Policy, Rejected, dec, positive, floor_step, ceil_step, plan,
                     json_number, normalize_take_profit_overrides, take_profit_targets,
                     price_on_step, resolve_take_profit_targets)
from .closing import close_fraction, close_quantity
from .scaling import merged_position, remaining_risk


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


class Book:
    def __init__(self, path, clock=time.time, initial_equity='5000'):
        self.path, self.clock = Path(path), clock
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            db.executescript('''
              PRAGMA journal_mode=WAL;
              CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS journal (seq INTEGER PRIMARY KEY AUTOINCREMENT, time REAL NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS receipts (key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, body TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS entry_refunds (order_id TEXT PRIMARY KEY, entry_day TEXT NOT NULL, time REAL NOT NULL);
            ''')
            state = dict(version=0, cash=str(positive(initial_equity)), policy=None,
                policy_revision=0, policy_proposal=None, daily=None, loss_streak=0,
                cooldown_until=0, pending=[], positions=[], closed=[], quotes={},
                instruments={}, quote_error='行情尚未就绪', heartbeat=0, mode='paper')
            db.execute('INSERT OR IGNORE INTO state VALUES(1,?)', (encoded(state),))
        self.path.chmod(0o600)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.execute('PRAGMA busy_timeout=15000')
        return db

    @contextmanager
    def transaction(self):
        db = self.connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            state = json.loads(db.execute('SELECT body FROM state WHERE id=1').fetchone()[0])
            yield db, state
            state['version'] += 1
            db.execute('UPDATE state SET body=? WHERE id=1', (encoded(state),))
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def read_transaction(self):
        """Read one committed WAL revision without waiting for a ledger writer.

        Display data only: execution continues to use BEGIN IMMEDIATE and the
        fresh account/market guards. Never promote this view into a command.
        """
        db = self.connect()
        try:
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            state = json.loads(db.execute('SELECT body FROM state WHERE id=1').fetchone()[0])
            yield db, state
        finally:
            db.close()

    def event(self, db, kind, body):
        db.execute('INSERT INTO journal(time,kind,body) VALUES(?,?,?)', (self.clock(), kind, encoded(body)))

    def refund_entry(self, db, state, order):
        """Refund one zero-fill opening on its original Beijing calendar day.

        The primary-key receipt makes repeated cancellation/reconciliation
        idempotent. A later-day cancellation never edits the new day's quota.
        Callers must use this only after establishing that the order had no
        fill; partial or completed fills are never refunded.
        """
        identity = order.get('id')
        if not isinstance(identity, str) or not identity:
            return False
        self.day(state)
        entry_day = order.get('entry_day')
        if not entry_day and order.get('created') is not None:
            try:
                entry_day = datetime.fromtimestamp(float(order['created']), ZoneInfo('Asia/Shanghai')).date().isoformat()
            except (TypeError, ValueError, OverflowError, OSError):
                return False
        if not entry_day or entry_day != state['daily']['key']:
            return False
        inserted = db.execute('INSERT OR IGNORE INTO entry_refunds(order_id,entry_day,time) VALUES(?,?,?)',
                              (identity, entry_day, self.clock())).rowcount
        if inserted != 1:
            return False
        previous = state['daily']['entries']
        state['daily']['entries'] = max(0, previous - 1)
        self.event(db, 'entry_quota_refunded', dict(id=identity, entry_day=entry_day,
            entries_before=previous, entries_after=state['daily']['entries']))
        return previous > 0

    def policy(self, state):
        if state['policy'] is None:
            raise Rejected('规则尚未配置，禁止开仓')
        return Policy.parse(state['policy'])

    def configure(self, policy):
        """Local operator only. No mobile API can change or reset policy/ledger."""
        policy.validate()
        with self.transaction() as (db, state):
            if state['policy'] is None:
                state['policy'] = policy.document()
                state['policy_revision'] = 1
                self.event(db, 'policy_initialized', state['policy'])
            elif encoded(state['policy']) != encoded(policy.document()):
                if state['policy_proposal'] is not None:
                    raise Rejected('已有待生效规则，不能覆盖其等待期')
                state['policy_proposal'] = dict(policy=policy.document(),
                    effective_after=self.clock() + self.policy(state).policy_change_delay_seconds)
                self.event(db, 'policy_proposed', state['policy_proposal'])

    def activate_policy_now(self, policy):
        """Explicit local owner override; preserve all risk history and counters.

        Used only for an owner-authorized policy activation, never exposed as a
        phone endpoint. Existing positions/uncertain orders must first settle.
        """
        policy.validate()
        if hasattr(self, 'outbox') and self.outbox.unresolved():
            raise Rejected('仍有待核对请求，暂不能立即切换规则')
        with self.transaction() as (db, state):
            if state['positions'] or state['pending']:
                raise Rejected('仍有持仓或挂单，暂不能立即切换规则')
            proposal = state['policy_proposal']
            if proposal and encoded(proposal['policy']) != encoded(policy.document()):
                raise Rejected('待生效规则与本次批准的规则不一致')
            if encoded(state['policy']) != encoded(policy.document()):
                state['policy'] = policy.document()
                state['policy_revision'] += 1
            state['policy_proposal'] = None
            self.event(db, 'policy_owner_activated', dict(revision=state['policy_revision'],
                history_preserved=True))

    def tighten_margin_caps(self, major, alt):
        """Owner-only tightening for new entries; never alter an existing exit plan.

        Missing legacy caps mean unbounded, not the new default. This operation
        only adds/reduces the two collateral ceilings and invalidates previews.
        It is deliberately unavailable to the mobile API.
        """
        from dataclasses import replace
        with self.transaction() as (db, state):
            current = self.policy(state)
            updated = replace(current, major_margin_fraction=str(major),
                              alt_margin_fraction=str(alt)).validate()
            if state['pending'] or state['policy_proposal']:
                raise Rejected('有挂单或待生效规则，不能直接收紧保证金额度')
            for field in ('major_margin_fraction', 'alt_margin_fraction'):
                previous = getattr(current, field)
                if previous is not None and dec(getattr(updated, field)) > dec(previous):
                    raise Rejected('此操作只能收紧保证金额度')
            if encoded(updated.document()) != encoded(current.document()):
                state['policy'] = updated.document()
                state['policy_revision'] += 1
                self.event(db, 'margin_caps_owner_tightened', dict(
                    revision=state['policy_revision'], major=str(major), alt=str(alt),
                    existing_exit_plans_preserved=True))

    def separate_major_allowances(self):
        """Explicit owner-only migration of entry pools, never an exchange write.

        Existing position/intent exit plans and all discipline history remain
        untouched. A revision change invalidates previously reviewed previews.
        """
        from dataclasses import replace
        with self.transaction() as (db, state):
            current = self.policy(state)
            if current.major_allowance_scope == 'independent':
                return
            if state['policy_proposal'] is not None:
                raise Rejected('有待生效规则，请先核对其额度范围')
            state['policy'] = replace(current, major_allowance_scope='independent').validate().document()
            state['policy_revision'] += 1
            self.event(db, 'major_allowances_owner_separated', dict(
                revision=state['policy_revision'], existing_exit_plans_preserved=True,
                history_preserved=True))

    def quote(self, state, symbol):
        q = state['quotes'].get(symbol)
        if not q or not 0 <= self.clock() - q['time'] <= self.policy(state).quote_max_age_seconds:
            raise Rejected('行情缺失或已过期，等待恢复')
        return q

    def activate_streak_cooldown(self):
        """Owner-requested migration; keep counters and existing position exit plans.

        Reconstruct cooldown eligibility from complete settled-trade history,
        including a completed streak followed by a winning trade. Never clear
        a valid three-loss cooldown merely because today's streak was reset.
        """
        from dataclasses import replace
        with self.transaction() as (db, state):
            current = self.policy(state)
            if current.cooldown_after_losses == current.loss_streak:
                return
            if state['pending'] or state['policy_proposal']:
                raise Rejected('有未完成委托或待生效规则，暂不能切换冷静期')
            streak, eligible = 0, 0
            for trade in sorted(state['closed'], key=lambda x: x['closed']):
                streak = streak + 1 if dec(trade['pnl']) < 0 else 0
                if streak >= current.loss_streak:
                    eligible = max(eligible, trade['closed'] + current.loss_cooldown_seconds)
            state['cooldown_until'] = min(state['cooldown_until'], eligible)
            state['policy'] = replace(current, cooldown_after_losses=current.loss_streak).document()
            state['policy_revision'] += 1
            self.event(db, 'streak_cooldown_owner_activated', dict(revision=state['policy_revision'], cooldown_until=state['cooldown_until']))

    def settle_discipline(self, state, pnl, closed):
        """Account discipline uses the active rule; R/TP keep each entry's rule."""
        p = self.policy(state)
        state['loss_streak'] = state['loss_streak'] + 1 if dec(pnl) < 0 else 0
        if state['loss_streak'] >= p.cooldown_after_losses:
            state['cooldown_until'] = max(state['cooldown_until'], closed + p.loss_cooldown_seconds)

    def activate_profit_entry_bonus(self):
        """Explicit local owner rule: one extra entry per net winning full close.

        Count today's settled trades, including those already closed today.
        Preserve counters, sticky daily locks, cooldowns and every exit plan.
        """
        from dataclasses import replace
        with self.transaction() as (db, state):
            current = self.policy(state)
            if current.profit_entry_bonus == 1:
                return
            if state['pending'] or state['policy_proposal']:
                raise Rejected('有未完成委托或待生效规则，暂不能调整奖励次数')
            state['policy'] = replace(current, profit_entry_bonus=1).document()
            state['policy_revision'] += 1
            self.event(db, 'profit_entry_bonus_owner_activated', dict(revision=state['policy_revision']))

    def entry_bonus(self, state):
        """Derive credits from unique whole trades, never partial fills or reads.

        Only realized net PnL after costs in the current Beijing calendar day
        qualifies. Exchange settlement received after midnight stays attributed
        to its actual close day. Repeated snapshots/restarts cannot mint credits.
        """
        p = self.policy(state)
        if not p.profit_entry_bonus or not state['daily']:
            return 0
        day = state['daily']['key']
        winners = {trade['id'] for trade in state['closed']
            if trade.get('id') and dec(trade.get('remaining', '0')) == 0
            and dec(trade['pnl']) > 0
            and datetime.fromtimestamp(trade['closed'], ZoneInfo('Asia/Shanghai')).date().isoformat() == day}
        return len(winners) * p.profit_entry_bonus

    def entry_limit(self, state):
        return self.policy(state).max_entries + self.entry_bonus(state)

    def equity(self, state):
        value = dec(state['cash'])
        for pos in state['positions']:
            q = state['quotes'].get(pos['symbol'])
            mark = dec(q['last']) if q else dec(pos['entry'])
            value += (mark - dec(pos['entry'])) * dec(pos['remaining']) * pos['direction']
        return value

    def day(self, state):
        key = datetime.fromtimestamp(self.clock(), ZoneInfo('Asia/Shanghai')).date().isoformat()
        if state['daily'] is None or state['daily']['key'] != key:
            previous = state['daily']
            state['daily'] = dict(key=key, start_equity=str(self.equity(state)), entries=0, locked_reason=None)
            self.begin_day(state)
            # A completed loss streak starts a new daily streak after midnight;
            # the independently persisted 24h loss cooldown is never cleared.
            if previous and state['policy'] and state['loss_streak'] >= self.policy(state).loss_streak:
                state['loss_streak'] = 0
        if state['policy']:
            p = self.policy(state)
            if state['loss_streak'] >= p.loss_streak:
                state['daily']['locked_reason'] = state['daily']['locked_reason'] or '连续亏损停手'
            start = dec(state['daily']['start_equity'])
            if start <= 0 or self.daily_pnl(state) <= -start * dec(p.daily_loss_fraction):
                state['daily']['locked_reason'] = state['daily']['locked_reason'] or '当日净值亏损停手'

    def begin_day(self, state):
        """Subclass hook for a source-scoped daily valuation baseline."""

    def daily_pnl(self, state):
        return self.equity(state) - dec(state['daily']['start_equity'])

    def entry_guard(self, state, exclude_pending=None, add_to=None):
        p = self.policy(state)
        self.day(state)
        if state['daily']['locked_reason']:
            raise Rejected(state['daily']['locked_reason'])
        if self.clock() < state['cooldown_until']:
            raise Rejected(f'连续亏损 {p.cooldown_after_losses} 笔后的 24 小时冷静期内，禁止新开仓')
        if exclude_pending is None and state['daily']['entries'] >= self.entry_limit(state):
            raise Rejected('今日开仓次数已用完')
        pending = [x for x in state['pending'] if x['id'] != exclude_pending]
        scale_orders = [x for x in pending if (x.get('plan') or {}).get('add_to')]
        position_count = len(state['positions']) + len(pending) - len(scale_orders)
        if add_to is not None and not any(pos['id'] == add_to for pos in state['positions']):
            raise Rejected('加仓目标持仓不存在，暂停加仓')
        if add_to is None and position_count >= p.max_positions:
            raise Rejected('持仓与挂单数量已达上限')
        for pos in state['positions']:
            self.quote(state, pos['symbol'])
        return pending

    def reserved(self, state, exclude_pending=None):
        result = Decimal(0)
        for pos in state['positions']:
            result += dec(pos['margin']) * dec(pos['remaining']) / dec(pos['quantity'])
        result += sum((dec(x['plan']['margin']) for x in state['pending'] if x['id'] != exclude_pending), Decimal(0))
        return result

    def exposure_quantity_limit(self, state, symbol, entry, side, exclude_pending=None):
        """Reserve gross exposure within the persisted policy's allowance pool.

        Each independent pool must fit equity after normalized exposure,
        entry fees and adverse entry-to-mark loss. Legacy policies retain
        their shared portfolio pool. Orders and positions use the same pool.
        Market moves can subsequently exceed the cap; they never grant a new
        entry permission or trigger an unrequested automatic liquidation.
        """
        p = self.policy(state)
        cap = p.exposure_cap(symbol)
        if cap is None:
            return None

        def unit_reservation(sym, price, direction):
            mark = positive(self.quote(state, sym)['last'])
            loss = max(Decimal(0), (price - mark) * direction)
            return max(price, mark) / p.exposure_cap(sym) + price * dec(p.fee_rate) + loss

        used = Decimal(0)
        for pos in state['positions']:
            if p.allowance_pool(pos['symbol'], exposure=True) != p.allowance_pool(symbol, exposure=True):
                continue
            mark = positive(self.quote(state, pos['symbol'])['last'])
            used += mark * dec(pos['remaining']) / p.exposure_cap(pos['symbol'])
        for order in state['pending']:
            if order['id'] != exclude_pending and p.allowance_pool(order['symbol'], exposure=True) == p.allowance_pool(symbol, exposure=True):
                used += dec(order['plan']['quantity']) * unit_reservation(
                    order['symbol'], dec(order['plan']['entry']), 1 if order['side'] == 'long' else -1)
        remaining = max(Decimal(0), self.equity(state) - used)
        return remaining / unit_reservation(symbol, entry, 1 if side == 'long' else -1)

    def margin_quantity_limit(self, state, symbol, entry, leverage, exclude_pending=None):
        """Reserve pool collateral at current/entry prices; display is not authority."""
        p = self.policy(state)
        cap = p.margin_cap(symbol)
        if cap is None:
            return None
        used = Decimal(0)
        for pos in state['positions']:
            if p.allowance_pool(pos['symbol']) == p.allowance_pool(symbol):
                mark = positive(self.quote(state, pos['symbol'])['last'])
                used += max(mark, dec(pos['entry'])) * dec(pos['remaining']) / positive(pos['leverage'])
        for order in state['pending']:
            if order['id'] != exclude_pending and p.allowance_pool(order['symbol']) == p.allowance_pool(symbol):
                used += dec(order['plan']['margin'])
        return max(Decimal(0), self.equity(state) * cap - used) * leverage / entry

    def quantity_limit(self, state, symbol, entry, side, leverage, exclude_pending=None):
        limits = [self.exposure_quantity_limit(state, symbol, entry, side, exclude_pending),
                  self.margin_quantity_limit(state, symbol, entry, leverage, exclude_pending)]
        return min(x for x in limits if x is not None) if any(x is not None for x in limits) else None

    def remaining_exposure_notional(self, state, symbol):
        """Raw gross-notional room in this App-managed allowance pool.

        Open positions reserve mark notional; pending orders reserve the larger
        of mark and reviewed entry. External account activity is intentionally
        not part of the App's allocation ledger.
        """
        p = self.policy(state)
        pool, cap = p.allowance_pool(symbol, exposure=True), p.exposure_cap(symbol)
        if cap is None:
            return None
        used = Decimal(0)
        for pos in state['positions']:
            if p.allowance_pool(pos['symbol'], exposure=True) == pool:
                mark = positive(self.quote(state, pos['symbol'])['last'])
                used += mark * dec(pos['remaining'])
        for order in state['pending']:
            if p.allowance_pool(order['symbol'], exposure=True) == pool:
                mark = positive(self.quote(state, order['symbol'])['last'])
                entry = positive(order['plan']['entry'])
                used += max(mark, entry) * dec(order['plan']['quantity'])
        return max(Decimal(0), self.equity(state) * cap - used)

    def available_entry_funds(self, state):
        return max(Decimal(0), self.equity(state) - self.reserved(state))

    def available_entry_quantity_limit(self, state, entry, leverage, unit_cost):
        """Quantity ceiling for collateral plus the full modeled cost reserve."""
        available = self.available_entry_funds(state)
        per_unit = positive(entry) / positive(leverage) + dec(unit_cost)
        return available / per_unit

    @staticmethod
    def scale_parent_snapshot(pos):
        """Fields that change the scale-in baseline or its safe fill limits."""
        keys = ('id', 'symbol', 'side', 'direction', 'quantity', 'remaining',
                'entry', 'original_stop', 'stop', 'risk', 'margin', 'leverage',
                'entry_fee', 'breakeven', 'stages', 'take_profit_overrides',
                'take_profit_prices', 'policy_revision')
        return {key: copy.deepcopy(pos[key]) for key in keys if key in pos}

    @staticmethod
    def scale_target(state, request):
        symbol, side = request['symbol'], request['side']
        positions = [pos for pos in state['positions'] if pos['symbol'] == symbol]
        pending = [order for order in state['pending'] if order['symbol'] == symbol]
        if pending:
            raise Rejected('同币种已有挂单，不允许重复下单')
        requested_id = request.get('add_to')
        if len(positions) > 1:
            raise Rejected('同币种存在多个持仓，暂停加仓')
        pos = positions[0] if positions else None
        if requested_id is not None and (pos is None or pos['id'] != requested_id):
            raise Rejected('加仓目标持仓不存在，暂停加仓')
        if pos is None:
            if requested_id is not None:
                raise Rejected('加仓目标持仓不存在，暂停加仓')
            return None
        if pos['side'] != side:
            raise Rejected('已有反向持仓，不允许反向加仓')
        return pos

    def inherited_leverage(self, state, symbol):
        return None

    def preview_in(self, state, request, reviewed_plan=None, leverage_override=None,
                   extra_quantity_limit=None):
        if set(request) - {'sizing_mode', 'add_to', 'take_profits'} != {'symbol', 'side', 'kind', 'price', 'stop', 'fraction', 'leverage'}:
            raise Rejected('订单字段不匹配；仓位与权益必须由服务端计算')
        symbol, side, kind = request['symbol'], request['side'], request['kind']
        if symbol not in state['instruments'] or not state['instruments'][symbol].get('tradable', True) or kind not in ('market', 'limit'):
            raise Rejected('不支持的合约或订单类型')
        if side not in ('long', 'short'):
            raise Rejected('方向无效')
        p = self.policy(state)
        sizing_mode = request.get('sizing_mode')
        if sizing_mode is None:
            if p.entry_sizing == 'exposure':
                raise Rejected('开仓规则已更新，请更新 App 后重新开单')
            sizing_mode = 'risk'
        if p.entry_sizing == 'exposure' and sizing_mode != 'exposure':
            raise Rejected('当前规则要求按剩余名义额度计算仓位')
        if p.entry_sizing == 'margin' and sizing_mode == 'exposure':
            raise Rejected('旧规则不支持名义额度仓位，请按旧规则重新预览')
        candidate=next((pos for pos in state['positions'] if pos['symbol']==symbol),None)
        self.entry_guard(state,add_to=candidate['id'] if candidate else None)
        target = self.scale_target(state, request)
        add_to = target['id'] if target else None
        if target and (dec(request['stop']) != dec(target['stop']) or
                       (p.entry_sizing == 'margin' and dec(request['leverage']) != dec(target['leverage']))):
            raise Rejected('加仓的止损和杠杆必须与现有持仓一致，请更新计划')
        self.entry_guard(state, add_to=add_to)
        q = self.quote(state, symbol)
        direction = 1 if side == 'long' else -1
        entry = positive(request['price'], '委托价格') if kind == 'limit' else positive(q['ask'] if side == 'long' else q['bid']) * (1 + direction * dec(p.slippage_rate))
        if kind == 'limit' and not price_on_step(entry, state['instruments'][symbol]['tick'], '委托价格'):
            raise Rejected('委托价格不符合最小价格单位')
        stop = positive(target['stop'], '止损价格') if target else positive(request['stop'], '初始止损价格')
        inherited = self.inherited_leverage(state, symbol)
        if leverage_override is not None:
            leverage = dec(leverage_override)
        elif target:
            leverage = dec(target['leverage']) if p.entry_sizing == 'exposure' else target['leverage']
        elif p.entry_sizing == 'exposure':
            leverage = p.target_leverage(symbol, state['instruments'][symbol], inherited)
        else:
            leverage = request['leverage']
        limit = self.quantity_limit(state, symbol, entry, side, leverage)
        distance = (entry - stop) * direction
        unit_cost = entry * (2 * dec(p.fee_rate) + dec(p.slippage_rate))
        if p.entry_sizing == 'exposure':
            collateral_limit = self.available_entry_quantity_limit(state, entry, leverage, unit_cost)
            limit = min(limit, collateral_limit) if limit is not None else collateral_limit
        if extra_quantity_limit is not None:
            limit = min(limit, dec(extra_quantity_limit)) if limit is not None else dec(extra_quantity_limit)
        if target:
            remaining_budget = max(Decimal(0), self.equity(state) * dec(p.risk_fraction) - remaining_risk(target, p))
            distance = (entry - stop) * direction
            unit_risk = distance + entry * (2 * dec(p.fee_rate) + dec(p.slippage_rate))
            if distance <= 0 or unit_risk <= 0:
                raise Rejected('当前价格无法在现有止损下加仓')
            # The entry fee also reduces equity. Include its risk-fraction
            # impact and any adverse entry-to-mark loss when reserving size.
            mark = positive(q['last'])
            immediate_loss = max(Decimal(0), (entry - mark) * direction)
            budget_per_unit = unit_risk + (entry * dec(p.fee_rate) + immediate_loss) * dec(p.risk_fraction)
            remaining_quantity_limit = remaining_budget / budget_per_unit
            limit = min(remaining_quantity_limit, limit) if limit is not None else remaining_quantity_limit
        if reviewed_plan is not None:
            if target:
                snapshot = self.scale_parent_snapshot(target)
                if reviewed_plan.get('add_to') != target['id'] or reviewed_plan.get('parent_snapshot') != snapshot:
                    raise Rejected('加仓目标持仓已变化，请重新预览')
            distance = (entry - stop) * direction
            unit_risk = distance + unit_cost
            if distance <= 0:
                raise Rejected('行情已越过止损价，不能开仓')
            bounds = [dec(reviewed_plan['quantity']), dec(reviewed_plan['notional']) / entry,
                      dec(reviewed_plan['margin']) * leverage / entry, dec(reviewed_plan['risk']) / unit_risk]
            limit = min(bounds + ([limit] if limit is not None else []))
        result = plan(p, self.equity(state), entry, stop, side,
                      request['fraction'], leverage, state['instruments'][symbol],
                      limit, sizing_mode, inherited,
                      self.remaining_exposure_notional(state, symbol)
                      if sizing_mode == 'exposure' else None)
        tick = positive(state['instruments'][symbol]['tick'])
        override_input = request.get('take_profits')
        if target and override_input is None:
            override_input = target.get('take_profit_overrides')
        if target:
            projected = merged_position(target, result['quantity'], entry,
                dec(result['quantity']) * entry * dec(p.fee_rate), p)
            target_entry, target_quantity = projected['entry'], projected['quantity']
            target_risk, target_fee = projected['risk'], projected['entry_fee']
        else:
            target_entry, target_quantity = entry, result['quantity']
            target_risk = result['risk']
            target_fee = entry * dec(result['quantity']) * dec(p.fee_rate)
        overrides = normalize_take_profit_overrides(override_input, p, target_entry, side, tick)
        result['take_profit_overrides'] = overrides
        result['take_profit_prices'] = take_profit_targets(
            target_entry, target_quantity, target_risk, target_fee, p, side, tick, overrides)
        if target:
            result['add_to'] = target['id']
            result['parent_snapshot'] = self.scale_parent_snapshot(target)
            result['merged_budget'] = str(self.equity(state) * dec(p.risk_fraction))
            result['merged_risk'] = str(remaining_risk(target, p) + dec(result['risk']))
        if dec(result['margin']) + self.reserved(state) + dec(result['costs']) > self.equity(state):
            raise Rejected('可用保证金不足，不能叠加已有风险')
        return result

    def preview(self, request):
        with self.transaction() as (_, state):
            return dict(plan=self.preview_in(state, request), policy_revision=state['policy_revision'], mode='paper')

    def auto_stop(self, request):
        """Reverse one cash-R from selected collateral, current quotes and costs.

        This is a calculation only. Entry still rechecks all guards and sizing.
        Tick rounding moves toward entry, never above the cash-risk budget.
        """
        with self.read_transaction() as (_, state):
            self.entry_guard(state)
            p, symbol, side = self.policy(state), request['symbol'], request['side']
            instrument = state['instruments'].get(symbol)
            mode = request.get('sizing_mode')
            if mode is None:
                if p.entry_sizing == 'exposure':
                    raise Rejected('开仓规则已更新，请更新 App 后重新开单')
                mode = 'margin'
            if not instrument or side not in ('long', 'short'):
                raise Rejected('请选择有效合约和仓位比例')
            if p.entry_sizing == 'exposure' and mode != 'exposure':
                raise Rejected('当前规则要求按剩余名义额度计算仓位')
            if p.entry_sizing == 'margin' and mode != 'margin':
                raise Rejected('当前旧规则要求保证金仓位比例')
            leverage = p.target_leverage(symbol, instrument, self.inherited_leverage(state, symbol)) if mode == 'exposure' else request['leverage']
            fraction = dec(request['fraction'], '仓位比例')
            if fraction < 0 or fraction > 1 or (mode == 'margin' and fraction <= 0):
                raise Rejected('仓位比例必须在 0 和 1 之间')
            if mode == 'margin':
                if type(leverage) is not int or not 1 <= leverage <= min(self.inherited_leverage(state, symbol) or p.leverage_cap(symbol), int(instrument['max_leverage'])):
                    raise Rejected('杠杆超过币种或交易所上限')
                if p.margin_cap(symbol) is None or fraction > p.margin_cap(symbol):
                    raise Rejected('保证金比例超过当前币种上限')
            q = self.quote(state, symbol)
            direction = 1 if side == 'long' else -1
            entry = positive(request['price'], '委托价格') if request['kind'] == 'limit' else positive(q['ask'] if side == 'long' else q['bid']) * (1 + direction * dec(p.slippage_rate))
            equity = positive(self.equity(state))
            limit = self.quantity_limit(state, symbol, entry, side, leverage)
            if mode == 'exposure':
                raw_remaining = self.remaining_exposure_notional(state, symbol)
                requested_quantity = raw_remaining * fraction / entry
                cost_per_unit = entry * (2 * dec(p.fee_rate) + dec(p.slippage_rate))
                collateral_limit = self.available_entry_quantity_limit(state, entry, leverage, cost_per_unit)
                limit = min(limit, collateral_limit) if limit is not None else collateral_limit
            else:
                requested_quantity = equity * fraction * dec(leverage) / entry
            quantity = floor_step(min(requested_quantity, limit if limit is not None else Decimal('Infinity')), positive(instrument['step']))
            if quantity < positive(instrument['minimum']):
                raise Rejected('剩余仓位额度低于最小委托量')
            distance = equity * dec(p.risk_fraction) / quantity - entry * (2 * dec(p.fee_rate) + dec(p.slippage_rate))
            stop = (ceil_step if side == 'long' else floor_step)(entry - direction * distance, positive(instrument['tick']))
            if distance <= 0 or stop <= 0 or (entry - stop) * direction <= 0:
                raise Rejected('此仓位无法设置有效的 1R 止损，请调整仓位比例')
            ticket = dict(request, stop=str(stop), sizing_mode=mode)
            sized = self.preview_in(state, ticket)
            if mode == 'exposure' and dec(sized['quantity']) < quantity:
                # If available margin or an exposure ceiling reduced the
                # requested quantity, retain a 1R stop for the amount actually
                # authorized by preview.
                distance = equity * dec(p.risk_fraction) / dec(sized['quantity']) - entry * (
                    2 * dec(p.fee_rate) + dec(p.slippage_rate))
                stop = (ceil_step if side == 'long' else floor_step)(
                    entry - direction * distance, positive(instrument['tick']))
                if distance <= 0 or stop <= 0 or (entry - stop) * direction <= 0:
                    raise Rejected('此仓位无法设置有效的 1R 止损，请调整仓位比例')
                ticket['stop'] = str(stop)
                sized = self.preview_in(state, ticket)
            return dict(stop=str(stop), plan=sized, policy_revision=state['policy_revision'])

    def command(self, key, payload, operation):
        if not isinstance(key, str) or not 16 <= len(key) <= 80 or not all(c.isalnum() or c in '-_' for c in key):
            raise Rejected('请求编号无效')
        fingerprint = hashlib.sha256(encoded(payload).encode()).hexdigest()
        with self.transaction() as (db, state):
            prior = db.execute('SELECT fingerprint,body FROM receipts WHERE key=?', (key,)).fetchone()
            if prior:
                if prior[0] != fingerprint:
                    raise Rejected('同一请求编号不能用于不同指令')
                return json.loads(prior[1])
            result = operation(db, state)
            db.execute('INSERT INTO receipts VALUES(?,?,?)', (key, fingerprint, encoded(result)))
            return result

    def enter(self, key, request, revision):
        def execute(db, state):
            self.policy(state)
            if revision != state['policy_revision']:
                raise Rejected('规则版本已变化，请重新预览')
            sized = self.preview_in(state, request)
            order = dict(id=uuid.uuid4().hex, **request, plan=sized, created=self.clock(),
                         expires=self.clock() + self.policy(state).pending_ttl_seconds,
                         entry_day=state['daily']['key'])
            state['daily']['entries'] += 1
            if request['kind'] == 'market':
                self.fill(db, state, order, dec(sized['entry']))
                status = 'filled'
            else:
                state['pending'].append(order)
                status = 'pending'
            self.event(db, 'entry_accepted', dict(id=order['id'], status=status, request=request, plan=sized))
            return dict(id=order['id'], status=status, mode='paper', plan=sized)
        return self.command(key, dict(action='enter', request=request, revision=revision), execute)

    def fill(self, db, state, order, price):
        p, original = self.policy(state), order['plan']
        quantity = dec(original['quantity'])
        direction = 1 if order['side'] == 'long' else -1
        add_to = original.get('add_to')
        target = None
        if add_to:
            target = next((pos for pos in state['positions'] if pos['id'] == add_to), None)
            if target is None or target['symbol'] != order['symbol'] or target['side'] != order['side']:
                raise Rejected('加仓目标持仓已消失或方向变化，暂停成交')
            if original.get('parent_snapshot') != self.scale_parent_snapshot(target):
                raise Rejected('加仓目标持仓已变化，请重新预览')
            if any(x['symbol'] == order['symbol'] and x['id'] != order['id'] for x in state['pending']):
                raise Rejected('同币种已有挂单，暂停加仓')
        stop = dec(target['stop']) if target else dec(original['stop'])
        distance = (price - stop) * direction
        risk = quantity * (distance + price * (2 * dec(p.fee_rate) + dec(p.slippage_rate)))
        current_budget = self.equity(state) * dec(p.risk_fraction) * (dec(order['fraction']) if order.get('sizing_mode', 'risk') == 'risk' else 1)
        if target:
            if distance <= 0 or risk > dec(original['risk']):
                raise Rejected('成交条件已超出已预览的加仓风险')
        elif distance <= 0 or risk > min(dec(original['budget']), current_budget):
            raise Rejected('成交条件已超出原始风险预算')
        fee = quantity * price * dec(p.fee_rate)
        if target:
            parent_risk = remaining_risk(target, p)
            mark = positive(state['quotes'][order['symbol']]['last'])
            immediate_loss = max(Decimal(0), (price - mark) * direction * quantity)
            merged_risk_limit = min(dec(original['merged_budget']),
                                    (self.equity(state) - fee - immediate_loss) * dec(p.risk_fraction))
            if parent_risk + risk > merged_risk_limit:
                raise Rejected('成交后合并仓位风险超过当前单笔风险上限')
        leverage = dec(target['leverage']) if target else dec(original.get('leverage', order['leverage']))
        margin = quantity * price / leverage
        quantity_limit = self.quantity_limit(state, order['symbol'], price, order['side'], leverage, order['id'])
        if quantity_limit is not None and quantity > quantity_limit:
            raise Rejected('成交时账户总名义仓位额度不足')
        if (p.margin_mode == 'isolated' and margin <= risk) or margin + fee + self.reserved(state, order['id']) > self.equity(state):
            raise Rejected('成交时保证金检查失败')
        if target:
            updates = merged_position(target, quantity, price, fee, p)
            tick = positive(state['instruments'][order['symbol']]['tick'])
            override_input = order.get('take_profits')
            if override_input is None:
                override_input = target.get('take_profit_overrides')
            overrides = normalize_take_profit_overrides(override_input, p,
                updates['entry'], order['side'], tick)
            active_targets, frozen = resolve_take_profit_targets(updates['entry'], updates['quantity'],
                updates['risk'], updates['entry_fee'], p, order['side'], tick, overrides, original.get('take_profit_prices'))
            lifecycle_pnl = dec(target.get('pnl', '0')) - fee
            target.update(updates, take_profit_overrides=overrides, take_profit_prices=active_targets,
                take_profit_frozen=frozen)
            target['pnl'] = str(lifecycle_pnl)
            target.pop('current_r', None)
            state['cash'] = str(dec(state['cash']) - fee)
            self.event(db, 'paper_scale_in', dict(order_id=order['id'], position_id=target['id'],
                price=str(price), quantity=str(quantity), fee=str(fee), plan=original,
                position=copy.deepcopy(target)))
            return
        tick = positive(state['instruments'][order['symbol']]['tick'])
        overrides = normalize_take_profit_overrides(order.get('take_profits'), p, price, order['side'], tick)
        active_targets, frozen = resolve_take_profit_targets(price, quantity, risk, fee, p,
            order['side'], tick, overrides, original.get('take_profit_prices'))
        pos = dict(id=order['id'], symbol=order['symbol'], side=order['side'], direction=direction,
            quantity=str(quantity), remaining=str(quantity), entry=str(price), original_stop=str(stop),
            stop=str(stop), risk=str(risk), margin=str(margin), leverage=json_number(leverage), margin_mode=p.margin_mode,
            entry_fee=str(fee), pnl=str(-fee), breakeven=False, stages=[], opened=self.clock(),
            policy=p.document(), policy_revision=state['policy_revision'], protection='server-paper-only',
            take_profit_overrides=overrides, take_profit_prices=active_targets,
            take_profit_frozen=frozen)
        state['cash'] = str(dec(state['cash']) - fee)
        state['positions'].append(pos)
        self.event(db, 'paper_fill', pos)

    def reduce(self, db, state, pos, quantity, price, reason):
        quantity = min(positive(quantity), dec(pos['remaining']))
        p = Policy.parse(pos['policy'])
        execution = price * (1 - pos['direction'] * dec(p.slippage_rate))
        fee = execution * quantity * dec(p.fee_rate)
        pnl = (execution - dec(pos['entry'])) * quantity * pos['direction'] - fee
        state['cash'] = str(dec(state['cash']) + pnl)
        pos['pnl'] = str(dec(pos['pnl']) + pnl)
        pos['remaining'] = str(dec(pos['remaining']) - quantity)
        self.event(db, 'paper_reduce', dict(id=pos['id'], quantity=str(quantity), price=str(execution), fee=str(fee), pnl=str(pnl), reason=reason))
        if dec(pos['remaining']) == 0:
            state['positions'].remove(pos)
            pos['closed'], pos['close_reason'] = self.clock(), reason
            state['closed'].append(copy.deepcopy(pos))
            # Whole-trade net PnL includes every partial fill and all modeled fees.
            self.settle_discipline(state, pos['pnl'], self.clock())
            self.day(state)

    def manage(self, db, state, pos, q):
        p, mark = Policy.parse(pos['policy']), positive(q['last'])
        direction = pos['direction']
        if (mark - dec(pos['stop'])) * direction <= 0:
            self.reduce(db, state, pos, dec(pos['remaining']), positive(q['bid'] if direction == 1 else q['ask']), 'stop')
            return
        quantity, entry, risk = dec(pos['quantity']), dec(pos['entry']), dec(pos['risk'])
        # Fixed-original-size net R, independent of later reductions.
        net = (mark - entry) * quantity * direction - dec(pos['entry_fee']) - mark * quantity * dec(p.fee_rate)
        r = net / risk
        pos['current_r'] = str(r)
        if not pos['breakeven'] and r >= dec(p.breakeven_r):
            unit_entry_fee = dec(pos['entry_fee']) / quantity
            fee, slip = dec(p.fee_rate), dec(p.slippage_rate)
            target = (entry + unit_entry_fee) / ((1 - slip) * (1 - fee)) if direction == 1 else (entry - unit_entry_fee) / ((1 + slip) * (1 + fee))
            tick = dec(state['instruments'][pos['symbol']]['tick'])
            target = ceil_step(target, tick) if direction == 1 else floor_step(target, tick)
            pos['stop'] = str(max(dec(pos['stop']), target) if direction == 1 else min(dec(pos['stop']), target))
            pos['breakeven'] = True
            self.event(db, 'breakeven_armed', dict(id=pos['id'], stop=pos['stop'], trigger_r=str(r)))
            if (mark - dec(pos['stop'])) * direction <= 0:
                self.reduce(db, state, pos, dec(pos['remaining']), positive(q['bid'] if direction == 1 else q['ask']), 'breakeven_crossed')
                return
        overrides = pos.get('take_profit_overrides') or [None] * len(p.take_profits)
        frozen = pos.get('take_profit_frozen') or [False] * len(p.take_profits)
        for i, (target, fraction) in enumerate(p.take_profits):
            if i in pos['stages']:
                continue
            trigger_price = pos['take_profit_prices'][i] if frozen[i] else overrides[i]
            if trigger_price is not None:
                if (mark - dec(trigger_price)) * direction < 0:
                    continue
            elif r < dec(target):
                continue
            remaining = dec(pos['remaining'])
            step = dec(state['instruments'][pos['symbol']]['step'])
            amount = remaining if i == len(p.take_profits) - 1 else min(remaining, floor_step(quantity * dec(fraction), step))
            pos['stages'].append(i)
            if amount:
                reason = f'tp_price_{trigger_price}' if trigger_price is not None else f'tp_{target}R'
                self.reduce(db, state, pos, amount, positive(q['bid'] if direction == 1 else q['ask']), reason)
            else:
                self.event(db, 'tp_below_lot', dict(id=pos['id'], stage=i))
            if dec(pos['remaining']) == 0:
                break

    def update_market(self, instruments, quotes):
        """Trusted worker input only; there is intentionally no HTTP price setter."""
        with self.transaction() as (db, state):
            now = self.clock()
            # Retain metadata for managed exits, but never open a delisted contract.
            owned = {p['symbol'] for p in state['positions']} | {p['symbol'] for p in state['pending']}
            retained = {k:dict(v, tradable=False) for k,v in state['instruments'].items() if k in owned and k not in instruments}
            state['instruments'] = dict(retained, **instruments)
            accepted = {}
            for symbol, q in quotes.items():
                if symbol not in instruments or not 0 <= now - float(q['time']) <= 15:
                    continue
                if any(positive(q[k]) <= 0 for k in ('last', 'bid', 'ask')) or dec(q['bid']) > dec(q['ask']):
                    continue
                if float(q['time']) < state['quotes'].get(symbol, {}).get('time', 0):
                    continue
                instrument = instruments[symbol]
                for k in ('step', 'tick', 'minimum'):
                    positive(instrument[k])
                accepted[symbol] = q
                state['instruments'][symbol] = instrument
                state['quotes'][symbol] = q
            state['heartbeat'] = now
            state['quote_error'] = None if accepted else '未收到新鲜有效行情'
            self.day(state)
            for pos in list(state['positions']):
                if pos['symbol'] in accepted:
                    self.manage(db, state, pos, accepted[pos['symbol']])
            self.day(state)
            for order in list(state['pending']):
                reason = None
                try:
                    self.entry_guard(state, order['id'], add_to=(order.get('plan') or {}).get('add_to'))
                    if now >= order['expires']:
                        raise Rejected('挂单已过期')
                    if order['symbol'] not in accepted:
                        continue
                    q = accepted[order['symbol']]
                    price = positive(q['ask'] if order['side'] == 'long' else q['bid'])
                    crosses = price <= dec(order['price']) if order['side'] == 'long' else price >= dec(order['price'])
                    if not crosses:
                        continue
                    if order['entry_day'] != state['daily']['key'] and state['daily']['entries'] >= self.entry_limit(state):
                        raise Rejected('跨日成交已超出今日开仓次数')
                    self.fill(db, state, order, price)
                    if order['entry_day'] != state['daily']['key']:
                        state['daily']['entries'] += 1
                except Rejected as exc:
                    reason = str(exc)
                state['pending'].remove(order)
                if reason:
                    self.refund_entry(db, state, order)
                    self.event(db, 'pending_cancelled_by_guard', dict(id=order['id'], reason=reason))
            proposal = state['policy_proposal']
            if proposal and now >= proposal['effective_after'] and not state['positions'] and not state['pending']:
                state['policy'] = proposal['policy']
                state['policy_revision'] += 1
                state['policy_proposal'] = None
                self.event(db, 'policy_activated', dict(revision=state['policy_revision']))

    def market_failed(self, message):
        with self.transaction() as (_, state):
            state['quote_error'] = message[:180]
            state['heartbeat'] = self.clock()

    def cancel(self, key, identity):
        def execute(db, state):
            order = next((x for x in state['pending'] if x['id'] == identity), None)
            if order is None:
                raise Rejected('挂单不存在')
            state['pending'].remove(order)
            self.refund_entry(db, state, order)
            self.event(db, 'pending_cancelled', dict(id=identity))
            return dict(id=identity, status='cancelled', mode='paper')
        return self.command(key, dict(action='cancel', id=identity), execute)

    def close(self, key, identity, fraction='1'):
        fraction = close_fraction(fraction)
        def execute(db, state):
            pos = next((x for x in state['positions'] if x['id'] == identity), None)
            if pos is None:
                raise Rejected('持仓不存在')
            q = self.quote(state, pos['symbol'])
            quantity = close_quantity(pos['remaining'], state['instruments'][pos['symbol']], fraction)
            self.reduce(db, state, pos, quantity, dec(q['bid'] if pos['direction'] == 1 else q['ask']), 'manual')
            return dict(id=identity, status='closed' if dec(pos['remaining']) == 0 else 'reduced',
                        quantity=str(quantity), remaining=pos['remaining'], mode='paper')
        request = dict(action='close', id=identity)
        if fraction != 1:  # Preserve the fingerprint of pre-upgrade full closes.
            request['fraction'] = str(fraction.normalize())
        return self.command(key, request, execute)

    def tighten(self, key, identity, stop):
        def execute(db, state):
            pos = next((x for x in state['positions'] if x['id'] == identity), None)
            if pos is None:
                raise Rejected('持仓不存在')
            value = positive(stop)
            tick = dec(state['instruments'][pos['symbol']]['tick'])
            mark = dec(self.quote(state, pos['symbol'])['last'])
            if value % tick or (value - dec(pos['stop'])) * pos['direction'] < 0 or (mark - value) * pos['direction'] <= 0:
                raise Rejected('止损只能收紧，且不能越过当前价格')
            pos['stop'] = str(value)
            self.event(db, 'stop_tightened', dict(id=identity, stop=str(value)))
            return dict(id=identity, stop=str(value), mode='paper')
        return self.command(key, dict(action='tighten', id=identity, stop=stop), execute)

    def change_protection(self, key, identity, stop=None, take_profits=None):
        """Tighten an open paper stop and/or adjust outstanding TP prices."""
        request = dict(action='change_protection', id=identity, stop=stop, take_profits=take_profits)

        def execute(db, state):
            pos = next((x for x in state['positions'] if x['id'] == identity), None)
            if pos is None:
                raise Rejected('持仓不存在')
            policy = Policy.parse(pos['policy'])
            count = len(policy.take_profits)
            if take_profits is not None and len(take_profits) != count:
                raise Rejected('止盈档位与当前持仓规则不匹配')
            selected = stop is not None or (take_profits is not None and any(x is not None for x in take_profits))
            if not selected:
                raise Rejected('请至少填写一项要修改的保护价格')

            quote = self.quote(state, pos['symbol'])
            mark = positive(quote['last'], '最新价格')
            tick = positive(state['instruments'][pos['symbol']]['tick'], '最小价格单位')
            if stop is not None:
                value = positive(stop, '止损价格')
                if not price_on_step(value, tick, '止损价格') or (value < dec(pos['stop']) if pos['direction'] == 1 else value > dec(pos['stop'])) or (value >= mark if pos['direction'] == 1 else value <= mark):
                    raise Rejected('止损只能收紧，且不能越过当前价格')
                pos['stop'] = str(value)

            old_overrides = pos.get('take_profit_overrides') or [None] * count
            overrides = list(old_overrides)
            if not isinstance(pos.get('take_profit_prices'), list) or len(pos['take_profit_prices']) != count:
                pos['take_profit_overrides'] = normalize_take_profit_overrides(
                    overrides, policy, pos['entry'], pos['side'], tick)
                pos['take_profit_prices'] = take_profit_targets(pos['entry'], pos['quantity'],
                    pos['risk'], pos['entry_fee'], policy, pos['side'], tick,
                    pos['take_profit_overrides'])
            if any(value is not None for value in take_profits or ()):
                if len(old_overrides) != count:
                    raise Rejected('当前持仓的止盈档位无法核对')
                stages = set(pos.get('stages') or [])
                for index, raw in enumerate(take_profits):
                    if raw is None:
                        continue
                    if index in stages:
                        raise Rejected('已完成的止盈档位不能修改')
                    value = positive(raw, '止盈价格')
                    if (value <= mark if pos['direction'] == 1 else value >= mark):
                        raise Rejected('止盈价格必须位于当前价格的有利一侧')
                    overrides[index] = str(value)
                normalized = normalize_take_profit_overrides(overrides, policy, pos['entry'], pos['side'], tick)
                prices = [pos['take_profit_prices'][index] if value is None else normalized[index]
                          for index, value in enumerate(take_profits)]
                active_prices = take_profit_targets(pos['entry'], pos['quantity'], pos['risk'],
                    pos['entry_fee'], policy, pos['side'], tick, prices)
                pos['take_profit_overrides'] = normalized
                pos['take_profit_prices'] = active_prices
            self.event(db, 'protection_changed', dict(id=identity, stop=pos['stop'],
                take_profit_prices=pos.get('take_profit_prices')))
            return dict(id=identity, status='verified', stop=pos['stop'],
                take_profit_prices=pos.get('take_profit_prices'), mode='paper')

        return self.command(key, request, execute)

    def entry_stop(self, key, identity):
        with self.read_transaction() as (_, state):
            pos = next((x for x in state['positions'] if x['id'] == identity), None)
            if not pos:
                raise Rejected('持仓不存在或已平仓')
            value = (ceil_step if pos['direction'] == 1 else floor_step)(dec(pos['entry']), dec(state['instruments'][pos['symbol']]['tick']))
            if (dec(pos['stop']) - value) * pos['direction'] >= 0:
                return dict(id=identity, stop=pos['stop'], status='already_protected')
        return self.tighten(key, identity, str(value))

    def snapshot(self):
        with self.transaction() as (_, state):
            return self.render_snapshot(state)

    def render_snapshot(self, state):
        self.day(state)
        result = copy.deepcopy(state)
        for position in result.get('positions', []):
            policy = Policy.parse(position['policy'])
            count = len(policy.take_profits)
            prices = position.get('take_profit_prices')
            if not isinstance(prices, list) or len(prices) != count:
                prices = [None] * count
                if position.get('protection') == 'server-paper-only':
                    try:
                        tick = positive(state['instruments'][position['symbol']]['tick'])
                        overrides = normalize_take_profit_overrides(
                            position.get('take_profit_overrides'), policy, position['entry'],
                            position['side'], tick)
                        prices = take_profit_targets(position['entry'], position['quantity'],
                            position['risk'], position['entry_fee'], policy, position['side'], tick, overrides)
                    except (Rejected, KeyError, TypeError):
                        pass
            position['take_profit_prices'] = prices
            editable = (position.get('protection') == 'server-paper-only'
                or (position.get('protection') == 'exchange-verified' and all(price is not None for price in prices)))
            position['protection_editable'] = bool(editable and not position.get('protection_edit_pending')
                and not position.get('take_profit_error'))
            stages = set(position.get('stages') or [])
            position['take_profit_editable'] = [
                bool(position['protection_editable'] and prices[index] is not None and index not in stages)
                for index in range(count)
            ]
        if state['policy']:
            result['policy'] = self.policy(state).document()
        result['equity'] = str(self.equity(state))
        result['server_time'] = self.clock()
        if state['policy'] and state['daily']:
            p = self.policy(state)
            start = dec(state['daily']['start_equity'])
            limit = self.entry_limit(state)
            result['discipline'] = dict(entries_remaining=max(0, limit - state['daily']['entries']),
                entries_limit=limit, entries_bonus=limit-p.max_entries,
                losses_remaining=0 if state['cooldown_until'] > self.clock() else max(0, p.loss_streak - state['loss_streak']),
                daily_loss_limit=str(start * dec(p.daily_loss_fraction)),
                daily_loss_remaining=str(max(Decimal(0), start * dec(p.daily_loss_fraction) + self.daily_pnl(state))))
        result['blockers'] = []
        try:
            self.entry_guard(state)
        except Rejected as exc:
            if str(exc) == '持仓与挂单数量已达上限':
                eligible = next((pos for pos in state['positions']
                    if not any(order['symbol'] == pos['symbol'] for order in state['pending'])), None)
                if eligible is not None:
                    try:
                        self.entry_guard(state, add_to=eligible['id'])
                    except Rejected as scale_exc:
                        result['blockers'].append(str(scale_exc))
                else:
                    result['blockers'].append(str(exc))
            else:
                result['blockers'].append(str(exc))
        if not any(0 <= self.clock() - q['time'] <= 15 for q in state['quotes'].values()):
            result['blockers'].append('没有新鲜行情')
        result['closed'] = result['closed'][-30:]
        return result

    def events(self, limit=100):
        with self.connect() as db:
            return [dict(seq=row[0], time=row[1], kind=row[2], body=json.loads(row[3]))
                    for row in db.execute('SELECT seq,time,kind,body FROM journal ORDER BY seq DESC LIMIT ?', (min(limit, 200),))]

    def statistics(self, days=30):
        from .statistics import summarize
        if type(days) is not int or days not in (0, 7, 30):
            raise Rejected('统计区间无效')
        # One SQLite read transaction keeps the complete state and fee journal
        # at the same revision without mutating the trading ledger.
        with self.read_transaction() as (db, state):
            fees = []
            for timestamp, kind, body in db.execute("SELECT time,kind,body FROM journal WHERE kind IN ('paper_fill','paper_reduce')"):
                event = json.loads(body)
                fees.append((timestamp, event['entry_fee'] if kind == 'paper_fill' else event['fee']))
            return summarize(state, self.equity(state), fees, self.clock(), days)
